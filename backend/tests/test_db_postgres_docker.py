"""F1-04 critérios 18 a 22 (migrações, integridade) e 25 a 27 (repositório) em Postgres real.

Rodam com `make compose-smoke`.

Sobe só o serviço `db` do Compose, em projeto isolado e com credenciais aleatórias.
"""

import asyncio
import functools
import inspect
import secrets
import shutil
import subprocess
import time
from collections.abc import Awaitable, Callable, Iterator
from pathlib import Path
from typing import Any

import asyncpg  # type: ignore[import-untyped]
import pytest

from inovalytics.api.config import PostgresSettings
from inovalytics.db import migrate
from inovalytics.db import repository as repo

pytestmark = pytest.mark.docker

PROJECT = "inovalytics-migrate-smoke"
TABLES = {"project", "dataset", "label_class", "image", "annotation"}
SHA_A = "a" * 64
SHA_B = "b" * 64


def _docker_ready() -> bool:
    if shutil.which("docker") is None:
        return False
    return subprocess.run(["docker", "info"], capture_output=True).returncode == 0


def _dc(root: Path, env: Path, override: Path, *args: str) -> subprocess.CompletedProcess[str]:
    cmd = [
        "docker", "compose", "-p", PROJECT, "--env-file", str(env),
        "-f", "docker-compose.yml", "-f", str(override), *args,
    ]  # fmt: skip
    return subprocess.run(cmd, cwd=root, capture_output=True, text=True, timeout=600)


@pytest.fixture(scope="module")
def pg(repo_root: Path, tmp_path_factory: pytest.TempPathFactory) -> Iterator[PostgresSettings]:
    if not _docker_ready():
        pytest.skip("docker indisponível")
    tmp = tmp_path_factory.mktemp("migrate")
    password = secrets.token_hex(8)
    env = tmp / "m.env"
    env.write_text(
        f"POSTGRES_PASSWORD={password}\nS3_ACCESS_KEY=ak{secrets.token_hex(6)}\n"
        f"S3_SECRET_KEY={secrets.token_hex(16)}\n",
        encoding="utf-8",
    )
    override = tmp / "override.yml"
    override.write_text(
        'services:\n  db:\n    ports:\n      - "127.0.0.1::5432"\n', encoding="utf-8"
    )
    try:
        r = _dc(repo_root, env, override, "up", "-d", "--wait", "db")
        if r.returncode != 0:
            pytest.fail(f"falha ao subir o db: {r.stderr[-800:]}")
        port_out = _dc(repo_root, env, override, "port", "db", "5432").stdout.strip()
        port = int(port_out.rsplit(":", 1)[1])
        cfg = PostgresSettings(host="127.0.0.1", port=port, password=password)
        deadline = time.time() + 30
        while True:
            try:
                asyncio.run(_ping(cfg))
                break
            except Exception:
                if time.time() > deadline:
                    raise
                time.sleep(1)
        yield cfg
    finally:
        _dc(repo_root, env, override, "down", "-v", "--remove-orphans")


async def _ping(cfg: PostgresSettings) -> None:
    c = await asyncpg.connect(dsn=cfg.dsn, timeout=5)
    await c.close()


def _with_db(
    migrated: bool = True,
) -> Callable[[Callable[..., Awaitable[None]]], Callable[..., None]]:
    """Roda o teste assíncrono com uma conexão limpa (sem pytest-asyncio: dependência nova)."""

    def deco(fn: Callable[..., Awaitable[None]]) -> Callable[..., None]:
        sig = inspect.signature(fn)
        params = list(sig.parameters.values())[1:]  # tira `conn`

        @functools.wraps(fn)
        def wrapper(pg: PostgresSettings, *args: Any, **kwargs: Any) -> None:
            async def body() -> None:
                c = await asyncpg.connect(dsn=pg.dsn, timeout=10)
                try:
                    await c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
                    if migrated:
                        await migrate.up(c)
                    await fn(c, *args, **kwargs)
                finally:
                    await c.close()

            asyncio.run(body())

        wrapper.__signature__ = sig.replace(  # type: ignore[attr-defined]
            parameters=[inspect.Parameter("pg", inspect.Parameter.POSITIONAL_OR_KEYWORD), *params]
        )
        return wrapper

    return deco


