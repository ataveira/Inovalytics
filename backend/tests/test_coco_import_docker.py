"""F1-06c critério 37: importação COCO real (API + asyncpg) contra Postgres migrado.

Rodam com `make compose-smoke`. Sobem só o serviço `db` do Compose, em projeto isolado, com senha
aleatória e porta FIXA em 127.0.0.1 (porta efêmera muda após stop/up). As imagens entram pela rota
da F1-05 com um `ObjectStore` em memória: o S3 não faz parte do que se verifica aqui.

Para validar sem Docker, `INOVALYTICS_DISPOSABLE_PG_DSN` aponta um Postgres DESCARTÁVEL já
rodando (o esquema `public` é APAGADO a cada teste); nunca aponte para dados que importam.
"""

import asyncio
import json
import os
import secrets
import shutil
import socket
import subprocess
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import asyncpg  # type: ignore[import-untyped]
import pytest
from fastapi.testclient import TestClient

from _imgbytes import jpeg
from inovalytics.api import imports
from inovalytics.api.config import Settings
from inovalytics.api.imports import ConnCocoRepository
from inovalytics.api.main import create_app
from inovalytics.db import migrate
from inovalytics.db import repository as repo
from inovalytics.imports.coco_parser import CocoLimits, parse_coco
from inovalytics.imports.coco_service import import_coco

pytestmark = pytest.mark.docker

PROJECT = "inovalytics-coco-smoke"
DSN_ENV = "INOVALYTICS_DISPOSABLE_PG_DSN"
SIZES = {"a.jpg": (200, 100), "b.jpg": (100, 100), "c.jpg": (64, 48)}


def _docker_ready() -> bool:
    if shutil.which("docker") is None:
        return False
    return subprocess.run(["docker", "info"], capture_output=True).returncode == 0


def _free_port() -> int:
    with socket.socket() as sk:
        sk.bind(("127.0.0.1", 0))
        return int(sk.getsockname()[1])


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


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


def _dc(root: Path, env: Path, override: Path, *args: str) -> subprocess.CompletedProcess[str]:
    cmd = [
        "docker", "compose", "-p", PROJECT, "--env-file", str(env),
        "-f", "docker-compose.yml", "-f", str(override), *args,
    ]  # fmt: skip
    return subprocess.run(cmd, cwd=root, capture_output=True, text=True, timeout=600)


@pytest.fixture(scope="module")
def cfg(repo_root: Path, tmp_path_factory: pytest.TempPathFactory) -> Iterator[Settings]:
    external = os.environ.get(DSN_ENV)
    if external:
        u = urlparse(external)
        yield Settings(
            postgres_host=u.hostname or "127.0.0.1",
            postgres_port=u.port or 5432,
            postgres_user=unquote(u.username or ""),
            postgres_password=unquote(u.password or ""),
            postgres_db=(u.path or "/postgres").lstrip("/"),
            s3_access_key="x",
            s3_secret_key="x",
            check_timeout=5.0,
        )
        return
    if not _docker_ready():
        pytest.skip("docker indisponível")
    tmp = tmp_path_factory.mktemp("coco")
    password = secrets.token_hex(8)
    env = tmp / "c.env"
    env.write_text(
        f"POSTGRES_PASSWORD={password}\nS3_ACCESS_KEY=ak{secrets.token_hex(6)}\n"
        f"S3_SECRET_KEY={secrets.token_hex(16)}\n",
        encoding="utf-8",
    )
    override = tmp / "override.yml"
    port = _free_port()
    override.write_text(
        f'services:\n  db:\n    ports:\n      - "127.0.0.1:{port}:5432"\n', encoding="utf-8"
    )
    try:
        r = _dc(repo_root, env, override, "up", "-d", "--wait", "db")
        if r.returncode != 0:
            pytest.fail(f"falha ao subir o db: {r.stderr[-800:]}")
        s = Settings(
            postgres_host="127.0.0.1",
            postgres_port=port,
            postgres_password=password,
            s3_access_key="x",
            s3_secret_key="x",
            check_timeout=5.0,
        )
        _run(_wait_pg(s.postgres_dsn))
        yield s
    finally:
        _dc(repo_root, env, override, "down", "-v", "--remove-orphans")


class MemStore:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def put(self, key: str, fileobj: Any, content_type: str) -> None:
        self.objects[key] = fileobj.read()

    async def delete(self, key: str) -> None:
        self.objects.pop(key, None)


async def _sql(cfg: Settings, query: str, *args: Any) -> list[Any]:
    c = await asyncpg.connect(cfg.postgres_dsn, timeout=10)
    try:
        return list(await c.fetch(query, *args))
    finally:
        await c.close()


