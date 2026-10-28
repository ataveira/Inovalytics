"""Configuração da API lida do ambiente (variáveis POSTGRES_* e S3_*)."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import quote

_REQUIRED = ("POSTGRES_PASSWORD", "S3_ACCESS_KEY", "S3_SECRET_KEY")


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
        )
