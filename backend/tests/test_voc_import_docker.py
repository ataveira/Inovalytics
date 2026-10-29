"""F1-08d critério 34: importação Pascal VOC real (API + asyncpg) contra Postgres migrado.

Rodam com `make compose-smoke`. Reaproveitam a infraestrutura de `test_coco_import_docker` (só o
serviço `db` do Compose em projeto isolado, senha aleatória e porta fixa em 127.0.0.1 durante o
módulo; ou, sem Docker, `INOVALYTICS_DISPOSABLE_PG_DSN` apontando um Postgres DESCARTÁVEL: o
esquema `public` é APAGADO a cada teste). As imagens entram pela rota da F1-05 com um `ObjectStore`
em memória. O app roda no processo do teste (como nas F1-06/07); o arquivo sentinela e o servidor
de teste que falha se contatado provam que o XXE não lê arquivo nem abre conexão.
"""

import json
import threading
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import test_coco_import_docker as base
from inovalytics.api import imports
from inovalytics.api.imports import ConnCocoRepository
from inovalytics.db import repository as repo
from test_api_voc_xxe import LEAK_MARK, TripwireServer
from test_voc_parser import ann, bnd, obj, size

pytestmark = pytest.mark.docker

cfg = base.cfg
world = base.world
client = base.client
World = base.World
_run = base._run
_sql = base._sql

FILES = {
    "train/a.xml": ann(  # imagem a.jpg 200x100
        obj("gato"), obj("cão", box=bnd(1, 1, 200, 100)), filename="a.jpg", size=size(200, 100)
    ),
    "b.xml": ann(obj("gato", box=bnd(1, 1, 50, 50)), size=size(100, 100)),
    "c.xml": ann(),  # imagem de fundo: nenhuma caixa
}


def payload(files: dict[str, str] | None = None) -> bytes:
    return json.dumps({"annotations": FILES if files is None else files}).encode()


def _import(c: TestClient, w: Any, data: bytes, **params: str) -> Any:
    return c.post(
        f"{w.url}/imports/pascal-voc",
        content=data,
        headers={"Content-Type": "application/json"},
        params=params,
    )


def test_import_creates_annotations_and_classes(client: TestClient, world: Any) -> None:
    r = _import(client, world, payload())
    assert r.status_code == 201, r.text
    j = r.json()
    assert j["status"] == "importado" and j["imagens"] == {"declaradas": 3, "casadas": 3}
    assert j["classes"]["criadas"] == 2 and j["anotacoes"]["importadas"] == 3
    assert j["imagens_sem_caixas"] == 1 and j["sem_size"] == 1
    assert world.count("annotation") == 3 and world.count("label_class") == 2
    rows = _run(
        _sql(
            world.cfg,
            "SELECT x_center, y_center, width, height FROM annotation a JOIN image i ON i.id ="
            " a.image_id WHERE i.file_name = 'a.jpg' ORDER BY a.id",
        )
    )
    got = [tuple(r.values()) for r in rows]
    assert got[0] == pytest.approx((0.5, 0.5, 0.5, 0.5), abs=1e-6)
    assert got[1] == pytest.approx((0.5, 0.5, 1.0, 1.0), abs=1e-6)


def test_reimport_changes_nothing(client: TestClient, world: Any) -> None:
    assert _import(client, world, payload()).status_code == 201
    before = _run(_sql(world.cfg, "SELECT id FROM annotation ORDER BY id"))
    r = _import(client, world, payload())
    assert r.status_code == 200 and r.json()["status"] == "sem_alteracoes"
    assert r.json()["anotacoes"]["ja_existentes"] == 3 and r.json()["classes"]["criadas"] == 0
    assert _run(_sql(world.cfg, "SELECT id FROM annotation ORDER BY id")) == before
    assert world.count("label_class") == 2


def test_dry_run_writes_nothing(client: TestClient, world: Any) -> None:
    r = _import(client, world, payload(), dry_run="true")
    assert r.status_code == 200 and r.json()["status"] == "simulado"
    assert r.json()["anotacoes"]["importadas"] == 3
    assert world.count("annotation") == 0 and world.count("label_class") == 0


def test_xxe_in_the_middle_of_the_batch_leaves_the_database_intact(
    client: TestClient, world: Any, tmp_path: Path
) -> None:
    sentinel = tmp_path / "fora-do-alcance.txt"
    sentinel.write_text(LEAK_MARK, encoding="utf-8")
    trip = TripwireServer()
    try:
        evil = (
            f'<!DOCTYPE annotation [<!ENTITY x SYSTEM "{sentinel.as_uri()}">'
            f'<!ENTITY y SYSTEM "http://127.0.0.1:{trip.port}/y">]>'
            "<annotation><object><name>&x;&y;</name></object></annotation>"
        )
        files = {"a.xml": FILES["train/a.xml"], "b.xml": evil, "c.xml": FILES["c.xml"]}
        r = _import(client, world, payload(files))
    finally:
        trip.close()
    assert r.status_code == 422, r.text
    erros = r.json()["erros"]
    assert [(e["code"], e["arquivo"]) for e in erros] == [("doctype_proibido", "b.xml")]
    assert erros[0]["linha"] == 1
    assert trip.hits == 0 and LEAK_MARK not in r.text
    assert world.count("annotation") == 0 and world.count("label_class") == 0


def test_error_in_the_middle_leaves_the_database_intact(client: TestClient, world: Any) -> None:
    files = {**FILES, "inexistente.xml": ann(obj())}
    r = _import(client, world, payload(files))
    assert r.status_code == 422, r.text
    assert [e["code"] for e in r.json()["erros"]] == ["imagem_nao_encontrada"]
    assert world.count("annotation") == 0 and world.count("label_class") == 0


def test_forced_failure_in_phase_2_rolls_back_created_classes(
    client: TestClient, world: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Poisoned(ConnCocoRepository):
        """Injeta, no último lote, uma caixa que viola o CHECK do banco (largura zero)."""

        async def insert_annotation_batch(self, project_id: int, rows: Any) -> int:
            bad = (rows[0][0], repo.NewAnnotation(rows[0][1].class_id, 0.5, 0.5, 0.0, 0.5))
            return await super().insert_annotation_batch(project_id, [*rows, bad])

    monkeypatch.setattr(imports, "ConnCocoRepository", Poisoned)
    r = _import(client, world, payload())
    assert r.status_code == 409 and r.json()["code"] == "conflito_integridade", r.text
    assert world.count("label_class") == 0 and world.count("annotation") == 0
    monkeypatch.undo()
    assert _import(client, world, payload()).status_code == 201


def test_concurrent_imports_do_not_duplicate_classes_or_annotations(
    client: TestClient, world: Any
) -> None:
    data = payload()
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
    assert world.count("label_class") == 2 and world.count("annotation") == 3


def test_deleting_the_project_cascades_to_imported_annotations(
    client: TestClient, world: Any
) -> None:
    assert _import(client, world, payload()).status_code == 201

    async def drop() -> None:
        c = await base.asyncpg.connect(world.cfg.postgres_dsn, timeout=10)
        try:
            await repo.delete_project(c, world.project_id)
        finally:
            await c.close()

    _run(drop())
    for table in ("annotation", "label_class", "image", "dataset", "project"):
        assert world.count(table) == 0, table

