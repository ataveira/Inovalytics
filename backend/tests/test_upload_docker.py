"""F1-05c critérios 31 a 35: upload real (API + asyncpg + boto3) contra Postgres e SeaweedFS.

Rodam com `make compose-smoke`. Sobem só `db` e `storage` do Compose em projeto isolado, com
credenciais aleatórias e portas efêmeras em 127.0.0.1; a API roda em processo (TestClient) com o
pool preguiçoso e o cliente boto3 reais.
"""

import asyncio
import hashlib
import secrets
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import asyncpg  # type: ignore[import-untyped]
import pytest
from fastapi.testclient import TestClient

from _imgbytes import jpeg, png
from inovalytics.api.config import Settings
from inovalytics.api.main import create_app, ensure_bucket, make_s3_client
from inovalytics.db import migrate
from inovalytics.db import repository as repo

pytestmark = pytest.mark.docker

PROJECT = "inovalytics-upload-smoke"


def _docker_ready() -> bool:
    if shutil.which("docker") is None:
        return False
    return subprocess.run(["docker", "info"], capture_output=True).returncode == 0


@dataclass
class Infra:
    settings: Settings
    s3: Any
    root: Path
    env: Path
    override: Path

    def dc(self, *args: str) -> subprocess.CompletedProcess[str]:
        cmd = [
            "docker", "compose", "-p", PROJECT, "--env-file", str(self.env),
            "-f", "docker-compose.yml", "-f", str(self.override), *args,
        ]  # fmt: skip
        return subprocess.run(cmd, cwd=self.root, capture_output=True, text=True, timeout=600)


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _free_port() -> int:
    import socket

    with socket.socket() as sk:
        sk.bind(("127.0.0.1", 0))
        return int(sk.getsockname()[1])


async def _wait_pg(dsn: str) -> None:
    deadline = time.time() + 40
    while True:
        try:
            c = await asyncpg.connect(dsn, timeout=5)
            await c.close()
            return
        except Exception:
            if time.time() > deadline:
                raise
            await asyncio.sleep(1)


@pytest.fixture(scope="module")
def infra(repo_root: Path, tmp_path_factory: pytest.TempPathFactory) -> Iterator[Infra]:
    if not _docker_ready():
        pytest.skip("docker indisponível")
    tmp = tmp_path_factory.mktemp("upload")
    pg_pass, ak, sk = secrets.token_hex(8), "ak" + secrets.token_hex(6), secrets.token_hex(16)
    env = tmp / "u.env"
    env.write_text(
        f"POSTGRES_PASSWORD={pg_pass}\nS3_ACCESS_KEY={ak}\nS3_SECRET_KEY={sk}\n", encoding="utf-8"
    )
    override = tmp / "override.yml"
    # Portas fixas: com porta efêmera, `stop`/`up` do storage muda a porta mapeada no host.
    pg_port, s3_port = _free_port(), _free_port()
    override.write_text(
        f'services:\n  db:\n    ports:\n      - "127.0.0.1:{pg_port}:5432"\n'
        f'  storage:\n    ports:\n      - "127.0.0.1:{s3_port}:8333"\n',
        encoding="utf-8",
    )
    stub = Infra(Settings(postgres_password=pg_pass), None, repo_root, env, override)
    try:
        r = stub.dc("up", "-d", "--wait", "db", "storage")
        if r.returncode != 0:
            pytest.fail(f"falha ao subir db/storage: {r.stderr[-800:]}")
        cfg = Settings(
            postgres_host="127.0.0.1",
            postgres_port=pg_port,
            postgres_password=pg_pass,
            s3_endpoint_url=f"http://127.0.0.1:{s3_port}",
            s3_access_key=ak,
            s3_secret_key=sk,
            check_timeout=5.0,
        )
        _run(_wait_pg(cfg.postgres_dsn))

        async def prepare() -> None:
            c = await asyncpg.connect(cfg.postgres_dsn, timeout=10)
            try:
                await c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
                await migrate.up(c)
            finally:
                await c.close()
            s3 = make_s3_client(cfg)
            await ensure_bucket(cfg, s3)

        _run(prepare())
        yield Infra(cfg, make_s3_client(cfg), repo_root, env, override)
    finally:
        stub.dc("down", "-v", "--remove-orphans")


@dataclass
class World:
    infra: Infra
    dataset_id: int

    @property
    def url(self) -> str:
        return f"/datasets/{self.dataset_id}/images"

    def objects(self) -> list[str]:
        r = self.infra.s3.list_objects_v2(
            Bucket=self.infra.settings.s3_bucket, Prefix=f"datasets/{self.dataset_id}/"
        )
        return [o["Key"] for o in r.get("Contents", [])]

    def rows(self) -> list[Any]:
        async def q() -> list[Any]:
            c = await asyncpg.connect(self.infra.settings.postgres_dsn, timeout=10)
            try:
                return list(
                    await c.fetch("SELECT * FROM image WHERE dataset_id = $1", self.dataset_id)
                )
            finally:
                await c.close()

        return list(_run(q()))


