"""F1-05c critérios 20 a 29: rota de upload (TestClient, fakes injetados, sem rede e sem Docker)."""

import ast
import asyncio
import json
import tracemalloc
from collections.abc import Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import asyncpg  # type: ignore[import-untyped]
import pytest
from fastapi.testclient import TestClient

from _imgbytes import jpeg, png
from inovalytics.api import images
from inovalytics.api.config import Settings
from inovalytics.api.images import (
    ConnImageRepository,
    LazyObjectStore,
    LazyPool,
    RequestBodyLimitMiddleware,
)
from inovalytics.api.main import create_app
from inovalytics.db import repository as repo
from test_upload_service import SECRETS, FakeRepo, FakeStore

pytestmark = pytest.mark.usefixtures("no_external_network")

URL = "/datasets/1/images"


class FakeProvider:
    def __init__(self, repo_: FakeRepo, fail: Exception | None = None) -> None:
        self.repo = repo_
        self.fail = fail
        self.acquired = 0
        self.closed = 0

    @asynccontextmanager
    async def acquire(self) -> Any:
        self.acquired += 1
        if self.fail is not None:
            raise self.fail
        yield "conn"

    async def close(self) -> None:
        self.closed += 1


class Env:
    def __init__(self, monkeypatch: pytest.MonkeyPatch, **cfg: Any) -> None:
        self.calls: list[str] = []
        self.repo = FakeRepo(self.calls)
        self.store = FakeStore(self.calls)
        self.provider = FakeProvider(self.repo)
        monkeypatch.setattr(images, "ConnImageRepository", lambda conn: self.repo)
        self.settings = Settings(
            postgres_password=SECRETS[2],
            s3_access_key=SECRETS[1],
            s3_secret_key=SECRETS[0],
            **cfg,
        )

    def client(self, checks: Any = None) -> TestClient:
        async def ok() -> None:
            return None

        app = create_app(
            self.settings,
            checks=checks if checks is not None else {"postgres": ok, "storage": ok},
            db_provider=self.provider,
            object_store=self.store,
        )
        return TestClient(app)


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> Env:
    return Env(monkeypatch)


def post(c: TestClient, files: list[tuple[str, bytes, str]], url: str = URL, **data: str) -> Any:
    return c.post(url, files=[("files", f) for f in files], data=data or None)


PART_HEAD = (
    b'--b\r\nContent-Disposition: form-data; name="files"; filename="a.png"\r\n'
    b"Content-Type: image/png\r\n\r\n"
)


def asgi_post(app: Any, chunks: Iterator[bytes], with_length: int | None = None) -> tuple[int, Any]:
    """Chama a app ASGI direto, entregando o corpo sob demanda (o TestClient lê tudo antes)."""
    headers = [(b"content-type", b"multipart/form-data; boundary=b")]
    if with_length is not None:
        headers.append((b"content-length", str(with_length).encode()))
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": URL,
        "raw_path": URL.encode(),
        "query_string": b"",
        "headers": headers,
        "client": ("127.0.0.1", 1),
        "server": ("test", 80),
    }
    out: dict[str, Any] = {"status": 0, "body": b""}

    async def receive() -> dict[str, Any]:
        chunk = next(chunks, None)
        if chunk is None:
            return {"type": "http.request", "body": b"", "more_body": False}
        return {"type": "http.request", "body": chunk, "more_body": True}

    async def send(m: dict[str, Any]) -> None:
        if m["type"] == "http.response.start":
            out["status"] = m["status"]
        elif m["type"] == "http.response.body":
            out["body"] += m.get("body", b"")

    asyncio.run(app(scope, receive, send))
    return out["status"], json.loads(out["body"])


# 20
@pytest.mark.parametrize(
    "data,ctype,name",
    [(png(8, 6), "image/png", "a.png"), (jpeg(8, 6), "image/jpeg", "a.jpg")],
)
def test_upload_created_then_idempotent(env: Env, data: bytes, ctype: str, name: str) -> None:
    c = env.client()
    r = post(c, [(name, data, ctype)])
    assert r.status_code == 201
    img = r.json()["images"][0]
    assert img["created"] is True and img["content_type"] == ctype
    assert (img["width_px"], img["height_px"], img["size_bytes"]) == (8, 6, len(data))
    assert img["file_name"] == name and len(img["sha256"]) == 64 and "id" in img
    r2 = post(c, [(name, data, ctype)])
    assert r2.status_code == 200
    assert r2.json()["images"][0]["created"] is False
    assert r2.json()["images"][0]["id"] == img["id"]
    assert env.store.puts == 1


