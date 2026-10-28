"""Executor de migrações SQL puro sobre asyncpg e CLI (F1-04b, ADR-006).

Uso: `python -m inovalytics.db.migrate {up,down,status}`. Roda sempre fora do startup da API
(ADR-005). Sem ORM e sem dependência nova. Arquivos: `NNNN_nome.up.sql` / `NNNN_nome.down.sql`
em `inovalytics/db/migrations/`. A tabela `schema_migrations` é criada pelo executor.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
import re
import sys
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from importlib import resources
from typing import Any, Protocol

from inovalytics.api.config import PostgresSettings

# Chave constante do pg_advisory_lock (bigint): ASCII "INOVMIGR". Reservada às migrações; outros
# usos futuros de advisory lock devem escolher chaves diferentes.
ADVISORY_LOCK_KEY = 0x494E4F564D494752

_FILE_RE = re.compile(r"^(\d{4})_([a-z0-9_]+)\.(up|down)\.sql$")

CREATE_TABLE_SQL = (
    "CREATE TABLE IF NOT EXISTS schema_migrations ("
    "version integer PRIMARY KEY, "
    "name text NOT NULL, "
    "checksum_sha256 text NOT NULL, "
    "applied_at timestamptz NOT NULL DEFAULT now())"
)
INSERT_SQL = "INSERT INTO schema_migrations (version, name, checksum_sha256) VALUES ($1, $2, $3)"
DELETE_SQL = "DELETE FROM schema_migrations WHERE version = $1"
SELECT_SQL = "SELECT version, name, checksum_sha256 FROM schema_migrations ORDER BY version"
LOCK_SQL = "SELECT pg_advisory_lock($1)"
UNLOCK_SQL = "SELECT pg_advisory_unlock($1)"
EXISTS_SQL = "SELECT to_regclass('schema_migrations') IS NOT NULL"


class MigrationError(Exception):
    """Erro de migração. A mensagem nunca contém DSN, senha nem texto de SQL."""


class Connection(Protocol):
    """Subconjunto da conexão asyncpg usado aqui (permite conexão falsa nos testes)."""

    async def execute(self, query: str, *args: Any) -> Any: ...
    async def fetch(self, query: str, *args: Any) -> Sequence[Any]: ...
    async def fetchval(self, query: str, *args: Any) -> Any: ...
    def transaction(self) -> AbstractAsyncContextManager[Any]: ...
    async def close(self) -> None: ...


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    up_sql: str
    down_sql: str
    checksum: str  # SHA-256 do arquivo `up`


@dataclass(frozen=True)
class Applied:
    version: int
    name: str
    checksum: str


def _label(version: int, name: str) -> str:
    return f"{version:04d}_{name}"


def discover() -> list[Migration]:
    """Lê as migrações do pacote, em ordem numérica, com o SHA-256 de cada `up`."""
    root = resources.files("inovalytics.db") / "migrations"
    ups: dict[int, tuple[str, bytes]] = {}
    downs: dict[int, str] = {}
    for entry in root.iterdir():
        m = _FILE_RE.match(entry.name)
        if not m:
            continue
        version, name, kind = int(m.group(1)), m.group(2), m.group(3)
        data = entry.read_bytes()
        if kind == "up":
            ups[version] = (name, data)
        else:
            downs[version] = data.decode("utf-8")
    result: list[Migration] = []
    for version in sorted(ups):
        name, data = ups[version]
        if version not in downs:
            raise MigrationError(f"migração {_label(version, name)} sem arquivo down")
        result.append(Migration(version, name, data.decode("utf-8"), downs[version], _sha256(data)))
    return result


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def validate_applied(applied: Sequence[Applied], available: Sequence[Migration]) -> None:
    """Confere o histórico aplicado contra o disco; levanta MigrationError se divergir."""
    by_version = {m.version: m for m in available}
    previous = 0
    for a in applied:
        label = _label(a.version, a.name)
        disk = by_version.get(a.version)
        if disk is None:
            raise MigrationError(f"migração aplicada {label} não existe no disco")
        if disk.name != a.name:
            raise MigrationError(f"migração {label} aplicada com outro nome no disco")
        if disk.checksum != a.checksum:
            raise MigrationError(f"migração {label} foi alterada depois de aplicada (checksum)")
        if a.version != previous + 1:
            raise MigrationError(f"migrações aplicadas fora de ordem ou com lacuna em {label}")
        previous = a.version
    # Contiguidade das disponíveis (1..n) para que "pendentes" seja um sufixo bem definido.
    for i, m in enumerate(available, start=1):
        if m.version != i:
            raise MigrationError(f"versões no disco não contíguas em {_label(m.version, m.name)}")


def plan(applied: Sequence[Applied], available: Sequence[Migration]) -> list[Migration]:
    """Função pura: devolve as migrações pendentes, em ordem; erro se o histórico divergir."""
    validate_applied(applied, available)
    done = {a.version for a in applied}
    return [m for m in available if m.version not in done]


async def _load_applied(conn: Connection) -> list[Applied]:
    rows = await conn.fetch(SELECT_SQL)
    return [Applied(int(r["version"]), str(r["name"]), str(r["checksum_sha256"])) for r in rows]


async def _locked(
    conn: Connection, action: Callable[[], Awaitable[list[Migration]]]
) -> list[Migration]:
    await conn.execute(LOCK_SQL, ADVISORY_LOCK_KEY)
    try:
        await conn.execute(CREATE_TABLE_SQL)
        return await action()
    finally:
        await conn.execute(UNLOCK_SQL, ADVISORY_LOCK_KEY)


async def up(conn: Connection, available: Sequence[Migration] | None = None) -> list[Migration]:
    """Aplica as pendentes em ordem, cada uma em transação com o registro. Devolve as aplicadas."""
    migrations = discover() if available is None else list(available)

    async def action() -> list[Migration]:
        pending = plan(await _load_applied(conn), migrations)
        done: list[Migration] = []
        for m in pending:
            try:
                async with conn.transaction():
                    await conn.execute(m.up_sql)
                    await conn.execute(INSERT_SQL, m.version, m.name, m.checksum)
            except Exception as exc:
                raise MigrationError(
                    f"falha ao aplicar {_label(m.version, m.name)} ({type(exc).__name__})"
                ) from None
            done.append(m)
        return done

    return await _locked(conn, action)


async def down(
    conn: Connection, steps: int, available: Sequence[Migration] | None = None
) -> list[Migration]:
    """Reverte as `steps` últimas, na ordem inversa. Só para desenvolvimento e teste (ADR-006)."""
    if steps < 1:
        raise MigrationError("steps deve ser um inteiro positivo")
    migrations = discover() if available is None else list(available)

    async def action() -> list[Migration]:
        applied = await _load_applied(conn)
        validate_applied(applied, migrations)
        if steps > len(applied):
            raise MigrationError(
                f"steps={steps} maior que o número de migrações aplicadas ({len(applied)})"
            )
        by_version = {m.version: m for m in migrations}
        reverted: list[Migration] = []
        for a in reversed(applied[len(applied) - steps :]):
            m = by_version[a.version]
            try:
                async with conn.transaction():
                    await conn.execute(m.down_sql)
                    await conn.execute(DELETE_SQL, m.version)
            except Exception as exc:
                raise MigrationError(
                    f"falha ao reverter {_label(m.version, m.name)} ({type(exc).__name__})"
                ) from None
            reverted.append(m)
        return reverted

    return await _locked(conn, action)


async def status(
    conn: Connection, available: Sequence[Migration] | None = None
) -> list[tuple[int, str, bool]]:
    """Lista (versão, nome, aplicada). Não cria a tabela de controle."""
    migrations = discover() if available is None else list(available)
    applied: list[Applied] = []
    if await conn.fetchval(EXISTS_SQL):
        applied = await _load_applied(conn)
    validate_applied(applied, migrations)
    done = {a.version for a in applied}
    return [(m.version, m.name, m.version in done) for m in migrations]


# ---------------------------------------------------------------- CLI

Connector = Callable[[PostgresSettings], Awaitable[Connection]]


async def _asyncpg_connect(cfg: PostgresSettings) -> Connection:
    import asyncpg  # type: ignore[import-untyped]

    conn: Connection = await asyncpg.connect(dsn=cfg.dsn, timeout=10)
    return conn


def _positive_int(raw: str) -> int:
    try:
        value = int(raw)
    except ValueError:
        raise argparse.ArgumentTypeError("deve ser um inteiro positivo") from None
    if value < 1:
        raise argparse.ArgumentTypeError("deve ser um inteiro positivo")
    return value


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> Any:  # nunca ecoa valores sensíveis
        raise _UsageError(message)


class _UsageError(Exception):
    pass


def _build_parser() -> argparse.ArgumentParser:
    p = _Parser(prog="python -m inovalytics.db.migrate", description="Migrações do Inovalytics.")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("up", help="aplica as migrações pendentes")
    d = sub.add_parser("down", help="reverte as N últimas (só desenvolvimento e teste)")
    d.add_argument("--steps", type=_positive_int, required=True, help="quantas reverter (>= 1)")
    sub.add_parser("status", help="lista versão, nome e situação")
    return p


async def _run(args: argparse.Namespace, cfg: PostgresSettings, connect: Connector) -> None:
    conn = await connect(cfg)
    try:
        if args.command == "up":
            done = await up(conn)
            if not done:
                print("nada a aplicar")
            for m in done:
                print(f"aplicada {_label(m.version, m.name)}")
        elif args.command == "down":
            for m in await down(conn, args.steps):
                print(f"revertida {_label(m.version, m.name)}")
        else:
            for version, name, applied in await status(conn):
                print(f"{version:04d} {name} {'aplicada' if applied else 'pendente'}")
    finally:
        await conn.close()


def main(
    argv: Sequence[str] | None = None,
    env: Mapping[str, str] | None = None,
    connect: Connector | None = None,
) -> int:
    """Códigos: 0 ok, 1 erro de migração ou de conexão, 2 uso."""
    try:
        args = _build_parser().parse_args(argv)
    except _UsageError as exc:
        print(f"erro de uso: {exc}", file=sys.stderr)
        return 2
    except SystemExit as exc:  # --help
        return int(exc.code or 0) if isinstance(exc.code, int) or exc.code is None else 2
    try:
        cfg = PostgresSettings.from_env(os.environ if env is None else env)
    except ValueError as exc:  # a mensagem cita só o NOME da variável
        print(f"erro de configuração: {exc}", file=sys.stderr)
        return 1
    try:
        asyncio.run(_run(args, cfg, connect or _asyncpg_connect))
    except MigrationError as exc:
        print(f"erro de migração: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        # Só o tipo: a mensagem do driver pode conter host, DSN ou trechos de SQL.
        print(f"erro de conexão ou de banco: {type(exc).__name__}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
