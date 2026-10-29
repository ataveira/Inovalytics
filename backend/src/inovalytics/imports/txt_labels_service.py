"""Serviço de importação de rótulos txt (F1-07c): casa rótulos com imagens e grava tudo ou nada.

Mesmo desenho do serviço COCO, sobre o núcleo `import_core`: a fase 1 só lê (dataset, imagens,
classes) e acumula todos os defeitos (os do parser, adiados com `collect_errors=True`, e os de
casamento); com qualquer defeito nada é escrito. A fase 2 roda numa transação com o bloqueio do
projeto, relê o estado, cria as classes ausentes e insere só o excedente do multiconjunto
`(imagem, classe, cx, cy, w, h)`, em blocos.

As caixas já são normalizadas: nenhuma dimensão de imagem é usada. Independe de HTTP e de S3 e não
toca em disco: a chave do rótulo só é reduzida a texto (`sanitize_file_name`) e comparada por nome.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from inovalytics.imports.common import shown
from inovalytics.imports.errors import ErrorCollector, ImportRejectedError
from inovalytics.imports.import_core import (
    ImportReport,
    ImportRepository,
    Planned,
    missing_classes,
    read_stored_images,
    simulate,
    write_all,
)
from inovalytics.imports.names_yaml import TxtLimits
from inovalytics.imports.txt_labels_parser import K_LABEL, ParsedTxtLabels
from inovalytics.uploads.naming import sanitize_file_name

if TYPE_CHECKING:
    from inovalytics.db.repository import ImportImage

INSERT_CHUNK = 5_000
TXT_SUFFIX = ".txt"

__all__ = ["INSERT_CHUNK", "ImportReport", "import_txt_labels"]


@dataclass(frozen=True, slots=True)
class _Decl:
    """Classe declarada em `names`: o id é o índice (as linhas de rótulo citam o índice)."""

    id: int
    name: str
    index: int


def _stem(file_name: str) -> str | None:
    """Nome da imagem gravada sem a última extensão (None se não tem extensão)."""
    dot = file_name.rfind(".")
    return file_name[:dot] if dot > 0 else None


def _match(
    parsed: ParsedTxtLabels, stored: Sequence[ImportImage], col: ErrorCollector
) -> dict[int, ImportImage]:
    """Arquivo de rótulo (posição) -> imagem gravada, por nome-base exato (sensível a caixa)."""
    by_stem: dict[str, list[ImportImage]] = {}
    for img in stored:
        stem = _stem(img.file_name)
        if stem is not None:
            by_stem.setdefault(stem, []).append(img)
    matched: dict[int, ImportImage] = {}
    seen: set[str] = set()
    for f in parsed.files:
        name = sanitize_file_name(f.base_name + TXT_SUFFIX)
        shown_name = lambda f=f: shown(f.name)  # noqa: E731  (lazy: coletor cheio não paga)
        if not name.endswith(TXT_SUFFIX) or name == TXT_SUFFIX:
            col.add("rotulo_nome_invalido", K_LABEL, None, f.index, "nome de rótulo inválido",
                    file=shown_name)  # fmt: skip
            continue
        stem = name[: -len(TXT_SUFFIX)]
        if stem in seen:
            msg = "mais de um arquivo de rótulo reduz ao mesmo nome-base"
            col.add("rotulo_nome_duplicado", K_LABEL, None, f.index, msg, file=shown_name)
            continue
        seen.add(stem)
        found = by_stem.get(stem, [])
        if not found:
            msg = "nenhuma imagem do dataset corresponde ao arquivo de rótulo"
            col.add("imagem_nao_encontrada", K_LABEL, None, f.index, msg, file=shown_name)
        elif len(found) > 1:
            msg = "mais de uma imagem gravada corresponde ao arquivo de rótulo"
            col.add("imagem_ambigua", K_LABEL, None, f.index, msg, file=shown_name)
        else:
            matched[f.index] = found[0]
    return matched


def _plan(parsed: ParsedTxtLabels, matched: dict[int, ImportImage]) -> list[Planned]:
    """Caixas na ordem (arquivo, linha); os valores gravados são exatamente os do parser."""
    planned: list[Planned] = []
    for b in parsed.boxes:
        img = matched.get(b.file_index)
        if img is not None:
            planned.append(Planned(img.id, b.class_id, (b.cx, b.cy, b.w, b.h)))
    return planned


def _report(
    status: str,
    dataset_id: int,
    parsed: ParsedTxtLabels,
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
        warnings=parsed.warnings,
        warnings_total=parsed.warnings_total,
        images_without_boxes=sum(1 for f in parsed.files if f.box_count == 0),
    )


async def import_txt_labels(
    repo: ImportRepository,
    dataset_id: int,
    parsed: ParsedTxtLabels,
    *,
    create_classes: bool = True,
    dry_run: bool = False,
    limits: TxtLimits | None = None,
) -> ImportReport:
    """Importa as caixas de `parsed` no dataset. Levanta `NotFoundError` (dataset inexistente),
    `ImportRejectedError` (defeitos por item, nada gravado) ou erros de domínio do repositório."""
    lim = limits or TxtLimits()
    dataset = await repo.get_dataset(dataset_id)
    project_id = dataset.project_id
    decls = [_Decl(i, n, i) for i, n in enumerate(parsed.names)]

    # Fase 1: somente leitura; todos os defeitos de uma vez.
    stored = await read_stored_images(repo, dataset_id, lim.max_images, ImportRejectedError)
    classes = await repo.list_classes(project_id)
    col = ErrorCollector(lim.max_errors)
    col.absorb(parsed.errors, parsed.errors_total)
    matched = _match(parsed, stored, col)
    planned = _plan(parsed, matched)
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
        status, dataset_id, parsed, created=len(missing), inserted=len(rows), already=already
    )