async def _tables(c: Any) -> set[str]:
    rows = await c.fetch("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
    return {r["tablename"] for r in rows}


async def _catalog(c: Any) -> dict[str, list[Any]]:
    cols = await c.fetch(
        "SELECT table_name, column_name, data_type, is_nullable, column_default, is_identity,"
        " identity_generation FROM information_schema.columns WHERE table_schema = 'public'"
        " AND table_name <> 'schema_migrations' ORDER BY 1, 2"
    )
    cons = await c.fetch(
        "SELECT conrelid::regclass::text AS t, conname, pg_get_constraintdef(oid) AS d"
        " FROM pg_constraint WHERE connamespace = 'public'::regnamespace"
        " AND conrelid::regclass::text <> 'schema_migrations' ORDER BY 1, 2"
    )
    idx = await c.fetch(
        "SELECT tablename, indexname, indexdef FROM pg_indexes WHERE schemaname = 'public'"
        " AND tablename <> 'schema_migrations' ORDER BY 1, 2"
    )
    return {"cols": [tuple(r) for r in cols], "cons": [tuple(r) for r in cons],
            "idx": [tuple(r) for r in idx]}  # fmt: skip


# 18
@_with_db(migrated=False)
async def test_up_creates_schema_and_is_idempotent(conn: Any) -> None:
    done = await migrate.up(conn)
    assert len(done) == 1
    assert await _tables(conn) == TABLES | {"schema_migrations"}
    before = await conn.fetch("SELECT * FROM schema_migrations")
    assert len(before) == 1
    assert await migrate.up(conn) == []
    assert [tuple(r) for r in await conn.fetch("SELECT * FROM schema_migrations")] == [
        tuple(r) for r in before
    ]


# 19
@_with_db(migrated=False)
async def test_reversible_same_catalog(conn: Any) -> None:
    await migrate.up(conn)
    first = await _catalog(conn)
    await migrate.down(conn, 1)
    assert await _tables(conn) == {"schema_migrations"}
    assert await conn.fetchval("SELECT count(*) FROM schema_migrations") == 0
    await migrate.up(conn)
    assert await _catalog(conn) == first


# 20
def test_concurrent_up(pg: PostgresSettings) -> None:
    asyncio.run(_concurrent(pg))


async def _concurrent(pg: PostgresSettings) -> None:
    conn = await asyncpg.connect(dsn=pg.dsn)
    await conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
    c1 = await asyncpg.connect(dsn=pg.dsn)
    c2 = await asyncpg.connect(dsn=pg.dsn)
    try:
        results = await asyncio.gather(migrate.up(c1), migrate.up(c2))
    finally:
        await c1.close()
        await c2.close()
    assert sorted(len(r) for r in results) == [0, 1]
    assert await conn.fetchval("SELECT count(*) FROM schema_migrations") == 1
    assert await _tables(conn) == TABLES | {"schema_migrations"}
    await conn.close()


# ---- 21 e 22: integridade
async def _project(c: Any, name: str = "p") -> int:
    return int(await c.fetchval("INSERT INTO project (name) VALUES ($1) RETURNING id", name))


async def _dataset(c: Any, pid: int, name: str = "d") -> int:
    return int(
        await c.fetchval(
            "INSERT INTO dataset (project_id, name) VALUES ($1, $2) RETURNING id", pid, name
        )
    )


async def _class(c: Any, pid: int, name: str = "c", pos: int = 0) -> int:
    return int(
        await c.fetchval(
            "INSERT INTO label_class (project_id, name, position) VALUES ($1, $2, $3) RETURNING id",
            pid,
            name,
            pos,
        )  # fmt: skip
    )


async def _image(c: Any, did: int, pid: int, sha: str = SHA_A, key: str | None = None) -> int:
    return int(
        await c.fetchval(
            "INSERT INTO image (dataset_id, project_id, object_key, file_name, sha256, size_bytes,"
            " width_px, height_px) VALUES ($1, $2, $3, 'f.jpg', $4, 10, 100, 100) RETURNING id",
            did,
            pid,
            key or f"k/{secrets.token_hex(6)}",
            sha,
        )  # fmt: skip
    )


async def _ann(
    c: Any,
    iid: int,
    pid: int,
    cid: int,
    x: float = 0.5,
    y: float = 0.5,
    w: float = 0.2,
    h: float = 0.2,
) -> None:
    await c.execute(
        "INSERT INTO annotation (image_id, project_id, class_id, x_center, y_center, width, height)"
        " VALUES ($1, $2, $3, $4, $5, $6, $7)",
        iid, pid, cid, x, y, w, h,
    )  # fmt: skip


@_with_db()
async def test_annotation_unknown_image_fk(db: Any) -> None:
    pid = await _project(db)
    cid = await _class(db, pid)
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await _ann(db, 999999, pid, cid)


@_with_db()
async def test_class_uniqueness(db: Any) -> None:
    p1, p2 = await _project(db, "p1"), await _project(db, "p2")
    await _class(db, p1, "a", 0)
    with pytest.raises(asyncpg.UniqueViolationError):
        await _class(db, p1, "a", 1)
    with pytest.raises(asyncpg.UniqueViolationError):
        await _class(db, p1, "b", 0)
    await _class(db, p2, "a", 0)  # outro projeto aceita
    await _class(db, p1, "A", 2)  # nomes sensíveis a caixa (H7)


@_with_db()
async def test_annotation_class_from_other_project(db: Any) -> None:
    p1, p2 = await _project(db, "p1"), await _project(db, "p2")
    img = await _image(db, await _dataset(db, p1), p1)
    other_class = await _class(db, p2)
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await _ann(db, img, p1, other_class)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("x", -0.01), ("x", 1.01), ("y", -0.01), ("y", 1.01), ("w", 0.0), ("w", 1.01),
        ("h", 0.0), ("h", 1.01), ("x", float("nan")), ("w", float("nan")),
        ("x", float("inf")), ("w", float("inf")), ("h", float("-inf")),
    ],
)  # fmt: skip
@_with_db()
async def test_invalid_box(db: Any, field: str, value: float) -> None:
    pid = await _project(db)
    cid = await _class(db, pid)
    img = await _image(db, await _dataset(db, pid), pid)
    with pytest.raises(asyncpg.CheckViolationError):
        await _ann(db, img, pid, cid, **{field: value})