async def _fresh(cfg: Settings) -> tuple[int, int]:
    """Esquema novo e migrado, com um projeto e um dataset. Devolve (project_id, dataset_id)."""
    c = await asyncpg.connect(cfg.postgres_dsn, timeout=10)
    try:
        await c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
        await migrate.up(c)
        p = await repo.create_project(c, "p")
        return p.id, (await repo.create_dataset(c, p.id, "d")).id
    finally:
        await c.close()


class World:
    def __init__(self, cfg: Settings) -> None:
        self.cfg = cfg
        self.project_id, self.dataset_id = _run(_fresh(cfg))
        self.store = MemStore()

    def app(self) -> Any:
        async def ok() -> None:
            return None

        return create_app(self.cfg, checks={"postgres": ok, "storage": ok}, object_store=self.store)

    @property
    def url(self) -> str:
        return f"/datasets/{self.dataset_id}"

    def upload(self, c: TestClient) -> None:
        files = [("files", (n, jpeg(w, h), "image/jpeg")) for n, (w, h) in SIZES.items()]
        r = c.post(f"{self.url}/images", files=files)
        assert r.status_code == 201, r.text

    def count(self, table: str) -> int:
        return int(_run(_sql(self.cfg, f"SELECT count(*) AS n FROM {table}"))[0]["n"])


def coco(*, bad_image: bool = False) -> bytes:
    images = [{"id": i, "file_name": n, "width": w, "height": h}
              for i, (n, (w, h)) in enumerate(SIZES.items(), 1)]  # fmt: skip
    if bad_image:
        images[1] = {**images[1], "file_name": "inexistente.jpg"}
    boxes = [
        (1, 10, [20, 10, 40, 30]),
        (1, 11, [0, 0, 200, 100]),
        (2, 10, [10, 10, 30, 30]),
        (3, 11, [5, 5, 10, 10]),
        (3, 10, [-4, 0, 20, 20]),  # ultrapassa a borda: aviso, gravada sem alteração
    ]
    anns = [{"id": i, "image_id": im, "category_id": cat, "bbox": b}
            for i, (im, cat, b) in enumerate(boxes, 1)]  # fmt: skip
    cats = [{"id": 10, "name": "gato"}, {"id": 11, "name": "cão"}]
    return json.dumps({"images": images, "categories": cats, "annotations": anns}).encode()


@pytest.fixture
def world(cfg: Settings) -> World:
    return World(cfg)


@pytest.fixture
def client(world: World) -> Iterator[TestClient]:
    with TestClient(world.app()) as c:
        world.upload(c)
        yield c


def _import(c: TestClient, w: World, data: bytes, **params: str) -> Any:
    return c.post(
        f"{w.url}/imports/coco",
        content=data,
        headers={"Content-Type": "application/json"},
        params=params,
    )


# 37 (a)
def test_import_creates_annotations_and_classes(client: TestClient, world: World) -> None:
    r = _import(client, world, coco())
    assert r.status_code == 201, r.text
    j = r.json()
    assert j["status"] == "importado"
    assert j["imagens"] == {"declaradas": 3, "casadas": 3}
    assert j["classes"]["criadas"] == 2 and j["anotacoes"]["importadas"] == 5
    assert [w["code"] for w in j["avisos"]] == ["caixa_fora_da_imagem"]
    assert world.count("annotation") == 5 and world.count("label_class") == 2
    rows = _run(
        _sql(
            world.cfg,
            "SELECT x_center, y_center, width, height FROM annotation a JOIN image i ON i.id ="
            " a.image_id WHERE i.file_name = 'a.jpg' ORDER BY a.id",
        )
    )
    got = [tuple(r.values()) for r in rows]
    assert got[0] == pytest.approx((0.2, 0.25, 0.2, 0.3), abs=1e-9)
    assert got[1] == (0.5, 0.5, 1.0, 1.0)


# 37 (b)
def test_reimport_changes_nothing(client: TestClient, world: World) -> None:
    assert _import(client, world, coco()).status_code == 201
    before = _run(_sql(world.cfg, "SELECT id FROM annotation ORDER BY id"))
    r = _import(client, world, coco())
    assert r.status_code == 200 and r.json()["status"] == "sem_alteracoes"
    assert r.json()["anotacoes"]["ja_existentes"] == 5 and r.json()["classes"]["criadas"] == 0
    assert _run(_sql(world.cfg, "SELECT id FROM annotation ORDER BY id")) == before
    assert world.count("label_class") == 2


