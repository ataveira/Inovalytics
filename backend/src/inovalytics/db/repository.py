"""Repositório asyncpg sem ORM (F1-04c, ADR-006).

Funções assíncronas sobre uma conexão injetada, sem estado global. Todo SQL é constante de módulo
com parâmetros `$n`; nenhum valor é interpolado no texto. Erros do asyncpg viram erros de domínio
com mensagem fixa: nunca carregam DSN, senha nem texto de SQL. Não é usado no startup da API.
"""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

import asyncpg  # type: ignore[import-untyped]


class RepositoryError(Exception):
    """Base dos erros de domínio. A mensagem é fixa e não contém dados de conexão nem SQL."""


class NotFoundError(RepositoryError):
    """Registro (ou pai referenciado) inexistente."""


class DuplicateError(RepositoryError):
    """Violação de unicidade ou de integridade referencial por uso (ex.: nome repetido)."""


class InvalidValueError(RepositoryError):
    """Valor rejeitado por uma regra (CHECK) do banco."""


class InvalidAnnotationError(InvalidValueError):
    """Anotação rejeitada pelo banco (caixa fora dos limites ou classe de outro projeto)."""


class LockTimeoutError(RepositoryError):
    """O bloqueio consultivo do projeto não foi obtido dentro do `lock_timeout`."""


class Connection(Protocol):
    """Subconjunto da conexão asyncpg usado aqui (permite conexão falsa nos testes)."""

    async def execute(self, query: str, *args: Any) -> str: ...
    async def executemany(self, query: str, args: Sequence[Sequence[Any]]) -> None: ...
    async def fetch(self, query: str, *args: Any) -> Sequence[Any]: ...
    async def fetchrow(self, query: str, *args: Any) -> Any: ...
    async def fetchval(self, query: str, *args: Any) -> Any: ...
    def transaction(self) -> AbstractAsyncContextManager[Any]: ...


@dataclass(frozen=True)
class Project:
    id: int
    name: str
    created_at: datetime


@dataclass(frozen=True)
class Dataset:
    id: int
    project_id: int
    name: str
    created_at: datetime


@dataclass(frozen=True)
class LabelClass:
    id: int
    project_id: int
    name: str
    position: int
    created_at: datetime


@dataclass(frozen=True)
class Image:
    id: int
    dataset_id: int
    project_id: int
    object_key: str
    file_name: str
    sha256: str
    size_bytes: int
    width_px: int
    height_px: int
    split: str | None
    created_at: datetime


@dataclass(frozen=True)
class ImportImage:
    """Visão mínima da imagem para casar nomes e converter caixas (F1-06)."""

    id: int
    file_name: str
    width_px: int
    height_px: int


@dataclass(frozen=True)
class NewAnnotation:
    class_id: int
    x_center: float
    y_center: float
    width: float
    height: float


@dataclass(frozen=True)
class Annotation:
    id: int
    image_id: int
    project_id: int
    class_id: int
    x_center: float
    y_center: float
    width: float
    height: float
    created_at: datetime


