"""F1-07d critério 33: importação de rótulos txt real (API + asyncpg) contra Postgres migrado.

Rodam com `make compose-smoke`. Reaproveitam a infraestrutura de `test_coco_import_docker` (só o
serviço `db` do Compose em projeto isolado, senha aleatória e porta FIXA em 127.0.0.1; ou, sem
Docker, `INOVALYTICS_DISPOSABLE_PG_DSN` apontando um Postgres DESCARTÁVEL: o esquema `public` é
APAGADO a cada teste). As imagens entram pela rota da F1-05 com um `ObjectStore` em memória.
"""

import json
import threading
from typing import Any

import pytest
from fastapi.testclient import TestClient

import test_coco_import_docker as base
from inovalytics.api import imports
from inovalytics.api.imports import ConnCocoRepository
from inovalytics.db import repository as repo

pytestmark = pytest.mark.docker

# Mesmas fixtures (escopo de módulo preservado): sobem/derrubam o banco para este módulo.
cfg = base.cfg
world = base.world
client = base.client
World = base.World
_run = base._run
_sql = base._sql

NAMES = ["gato", "cão"]
LABELS = {
    "train/labels/a.txt": "0 0.2 0.25 0.2 0.3\n1 0.5 0.5 1 1\n",
    "b.txt": "0 0.25 0.25 0.3 0.3\n",
    "c.txt": "",  # imagem de fundo: nenhuma caixa
}


def payload(labels: dict[str, str] | None = None) -> bytes:
    return json.dumps({"names": NAMES, "labels": LABELS if labels is None else labels}).encode()


def _import(c: TestClient, w: Any, data: bytes, **params: str) -> Any:
    return c.post(
        f"{w.url}/imports/yolo-txt",
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
    assert j["imagens_sem_caixas"] == 1
    assert world.count("annotation") == 3 and world.count("label_class") == 2
    rows = _run(
        _sql(
            world.cfg,
            "SELECT x_center, y_center, width, height FROM annotation a JOIN image i ON i.id ="
            " a.image_id WHERE i.file_name = 'a.jpg' ORDER BY a.id",
        )
    )
    got = [tuple(r.values()) for r in rows]
    assert got[0] == pytest.approx((0.2, 0.25, 0.2, 0.3), abs=1e-6)
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


@pytest.mark.parametrize(
    ("labels", "code"),
    [
        ({"a.txt": "0 .5 .5 .1 .1\n9 .5 .5 .1 .1\n", "b.txt": "0 .5 .5 .1 .1\n"},
         "classe_fora_do_intervalo"),
        ({"a.txt": "0 .5 .5 .1 .1\n", "inexistente.txt": "0 .5 .5 .1 .1\n"},
         "imagem_nao_encontrada"),
    ],
)  # fmt: skip
def test_error_in_the_middle_leaves_the_database_intact(
    client: TestClient, world: Any, labels: dict[str, str], code: str
) -> None:
    r = _import(client, world, payload(labels))
    assert r.status_code == 422, r.text
    assert [e["code"] for e in r.json()["erros"]] == [code]
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
