"""Núcleo de importação independente do formato (F1-07a): extraído do serviço COCO.

Reúne o que COCO e rótulos txt compartilham: protocolo do repositório, relatório, nome exibido,
classes (mapeamento e criação), excedente do multiconjunto e escrita em blocos dentro de uma única
transação. Não conhece HTTP, S3, disco nem o formato de origem; cada serviço injeta o tamanho do
bloco e o tipo de exceção de recusa (`ImportRejectedError` ou subclasse).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from inovalytics.db.repository import NewAnnotation
from inovalytics.imports.errors import ErrorCollector, ImportRejectedError, ItemError

if TYPE_CHECKING:
    from inovalytics.db.repository import Annotation, Dataset, ImportImage, LabelClass

K_FILE = "arquivo"
K_CLASS = "categoria"

_Key = tuple[int, int, float, float, float, float]  # imagem, classe, cx, cy, w, h (normalizados)


class ImportRepository(Protocol):
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


class ClassDecl(Protocol):
    """Classe declarada pela origem: `id` (chave usada pelas caixas), `name` e posição `index`."""

    @property
    def id(self) -> int: ...
    @property
    def name(self) -> str: ...
    @property
    def index(self) -> int: ...


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
    images_without_boxes: int = 0  # só rótulos txt

    @property
    def warnings_truncated(self) -> bool:
        return self.warnings_total > len(self.warnings)


@dataclass(frozen=True, slots=True)
class Planned:
    image_id: int  # id gravado
    category_id: int  # id da classe na origem
    norm: tuple[float, float, float, float]


def missing_classes(
    decls: Sequence[ClassDecl], classes: Sequence[LabelClass], col: ErrorCollector | None
) -> list[ClassDecl]:
    """Classes declaradas sem classe de mesmo nome; com `col`, cada uma vira `classe_ausente`."""
    names = {c.name for c in classes}
    missing = [c for c in decls if c.name not in names]
    if col is not None:
        for c in missing:
            col.add("classe_ausente", K_CLASS, c.id, c.index, "classe não existe no projeto")
    return missing


def excess(
    planned: Sequence[Planned],
    class_by_category: dict[int, int],
    existing: Sequence[Annotation],
) -> tuple[list[tuple[int, NewAnnotation]], int]:
    """Linhas a inserir (excedente do multiconjunto, na ordem de origem) e quantas já existem."""
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


async def read_stored_images(
    repo: ImportRepository, dataset_id: int, max_images: int, exc: type[ImportRejectedError]
) -> Sequence[ImportImage]:
    """Imagens gravadas do dataset; acima de `max_images` é erro, nunca truncamento."""
    stored = await repo.list_images_for_import(dataset_id, max_images + 1)
    if len(stored) > max_images:
        raise exc(
            [
                ItemError(
                    "limite_excedido", K_FILE, None, None, "limite excedido: max_images (dataset)"
                )
            ]
        )
    return stored


async def simulate(
    repo: ImportRepository,
    decls: Sequence[ClassDecl],
    planned: Sequence[Planned],
    classes: Sequence[LabelClass],
) -> tuple[list[ClassDecl], list[tuple[int, NewAnnotation]], int]:
    """`dry_run`: só leituras. Devolve classes a criar, linhas novas e quantas já existem."""
    image_ids = sorted({p.image_id for p in planned})
    missing = missing_classes(decls, classes, None)
    by_name = {c.name: c.id for c in classes}
    class_by_category = {c.id: by_name.get(c.name, -(c.index + 1)) for c in decls}
    existing = await repo.list_annotations_for_images(image_ids) if image_ids else []
    rows, already = excess(planned, class_by_category, existing)
    return missing, rows, already


async def write_all(
    repo: ImportRepository,
    *,
    project_id: int,
    dataset_id: int,
    stored: Sequence[ImportImage],
    decls: Sequence[ClassDecl],
    planned: Sequence[Planned],
    create_classes: bool,
    max_images: int,
    max_errors: int,
    chunk: int,
    exc: type[ImportRejectedError],
) -> tuple[list[ClassDecl], list[tuple[int, NewAnnotation]], int]:
    """Fase 2: uma transação; o bloqueio vem primeiro e o estado é relido sob ele."""
    image_ids = sorted({p.image_id for p in planned})
    async with repo.transaction():
        await repo.lock_project_for_import(project_id)
        # As imagens não são protegidas pelo bloqueio do projeto (o upload não o toma): se o
        # conjunto mudou desde o plano da fase 1, nada é gravado e o cliente repete a chamada.
        if await repo.list_images_for_import(dataset_id, max_images + 1) != stored:
            raise exc(
                [
                    ItemError(
                        "dataset_alterado",
                        K_FILE,
                        None,
                        None,
                        "as imagens do dataset mudaram durante a importação; nada foi gravado, "
                        "tente novamente",
                    )
                ]
            )
        classes = await repo.list_classes(project_id)
        if not create_classes:
            late = ErrorCollector(max_errors)
            missing_classes(decls, classes, late)
            late.raise_if_any(exc)
        by_name = {c.name: c.id for c in classes}
        missing = sorted(missing_classes(decls, classes, None), key=lambda c: c.id)
        position = max((c.position for c in classes), default=-1) + 1
        for cat in missing:
            made = await repo.create_class(project_id, cat.name, position)
            by_name[cat.name] = made.id
            position += 1
        class_by_category = {c.id: by_name[c.name] for c in decls}
        existing = await repo.list_annotations_for_images(image_ids) if image_ids else []
        rows, already = excess(planned, class_by_category, existing)
        for start in range(0, len(rows), chunk):
            await repo.insert_annotation_batch(project_id, rows[start : start + chunk])
    return missing, rows, already