@_with_db()
async def test_valid_edge_box(db: Any) -> None:
    pid = await _project(db)
    cid = await _class(db, pid)
    img = await _image(db, await _dataset(db, pid), pid)
    await _ann(db, img, pid, cid, x=0.0, y=1.0, w=1.0, h=1.0)


@_with_db()
async def test_image_sha_uniqueness_and_format(db: Any) -> None:
    pid = await _project(db)
    d1, d2 = await _dataset(db, pid, "d1"), await _dataset(db, pid, "d2")
    await _image(db, d1, pid, SHA_A)
    with pytest.raises(asyncpg.UniqueViolationError):
        await _image(db, d1, pid, SHA_A)
    await _image(db, d2, pid, SHA_A)
    for bad in ("A" * 64, "a" * 63, "g" * 64):
        with pytest.raises(asyncpg.CheckViolationError):
            await _image(db, d1, pid, bad)


@_with_db()
async def test_image_dataset_from_other_project(db: Any) -> None:
    p1, p2 = await _project(db, "p1"), await _project(db, "p2")
    d1 = await _dataset(db, p1)
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await _image(db, d1, p2)


@_with_db()
async def test_cascade_project_delete(db: Any) -> None:
    pid = await _project(db)
    cid = await _class(db, pid)
    img = await _image(db, await _dataset(db, pid), pid)
    await _ann(db, img, pid, cid)
    await db.execute("DELETE FROM project WHERE id = $1", pid)
    for t in TABLES:
        assert await db.fetchval(f"SELECT count(*) FROM {t}") == 0, t  # noqa: S608


@_with_db()
async def test_delete_image_removes_annotations_and_class_rules(db: Any) -> None:
    pid = await _project(db)
    used, unused = await _class(db, pid, "u", 0), await _class(db, pid, "n", 1)
    img = await _image(db, await _dataset(db, pid), pid)
    await _ann(db, img, pid, used)
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await db.execute("DELETE FROM label_class WHERE id = $1", used)
    await db.execute("DELETE FROM label_class WHERE id = $1", unused)
    await db.execute("DELETE FROM image WHERE id = $1", img)
    assert await db.fetchval("SELECT count(*) FROM annotation") == 0
    await db.execute("DELETE FROM label_class WHERE id = $1", used)  # sem uso agora


# ---- F1-04c: repositório em Postgres real (critérios 25 a 27)
def _img_kw(key: str = "k/1") -> dict[str, Any]:
    return {"object_key": key, "file_name": "f.jpg", "sha256": SHA_A, "size_bytes": 10,
            "width_px": 100, "height_px": 80}  # fmt: skip


async def _repo_world(c: Any) -> tuple[repo.Project, repo.Dataset, repo.LabelClass, repo.Image]:
    p = await repo.create_project(c, "p")
    d = await repo.create_dataset(c, p.id, "d")
    k = await repo.create_class(c, p.id, "car", 0)
    img, created = await repo.insert_image(c, d.id, **_img_kw())
    assert created
    return p, d, k, img


