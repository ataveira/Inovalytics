"""API FastAPI do Inovalytics: apenas endpoints de saúde (F0-02)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import asyncpg  # type: ignore[import-untyped]
import boto3  # type: ignore[import-untyped]
from botocore.config import Config  # type: ignore[import-untyped]
from botocore.exceptions import ClientError  # type: ignore[import-untyped]
from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse

from inovalytics import __version__
from inovalytics.api.config import Settings

logger = logging.getLogger(__name__)

# Uma verificação levanta exceção quando a dependência está indisponível.
Check = Callable[[], Awaitable[None]]


async def check_postgres(settings: Settings) -> None:
    conn = await asyncpg.connect(settings.postgres_dsn, timeout=settings.check_timeout)
    try:
        await conn.execute("SELECT 1")
    finally:
        await conn.close()


def _check_s3_sync(settings: Settings) -> None:
    client: Any = boto3.client(
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
    try:
        client.head_bucket(Bucket=settings.s3_bucket)
    except ClientError as exc:
        status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        if status != 404:
            raise
        # Bucket ainda não existe (primeira subida): criação idempotente de infraestrutura.
        client.create_bucket(Bucket=settings.s3_bucket)


async def check_s3(settings: Settings) -> None:
    await asyncio.to_thread(_check_s3_sync, settings)


def create_app(
    settings: Settings | None = None,
    checks: dict[str, Check] | None = None,
) -> FastAPI:
    """Cria a app. `checks` permite injetar verificações (fakes nos testes)."""
    cfg = settings or Settings.from_env()
    app = FastAPI(title="Inovalytics API", version=__version__)
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

    return app


def app_factory() -> FastAPI:
    """Entrypoint para `uvicorn --factory inovalytics.api.main:app_factory`."""
    return create_app()