INSERT_PROJECT_SQL = "INSERT INTO project (name) VALUES ($1) RETURNING id, name, created_at"
SELECT_PROJECT_SQL = "SELECT id, name, created_at FROM project WHERE id = $1"
LIST_PROJECTS_SQL = "SELECT id, name, created_at FROM project ORDER BY id"
DELETE_PROJECT_SQL = "DELETE FROM project WHERE id = $1"
INSERT_DATASET_SQL = (
    "INSERT INTO dataset (project_id, name) VALUES ($1, $2) "
    "RETURNING id, project_id, name, created_at"
)
SELECT_DATASET_SQL = "SELECT id, project_id, name, created_at FROM dataset WHERE id = $1"
INSERT_CLASS_SQL = (
    "INSERT INTO label_class (project_id, name, position) VALUES ($1, $2, $3) "
    "RETURNING id, project_id, name, position, created_at"
)
LIST_CLASSES_SQL = (
    "SELECT id, project_id, name, position, created_at FROM label_class "
    "WHERE project_id = $1 ORDER BY position"
)
LIST_IMAGES_FOR_IMPORT_SQL = (
    "SELECT id, file_name, width_px, height_px FROM image WHERE dataset_id = $1 ORDER BY id "
    "LIMIT $2"
)
LIST_ANNOTATIONS_FOR_IMAGES_SQL = (
    "SELECT id, image_id, project_id, class_id, x_center, y_center, width, height, created_at "
    "FROM annotation WHERE image_id = ANY($1::bigint[]) ORDER BY id"
)
SET_LOCAL_CONFIG_SQL = "SELECT set_config($1, $2, true)"
LOCK_PROJECT_SQL = "SELECT pg_advisory_xact_lock($1::int, $2::int)"
INSERT_IMAGE_SQL = (
    "INSERT INTO image (dataset_id, project_id, object_key, file_name, sha256, size_bytes, "
    "width_px, height_px, split) "
    "SELECT d.id, d.project_id, $2, $3, $4, $5, $6, $7, $8 FROM dataset d WHERE d.id = $1 "
    "ON CONFLICT (dataset_id, sha256) DO NOTHING "
    "RETURNING id, dataset_id, project_id, object_key, file_name, sha256, size_bytes, "
    "width_px, height_px, split, created_at"
)
SELECT_IMAGE_BY_SHA_SQL = (
    "SELECT id, dataset_id, project_id, object_key, file_name, sha256, size_bytes, "
    "width_px, height_px, split, created_at FROM image WHERE dataset_id = $1 AND sha256 = $2"
)
SELECT_IMAGE_PROJECT_SQL = "SELECT project_id FROM image WHERE id = $1"
INSERT_ANNOTATION_SQL = (
    "INSERT INTO annotation (image_id, project_id, class_id, x_center, y_center, width, height) "
    "VALUES ($1, $2, $3, $4, $5, $6, $7)"
)
LIST_ANNOTATIONS_SQL = (
    "SELECT id, image_id, project_id, class_id, x_center, y_center, width, height, created_at "
    "FROM annotation WHERE image_id = $1 ORDER BY id"
)


def _failure(
    exc: BaseException,
    fk_error: type[RepositoryError],
    check_error: type[RepositoryError] = InvalidValueError,
) -> BaseException:
    """Erro de domínio equivalente ao do asyncpg (sem a mensagem original) ou o próprio `exc`.

    O chamador faz `raise` fora do bloco `except`, para que o erro original (que pode trazer
    texto de SQL ou dados) não fique em `__context__`.
    """
    if isinstance(exc, asyncpg.exceptions.UniqueViolationError):
        return DuplicateError("registro duplicado")
    if isinstance(exc, asyncpg.exceptions.ForeignKeyViolationError):
        return fk_error("referência inválida")
    if isinstance(exc, asyncpg.exceptions.CheckViolationError):
        return check_error("valor rejeitado pelas regras do banco")
    if isinstance(exc, asyncpg.exceptions.LockNotAvailableError):
        return LockTimeoutError("tempo esgotado ao obter o bloqueio")
    return exc


def _project(r: Any) -> Project:
    return Project(r["id"], r["name"], r["created_at"])


def _dataset(r: Any) -> Dataset:
    return Dataset(r["id"], r["project_id"], r["name"], r["created_at"])


def _class(r: Any) -> LabelClass:
    return LabelClass(r["id"], r["project_id"], r["name"], r["position"], r["created_at"])


def _image(r: Any) -> Image:
    return Image(
        r["id"], r["dataset_id"], r["project_id"], r["object_key"], r["file_name"], r["sha256"],
        r["size_bytes"], r["width_px"], r["height_px"], r["split"], r["created_at"],
    )  # fmt: skip


def _annotation(r: Any) -> Annotation:
    return Annotation(
        r["id"], r["image_id"], r["project_id"], r["class_id"],
        r["x_center"], r["y_center"], r["width"], r["height"], r["created_at"],
    )  # fmt: skip