@_with_db()
async def test_repo_roundtrip_and_delete_cascade(c: Any) -> None:
    p, _, k, img = await _repo_world(c)
    assert await repo.get_project(c, p.id) == p
    assert [x.id for x in await repo.list_projects(c)] == [p.id]
    n = await repo.insert_annotations(c, img.id, [repo.NewAnnotation(k.id, 0.5, 0.5, 1.0, 0.2)])
    assert n == 1
    got = await repo.list_annotations(c, img.id)
    assert [(a.class_id, a.project_id, a.width) for a in got] == [(k.id, p.id, 1.0)]
    await repo.delete_project(c, p.id)
    for t in TABLES:
        assert await c.fetchval(f"SELECT count(*) FROM {t}") == 0  # noqa: S608
    with pytest.raises(repo.NotFoundError):
        await repo.delete_project(c, p.id)
    with pytest.raises(repo.NotFoundError):
        await repo.get_project(c, p.id)


@_with_db()
async def test_repo_insert_image_idempotent(c: Any) -> None:
    _, d, _, img = await _repo_world(c)
    again, created = await repo.insert_image(c, d.id, **_img_kw("k/outro"))
    assert created is False and again == img
    assert await c.fetchval("SELECT count(*) FROM image") == 1
    with pytest.raises(repo.NotFoundError):
        await repo.insert_image(c, d.id + 999, **_img_kw("k/x"))
    other = {**_img_kw("k/1"), "sha256": SHA_B}  # object_key repetida com sha diferente
    with pytest.raises(repo.DuplicateError):
        await repo.insert_image(c, d.id, **other)


def test_repo_insert_image_concurrent_same_sha(pg: PostgresSettings) -> None:
    async def body() -> None:
        c0 = await asyncpg.connect(dsn=pg.dsn)
        await c0.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
        await migrate.up(c0)
        _, d, _, _ = await _repo_world(c0)
        await c0.execute("DELETE FROM image")
        conns = [await asyncpg.connect(dsn=pg.dsn) for _ in range(4)]
        try:
            res = await asyncio.gather(
                *(repo.insert_image(c, d.id, **_img_kw(f"k/{i}")) for i, c in enumerate(conns))
            )
        finally:
            for c in conns:
                await c.close()
        assert sorted(r[1] for r in res) == [False, False, False, True]
        assert len({r[0].id for r in res}) == 1
        assert await c0.fetchval("SELECT count(*) FROM image") == 1
        await c0.close()

    asyncio.run(body())


@_with_db()
async def test_repo_batch_all_or_nothing(c: Any) -> None:
    _, _, k, img = await _repo_world(c)
    good = repo.NewAnnotation(k.id, 0.5, 0.5, 0.2, 0.2)
    for bad in (
        repo.NewAnnotation(k.id, 1.01, 0.5, 0.2, 0.2),
        repo.NewAnnotation(k.id, 0.5, 0.5, float("nan"), 0.2),
        repo.NewAnnotation(k.id, 0.5, 0.5, float("inf"), 0.2),
    ):
        with pytest.raises(repo.InvalidAnnotationError):
            await repo.insert_annotations(c, img.id, [good, bad])
        assert await c.fetchval("SELECT count(*) FROM annotation") == 0
    with pytest.raises(repo.NotFoundError):
        await repo.insert_annotations(c, img.id + 999, [good])
    assert await repo.insert_annotations(c, img.id, [good, good]) == 2


@_with_db()
async def test_repo_annotation_with_foreign_class_is_rejected(c: Any) -> None:
    _, _, _, img = await _repo_world(c)
    p2 = await repo.create_project(c, "p2")
    k2 = await repo.create_class(c, p2.id, "car", 0)
    with pytest.raises(repo.InvalidAnnotationError):
        await repo.insert_annotations(c, img.id, [repo.NewAnnotation(k2.id, 0.5, 0.5, 0.2, 0.2)])
    assert await c.fetchval("SELECT count(*) FROM annotation") == 0


SENTINEL_NAME = "segredo-sentinela-77aa"


@_with_db()
async def test_repo_domain_errors_do_not_leak(c: Any) -> None:
    p, _, _, _ = await _repo_world(c)
    with pytest.raises(repo.DuplicateError) as ei:
        await repo.create_project(c, "p")
    with pytest.raises(repo.DuplicateError):
        await repo.create_class(c, p.id, "car", 5)
    with pytest.raises(repo.DuplicateError):
        await repo.create_class(c, p.id, SENTINEL_NAME, 0)
    with pytest.raises(repo.NotFoundError) as ei2:
        await repo.create_dataset(c, p.id + 999, SENTINEL_NAME)
    for e in (ei.value, ei2.value):
        text = str(e) + repr(e) + repr(e.__context__) + repr(e.__cause__)
        assert SENTINEL_NAME not in text and "INSERT" not in text and "DETAIL" not in text
