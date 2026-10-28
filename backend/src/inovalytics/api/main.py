"""API FastAPI do Inovalytics: apenas endpoints de saúde (F0-02)."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

import asyncpg  # type: ignore[import-untyped]
import boto3  # type: ignore[import-untyped]
from botocore.config import Config  # type: ignore[import-untyped]
from botocore.exceptions import (  # type: ignore[import-untyped]
    ClientError,
    ConnectionClosedError,
    ConnectTimeoutError,
    EndpointConnectionError,
    ReadTimeoutError,
)
from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse

from inovalytics import __version__
from inovalytics.api.config import Settings
from inovalytics.api.images import DbProvider, LazyObjectStore, LazyPool, register_upload
from inovalytics.uploads.store import Boto3ObjectStore, ObjectStore

logger = logging.getLogger(__name__)

# Uma verificação levanta exceção quando a dependência está indisponível.
Check = Callable[[], Awaitable[None]]


async def check_postgres(settings: Settings) -> None:
    conn = await asyncpg.connect(settings.postgres_dsn, timeout=settings.check_timeout)
    try:
        await conn.execute("SELECT 1")
    finally:
        await conn.close()


def make_s3_client(settings: Settings) -> Any:
    # O retry é nosso (ensure_bucket): o boto3 não repete (max_attempts=1).
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        region_name="us-east-1",
        config=Config(
            connect_timeout=settings.check_timeout,
            read_timeout=settings.check_timeout,
            retries={"max_attempts": 1},
            s3={"addressing_style": "path"},
        ),
    )


async def check_s3(settings: Settings, client: Any = None) -> None:
    """Somente leitura: nunca cria nem altera nada."""
    c = client if client is not None else make_s3_client(settings)
    await asyncio.to_thread(c.head_bucket, Bucket=settings.s3_bucket)


class StorageStartupError(RuntimeError):
    """Storage indisponível no startup. A mensagem não contém segredos nem endpoint."""


_TRANSIENT = (
    EndpointConnectionError,
    ConnectTimeoutError,
    ReadTimeoutError,
    ConnectionClosedError,
    TimeoutError,
    ConnectionError,
)
_BUCKET_EXISTS = ("BucketAlreadyOwnedByYou", "BucketAlreadyExists")
BACKOFF_BASE = 0.5
BACKOFF_MAX = 5.0


def backoff_delay(attempt: int) -> float:
    """Espera após a tentativa `attempt` (1-based): crescente e limitada."""
    return float(min(BACKOFF_BASE * 2 ** (attempt - 1), BACKOFF_MAX))


def _is_transient(exc: BaseException) -> bool:
    if isinstance(exc, ClientError):
        status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        return isinstance(status, int) and status >= 500
    return isinstance(exc, _TRANSIENT)


def _head_or_create(client: Any, bucket: str) -> str:
    """Garante o bucket. Devolve "existed", "created" ou "raced" (outra réplica criou).

    BucketAlreadyOwnedByYou é sucesso (corrida entre réplicas com as mesmas credenciais).
    BucketAlreadyExists indica nome em uso, talvez por OUTRO dono: repete head_bucket e só
    aceita se o bucket estiver acessível; 403/404 falha como erro permanente.
    """
    try:
        client.head_bucket(Bucket=bucket)
        return "existed"
    except ClientError as exc:
        if exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode") != 404:
            raise
    try:
        client.create_bucket(Bucket=bucket)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code")
        if code == "BucketAlreadyOwnedByYou":
            return "raced"
        if code != "BucketAlreadyExists":
            raise
        # Dono diferente (ou corrida ainda não visível): confirma o acesso. Se o head falhar com
        # 403/404, a exceção propaga e é tratada como permanente; 5xx segue o retry normal.
        client.head_bucket(Bucket=bucket)
        return "raced"
    return "created"


async def ensure_bucket(
    settings: Settings,
    client: Any = None,
    *,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    """Passo de startup: garante o bucket (idempotente), com retry e prazo limitados.

    Erro transitório repete até `s3_startup_retries` tentativas ou `s3_startup_timeout`;
    erro permanente (credenciais, permissão) falha na 1ª tentativa. Falha => StorageStartupError.

    Após timeout, a thread do boto3 (asyncio.to_thread) não é cancelável e termina sozinha em
    segundo plano, limitada por connect/read_timeout. É inofensiva: head/create são idempotentes.
    """
    bucket = settings.s3_bucket
    c = client if client is not None else make_s3_client(settings)
    deadline = clock() + settings.s3_startup_timeout
    total = settings.s3_startup_retries
    for attempt in range(1, total + 1):
        remaining = deadline - clock()
        try:
            if remaining <= 0:
                raise TimeoutError
            outcome = await asyncio.wait_for(
                asyncio.to_thread(_head_or_create, c, bucket), timeout=remaining
            )
        except Exception as exc:
            transient = _is_transient(exc)
            # Só bucket, tipo e tentativa: a mensagem original pode conter endpoint/segredos.
            logger.warning(
                "storage indisponível: bucket %s, erro %s, tentativa %d/%d",
                bucket,
                type(exc).__name__,
                attempt,
                total,
            )
            if not transient or attempt == total or deadline - clock() <= 0:
                raise StorageStartupError(
                    f"storage indisponível: bucket {bucket}, erro {type(exc).__name__}, "
                    f"tentativa {attempt}/{total}"
                ) from None
            await sleep(min(backoff_delay(attempt), max(deadline - clock(), 0.0)))
        else:
            suffix = {
                "existed": "",
                "created": " (criado)",
                "raced": " (garantido; já criado por outra réplica ou processo)",
            }[outcome]
            logger.info("bucket %s verificado%s", bucket, suffix)
            return


def create_app(
    settings: Settings | None = None,
    checks: dict[str, Check] | None = None,
    bucket_initializer: Callable[[], Awaitable[None]] | None = None,
    db_provider: DbProvider | None = None,
    object_store: ObjectStore | None = None,
) -> FastAPI:
    """Cria a app. `checks` permite injetar verificações (fakes nos testes).

    O startup garante o bucket S3 (`ensure_bucket`). Com `checks` injetados e sem
    `bucket_initializer`, o startup não faz chamada de rede (no-op); em produção
    (`checks=None`) usa o initializer real.

    `db_provider` e `object_store` alimentam a rota de upload (F1-05c). O pool do Postgres é
    preguiçoso: o startup não conecta (ADR-005) e o shutdown só o fecha se foi criado.
    """
    cfg = settings or Settings.from_env()
    provider: DbProvider = db_provider if db_provider is not None else LazyPool(cfg)
    store: ObjectStore = (
        object_store
        if object_store is not None
        else LazyObjectStore(lambda: Boto3ObjectStore(make_s3_client(cfg), cfg.s3_bucket))
    )
    init: Callable[[], Awaitable[None]]
    if bucket_initializer is not None:
        init = bucket_initializer
    elif checks is not None:

        async def init() -> None:
            return None

    else:

        async def init() -> None:
            await ensure_bucket(cfg)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        await init()
        try:
            yield
        finally:
            await provider.close()

    app = FastAPI(title="Inovalytics API", version=__version__, lifespan=lifespan)
    active: dict[str, Check] = (
        checks
        if checks is not None
        else {
            "postgres": lambda: check_postgres(cfg),
            "storage": lambda: check_s3(cfg),
        }
    )

    def get_checks() -> dict[str, Check]:
        return active

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready")
    async def ready(deps: dict[str, Check] = Depends(get_checks)) -> JSONResponse:  # noqa: B008
        results: dict[str, str] = {}
        for name, check in deps.items():
            try:
                await asyncio.wait_for(check(), timeout=cfg.check_timeout + 1)
                results[name] = "ok"
            except Exception as exc:
                # Só o tipo da exceção (sem str(exc) nem traceback): a mensagem pode conter
                # host, DSN ou senha.
                logger.warning("check %s falhou: %s", name, type(exc).__name__)
                results[name] = "unavailable"
        healthy = all(v == "ok" for v in results.values())
        return JSONResponse(
            status_code=200 if healthy else 503,
            content={"status": "ok" if healthy else "unavailable", "checks": results},
        )

    register_upload(app, cfg, provider, store)
    return app


def app_factory() -> FastAPI:
    """Entrypoint para `uvicorn --factory inovalytics.api.main:app_factory`."""
    # O uvicorn só configura os próprios loggers; sem isto o log INFO do startup se perderia.
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
    return create_app()