async def create_project(conn: Connection, name: str) -> Project:
    failure: BaseException | None = None
    try:
        row = await conn.fetchrow(INSERT_PROJECT_SQL, name)
    except asyncpg.exceptions.PostgresError as exc:
        failure = _failure(exc, NotFoundError)
    if failure is not None:
        raise failure
    return _project(row)


async def get_project(conn: Connection, project_id: int) -> Project:
    row = await conn.fetchrow(SELECT_PROJECT_SQL, project_id)
    if row is None:
        raise NotFoundError("projeto não encontrado")
    return _project(row)


async def list_projects(conn: Connection) -> list[Project]:
    return [_project(r) for r in await conn.fetch(LIST_PROJECTS_SQL)]


async def delete_project(conn: Connection, project_id: int) -> None:
    """Exclui o projeto e, em cascata, datasets, classes, imagens e anotações."""
    failure: BaseException | None = None
    try:
        status = await conn.execute(DELETE_PROJECT_SQL, project_id)
    except asyncpg.exceptions.PostgresError as exc:
        failure = _failure(exc, DuplicateError)
    if failure is not None:
        raise failure
    if status != "DELETE 1":
        raise NotFoundError("projeto não encontrado")


async def create_dataset(conn: Connection, project_id: int, name: str) -> Dataset:
    failure: BaseException | None = None
    try:
        row = await conn.fetchrow(INSERT_DATASET_SQL, project_id, name)
    except asyncpg.exceptions.PostgresError as exc:
        failure = _failure(exc, NotFoundError)
    if failure is not None:
        raise failure
    return _dataset(row)


async def get_dataset(conn: Connection, dataset_id: int) -> Dataset:
    row = await conn.fetchrow(SELECT_DATASET_SQL, dataset_id)
    if row is None:
        raise NotFoundError("dataset não encontrado")
    return _dataset(row)


async def create_class(conn: Connection, project_id: int, name: str, position: int) -> LabelClass:
    failure: BaseException | None = None
    try:
        row = await conn.fetchrow(INSERT_CLASS_SQL, project_id, name, position)
    except asyncpg.exceptions.PostgresError as exc:
        failure = _failure(exc, NotFoundError)
    if failure is not None:
        raise failure
    return _class(row)


async def insert_image(
    conn: Connection,
    dataset_id: int,
    *,
    object_key: str,
    file_name: str,
    sha256: str,
    size_bytes: int,
    width_px: int,
    height_px: int,
    split: str | None = None,
) -> tuple[Image, bool]:
    """Insere a imagem; idempotente por `(dataset_id, sha256)`. Devolve `(imagem, created)`."""
    failure: BaseException | None = None
    try:
        row = await conn.fetchrow(
            INSERT_IMAGE_SQL, dataset_id, object_key, file_name, sha256, size_bytes,
            width_px, height_px, split,
        )  # fmt: skip
        if row is not None:
            return _image(row), True
        existing = await conn.fetchrow(SELECT_IMAGE_BY_SHA_SQL, dataset_id, sha256)
    except asyncpg.exceptions.PostgresError as exc:
        failure = _failure(exc, NotFoundError)
    if failure is not None:
        raise failure
    if existing is None:
        raise NotFoundError("dataset não encontrado")
    return _image(existing), False


async def get_image_by_sha(conn: Connection, dataset_id: int, sha256: str) -> Image | None:
    """Imagem do dataset com esse SHA-256, ou `None` (base da deduplicação do upload)."""
    row = await conn.fetchrow(SELECT_IMAGE_BY_SHA_SQL, dataset_id, sha256)
    return None if row is None else _image(row)