def test_dry_run_writes_nothing(client: TestClient, world: World) -> None:
    r = _import(client, world, coco(), dry_run="true")
    assert r.status_code == 200 and r.json()["status"] == "simulado"
    assert r.json()["anotacoes"]["importadas"] == 5 and r.json()["classes"]["criadas"] == 2
    assert world.count("annotation") == 0 and world.count("label_class") == 0


# 37 (c)
def test_error_in_the_middle_leaves_the_database_intact(client: TestClient, world: World) -> None:
    r = _import(client, world, coco(bad_image=True))
    assert r.status_code == 422, r.text
    assert [e["code"] for e in r.json()["erros"]] == ["imagem_nao_encontrada"]
    assert world.count("annotation") == 0 and world.count("label_class") == 0


# 37 (d)
def test_forced_failure_in_phase_2_rolls_back_created_classes(
    client: TestClient, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Poisoned(ConnCocoRepository):
        """Injeta, no último lote, uma caixa que viola o CHECK do banco (largura zero)."""

        async def insert_annotation_batch(self, project_id: int, rows: Any) -> int:
            bad = (rows[0][0], repo.NewAnnotation(rows[0][1].class_id, 0.5, 0.5, 0.0, 0.5))
            return await super().insert_annotation_batch(project_id, [*rows, bad])

    monkeypatch.setattr(imports, "ConnCocoRepository", Poisoned)
    r = _import(client, world, coco())
    assert r.status_code == 409 and r.json()["code"] == "conflito_integridade", r.text
    assert world.count("label_class") == 0 and world.count("annotation") == 0
    monkeypatch.undo()
    assert (
        _import(client, world, coco()).status_code == 201
    )  # o estado limpo permite tentar de novo


def test_class_removed_by_another_connection_before_commit_rolls_back(
    client: TestClient, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Classe pré-existente apagada por outra conexão entre a leitura e a gravação: a FK recusa.
    insert = "INSERT INTO label_class (project_id, name, position) VALUES ($1, 'gato', 0)"
    _run(_sql(world.cfg, insert, world.project_id))

    class Racy(ConnCocoRepository):
        async def create_class(self, project_id: int, name: str, position: int) -> Any:
            made = await super().create_class(project_id, name, position)
            await _sql(world.cfg, "DELETE FROM label_class WHERE name = 'gato'")
            return made

    monkeypatch.setattr(imports, "ConnCocoRepository", Racy)
    r = _import(client, world, coco())
    assert r.status_code == 409, r.text
    assert world.count("annotation") == 0
    assert world.count("label_class") == 0  # 'gato' removida de fora; 'cão' desfeita


# 37 (e)
def test_concurrent_imports_do_not_duplicate_classes_or_annotations(
    client: TestClient, world: World
) -> None:
    data = coco()
    barrier = threading.Barrier(2)
    out: list[Any] = []

    def worker() -> None:
        with TestClient(world.app()) as c:  # app própria: outro loop, outro pool, outra conexão
            barrier.wait()
            out.append(_import(c, world, data))

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert sorted(r.status_code for r in out) == [200, 201], [r.text for r in out]
    assert world.count("label_class") == 2 and world.count("annotation") == 5


def test_concurrent_imports_through_two_pool_connections(world: World) -> None:
    async def body() -> None:
        pool = await asyncpg.create_pool(world.cfg.postgres_dsn, min_size=2, max_size=2)
        try:
            async with pool.acquire() as c:
                for n, (w, h) in SIZES.items():
                    await repo.insert_image(
                        c, world.dataset_id, object_key=f"k/{n}", file_name=n,
                        sha256=secrets.token_hex(32), size_bytes=1, width_px=w, height_px=h,
                    )  # fmt: skip
            parsed = parse_coco(coco(), CocoLimits())

            async def one() -> Any:
                async with pool.acquire() as conn:
                    return await import_coco(ConnCocoRepository(conn), world.dataset_id, parsed)

            reports = await asyncio.gather(one(), one(), one())
        finally:
            await pool.close()
        assert sorted(r.status for r in reports) == [
            "importado",
            "sem_alteracoes",
            "sem_alteracoes",
        ]
        assert sum(r.annotations_imported for r in reports) == 5
        assert sum(r.classes_created for r in reports) == 2

    _run(body())
    assert world.count("label_class") == 2 and world.count("annotation") == 5


# 37 (f)
def test_deleting_the_project_cascades_to_imported_annotations(
    client: TestClient, world: World
) -> None:
    assert _import(client, world, coco()).status_code == 201

    async def drop() -> None:
        c = await asyncpg.connect(world.cfg.postgres_dsn, timeout=10)
        try:
            await repo.delete_project(c, world.project_id)
        finally:
            await c.close()

    _run(drop())
    for table in ("annotation", "label_class", "image", "dataset", "project"):
        assert world.count(table) == 0, table
