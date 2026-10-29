"""Serviço de importação Pascal VOC (F1-08c): casa XMLs com imagens e grava tudo ou nada.

Mesmo desenho dos serviços COCO e de rótulos txt, sobre o núcleo `import_core`: a fase 1 só lê
(dataset, imagens, classes) e acumula todos os defeitos (os do parser, adiados com
`collect_errors=True`, os de casamento, de `<size>` e de caixa); com qualquer defeito nada é
escrito. A fase 2 roda numa transação com o bloqueio do projeto, relê o estado, cria as classes
ausentes e insere só o excedente do multiconjunto `(imagem, classe, cx, cy, w, h)`, em blocos.

A conversão para coordenadas normalizadas usa as dimensões GRAVADAS da imagem (nunca as do XML):
`<size>` só é conferido e, se divergir, é erro. Independe de HTTP e de S3 e não toca em disco: a
chave do XML e `<filename>` só são reduzidos a texto e comparados por nome.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from inovalytics.imports.common import shown
from inovalytics.imports.errors import ErrorCollector, ImportRejectedError
from inovalytics.imports.import_core import (
    ImportReport,
    ImportRepository,
    MatchItem,
    Planned,
    match_images,
    missing_classes,
    read_stored_images,
    simulate,
    write_all,
)
from inovalytics.imports.voc_parser import (
    K_ANNOTATION,
    K_OBJECT,
    SUFFIX,
    ParsedVoc,
    VocFile,
    VocLimits,
    convert_box,
)

if TYPE_CHECKING:
    from inovalytics.db.repository import ImportImage

INSERT_CHUNK = 5_000

__all__ = ["INSERT_CHUNK", "ImportReport", "import_voc"]


@dataclass(frozen=True, slots=True)
class _Decl:
    """Classe encontrada nos XMLs: o id é o índice na ordem da primeira aparição."""

    id: int
    name: str
    index: int


def _match(
    parsed: ParsedVoc, stored: Sequence[ImportImage], col: ErrorCollector
) -> dict[int, ImportImage]:
    """Arquivo (posição) -> imagem gravada: regra de casamento do ADR-010 (núcleo compartilhado)."""
    items = [MatchItem(f.index, f.key, f.base_name, f.declared_filename) for f in parsed.files]
    return match_images(items, stored, col, suffix=SUFFIX, kind=K_ANNOTATION)


def _check_sizes(
    parsed: ParsedVoc, matched: dict[int, ImportImage], col: ErrorCollector
) -> set[int]:
    """`<size>` precisa ser igual às dimensões gravadas. Devolve os arquivos divergentes."""
    bad: set[int] = set()
    for f in parsed.files:
        img = matched.get(f.index)
        if img is None or f.declared_size is None:
            continue
        if f.declared_size != (img.width_px, img.height_px):
            bad.add(f.index)
            col.add(
                "tamanho_divergente",
                K_ANNOTATION,
                None,
                f.index,
                "<size> difere das dimensões da imagem gravada",
                file=lambda f=f: shown(f.key),
                line=f.size_line,
            )
    return bad


def _plan(
    parsed: ParsedVoc,
    matched: dict[int, ImportImage],
    skip: set[int],
    col: ErrorCollector,
    warn: ErrorCollector,
) -> list[Planned]:
    """Caixas na ordem (arquivo, documento) já normalizadas com as dimensões gravadas."""
    by_index: dict[int, VocFile] = {f.index: f for f in parsed.files}
    planned: list[Planned] = []
    for b in parsed.boxes:
        img = matched.get(b.file_index)
        if img is None or b.file_index in skip:
            continue  # arquivo com erro: já relatado
        got = convert_box(b.xmin, b.ymin, b.xmax, b.ymax, img.width_px, img.height_px)
        label = lambda i=b.file_index: shown(by_index[i].key)  # noqa: E731
        if got is None:
            msg = "a caixa não cabe no esquema (centro fora da imagem ou maior que ela)"
            col.add("caixa_nao_representavel", K_OBJECT, None, b.obj_index, msg,
                    file=label, line=b.line, group=b.file_index)  # fmt: skip
            continue
        norm, outside = got
        if outside:
            msg = "a caixa ultrapassa a borda da imagem e foi mantida sem recorte"
            warn.add("caixa_fora_da_imagem", K_OBJECT, None, b.obj_index, msg,
                     file=label, line=b.line, group=b.file_index)  # fmt: skip
        planned.append(Planned(img.id, b.class_id, norm))
    return planned


def _analyse(
    parsed: ParsedVoc, stored: Sequence[ImportImage], col: ErrorCollector, warn: ErrorCollector
) -> list[Planned]:
    """Fase 1 inteira que usa CPU: casamento, conferência de `<size>` e planejamento."""
    matched = _match(parsed, stored, col)
    bad_size = _check_sizes(parsed, matched, col)
    return _plan(parsed, matched, bad_size, col, warn)


def _report(
    status: str,
    dataset_id: int,
    parsed: ParsedVoc,
    warn: ErrorCollector,
    *,
    created: int,
    inserted: int,
    already: int,
) -> ImportReport:
    declared = len(parsed.names)
    return ImportReport(
        status=status,
        dataset_id=dataset_id,
        images_declared=parsed.files_count,
        images_matched=parsed.files_count,
        classes_declared=declared,
        classes_created=created,
        classes_existing=declared - created,
        annotations_in_file=parsed.boxes_count,
        annotations_imported=inserted,
        annotations_already_existing=already,
        crowd_ignored=0,
        segmentations_ignored=0,
        warnings=tuple(warn.items()),
        warnings_total=warn.total,
        images_without_boxes=sum(1 for f in parsed.files if f.box_count == 0),
        ignored_difficult=parsed.ignored_difficult,
        ignored_truncated=parsed.ignored_truncated,
        ignored_occluded=parsed.ignored_occluded,
        ignored_pose=parsed.ignored_pose,
        ignored_parts=parsed.ignored_parts,
        without_size=parsed.without_size,
        origin_zero_adjustments=parsed.origin_zero_adjustments,
    )


async def import_voc(
    repo: ImportRepository,
    dataset_id: int,
    parsed: ParsedVoc,
    *,
    create_classes: bool = True,
    dry_run: bool = False,
    limits: VocLimits | None = None,
) -> ImportReport:
    """Importa as caixas de `parsed` no dataset. Levanta `NotFoundError` (dataset inexistente),
    `ImportRejectedError` (defeitos por item, nada gravado) ou erros de domínio do repositório."""
    lim = limits or VocLimits()
    dataset = await repo.get_dataset(dataset_id)
    project_id = dataset.project_id
    decls = [_Decl(i, n, i) for i, n in enumerate(parsed.names)]

    # Fase 1: somente leitura; todos os defeitos de uma vez.
    stored = await read_stored_images(repo, dataset_id, lim.max_images, ImportRejectedError)
    classes = await repo.list_classes(project_id)
    col = ErrorCollector(lim.max_errors)
    warn = ErrorCollector(lim.max_errors)
    col.absorb(parsed.errors, parsed.errors_total)
    # CPU-bound (casamento de até 50 000 arquivos e planejamento de até 300 000 caixas): fora do
    # laço de eventos; só esta thread toca nos coletores enquanto roda.
    planned = await asyncio.to_thread(_analyse, parsed, stored, col, warn)
    if not create_classes:
        missing_classes(decls, classes, col)
    col.raise_if_any(ImportRejectedError)

    if dry_run:
        missing, rows, already = await simulate(repo, decls, planned, classes)
        status = "simulado"
    else:
        missing, rows, already = await write_all(
            repo,
            project_id=project_id,
            dataset_id=dataset_id,
            stored=stored,
            decls=decls,
            planned=planned,
            create_classes=create_classes,
            max_images=lim.max_images,
            max_errors=lim.max_errors,
            chunk=INSERT_CHUNK,  # lido na chamada: os testes podem reduzi-lo
            exc=ImportRejectedError,
        )
        status = "importado" if missing or rows else "sem_alteracoes"
    return _report(
        status, dataset_id, parsed, warn, created=len(missing), inserted=len(rows), already=already
    )
