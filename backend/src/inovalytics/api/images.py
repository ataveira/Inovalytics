"""Rota de upload de imagens (F1-05c, ADR-007): multipart, limite de bytes, pool preguiçoso.

Único módulo da camada `api` que importa `inovalytics.db`. O Postgres continua opcional no startup
(ADR-005): o pool é criado no primeiro uso, a falha não é memorizada e o shutdown só o fecha se
foi criado. Mensagens de erro são fixas, em português, sem DSN, endpoint, segredo nem caminho.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any, Protocol

import asyncpg  # type: ignore[import-untyped]
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from inovalytics.api.config import Settings
from inovalytics.db import repository as repo
from inovalytics.uploads.errors import UploadError
from inovalytics.uploads.limits import UploadLimits
from inovalytics.uploads.service import UploadItem, upload_images
from inovalytics.uploads.store import ObjectStore

log = logging.getLogger(__name__)

SPLITS = frozenset({"train", "val", "test"})
BODY_OVERHEAD = 64 * 1024  # margem para delimitadores e campos do multipart
_PATH_RE = re.compile(r"^/datasets/[^/]+/images/?$")
_ID_RE = re.compile(r"^[1-9][0-9]{0,18}$")
_MAX_BIGINT = 2**63 - 1
_CONTENT_TYPES = {"jpg": "image/jpeg", "png": "image/png"}
_DB_ERRORS = (asyncpg.PostgresError, asyncpg.InterfaceError, OSError, TimeoutError)
MAX_FORM_FIELDS = 8  # só `split` é esperado; o resto é lixo
DB_ACQUIRE_TIMEOUT = 10.0  # s: espera por conexão do pool (cobre criar o pool + fila)


class DbBusyError(Exception):
    """Sem conexão do pool dentro de `DB_ACQUIRE_TIMEOUT`."""


class DbUnavailableError(Exception):
    """Postgres indisponível (sem detalhes: a causa original pode conter host ou DSN)."""


def error_body(detail: str, code: str, file_index: int | None = None) -> dict[str, Any]:
    return {"detail": detail, "code": code, "file_index": file_index}


def error_response(
    status: int, detail: str, code: str, file_index: int | None = None
) -> JSONResponse:
    return JSONResponse(status_code=status, content=error_body(detail, code, file_index))


# --- Pool preguiçoso -------------------------------------------------------------------------


class DbProvider(Protocol):
    def acquire(self) -> Any:
        """Gerenciador de contexto assíncrono que entrega uma conexão (interface do repositório)."""

    async def close(self) -> None: ...


PoolFactory = Callable[[str, float], Awaitable[Any]]


async def _create_pool(dsn: str, timeout: float) -> Any:
    return await asyncpg.create_pool(dsn, min_size=1, max_size=5, timeout=timeout)


class LazyPool:
    """Pool asyncpg criado no primeiro uso, sob lock; falha não é memorizada."""

    def __init__(self, settings: Settings, factory: PoolFactory = _create_pool) -> None:
        self._settings = settings
        self._factory = factory
        self._pool: Any = None
        self._lock = asyncio.Lock()

    @property
    def created(self) -> bool:
        return self._pool is not None

    async def _get(self) -> Any:
        if self._pool is not None:
            return self._pool
        async with self._lock:
            if self._pool is None:
                self._pool = await self._factory(
                    self._settings.postgres_dsn, self._settings.check_timeout
                )
            return self._pool

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[Any]:
        pool = await self._get()
        async with pool.acquire() as conn:
            yield conn

    async def close(self) -> None:
        pool, self._pool = self._pool, None
        if pool is not None:
            await pool.close()


class LazyObjectStore:
    """Cria o `ObjectStore` real só na primeira gravação (boto3 não conecta ao ser criado)."""

    def __init__(self, factory: Callable[[], ObjectStore]) -> None:
        self._factory = factory
        self._store: ObjectStore | None = None

    def _get(self) -> ObjectStore:
        if self._store is None:
            self._store = self._factory()
        return self._store

    async def put(self, key: str, fileobj: Any, content_type: str) -> None:
        await self._get().put(key, fileobj, content_type)

    async def delete(self, key: str) -> None:
        await self._get().delete(key)


# --- Adaptador ImageRepository -> funções de db.repository ------------------------------------


class ConnImageRepository:
    """Liga as funções do repositório a uma conexão do pool (satisfaz `ImageRepository`)."""

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    async def get_dataset(self, dataset_id: int) -> repo.Dataset:
        return await repo.get_dataset(self._conn, dataset_id)

    async def get_image_by_sha(self, dataset_id: int, sha256: str) -> repo.Image | None:
        return await repo.get_image_by_sha(self._conn, dataset_id, sha256)

    async def insert_image(
        self,
        dataset_id: int,
        *,
        object_key: str,
        file_name: str,
        sha256: str,
        size_bytes: int,
        width_px: int,
        height_px: int,
        split: str | None = None,
    ) -> tuple[repo.Image, bool]:
        return await repo.insert_image(
            self._conn,
            dataset_id,
            object_key=object_key,
            file_name=file_name,
            sha256=sha256,
            size_bytes=size_bytes,
            width_px=width_px,
            height_px=height_px,
            split=split,
        )


# --- Middleware ASGI de limite de bytes (antes do parser multipart) ----------------------------


class _BodyState:
    def __init__(self) -> None:
        self.received = 0
        self.exceeded = False
        self.started = False


class RequestBodyLimitMiddleware:
    """Recusa com 413 corpos acima de `limit` nas rotas de upload, antes do multipart.

    Com `Content-Length` acima do limite, responde sem chamar a app nem ler o corpo. Sem
    `Content-Length` (chunked) ou com valor mentiroso, conta os bytes recebidos e, ao passar do
    limite, para de entregar o corpo, descarta a resposta da app e responde 413.
    """

    def __init__(self, app: ASGIApp, limit: int) -> None:
        self.app = app
        self.limit = limit

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope["method"] != "POST"
            or not _PATH_RE.match(scope["path"])
        ):
            await self.app(scope, receive, send)
            return
        declared = dict(scope["headers"]).get(b"content-length")
        if declared is not None:
            try:
                too_big = int(declared) > self.limit
            except ValueError:
                too_big = False
            if too_big:
                await self._reject(send)
                return
        state = _BodyState()

        async def limited_receive() -> Message:
            if state.exceeded:
                return {"type": "http.disconnect"}
            message = await receive()
            if message["type"] == "http.request":
                state.received += len(message.get("body", b""))
                if state.received > self.limit:
                    state.exceeded = True
                    return {"type": "http.disconnect"}
            return message

        async def guarded_send(message: Message) -> None:
            if state.exceeded:
                return  # a resposta da app (ex.: erro de parse) é descartada
            if message["type"] == "http.response.start":
                state.started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, guarded_send)
        except Exception:
            if not state.exceeded:
                raise
        if state.exceeded and not state.started:
            await self._reject(send)

    @staticmethod
    async def _reject(send: Send) -> None:
        body = json.dumps(
            error_body(
                "Corpo da requisição acima do tamanho máximo permitido.",
                "requisicao_grande_demais",
            ),
            ensure_ascii=False,
        ).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                    (b"connection", b"close"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


# --- Rota -------------------------------------------------------------------------------------


def _parse_dataset_id(raw: str) -> int | None:
    if not _ID_RE.fullmatch(raw):
        return None
    value = int(raw)
    return value if value <= _MAX_BIGINT else None


def _image_json(image: repo.Image, created: bool) -> dict[str, Any]:
    ext = image.object_key.rsplit(".", 1)[-1]
    return {
        "id": image.id,
        "dataset_id": image.dataset_id,
        "sha256": image.sha256,
        "size_bytes": image.size_bytes,
        "width_px": image.width_px,
        "height_px": image.height_px,
        "content_type": _CONTENT_TYPES.get(ext, "application/octet-stream"),
        "file_name": image.file_name,
        "split": image.split,
        "created": created,
    }


def register_upload(
    app: FastAPI,
    settings: Settings,
    provider: DbProvider,
    store: ObjectStore,
) -> None:
    """Acrescenta a rota `POST /datasets/{dataset_id}/images` e o middleware de limite."""
    limits = UploadLimits.from_settings(settings)
    app.add_middleware(
        RequestBodyLimitMiddleware,
        limit=limits.max_files * limits.max_bytes + BODY_OVERHEAD,
    )

    @app.post("/datasets/{dataset_id}/images")
    async def upload(dataset_id: str, request: Request) -> JSONResponse:
        ds = _parse_dataset_id(dataset_id)
        if ds is None:
            return error_response(422, "Identificador de dataset inválido.", "dataset_id_invalido")
        try:
            form = await request.form(max_files=limits.max_files + 1, max_fields=MAX_FORM_FIELDS)
        except HTTPException:
            return error_response(422, "Corpo multipart inválido.", "multipart_invalido")
        try:
            raw_split = form.get("split")
            if raw_split is not None and (not isinstance(raw_split, str)):
                return error_response(422, "Campo split inválido.", "split_invalido")
            split = raw_split or None
            if split is not None and split not in SPLITS:
                return error_response(
                    422, "Campo split deve ser train, val ou test.", "split_invalido"
                )
            parts = form.getlist("files")
            if not parts:
                return error_response(422, "Nenhum arquivo enviado.", "nenhum_arquivo")
            items: list[UploadItem] = []
            for i, part in enumerate(parts):
                if not isinstance(part, UploadFile):
                    return error_response(
                        422, "Campo files deve ser um arquivo.", "arquivo_invalido", i
                    )
                items.append(UploadItem(part.filename or "", part.file))
            return await _run(ds, items, split)
        finally:
            await form.close()

    async def _run(ds: int, items: list[UploadItem], split: str | None) -> JSONResponse:
        try:
            async with AsyncExitStack() as stack:
                try:
                    async with asyncio.timeout(DB_ACQUIRE_TIMEOUT) as deadline:
                        conn = await stack.enter_async_context(provider.acquire())
                except Exception as exc:
                    if deadline.expired():  # nosso prazo, não um TimeoutError do driver
                        log.error("timeout ao obter conexão do banco")
                        raise DbBusyError from None
                    log.error("banco indisponível: %s", type(exc).__name__)
                    raise DbUnavailableError from None
                results = await upload_images(
                    ConnImageRepository(conn), store, ds, items, limits, split=split
                )
        except DbBusyError:
            return error_response(
                503, "Banco de dados ocupado; tente novamente.", "banco_timeout_conexao"
            )
        except DbUnavailableError:
            return _db_down()
        except repo.NotFoundError:
            return error_response(404, "Dataset não encontrado.", "dataset_nao_encontrado")
        except UploadError as exc:
            return error_response(exc.status, exc.message, exc.code, exc.file_index)
        except asyncpg.IntegrityConstraintViolationError as exc:
            # Violação de restrição não é indisponibilidade: só o tipo vai ao log (sem mensagem).
            log.error("violação de integridade no upload: %s", type(exc).__name__)
            return error_response(
                409, "Conflito ao registrar a imagem no banco.", "conflito_integridade"
            )
        except _DB_ERRORS as exc:
            log.error("erro de banco no upload: %s", type(exc).__name__)
            return _db_down()
        except Exception as exc:
            log.error("erro inesperado no upload: %s", type(exc).__name__)
            return error_response(500, "Erro interno no servidor.", "erro_interno")
        status = 201 if any(r.created for r in results) else 200
        return JSONResponse(
            status_code=status,
            content={"images": [_image_json(r.image, r.created) for r in results]},
        )

    def _db_down() -> JSONResponse:
        return error_response(503, "Banco de dados indisponível.", "banco_indisponivel")