def test_split_is_forwarded(env: Env) -> None:
    r = post(env.client(), [("a.png", png(), "image/png")], split="val")
    assert r.status_code == 201 and r.json()["images"][0]["split"] == "val"


# 21
def test_413_file_over_limit() -> None:
    mp = pytest.MonkeyPatch()
    env = Env(mp, upload_max_bytes=1000, upload_max_files=2)
    c = env.client()
    ok = png() + b"\0" * (1000 - len(png()))
    assert post(c, [("a.png", ok, "image/png")]).status_code == 201
    r = post(c, [("b.png", ok + b"\0", "image/png")])
    assert r.status_code == 413 and r.json()["code"] == "arquivo_grande_demais"
    assert r.json()["file_index"] == 0
    mp.undo()


def test_413_by_content_length_does_not_read_body() -> None:
    reads = 0
    sent: list[dict[str, Any]] = []
    called = False

    async def app(scope: Any, receive: Any, send: Any) -> None:
        nonlocal called
        called = True

    async def receive() -> dict[str, Any]:
        nonlocal reads
        reads += 1
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(m: dict[str, Any]) -> None:
        sent.append(m)

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/datasets/1/images",
        "headers": [(b"content-length", b"5000")],
    }
    asyncio.run(RequestBodyLimitMiddleware(app, 1000)(scope, receive, send))
    assert sent[0]["status"] == 413 and not called and reads == 0
    assert json.loads(sent[1]["body"])["code"] == "requisicao_grande_demais"


def test_413_content_length_via_http_and_other_paths_untouched(monkeypatch: pytest.MonkeyPatch):
    env = Env(monkeypatch, upload_max_bytes=1000, upload_max_files=2)
    c = env.client()
    r = c.post(
        URL, content=b"x" * 70_000, headers={"content-type": "multipart/form-data; boundary=b"}
    )
    assert r.status_code == 413
    assert c.get("/health").status_code == 200
    assert env.provider.acquired == 0


def test_413_chunked_aborts_reading(monkeypatch: pytest.MonkeyPatch) -> None:
    env = Env(monkeypatch, upload_max_bytes=1000, upload_max_files=2)
    app = env.client().app
    yielded = 0

    def gen() -> Iterator[bytes]:
        nonlocal yielded
        yield PART_HEAD
        for _ in range(2000):
            yielded += 1
            yield b"x" * 4096

    status, body = asgi_post(app, gen())
    assert status == 413 and body["code"] == "requisicao_grande_demais"
    assert yielded < 40
    assert env.provider.acquired == 0 and env.store.puts == 0


# 22
@pytest.mark.parametrize(
    "name,data,ctype",
    [
        ("foto.jpg", b"isto e texto puro", "image/jpeg"),
        ("x.png", b"<html><body>oi</body></html>", "image/png"),
        ("x.gif", b"GIF89a\x01\x00\x01\x00\x00\x00\x00;", "image/gif"),
    ],
)
def test_415(env: Env, name: str, data: bytes, ctype: str) -> None:
    r = post(env.client(), [(name, data, ctype)])
    assert r.status_code == 415 and r.json()["code"] == "tipo_nao_suportado"
    assert env.store.puts == 0 and not env.repo.rows


