"""Serviço de importação COCO (F1-06b): casa imagens e classes e grava tudo ou nada.

Duas fases. Fase 1 só lê (dataset, imagens, classes) e acumula todos os erros por item; com
qualquer erro nada é escrito e nenhuma transação é aberta. Fase 2 roda numa única transação que
primeiro toma o bloqueio consultivo do projeto, relê as classes, cria as ausentes, lê as
anotações existentes das imagens afetadas (uma consulta) e insere só o excedente do multiconjunto
`(imagem, classe, x, y, w, h)`, em blocos. Qualquer exceção desfaz tudo, inclusive as classes.
`dry_run` executa só leituras.

Independe de HTTP e de S3; fala com o banco apenas pelo Protocol `CocoRepository`. Não toca em
disco: o `file_name` do arquivo COCO só é reduzido a texto (`sanitize_file_name`) e comparado.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from inovalytics.imports.coco_parser import (
    K_ANNOTATION,
    K_IMAGE,
    CocoLimits,
    ParsedCoco,
    representable,
    to_normalized,
)
from inovalytics.imports.errors import CocoImportError, ErrorCollector
from inovalytics.imports.import_core import (
    ImportReport,
    Planned,
    missing_classes,
    read_stored_images,
    shown,
    simulate,
    write_all,
)
from inovalytics.imports.import_core import ImportRepository as CocoRepository
from inovalytics.uploads.naming import sanitize_file_name

if TYPE_CHECKING:
    from inovalytics.db.repository import ImportImage

INSERT_CHUNK = 5_000

__all__ = ["INSERT_CHUNK", "CocoRepository", "ImportReport", "import_coco"]


def _match_images(
    parsed: ParsedCoco, stored: Sequence[ImportImage], col: ErrorCollector
) -> dict[int, ImportImage]:
    """Imagem do arquivo (id COCO) -> imagem gravada, por nome sanitizado exato."""
    by_name: dict[str, list[ImportImage]] = {}
    for s in stored:
        by_name.setdefault(s.file_name, []).append(s)
    matched: dict[int, ImportImage] = {}
    seen_names: set[str] = set()
    for img in parsed.images:
        name = sanitize_file_name(img.file_name)
        if name in seen_names:
            col.add(
                "imagem_nome_duplicado",
                K_IMAGE,
                img.id,
                img.index,
                f"mais de uma imagem do arquivo reduz ao nome '{shown(name)}'",
            )
            continue
        seen_names.add(name)
        found = by_name.get(name, [])
        if not found:
            col.add(
                "imagem_nao_encontrada",
                K_IMAGE,
                img.id,
                img.index,
                f"imagem '{shown(img.file_name)}' não encontrada no dataset",
            )
        elif len(found) > 1:
            col.add(
                "imagem_ambigua",
                K_IMAGE,
                img.id,
                img.index,
                f"mais de uma imagem gravada com o nome '{shown(name)}'",
            )
        elif (found[0].width_px, found[0].height_px) != (img.width, img.height):
            col.add(
                "dimensoes_divergentes",
                K_IMAGE,
                img.id,
                img.index,
                "width/height do arquivo diferem das dimensões da imagem gravada",
            )
        else:
            matched[img.id] = found[0]
    return matched


def _plan_boxes(
    parsed: ParsedCoco, matched: dict[int, ImportImage], col: ErrorCollector
) -> list[Planned]:
    """Converte para normalizado com as dimensões GRAVADAS (nunca as declaradas)."""
    planned: list[Planned] = []
    for box in parsed.boxes:
        img = matched.get(box.image_id)
        if img is None:
            continue  # imagem com erro: já relatado
        if not representable(box.x, box.y, box.w, box.h, img.width_px, img.height_px):
            col.add(
                "caixa_nao_representavel",
                K_ANNOTATION,
                box.annotation_id,
                box.index,
                "a caixa não cabe no esquema (centro fora da imagem ou maior que ela)",
            )
            continue
        norm = to_normalized(box.x, box.y, box.w, box.h, img.width_px, img.height_px)
        planned.append(Planned(img.id, box.category_id, norm))
    return planned


def _report(
    status: str,
    dataset_id: int,
    parsed: ParsedCoco,
    *,
    created: int,
    inserted: int,
    already: int,
) -> ImportReport:
    declared = len(parsed.categories)
    return ImportReport(
        status=status,
        dataset_id=dataset_id,
        images_declared=len(parsed.images),
        images_matched=len(parsed.images),
        classes_declared=declared,
        classes_created=created,
        classes_existing=declared - created,
        annotations_in_file=len(parsed.boxes) + len(parsed.crowd_ignored),
        annotations_imported=inserted,
        annotations_already_existing=already,
        crowd_ignored=len(parsed.crowd_ignored),
        segmentations_ignored=parsed.segmentations_ignored,
        warnings=parsed.warnings,
        warnings_total=parsed.warnings_total,
    )


async def import_coco(
    repo: CocoRepository,
    dataset_id: int,
    parsed: ParsedCoco,
    *,
    create_classes: bool = True,
    dry_run: bool = False,
    limits: CocoLimits | None = None,
) -> ImportReport:
    """Importa as caixas de `parsed` no dataset. Levanta `NotFoundError` (dataset inexistente),
    `CocoImportError` (defeitos por item, nada gravado) ou erros de domínio do repositório."""
    lim = limits or CocoLimits()
    dataset = await repo.get_dataset(dataset_id)
    project_id = dataset.project_id

    # Fase 1: somente leitura; todos os defeitos de uma vez.
    stored = await read_stored_images(repo, dataset_id, lim.max_images, CocoImportError)
    classes = await repo.list_classes(project_id)
    col = ErrorCollector(lim.max_errors)
    matched = _match_images(parsed, stored, col)
    planned = _plan_boxes(parsed, matched, col)
    if not create_classes:
        missing_classes(parsed.categories, classes, col)
    col.raise_if_any()

    if dry_run:
        missing, rows, already = await simulate(repo, parsed.categories, planned, classes)
        return _report(
            "simulado",
            dataset_id,
            parsed,
            created=len(missing),
            inserted=len(rows),
            already=already,
        )

    missing, rows, already = await write_all(
        repo,
        project_id=project_id,
        dataset_id=dataset_id,
        stored=stored,
        decls=parsed.categories,
        planned=planned,
        create_classes=create_classes,
        max_images=lim.max_images,
        max_errors=lim.max_errors,
        chunk=INSERT_CHUNK,  # lido na chamada: os testes podem reduzi-lo
        exc=CocoImportError,
    )
    changed = bool(missing) or bool(rows)
    return _report(
        "importado" if changed else "sem_alteracoes",
        dataset_id,
        parsed,
        created=len(missing),
        inserted=len(rows),
        already=already,
    )