@pytest.fixture
def world(infra: Infra) -> World:
    async def mk() -> int:
        c = await asyncpg.connect(infra.settings.postgres_dsn, timeout=10)
        try:
            p = await repo.create_project(c, "p" + secrets.token_hex(4))
            return (await repo.create_dataset(c, p.id, "d")).id
        finally:
            await c.close()

    return World(infra, _run(mk()))


@pytest.fixture
def client(infra: Infra) -> Iterator[TestClient]:
    async def ok() -> None:
        return None

    app = create_app(infra.settings, checks={"postgres": ok, "storage": ok})
    with TestClient(app) as c:
        yield c


def _post(c: TestClient, w: World, name: str, data: bytes, ctype: str) -> Any:
    return c.post(w.url, files=[("files", (name, data, ctype))])


# 31
@pytest.mark.parametrize(
    "data,ctype,ext",
    [(png(8, 6), "image/png", "png"), (jpeg(8, 6), "image/jpeg", "jpg")],
)
def test_real_upload(client: TestClient, world: World, data: bytes, ctype: str, ext: str) -> None:
    r = _post(client, world, f"f.{ext}", data, ctype)
    assert r.status_code == 201, r.text
    sha = hashlib.sha256(data).hexdigest()
    key = f"datasets/{world.dataset_id}/{sha}.{ext}"
    s3, bucket = world.infra.s3, world.infra.settings.s3_bucket
    head = s3.head_object(Bucket=bucket, Key=key)
    assert head["ContentType"] == ctype and head["ContentLength"] == len(data)
    assert s3.get_object(Bucket=bucket, Key=key)["Body"].read() == data
    (row,) = world.rows()
    assert row["sha256"] == sha and (row["width_px"], row["height_px"]) == (8, 6)
    assert row["object_key"] == key and row["size_bytes"] == len(data)


# 32
def test_resend_and_concurrent_are_idempotent(client: TestClient, world: World) -> None:
    data = png(5, 5)
    assert _post(client, world, "a.png", data, "image/png").status_code == 201
    r = _post(client, world, "b.png", data, "image/png")
    assert r.status_code == 200 and r.json()["images"][0]["created"] is False
    assert len(world.objects()) == 1 and len(world.rows()) == 1
    other = png(6, 6)
    with ThreadPoolExecutor(4) as ex:
        res = list(ex.map(lambda _: _post(client, world, "c.png", other, "image/png"), range(4)))
    assert all(x.status_code in (200, 201) for x in res)
    assert len(world.objects()) == 2 and len(world.rows()) == 2


# 33
def test_rejections_create_nothing(client: TestClient, world: World) -> None:
    big = png() + b"\0" * (world.infra.settings.upload_max_bytes + 1)
    assert _post(client, world, "g.png", big, "image/png").status_code == 413
    assert _post(client, world, "t.jpg", b"texto", "image/jpeg").status_code == 415
    assert _post(client, world, "v.png", b"", "image/png").status_code == 422
    assert world.objects() == [] and world.rows() == []


# 34
def test_compensation_when_insert_fails(
    client: TestClient, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def boom(*a: Any, **k: Any) -> Any:
        raise asyncpg.InterfaceError("falha forçada")

    monkeypatch.setattr(repo, "insert_image", boom)
    r = _post(client, world, "a.png", png(7, 7), "image/png")
    assert r.status_code == 503, r.text
    assert world.objects() == [] and world.rows() == []


def test_storage_stopped_gives_503_without_row(
    infra: Infra, client: TestClient, world: World
) -> None:
    assert infra.dc("stop", "storage").returncode == 0
    try:
        r = _post(client, world, "a.png", png(9, 9), "image/png")
        assert r.status_code == 503 and r.json()["code"] == "armazenamento_indisponivel"
        assert world.rows() == []
    finally:
        up = infra.dc("up", "-d", "--wait", "storage")
        assert up.returncode == 0, up.stderr[-400:]


# 35
def test_bucket_is_private_and_api_stays_healthy(client: TestClient, world: World) -> None:
    data = png(3, 3)
    r = _post(client, world, "a.png", data, "image/png")
    assert r.status_code == 201, r.text  # sem segredos: só detail/code
    (key,) = world.objects()
    base = world.infra.settings.s3_endpoint_url
    bucket = world.infra.settings.s3_bucket
    for path in (f"/{bucket}/{key}", f"/{bucket}", "/"):
        try:
            code = urllib.request.urlopen(base + path, timeout=5).status
        except urllib.error.HTTPError as exc:
            code = exc.code
        assert code in (401, 403), (path, code)
    assert client.get("/health").status_code == 200