# 23
def test_422_cases(monkeypatch: pytest.MonkeyPatch) -> None:
    env = Env(monkeypatch, upload_max_files=2, upload_max_dimension=100)
    c = env.client()
    cases = {
        "vazio": post(c, [("a.png", b"", "image/png")]),
        "truncado": post(c, [("a.png", png()[:20], "image/png")]),
        "dimensao": post(c, [("a.png", png(101, 5), "image/png")]),
        "muitos": post(c, [(f"{i}.png", png(i + 1), "image/png") for i in range(3)]),
        "split": post(c, [("a.png", png(), "image/png")], split="xxx"),
        "id": post(c, [("a.png", png(), "image/png")], url="/datasets/abc/images"),
        "id0": post(c, [("a.png", png(), "image/png")], url="/datasets/0/images"),
        "idneg": post(c, [("a.png", png(), "image/png")], url="/datasets/-1/images"),
        "sem": c.post(URL, data={"split": "train"}),
        "semform": c.post(URL),
    }
    for name, r in cases.items():
        assert r.status_code == 422, name
        body = r.json()
        assert body["code"] and body["detail"] and "file_index" in body, name
    assert cases["vazio"].json()["file_index"] == 0
    assert cases["dimensao"].json()["code"] == "imagem_invalida"
    assert cases["muitos"].json()["code"] == "arquivos_demais"
    assert cases["split"].json()["code"] == "split_invalido"
    assert env.store.puts == 0


def test_422_bad_multipart(env: Env) -> None:
    r = env.client().post(
        URL, content=b"lixo", headers={"content-type": "multipart/form-data; boundary="}
    )
    assert r.status_code == 422


def test_422_files_field_is_plain_text(env: Env) -> None:
    r = env.client().post(URL, data={"files": "texto"})
    assert r.status_code == 422 and r.json()["code"] == "arquivo_invalido"


# 24
def test_404_unknown_dataset(env: Env) -> None:
    r = post(env.client(), [("a.png", png(), "image/png")], url="/datasets/99/images")
    assert r.status_code == 404 and r.json()["code"] == "dataset_nao_encontrado"
    assert env.store.puts == 0


def _no_leak(text: str) -> None:
    for s in [*SECRETS, "storage:8333", "postgresql://", "/srv/"]:
        assert s not in text


def test_503_storage_failure(env: Env) -> None:
    env.store.fail_put_at = 1
    r = post(env.client(), [("a.png", png(), "image/png")])
    assert r.status_code == 503 and r.json()["code"] == "armazenamento_indisponivel"
    assert not env.repo.rows
    _no_leak(r.text)


@pytest.mark.parametrize(
    "exc",
    [RuntimeError("postgresql://u:PGPASS-xyz@db/x"), OSError("db:5432 recusou"), TimeoutError()],
)
def test_503_db_provider_failure(env: Env, exc: Exception) -> None:
    env.provider.fail = exc
    r = post(env.client(), [("a.png", png(), "image/png")])
    assert r.status_code == 503 and r.json()["code"] == "banco_indisponivel"
    assert env.store.puts == 0
    _no_leak(r.text)


def test_503_db_error_after_put_compensates(env: Env) -> None:
    async def boom(*a: Any, **k: Any) -> Any:
        raise asyncpg.InterfaceError("connection is closed postgresql://u:PGPASS-xyz@h/db")

    env.repo.insert_image = boom  # type: ignore[method-assign]
    r = post(env.client(), [("a.png", png(), "image/png")])
    assert r.status_code == 503
    assert env.store.puts == 1 and not env.store.objects  # objeto removido (compensação)
    _no_leak(r.text)


# 25
@pytest.mark.parametrize(
    "name,data,ext",
    [
        ("../../x/../etc/passwd.jpg", jpeg(), "jpg"),
        ("a\\b.png", png(), "png"),
        ("lixo.jpg", png(), "png"),
    ],
)
def test_key_never_contains_client_name(env: Env, name: str, data: bytes, ext: str) -> None:
    r = post(env.client(), [(name, data, "text/html")])
    assert r.status_code == 201
    img = r.json()["images"][0]
    (key,) = env.store.objects
    assert key == f"datasets/1/{img['sha256']}.{ext}"
    assert env.store.objects[key][1] == {"jpg": "image/jpeg", "png": "image/png"}[ext]
    assert "/" not in img["file_name"] and "\\" not in img["file_name"]
    assert img["file_name"] in {"passwd.jpg", "b.png", "lixo.jpg"}


# 26
def test_streaming_memory_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    env = Env(monkeypatch, upload_max_bytes=1024 * 1024, upload_max_files=1)
    app = env.client().app
    chunk = b"x" * 65536
    yielded = 0

    def gen() -> Iterator[bytes]:
        nonlocal yielded
        yield PART_HEAD
        for _ in range(1024):  # 64 MiB
            yielded += 1
            yield chunk

    tracemalloc.start()
    try:
        status, _ = asgi_post(app, gen())
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert status == 413
    assert peak < 10 * 1024 * 1024
    assert yielded <= 1024 * 1024 // 65536 + 4


