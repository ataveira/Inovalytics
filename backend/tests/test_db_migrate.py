"""F1-04b: executor e CLI de migrações com conexão falsa (sem Docker, sem rede)."""

import ast
import asyncio
import hashlib
from collections.abc import Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest

from inovalytics.api.config import PostgresSettings
from inovalytics.db import migrate
from inovalytics.db.migrate import (
    ADVISORY_LOCK_KEY,
    Applied,
    Migration,
    MigrationError,
    discover,
    plan,
)

pytestmark = pytest.mark.usefixtures("no_external_network")

SENT_PW = "pw-sentinela-9f8e7d"


def _mig(version: int, name: str, up_sql: str = "UP;") -> Migration:
    return Migration(
        version, name, up_sql, f"DOWN{version};", hashlib.sha256(up_sql.encode()).hexdigest()
    )


class FakeConn:
    """Imita o que o executor usa; registra a ordem das chamadas e simula transação."""

    def __init__(self, applied: Sequence[Applied] = (), fail_on: str | None = None) -> None:
        self.rows: list[tuple[int, str, str]] = [(a.version, a.name, a.checksum) for a in applied]
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.fail_on = fail_on
        self.table_exists = True
        self.closed = False
        self._snapshot: list[tuple[int, str, str]] | None = None

    async def execute(self, query: str, *args: Any) -> None:
        self.calls.append((query, args))
        if self.fail_on and query == self.fail_on:
            raise RuntimeError(f"boom {SENT_PW}")
        if query == migrate.INSERT_SQL:
            self.rows.append((args[0], args[1], args[2]))
        elif query == migrate.DELETE_SQL:
            self.rows = [r for r in self.rows if r[0] != args[0]]
        elif query == migrate.CREATE_TABLE_SQL:
            self.table_exists = True

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        self.calls.append((query, args))
        return [{"version": v, "name": n, "checksum_sha256": c} for v, n, c in sorted(self.rows)]

    async def fetchval(self, query: str, *args: Any) -> Any:
        self.calls.append((query, args))
        return self.table_exists

    @asynccontextmanager
    async def transaction(self) -> Any:
        self.calls.append(("BEGIN", ()))
        snap = list(self.rows)
        try:
            yield
        except BaseException:
            self.rows = snap
            self.calls.append(("ROLLBACK", ()))
            raise
        self.calls.append(("COMMIT", ()))

    async def close(self) -> None:
        self.closed = True

    def sql_calls(self) -> list[str]:
        return [q for q, _ in self.calls]


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _applied(*migs: Migration) -> list[Applied]:
    return [Applied(m.version, m.name, m.checksum) for m in migs]


# 9
def test_discover_order_and_checksum() -> None:
    migs = discover()
    assert [m.version for m in migs] == list(range(1, len(migs) + 1))
    first = migs[0]
    assert first.name == "initial"
    raw = (Path(migrate.__file__).parent / "migrations" / "0001_initial.up.sql").read_bytes()
    assert first.checksum == hashlib.sha256(raw).hexdigest()
    assert first.down_sql.strip()


def test_plan_is_pure_and_ordered() -> None:
    a, b, c = _mig(1, "a"), _mig(2, "b"), _mig(3, "c")
    avail = [a, b, c]
    assert plan([], avail) == avail
    assert plan(_applied(a), avail) == [b, c]
    assert plan(_applied(a, b, c), avail) == []


# 10
def test_up_idempotent_no_ddl() -> None:
    avail = [_mig(1, "a"), _mig(2, "b")]
    conn = FakeConn(_applied(*avail))
    assert _run(migrate.up(conn, avail)) == []
    assert not any(q in ("UP;",) for q in conn.sql_calls())
    assert "BEGIN" not in conn.sql_calls()


def test_up_applies_only_pending_in_order() -> None:
    a, b, c = _mig(1, "a", "UP_A;"), _mig(2, "b", "UP_B;"), _mig(3, "c", "UP_C;")
    conn = FakeConn(_applied(a))
    done = _run(migrate.up(conn, [a, b, c]))
    assert [m.version for m in done] == [2, 3]
    ddl = [q for q in conn.sql_calls() if q.startswith("UP_")]
    assert ddl == ["UP_B;", "UP_C;"]
    assert [r[0] for r in conn.rows] == [1, 2, 3]


# 11
def test_checksum_mismatch_applies_nothing() -> None:
    a = _mig(1, "a", "UP_A;")
    changed = _mig(1, "a", "UP_A_EDITADO;")
    b = _mig(2, "b", "UP_B;")
    conn = FakeConn(_applied(a))
    with pytest.raises(MigrationError, match=r"0001_a"):
        _run(migrate.up(conn, [changed, b]))
    assert "UP_B;" not in conn.sql_calls()
    assert "BEGIN" not in conn.sql_calls()


def test_applied_missing_on_disk_gap_and_out_of_order() -> None:
    a, b = _mig(1, "a"), _mig(2, "b")
    with pytest.raises(MigrationError, match="0002_b"):
        plan(_applied(a, b), [a])
    with pytest.raises(MigrationError, match="0002_b"):
        plan(_applied(b), [a, b])  # lacuna: 1 não aplicada, 2 aplicada
    with pytest.raises(MigrationError):
        plan(_applied(b, a), [a, b])  # fora de ordem


