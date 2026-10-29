"""Configuração da API lida do ambiente (variáveis POSTGRES_* e S3_*)."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import quote

_REQUIRED = ("POSTGRES_PASSWORD", "S3_ACCESS_KEY", "S3_SECRET_KEY")


def _positive(e: Mapping[str, str], name: str, default: float, *, integer: bool) -> float:
    """Lê um número positivo do ambiente; o erro cita só o NOME da variável.

    Ausente ou string vazia (ex.: `S3_STARTUP_TIMEOUT=` no .env) vale como não definida: padrão.
    """
    raw = e.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw) if integer else float(raw)
    except ValueError:
        raise ValueError(f"valor inválido (não numérico) em {name}") from None
    if not value > 0 or value == float("inf"):
        raise ValueError(f"valor inválido (deve ser positivo) em {name}")
    return value


@dataclass(frozen=True)
class PostgresSettings:
    """Só as variáveis POSTGRES_*: a CLI de migração (F1-04b) não exige as chaves S3."""

    host: str = "db"
    port: int = 5432
    user: str = "inovalytics"
    password: str = field(default="", repr=False)
    database: str = "inovalytics"

    @property
    def dsn(self) -> str:
        """DSN completo. Contém a senha: nunca logar nem imprimir."""
        user = quote(self.user, safe="")
        password = quote(self.password, safe="")
        host = quote(self.host, safe="")
        database = quote(self.database, safe="")
        return f"postgresql://{user}:{password}@{host}:{self.port}/{database}"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> PostgresSettings:
        e = os.environ if env is None else env
        d = cls()
        if not e.get("POSTGRES_PASSWORD"):
            raise ValueError("variável de ambiente obrigatória ausente ou vazia: POSTGRES_PASSWORD")
        try:
            port = int(e.get("POSTGRES_PORT") or d.port)
        except ValueError:
            raise ValueError("valor inválido (não numérico) em POSTGRES_PORT") from None
        return cls(
            host=e.get("POSTGRES_HOST") or d.host,
            port=port,
            user=e.get("POSTGRES_USER") or d.user,
            password=e["POSTGRES_PASSWORD"],
            database=e.get("POSTGRES_DB") or d.database,
        )


@dataclass(frozen=True)
class Settings:
    postgres_host: str = "db"
    postgres_port: int = 5432
    postgres_user: str = "inovalytics"
    postgres_password: str = field(default="", repr=False)
    postgres_db: str = "inovalytics"
    s3_endpoint_url: str = "http://storage:8333"
    s3_access_key: str = field(default="", repr=False)
    s3_secret_key: str = field(default="", repr=False)
    s3_bucket: str = "inovalytics"
    check_timeout: float = 3.0
    s3_startup_timeout: float = 30.0
    s3_startup_retries: int = 5
    upload_max_bytes: int = 20 * 1024 * 1024
    upload_max_files: int = 20
    upload_max_dimension: int = 16384
    upload_max_pixels: int = 100_000_000
    upload_read_timeout: float = (
        60.0  # leitura do S3 no PUT de upload (s); ver S3_UPLOAD_READ_TIMEOUT
    )
    import_max_json_bytes: int = 16 * 1024 * 1024
    import_max_images: int = 50_000
    import_max_annotations: int = 300_000
    import_max_categories: int = 1_000
    import_max_depth: int = 12
    import_max_errors: int = 100

    @property
    def postgres_dsn(self) -> str:
        """DSN completo. Contém a senha: nunca logar nem devolver em respostas."""
        user = quote(self.postgres_user, safe="")
        password = quote(self.postgres_password, safe="")
        return (
            f"postgresql://{user}:{password}@{self.postgres_host}:{self.postgres_port}"
            f"/{self.postgres_db}"
        )

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        e = os.environ if env is None else env
        d = cls()
        # Segredos obrigatórios: a mensagem traz só o NOME da variável, nunca o valor.
        for name in _REQUIRED:
            if not e.get(name):
                raise ValueError(f"variável de ambiente obrigatória ausente ou vazia: {name}")
        return cls(
            postgres_host=e.get("POSTGRES_HOST", d.postgres_host),
            postgres_port=int(e.get("POSTGRES_PORT", d.postgres_port)),
            postgres_user=e.get("POSTGRES_USER", d.postgres_user),
            postgres_password=e["POSTGRES_PASSWORD"],
            postgres_db=e.get("POSTGRES_DB", d.postgres_db),
            s3_endpoint_url=e.get("S3_ENDPOINT_URL", d.s3_endpoint_url),
            s3_access_key=e["S3_ACCESS_KEY"],
            s3_secret_key=e["S3_SECRET_KEY"],
            s3_bucket=e.get("S3_BUCKET", d.s3_bucket),
            check_timeout=float(e.get("HEALTH_CHECK_TIMEOUT", d.check_timeout)),
            s3_startup_timeout=_positive(
                e, "S3_STARTUP_TIMEOUT", d.s3_startup_timeout, integer=False
            ),
            s3_startup_retries=int(
                _positive(e, "S3_STARTUP_RETRIES", d.s3_startup_retries, integer=True)
            ),
            upload_max_bytes=int(
                _positive(e, "UPLOAD_MAX_BYTES", d.upload_max_bytes, integer=True)
            ),
            upload_max_files=int(
                _positive(e, "UPLOAD_MAX_FILES", d.upload_max_files, integer=True)
            ),
            upload_max_dimension=int(
                _positive(e, "UPLOAD_MAX_DIMENSION", d.upload_max_dimension, integer=True)
            ),
            upload_max_pixels=int(
                _positive(e, "UPLOAD_MAX_PIXELS", d.upload_max_pixels, integer=True)
            ),
            upload_read_timeout=_positive(
                e, "S3_UPLOAD_READ_TIMEOUT", d.upload_read_timeout, integer=False
            ),
            import_max_json_bytes=int(
                _positive(e, "IMPORT_MAX_JSON_BYTES", d.import_max_json_bytes, integer=True)
            ),
            import_max_images=int(
                _positive(e, "IMPORT_MAX_IMAGES", d.import_max_images, integer=True)
            ),
            import_max_annotations=int(
                _positive(e, "IMPORT_MAX_ANNOTATIONS", d.import_max_annotations, integer=True)
            ),
            import_max_categories=int(
                _positive(e, "IMPORT_MAX_CATEGORIES", d.import_max_categories, integer=True)
            ),
            import_max_depth=int(
                _positive(e, "IMPORT_MAX_DEPTH", d.import_max_depth, integer=True)
            ),
            import_max_errors=int(
                _positive(e, "IMPORT_MAX_ERRORS", d.import_max_errors, integer=True)
            ),
        )