async def insert_annotations(
    conn: Connection, image_id: int, boxes: Sequence[NewAnnotation]
) -> int:
    """Grava o lote em uma transação: ou todas as caixas entram, ou nenhuma.

    Chamada dentro de uma transação externa, `conn.transaction()` vira savepoint (correto, mas
    com custo por imagem); importações em massa usam `insert_annotation_batch`.
    """
    failure: BaseException | None = None
    try:
        async with conn.transaction():
            project_id = await conn.fetchval(SELECT_IMAGE_PROJECT_SQL, image_id)
            if project_id is None:
                raise NotFoundError("imagem não encontrada")
            await conn.executemany(
                INSERT_ANNOTATION_SQL,
                [
                    (image_id, project_id, b.class_id, b.x_center, b.y_center, b.width, b.height)
                    for b in boxes
                ],
            )
    except asyncpg.exceptions.PostgresError as exc:
        failure = _failure(exc, InvalidAnnotationError, InvalidAnnotationError)
    if failure is not None:
        raise failure
    return len(boxes)


async def list_annotations(conn: Connection, image_id: int) -> list[Annotation]:
    return [_annotation(r) for r in await conn.fetch(LIST_ANNOTATIONS_SQL, image_id)]


async def list_classes(conn: Connection, project_id: int) -> list[LabelClass]:
    """Classes do projeto em ordem de `position`."""
    return [_class(r) for r in await conn.fetch(LIST_CLASSES_SQL, project_id)]


async def list_images_for_import(
    conn: Connection, dataset_id: int, limit: int
) -> list[ImportImage]:
    """Até `limit` imagens do dataset (por id), só com o que o importador precisa."""
    rows = await conn.fetch(LIST_IMAGES_FOR_IMPORT_SQL, dataset_id, limit)
    return [ImportImage(r["id"], r["file_name"], r["width_px"], r["height_px"]) for r in rows]


async def list_annotations_for_images(
    conn: Connection, image_ids: Sequence[int]
) -> list[Annotation]:
    """Anotações de todas as imagens dadas, em uma só consulta."""
    return [
        _annotation(r) for r in await conn.fetch(LIST_ANNOTATIONS_FOR_IMAGES_SQL, list(image_ids))
    ]


IMPORT_LOCK_NAMESPACE = 1_001  # primeira chave do bloqueio consultivo das importações
DEFAULT_LOCK_TIMEOUT_MS = 10_000


async def lock_project_for_import(
    conn: Connection, project_id: int, timeout_ms: int = DEFAULT_LOCK_TIMEOUT_MS
) -> None:
    """Bloqueio consultivo da transação, por projeto; serializa importações concorrentes.

    Deve ser chamada dentro de `conn.transaction()` (o bloqueio e o `lock_timeout` local só
    valem até o fim da transação). Esgotado o prazo, levanta `LockTimeoutError`. Projetos
    distintos podem compartilhar a chave (máscara de 31 bits): só serializa a mais, nunca erra.
    """
    failure: BaseException | None = None
    try:
        await conn.fetchval(SET_LOCAL_CONFIG_SQL, "lock_timeout", str(timeout_ms))
        await conn.execute(LOCK_PROJECT_SQL, IMPORT_LOCK_NAMESPACE, project_id & 0x7FFFFFFF)
    except asyncpg.exceptions.PostgresError as exc:
        failure = _failure(exc, NotFoundError)
    if failure is not None:
        raise failure


async def insert_annotation_batch(
    conn: Connection, project_id: int, rows: Sequence[tuple[int, NewAnnotation]]
) -> int:
    """Insere `(image_id, caixa)` em um `executemany`, SEM transação própria.

    O chamador abre a transação (a importação segura uma só para tudo). `project_id` precisa ser
    o do dataset das imagens: as chaves estrangeiras compostas do esquema recusam o resto.
    """
    failure: BaseException | None = None
    try:
        await conn.executemany(
            INSERT_ANNOTATION_SQL,
            [
                (image_id, project_id, b.class_id, b.x_center, b.y_center, b.width, b.height)
                for image_id, b in rows
            ],
        )
    except asyncpg.exceptions.PostgresError as exc:
        failure = _failure(exc, InvalidAnnotationError, InvalidAnnotationError)
    if failure is not None:
        raise failure
    return len(rows)