def test_per_file_limit_streams_from_spool(monkeypatch: pytest.MonkeyPatch) -> None:
    env = Env(monkeypatch, upload_max_bytes=1024 * 1024, upload_max_files=2)
    big = png() + b"\0" * (1024 * 1024)
    tracemalloc.start()
    try:
        r = post(env.client(), [("a.png", big, "image/png")])
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert r.status_code == 413 and peak < 10 * 1024 * 1024


# 27
def test_postgres_optional_at_startup(env: Env) -> None:
    async def down() -> None:
        raise ConnectionError("postgresql://u:PGPASS-xyz@db/x")

    async def ok() -> None:
        return None

    env.provider.fail = ConnectionError("x")
    with env.client({"postgres": down, "storage": ok}) as c:
        assert c.get("/health").status_code == 200
        r = c.get("/health/ready")
        assert r.status_code == 503 and r.json()["checks"]["postgres"] == "unavailable"
        assert env.provider.acquired == 0
        assert post(c, [("a.png", png(), "image/png")]).status_code == 503
        assert env.provider.acquired == 1
    assert env.provider.closed == 1


class FakePool:
    def __init__(self) -> None:
        self.closed = False
        self.acquired = 0

    @asynccontextmanager
    async def acquire(self) -> Any:
        self.acquired += 1
        yield "conn"

    async def close(self) -> None:
        self.closed = True


def test_lazy_pool_single_creation_under_concurrency() -> None:
    created: list[FakePool] = []

    async def factory(dsn: str, timeout: float) -> FakePool:
        await asyncio.sleep(0)
        p = FakePool()
        created.append(p)
        return p

    async def use(lp: LazyPool) -> str:
        async with lp.acquire() as conn:
            return str(conn)

    async def main() -> None:
        lp = LazyPool(Settings(postgres_password="x"), factory)
        assert not lp.created
        assert await asyncio.gather(*[use(lp) for _ in range(10)]) == ["conn"] * 10
        assert len(created) == 1 and created[0].acquired == 10
        await lp.close()
        assert created[0].closed

    asyncio.run(main())


def test_lazy_pool_does_not_memoize_failure_and_close_without_pool() -> None:
    attempts = 0
    pool = FakePool()

    async def factory(dsn: str, timeout: float) -> FakePool:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("recusado")
        return pool

    async def main() -> None:
        lp = LazyPool(Settings(postgres_password="x"), factory)
        await lp.close()  # nunca criado: no-op
        with pytest.raises(OSError):
            async with lp.acquire():
                pass
        assert not lp.created
        async with lp.acquire() as conn:
            assert conn == "conn"
        assert attempts == 2 and lp.created

    asyncio.run(main())


def test_app_default_provider_does_not_connect_at_startup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def boom(*a: Any, **k: Any) -> Any:
        raise AssertionError("não deve conectar")

    monkeypatch.setattr(images, "_create_pool", boom)
    monkeypatch.setattr(asyncpg, "create_pool", boom)

    async def ok() -> None:
        return None

    app = create_app(Settings(postgres_password="x"), checks={"postgres": ok})
    with TestClient(app) as c:
        assert c.get("/health").status_code == 200


def test_lazy_object_store_builds_once_on_first_use() -> None:
    built = 0
    inner = FakeStore([])

    def factory() -> FakeStore:
        nonlocal built
        built += 1
        return inner

    s = LazyObjectStore(factory)  # type: ignore[arg-type]
    assert built == 0

    async def main() -> None:
        import io

        await s.put("k", io.BytesIO(b"1"), "image/png")
        await s.delete("k")

    asyncio.run(main())
    assert built == 1 and inner.puts == 1