# 12
def test_atomicity_failure_rolls_back_and_stops() -> None:
    a, b, c = _mig(1, "a", "UP_A;"), _mig(2, "b", "UP_B;"), _mig(3, "c", "UP_C;")
    conn = FakeConn(fail_on="UP_B;")
    with pytest.raises(MigrationError, match=r"0002_b") as ei:
        _run(migrate.up(conn, [a, b, c]))
    assert SENT_PW not in str(ei.value)
    assert [r[0] for r in conn.rows] == [1]
    assert "UP_C;" not in conn.sql_calls()
    calls = conn.sql_calls()
    assert calls.index("UP_B;") < calls.index("ROLLBACK")
    # cada migração e seu registro ficam dentro da mesma transação
    i = calls.index("UP_A;")
    assert (
        calls[i - 1] == "BEGIN" and calls[i + 1] == migrate.INSERT_SQL and calls[i + 2] == "COMMIT"
    )


# 13
def test_advisory_lock_order_and_release_on_failure() -> None:
    a = _mig(1, "a", "UP_A;")
    conn = FakeConn()
    _run(migrate.up(conn, [a]))
    calls = conn.calls
    assert calls[0] == (migrate.LOCK_SQL, (ADVISORY_LOCK_KEY,))
    assert calls[-1] == (migrate.UNLOCK_SQL, (ADVISORY_LOCK_KEY,))
    assert calls[1][0] == migrate.CREATE_TABLE_SQL
    assert calls[2][0] == migrate.SELECT_SQL  # estado só é lido depois do lock

    bad = FakeConn(fail_on="UP_A;")
    with pytest.raises(MigrationError):
        _run(migrate.up(bad, [a]))
    assert bad.calls[-1] == (migrate.UNLOCK_SQL, (ADVISORY_LOCK_KEY,))

    changed = FakeConn(_applied(_mig(1, "a", "OUTRO;")))
    with pytest.raises(MigrationError):
        _run(migrate.up(changed, [a]))
    assert changed.calls[-1] == (migrate.UNLOCK_SQL, (ADVISORY_LOCK_KEY,))


# 14
def test_down_steps() -> None:
    a, b, c = _mig(1, "a"), _mig(2, "b"), _mig(3, "c")
    conn = FakeConn(_applied(a, b, c))
    reverted = _run(migrate.down(conn, 2, [a, b, c]))
    assert [m.version for m in reverted] == [3, 2]
    assert [r[0] for r in conn.rows] == [1]
    ddl = [q for q in conn.sql_calls() if q.startswith("DOWN")]
    assert ddl == ["DOWN3;", "DOWN2;"]


def test_down_errors() -> None:
    a = _mig(1, "a")
    conn = FakeConn(_applied(a))
    with pytest.raises(MigrationError):
        _run(migrate.down(conn, 2, [a]))
    with pytest.raises(MigrationError):
        _run(migrate.down(conn, 0, [a]))
    assert [r[0] for r in conn.rows] == [1]


def test_up_never_reverts() -> None:
    a = _mig(1, "a")
    conn = FakeConn(_applied(a))
    _run(migrate.up(conn, [a]))
    assert not any(q.startswith("DOWN") for q in conn.sql_calls())


# 15
def _env(**extra: str) -> dict[str, str]:
    return {"POSTGRES_PASSWORD": SENT_PW, **extra}


def _connector(conn: FakeConn) -> Any:
    async def connect(cfg: PostgresSettings) -> FakeConn:
        return conn

    return connect


def test_cli_up_status_down(capsys: pytest.CaptureFixture[str]) -> None:
    conn = FakeConn()
    assert migrate.main(["status"], _env(), _connector(conn)) == 0
    assert "0001 initial pendente" in capsys.readouterr().out
    assert migrate.main(["up"], _env(), _connector(conn)) == 0
    assert "aplicada 0001_initial" in capsys.readouterr().out
    assert migrate.main(["up"], _env(), _connector(conn)) == 0
    assert "nada a aplicar" in capsys.readouterr().out
    assert migrate.main(["status"], _env(), _connector(conn)) == 0
    assert "0001 initial aplicada" in capsys.readouterr().out
    assert migrate.main(["down", "--steps", "1"], _env(), _connector(conn)) == 0
    assert "revertida 0001_initial" in capsys.readouterr().out
    assert conn.closed


def test_status_does_not_create_table() -> None:
    conn = FakeConn()
    conn.table_exists = False
    assert migrate.main(["status"], _env(), _connector(conn)) == 0
    assert migrate.CREATE_TABLE_SQL not in conn.sql_calls()


