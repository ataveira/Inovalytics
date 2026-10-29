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

import unicodedata
from collections import Counter
from collections.abc import Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from inovalytics.db.repository import NewAnnotation
from inovalytics.imports.coco_parser import (
    K_ANNOTATION,
    K_CATEGORY,
    K_FILE,
    K_IMAGE,
    CocoCategory,
    CocoLimits,
    ParsedCoco,
    representable,
    to_normalized,
)
from inovalytics.imports.errors import CocoImportError, ErrorCollector, ItemError
from inovalytics.uploads.naming import sanitize_file_name

if TYPE_CHECKING:
    from inovalytics.db.repository import Annotation, Dataset, ImportImage, LabelClass

INSERT_CHUNK = 5_000
SHOWN_NAME_CHARS = 200

_HIDDEN = frozenset({"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"})
_Key = tuple[int, int, float, float, float, float]  # imagem, classe, x, y, w, h (normalizados)


class CocoRepository(Protocol):
    """Repositório já ligado a uma conexão (ver `inovalytics.db.repository`)."""

    async def get_dataset(self, dataset_id: int) -> Dataset: ...
    async def list_images_for_import(
        self, dataset_id: int, limit: int
    ) -> Sequence[ImportImage]: ...
    async def list_classes(self, project_id: int) -> Sequence[LabelClass]: ...
    async def list_annotations_for_images(
        self, image_ids: Sequence[int]
    ) -> Sequence[Annotation]: ...
    def transaction(self) -> AbstractAsyncContextManager[Any]: ...
    async def lock_project_for_import(self, project_id: int) -> None: ...
    async def create_class(self, project_id: int, name: str, position: int) -> LabelClass: ...
    async def insert_annotation_batch(
        self, project_id: int, rows: Sequence[tuple[int, NewAnnotation]]
    ) -> int: ...


@dataclass(frozen=True)
class ImportReport:
    status: str  # "importado", "sem_alteracoes" ou "simulado"
    dataset_id: int
    images_declared: int
    images_matched: int
    classes_declared: int
    classes_created: int
    classes_existing: int
    annotations_in_file: int
    annotations_imported: int
    annotations_already_existing: int
    crowd_ignored: int
    segmentations_ignored: int
    warnings: tuple[ItemError, ...]
    warnings_total: int

    @property
    def warnings_truncated(self) -> bool:
        return self.warnings_total > len(self.warnings)


@dataclass(frozen=True, slots=True)
class _Planned:
    image_id: int  # id gravado
    category_id: int  # id no arquivo
    norm: tuple[float, float, float, float]


def _shown(name: str) -> str:
    clean = "".join(
        c
        for c in name
        if unicodedata.category(c)[0] != "C" and unicodedata.category(c) not in {"Zl", "Zp"}
    )
    return clean[:SHOWN_NAME_CHARS]


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
                f"mais de uma imagem do arquivo reduz ao nome '{_shown(name)}'",
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
                f"imagem '{_shown(img.file_name)}' não encontrada no dataset",
            )
        elif len(found) > 1:
            col.add(
                "imagem_ambigua",
                K_IMAGE,
                img.id,
                img.index,
                f"mais de uma imagem gravada com o nome '{_shown(name)}'",
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
) -> list[_Planned]:
    """Converte para normalizado com as dimensões GRAVADAS (nunca as declaradas)."""
    planned: list[_Planned] = []
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
        planned.append(_Planned(img.id, box.category_id, norm))
    return planned


def _missing_categories(
    parsed: ParsedCoco, classes: Sequence[LabelClass], col: ErrorCollector | None
) -> list[CocoCategory]:
    """Categorias do arquivo sem classe de mesmo nome; com `col`, cada uma vira `classe_ausente`."""
    names = {c.name for c in classes}
    missing = [c for c in parsed.categories if c.name not in names]
    if col is not None:
        for c in missing:
            col.add("classe_ausente", K_CATEGORY, c.id, c.index, "classe não existe no projeto")
    return missing


def _excess(
    planned: Sequence[_Planned],
    class_by_category: dict[int, int],
    existing: Sequence[Annotation],
) -> tuple[list[tuple[int, NewAnnotation]], int]:
    """Linhas a inserir (excedente do multiconjunto, na ordem do arquivo) e quantas já existem."""
    have: Counter[_Key] = Counter(
        (a.image_id, a.class_id, a.x_center, a.y_center, a.width, a.height) for a in existing
    )
    rows: list[tuple[int, NewAnnotation]] = []
    already = 0
    for p in planned:
        cid = class_by_category[p.category_id]
        key: _Key = (p.image_id, cid, *p.norm)
        if have[key] > 0:
            have[key] -= 1
            already += 1
        else:
            rows.append((p.image_id, NewAnnotation(cid, *p.norm)))
    return rows, already


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
    stored = await repo.list_images_for_import(dataset_id, lim.max_images + 1)
    if len(stored) > lim.max_images:
        raise CocoImportError(
            [
                ItemError(
                    "limite_excedido", K_FILE, None, None, "limite excedido: max_images (dataset)"
                )
            ]
        )
    classes = await repo.list_classes(project_id)
    col = ErrorCollector(lim.max_errors)
    matched = _match_images(parsed, stored, col)
    planned = _plan_boxes(parsed, matched, col)
    if not create_classes:
        _missing_categories(parsed, classes, col)
    col.raise_if_any()

    image_ids = sorted({p.image_id for p in planned})

    if dry_run:
        missing = _missing_categories(parsed, classes, None)
        by_name = {c.name: c.id for c in classes}
        class_by_category = {c.id: by_name.get(c.name, -(c.index + 1)) for c in parsed.categories}
        existing = await repo.list_annotations_for_images(image_ids) if image_ids else []
        rows, already = _excess(planned, class_by_category, existing)
        return _report(
            "simulado",
            dataset_id,
            parsed,
            created=len(missing),
            inserted=len(rows),
            already=already,
        )

    # Fase 2: uma transação; o bloqueio vem primeiro e o estado é relido sob ele.
    async with repo.transaction():
        await repo.lock_project_for_import(project_id)
        classes = await repo.list_classes(project_id)
        if not create_classes:
            late = ErrorCollector(lim.max_errors)
            _missing_categories(parsed, classes, late)
            late.raise_if_any()
        by_name = {c.name: c.id for c in classes}
        missing = sorted(_missing_categories(parsed, classes, None), key=lambda c: c.id)
        position = max((c.position for c in classes), default=-1) + 1
        for cat in missing:
            made = await repo.create_class(project_id, cat.name, position)
            by_name[cat.name] = made.id
            position += 1
        class_by_category = {c.id: by_name[c.name] for c in parsed.categories}
        existing = await repo.list_annotations_for_images(image_ids) if image_ids else []
        rows, already = _excess(planned, class_by_category, existing)
        for start in range(0, len(rows), INSERT_CHUNK):
            await repo.insert_annotation_batch(project_id, rows[start : start + INSERT_CHUNK])

    changed = bool(missing) or bool(rows)
    return _report(
        "importado" if changed else "sem_alteracoes",
        dataset_id,
        parsed,
        created=len(missing),
        inserted=len(rows),
        already=already,
    )