def test_adapter_forwards_connection_to_repository(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str, Any]] = []

    async def get_dataset(conn: Any, i: int) -> str:
        seen.append(("ds", conn))
        return "D"

    async def by_sha(conn: Any, i: int, s: str) -> None:
        seen.append(("sha", conn))

    async def insert(conn: Any, i: int, **kw: Any) -> str:
        seen.append(("ins", conn))
        assert kw["split"] == "train"
        return "I"

    monkeypatch.setattr(repo, "get_dataset", get_dataset)
    monkeypatch.setattr(repo, "get_image_by_sha", by_sha)
    monkeypatch.setattr(repo, "insert_image", insert)
    a = ConnImageRepository("C")

    async def main() -> None:
        assert await a.get_dataset(1) == "D"
        assert await a.get_image_by_sha(1, "s") is None
        kw: dict[str, Any] = dict(
            object_key="k", file_name="f", sha256="s", size_bytes=1, width_px=1, height_px=1
        )
        assert await a.insert_image(1, split="train", **kw) == "I"

    asyncio.run(main())
    assert [c for _, c in seen] == ["C", "C", "C"]


# 28 e 29
def test_python_multipart_registered(repo_root: Path) -> None:
    import tomllib

    data = tomllib.loads((repo_root / "pyproject.toml").read_text(encoding="utf-8"))
    deps = [d.split(">")[0].split("=")[0].strip().lower() for d in data["project"]["dependencies"]]
    assert "python-multipart" in deps
    assert "multipart" not in deps and "pillow" not in deps
    lic = (repo_root / "docs/licencas.md").read_text(encoding="utf-8")
    row = next(ln for ln in lic.splitlines() if ln.startswith("| python-multipart "))
    assert "Apache-2.0" in row and "github.com/Kludex/python-multipart" in row
    import importlib.metadata as md

    assert md.version("python-multipart") in row
    assert "pillow" not in lic.lower()


def test_only_images_module_imports_db(repo_root: Path) -> None:
    for path in (repo_root / "backend/src/inovalytics/api").glob("*.py"):
        mods: list[str] = []
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                mods += [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                mods.append(node.module or "")
        has_db = any(m.startswith("inovalytics.db") for m in mods)
        assert has_db == (path.name == "images.py"), path.name


def test_routes_are_only_health_and_upload() -> None:
    paths = {getattr(r, "path", "") for r in create_app(Settings(postgres_password="x")).routes}
    assert "/datasets/{dataset_id}/images" in paths
    assert {"/health", "/health/ready"} <= paths


# --- recomendações do revisor (F1-05) ---------------------------------------------------------


def test_acquire_timeout_is_503_with_clear_code(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(images, "DB_ACQUIRE_TIMEOUT", 0.05)

    @asynccontextmanager
    async def slow() -> Any:
        await asyncio.sleep(5)
        yield "conn"

    env.provider.acquire = slow  # type: ignore[method-assign]
    r = post(env.client(), [("a.png", png(), "image/png")])
    assert r.status_code == 503 and r.json()["code"] == "banco_timeout_conexao"
    assert env.store.puts == 0


def test_integrity_violation_is_409_without_leak(env: Env) -> None:
    async def boom(*a: Any, **k: Any) -> Any:
        raise asyncpg.exceptions.CheckViolationError("postgresql://u:PGPASS-xyz@h/db")

    env.repo.insert_image = boom  # type: ignore[method-assign]
    r = post(env.client(), [("a.png", png(), "image/png")])
    assert r.status_code == 409 and r.json()["code"] == "conflito_integridade"
    assert set(r.json()) == {"detail", "code", "file_index"}
    assert not env.store.objects
    _no_leak(r.text)


def test_unexpected_error_is_json_500(env: Env) -> None:
    env.repo.fail_insert = True
    r = post(env.client(), [("a.png", png(), "image/png")])
    assert r.status_code == 500 and r.json()["code"] == "erro_interno"
    assert set(r.json()) == {"detail", "code", "file_index"}
    _no_leak(r.text)


def test_too_many_files_is_422_from_service_not_parser(env: Env) -> None:
    env.settings = Settings(**{**env.settings.__dict__, "upload_max_files": 2})
    files = [(f"{i}.png", png(i), "image/png") for i in range(3)]
    r = post(env.client(), files)
    assert r.status_code == 422 and env.store.puts == 0


def test_too_many_form_fields_is_422(env: Env) -> None:
    extra = {f"f{i}": "x" for i in range(20)}
    r = post(env.client(), [("a.png", png(), "image/png")], **extra)
    assert r.status_code == 422 and env.store.puts == 0