@pytest.mark.parametrize(
    "argv",
    [["down"], ["down", "--steps", "0"], ["down", "--steps", "x"], [], ["bogus"], ["down", "-1"]],
)
def test_cli_usage_errors_exit_2(argv: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    conn = FakeConn()
    assert migrate.main(argv, _env(), _connector(conn)) == 2
    assert conn.calls == []


def test_cli_works_without_s3_env() -> None:
    env = {"POSTGRES_PASSWORD": SENT_PW}  # sem S3_*
    assert migrate.main(["status"], env, _connector(FakeConn())) == 0


def test_cli_missing_password_exit_1(capsys: pytest.CaptureFixture[str]) -> None:
    assert migrate.main(["status"], {}, _connector(FakeConn())) == 1
    assert "POSTGRES_PASSWORD" in capsys.readouterr().err


def test_cli_migration_error_exit_1_no_secrets(
    capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    conn = FakeConn(_applied(_mig(1, "initial", "OUTRO;")))
    assert migrate.main(["up"], _env(), _connector(conn)) == 1
    out = capsys.readouterr()
    assert "0001_initial" in out.err
    assert SENT_PW not in out.out + out.err + caplog.text


def test_cli_connection_error_exit_1_no_secrets(
    capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    dsn = PostgresSettings(password=SENT_PW).dsn

    async def connect(cfg: PostgresSettings) -> FakeConn:
        raise OSError(f"falha em {dsn}")

    assert migrate.main(["up"], _env(), connect) == 1
    out = capsys.readouterr()
    assert out.err.strip()
    assert SENT_PW not in out.out + out.err + caplog.text
    assert dsn not in out.out + out.err + caplog.text


def test_cli_db_error_during_migration_no_secrets(capsys: pytest.CaptureFixture[str]) -> None:
    conn = FakeConn(fail_on=migrate.CREATE_TABLE_SQL)
    assert migrate.main(["up"], _env(), _connector(conn)) == 1
    out = capsys.readouterr()
    assert SENT_PW not in out.out + out.err


def test_postgres_settings_repr_hides_password() -> None:
    assert SENT_PW not in repr(PostgresSettings(password=SENT_PW))


# 16
def test_api_main_does_not_import_db(repo_root: Path) -> None:
    src = (repo_root / "backend/src/inovalytics/api/main.py").read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(src)):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            names = [base] + [f"{base}.{a.name}" for a in node.names]
        for n in names:
            assert not n.startswith("inovalytics.db"), n


# 17
def test_no_new_dependencies(repo_root: Path) -> None:
    import tomllib

    data = tomllib.loads((repo_root / "pyproject.toml").read_text(encoding="utf-8"))
    assert data["project"]["dependencies"] == [
        "fastapi>=0.115",
        "uvicorn>=0.30",
        "asyncpg>=0.30",
        "boto3>=1.35",
    ]
    assert len(data["project"]["optional-dependencies"]["dev"]) == 7


def test_makefile_has_migrate_target(repo_root: Path) -> None:
    text = (repo_root / "Makefile").read_text(encoding="utf-8")
    assert "migrate:" in text
    assert "inovalytics.db.migrate up" in text


# revisão F1-04b: SQLSTATE, unlock que falha, down órfão
class _SqlStateError(Exception):
    sqlstate = "23503"


def test_failure_message_has_sqlstate_but_not_exception_text() -> None:
    class Conn(FakeConn):
        async def execute(self, query: str, *args: Any) -> None:
            if query == "UP_A;":
                raise _SqlStateError(f"segredo {SENT_PW} INSERT INTO t")
            await super().execute(query, *args)

    with pytest.raises(MigrationError) as ei:
        _run(migrate.up(Conn(), [_mig(1, "a", "UP_A;")]))
    text = str(ei.value)
    assert "SQLSTATE 23503" in text and "_SqlStateError" in text
    assert SENT_PW not in text and "INSERT" not in text


def test_unlock_failure_does_not_mask_original_error() -> None:
    class Conn(FakeConn):
        async def execute(self, query: str, *args: Any) -> None:
            if query == migrate.UNLOCK_SQL:
                raise RuntimeError("unlock falhou")
            await super().execute(query, *args)

    with pytest.raises(MigrationError, match=r"0001_a"):
        _run(migrate.up(Conn(fail_on="UP_A;"), [_mig(1, "a", "UP_A;")]))
    assert _run(migrate.up(Conn(), [])) == []  # sem erro original, unlock falho é engolido


def test_discover_orphan_down_file_is_runtime_error(monkeypatch: pytest.MonkeyPatch) -> None:
    class Entry:
        def __init__(self, name: str) -> None:
            self.name = name

        def read_bytes(self) -> bytes:
            return b"SELECT 1;"

    class Root:
        def __truediv__(self, _: str) -> "Root":
            return self

        def iterdir(self) -> list[Entry]:
            return [Entry("0001_a.up.sql"), Entry("0001_a.down.sql"), Entry("0002_b.down.sql")]

    monkeypatch.setattr(migrate.resources, "files", lambda _: Root())
    with pytest.raises(MigrationError, match=r"órfão 0002_b"):
        discover()


def test_dsn_quotes_host_and_database() -> None:
    cfg = PostgresSettings(host="h/o@st", user="u", password="p", database="d/b?x=1")
    dsn = cfg.dsn
    assert "h%2Fo%40st" in dsn and dsn.endswith("/d%2Fb%3Fx%3D1")
