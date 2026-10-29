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
from inovalytics.imports.common import shown
from inovalytics.imports.errors import ErrorCollector, ImportRejectedError, ItemError
from inovalytics.uploads.naming import sanitize_file_name

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
    images_without_boxes: int = 0  # rótulos txt e Pascal VOC
    # Só Pascal VOC: campos que o esquema não guarda (contados, nunca descartados em silêncio) e
    # avisos agregados.
    ignored_difficult: int = 0
    ignored_truncated: int = 0
    ignored_occluded: int = 0
    ignored_pose: int = 0
    ignored_parts: int = 0
    without_size: int = 0
    origin_zero_adjustments: int = 0

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


@dataclass(frozen=True, slots=True)
class MatchItem:
    """Arquivo de rótulo a casar: posição, nome exibível, nome-base (sem a extensão do rótulo) e,
    opcionalmente, o nome de imagem declarado dentro do próprio arquivo (`<filename>` do VOC)."""

    index: int
    name: str
    base_name: str
    declared: str | None = None


def file_stem(file_name: str) -> str | None:
    """Nome sem a última extensão (None se não tem extensão)."""
    dot = file_name.rfind(".")
    return file_name[:dot] if dot > 0 else None


def match_images(
    items: Sequence[MatchItem],
    stored: Sequence[ImportImage],
    col: ErrorCollector,
    *,
    suffix: str,
    kind: str,
) -> dict[int, ImportImage]:
    """Posição do arquivo -> imagem gravada. Sensível a maiúsculas; nunca adivinha.

    Sem `declared` (ou com nome vazio/sem extensão): casa pelo nome-base. Com `declared` com
    extensão: o nome-base precisa coincidir com o dele (`nome_divergente`) e o casamento é pelo
    nome completo exato. Duas chaves para a mesma imagem = `rotulo_nome_duplicado`.
    """
    by_stem: dict[str, list[ImportImage]] = {}
    by_full: dict[str, list[ImportImage]] = {}
    for img in stored:
        stem = file_stem(img.file_name)
        if stem is not None:
            by_stem.setdefault(stem, []).append(img)
        by_full.setdefault(img.file_name, []).append(img)
    matched: dict[int, ImportImage] = {}
    seen: set[tuple[bool, str]] = set()
    used: set[int] = set()
    for f in items:
        name = sanitize_file_name(f.base_name + suffix)
        shown_name = lambda f=f: shown(f.name)  # noqa: E731  (lazy: coletor cheio não paga)
        if not name.endswith(suffix) or name == suffix:
            col.add("rotulo_nome_invalido", kind, None, f.index, "nome de rótulo inválido",
                    file=shown_name)  # fmt: skip
            continue
        stem = name[: -len(suffix)]
        declared = sanitize_file_name(f.declared) if f.declared is not None else None
        if declared is not None and (not declared.strip() or file_stem(declared) is None):
            declared = None
        if declared is not None and file_stem(declared) != stem:
            msg = "o nome da imagem declarado no arquivo difere do nome do arquivo de rótulo"
            col.add("nome_divergente", kind, None, f.index, msg, file=shown_name)
            continue
        dedupe = (True, declared) if declared is not None else (False, stem)
        if dedupe in seen:
            msg = "mais de um arquivo de rótulo reduz ao mesmo nome-base"
            col.add("rotulo_nome_duplicado", kind, None, f.index, msg, file=shown_name)
            continue
        seen.add(dedupe)
        found = (by_full if declared is not None else by_stem).get(
            declared if declared is not None else stem, []
        )
        if not found:
            msg = "nenhuma imagem do dataset corresponde ao arquivo de rótulo"
            col.add("imagem_nao_encontrada", kind, None, f.index, msg, file=shown_name)
        elif len(found) > 1:
            msg = "mais de uma imagem gravada corresponde ao arquivo de rótulo"
            col.add("imagem_ambigua", kind, None, f.index, msg, file=shown_name)
        elif found[0].id in used:
            msg = "mais de um arquivo de rótulo reduz ao mesmo nome-base"
            col.add("rotulo_nome_duplicado", kind, None, f.index, msg, file=shown_name)
        else:
            used.add(found[0].id)
            matched[f.index] = found[0]
    return matched
