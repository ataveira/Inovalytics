"""F1-06c critérios 30 a 36: rota de importação COCO (TestClient, fakes, sem rede e sem Docker)."""

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from typing import Any

import asyncpg  # type: ignore[import-untyped]
import pytest
from fastapi.testclient import TestClient

from inovalytics.api import imports
from inovalytics.api.config import Settings
from inovalytics.api.images import RequestBodyLimitMiddleware
from inovalytics.api.main import create_app
from inovalytics.db.repository import ImportImage, LockTimeoutError
from test_coco_service import FakeRepo

pytestmark = pytest.mark.usefixtures("no_external_network")

URL = "/datasets/1/imports/coco"
JSON_H = {"Content-Type": "application/json"}
SECRETS = ["S3SECRET-qq", "S3ACCESS-qq", "PGPASS-qq", "postgresql://u:PGPASS-qq@dbhost/db"]
JSON_SENTINEL = "TEXTO-DO-JSON-zz"


def doc(**over: Any) -> dict[str, Any]:
    d: dict[str, Any] = {
        "images": [{"id": 1, "file_name": "a.jpg", "width": 200, "height": 100}],
        "categories": [{"id": 3, "name": "gato"}],
        "annotations": [{"id": 1, "image_id": 1, "category_id": 3, "bbox": [20, 10, 40, 30]}],
    }
    d.update(over)
    return d


def body(**over: Any) -> bytes:
    return json.dumps(doc(**over)).encode()


class FakeProvider:
    def __init__(self) -> None:
        self.fail: Exception | None = None
        self.hang = False
        self.acquired = 0
        self.closed = 0

    @asynccontextmanager
    async def acquire(self) -> Any:
        self.acquired += 1
        if self.hang:
            await asyncio.sleep(30)
        if self.fail is not None:
            raise self.fail
        yield "conn"

    async def close(self) -> None:
        self.closed += 1


class Env:
    def __init__(self, monkeypatch: pytest.MonkeyPatch, **cfg: Any) -> None:
        self.repo = FakeRepo()
        self.provider = FakeProvider()
        monkeypatch.setattr(imports, "ConnCocoRepository", lambda conn: self.repo)
        self.settings = Settings(
            postgres_password=SECRETS[2], s3_access_key=SECRETS[1], s3_secret_key=SECRETS[0], **cfg
        )

    def app(self) -> Any:
        async def ok() -> None:
            return None

        return create_app(
            self.settings,
            checks={"postgres": ok, "storage": ok},
            db_provider=self.provider,
            object_store=None,  # a rota não recebe ObjectStore; o upload não é exercido aqui
        )

    def client(self) -> TestClient:
        return TestClient(self.app())


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> Env:
    return Env(monkeypatch)


def post(c: TestClient, data: bytes | None = None, url: str = URL, **kw: Any) -> Any:
    headers = kw.pop("headers", JSON_H)
    return c.post(url, content=body() if data is None else data, headers=headers, **kw)


# 30
def test_create_report_repeat_and_dry_run(env: Env) -> None:
    with env.client() as c:
        r = post(c)
        assert r.status_code == 201
        assert r.json() == {
            "status": "importado",
            "dataset_id": 1,
            "imagens": {"declaradas": 1, "casadas": 1},
            "classes": {"declaradas": 1, "criadas": 1, "existentes": 0},
            "anotacoes": {
                "no_arquivo": 1,
                "importadas": 1,
                "ja_existentes": 0,
                "ignoradas_crowd": 0,
            },
            "segmentacoes_ignoradas": 0,
            "avisos": [],
            "avisos_total": 0,
            "avisos_truncados": False,
        }
        r = post(c)
        assert r.status_code == 200 and r.json()["status"] == "sem_alteracoes"
        assert r.json()["anotacoes"]["ja_existentes"] == 1
        assert len(env.repo.annotations) == 1


def test_dry_run_is_200_and_writes_nothing(env: Env) -> None:
    with env.client() as c:
        r = post(c, params={"dry_run": "true"})
    assert r.status_code == 200 and r.json()["status"] == "simulado"
    assert r.json()["classes"]["criadas"] == 1 and r.json()["anotacoes"]["importadas"] == 1
    assert (
        env.repo.writes() == [] and "BEGIN" not in env.repo.calls and "lock" not in env.repo.calls
    )


def test_create_classes_false_reports_missing_class(env: Env) -> None:
    with env.client() as c:
        r = post(c, params={"create_classes": "false"})
    assert r.status_code == 422
    assert [e["code"] for e in r.json()["erros"]] == ["classe_ausente"]
    assert env.repo.writes() == []


@pytest.mark.parametrize(
    "params", [{"dry_run": "banana"}, {"create_classes": "talvez"}, {"dry_run": ["true", "false"]}]
)
def test_invalid_query_is_422(env: Env, params: dict[str, Any]) -> None:
    with env.client() as c:
        r = post(c, params=params)
    assert r.status_code == 422 and r.json()["code"] == "query_invalida"
    assert env.provider.acquired == 0


@pytest.mark.parametrize("v", ["TRUE", "1", "False", "0"])
def test_boolean_spellings(env: Env, v: str) -> None:
    with env.client() as c:
        assert post(c, params={"dry_run": v}).status_code in (200, 201)


def test_warnings_and_crowd_in_report(env: Env) -> None:
    d = doc(
        annotations=[
            {"id": 1, "image_id": 1, "category_id": 3, "bbox": [-10, 0, 40, 30]},
            {"id": 2, "image_id": 1, "category_id": 3, "bbox": [0, 0, 5, 5], "iscrowd": 1},
            {"id": 3, "image_id": 1, "category_id": 3, "bbox": [1, 1, 5, 5], "segmentation": [[1]]},
        ]
    )
    with env.client() as c:
        r = post(c, json.dumps(d).encode())
    j = r.json()
    assert r.status_code == 201
    assert j["anotacoes"]["ignoradas_crowd"] == 1 and j["segmentacoes_ignoradas"] == 1
    assert [w["code"] for w in j["avisos"]] == ["caixa_fora_da_imagem"]
    assert set(j["avisos"][0]) == {"code", "kind", "id", "index", "message"}
    assert j["avisos_total"] == 1 and j["avisos_truncados"] is False


# 31
def test_unknown_dataset_is_404_before_reading_the_body(env: Env) -> None:
    env.repo.dataset_exists = False
    with env.client() as c:
        r = post(c)
    assert r.status_code == 404 and r.json()["code"] == "dataset_nao_encontrado"
    assert env.repo.calls == ["get_dataset"]


@pytest.mark.parametrize("ds", ["abc", "0", "-1", "9223372036854775808", "01", "1.5"])
def test_invalid_dataset_id_is_422(env: Env, ds: str) -> None:
    with env.client() as c:
        r = post(c, url=f"/datasets/{ds}/imports/coco")
    assert r.status_code == 422 and r.json()["code"] == "dataset_id_invalido"
    assert env.provider.acquired == 0


def test_max_bigint_dataset_id_is_accepted(env: Env) -> None:
    env.repo.dataset_exists = False
    with env.client() as c:
        r = post(c, url="/datasets/9223372036854775807/imports/coco")
    assert r.status_code == 404


# 32
@pytest.mark.parametrize(
    "ctype",
    ["application/zip", "multipart/form-data; boundary=x", "text/plain", None,
     "application/json; charset=utf-16", "application/jsonx", "application/json; charset=latin-1"],
)  # fmt: skip
def test_unsupported_content_type_is_415(env: Env, ctype: str | None) -> None:
    headers = {} if ctype is None else {"Content-Type": ctype}
    with env.client() as c:
        r = c.post(URL, content=body(), headers=headers)
    assert r.status_code == 415 and r.json()["code"] == "tipo_nao_suportado"
    assert env.provider.acquired == 0


@pytest.mark.parametrize(
    "ctype",
    ["application/json; charset=utf-8", "Application/JSON", 'application/json; charset="UTF-8"'],
)
def test_json_content_type_variants_are_accepted(env: Env, ctype: str) -> None:
    with env.client() as c:
        assert c.post(URL, content=body(), headers={"Content-Type": ctype}).status_code == 201


# 33
def test_content_length_over_limit_is_413_without_reading(monkeypatch: pytest.MonkeyPatch) -> None:
    e = Env(monkeypatch, import_max_json_bytes=100)
    with e.client() as c:
        r = post(c, b"x" * 101)
        assert r.status_code == 413 and r.json()["code"] == "requisicao_grande_demais"
        assert e.provider.acquired == 0 and e.repo.calls == []
        ok = post(c, b"x" * 100)  # no limite exato passa pelo middleware (e falha no parse)
        assert ok.status_code == 422 and ok.json()["erros"][0]["code"] == "json_invalido"


def test_chunked_body_over_limit_is_413(monkeypatch: pytest.MonkeyPatch) -> None:
    e = Env(monkeypatch, import_max_json_bytes=1000)

    def gen() -> Any:
        for _ in range(100):
            yield b"x" * 100

    with e.client() as c:
        r = c.post(URL, content=gen(), headers=JSON_H)
    assert r.status_code == 413
    assert "content-length" not in r.request.headers  # corpo em fluxo (chunked)
    assert r.request.headers.get("transfer-encoding") == "chunked"
    assert e.repo.writes() == []


def asgi_post(app: Any, chunks: list[bytes], headers: list[tuple[bytes, bytes]]) -> dict[str, Any]:
    out: dict[str, Any] = {"status": None, "pulled": 0, "body": b""}
    queue = list(chunks)

    async def receive() -> dict[str, Any]:
        if queue:
            out["pulled"] += 1
            return {"type": "http.request", "body": queue.pop(0), "more_body": bool(queue)}
        return {"type": "http.disconnect"}

    async def send(m: dict[str, Any]) -> None:
        if m["type"] == "http.response.start":
            out["status"] = m["status"]
        elif m["type"] == "http.response.body":
            out["body"] += m.get("body", b"")

    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST",
        "path": URL, "raw_path": URL.encode(), "query_string": b"", "root_path": "",
        "scheme": "http", "server": ("t", 80), "client": ("c", 1), "headers": headers,
        "state": {},
    }  # fmt: skip
    asyncio.run(app(scope, receive, send))
    return out


def test_streaming_aborts_before_accumulating_the_whole_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    e = Env(monkeypatch, import_max_json_bytes=1000)
    app = e.app()
    chunks = [b"x" * 100 for _ in range(500)]
    out = asgi_post(app, chunks, [(b"content-type", b"application/json")])
    assert out["status"] == 413
    assert out["pulled"] <= 12  # 10 blocos cabem; o 11º estoura; nada além disso é lido
    assert json.loads(out["body"])["code"] == "requisicao_grande_demais"
    assert e.repo.writes() == []


def test_lying_small_content_length_does_not_bypass_the_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    e = Env(monkeypatch, import_max_json_bytes=1000)
    out = asgi_post(
        e.app(),
        [b"x" * 600, b"y" * 600],
        [(b"content-type", b"application/json"), (b"content-length", b"10")],
    )
    assert out["status"] == 413 and e.repo.writes() == []


@pytest.mark.parametrize("value", [b"\xb2", b"\xb23", b"\xd9\xa3"])
def test_unicode_digit_content_length_is_4xx_not_500(
    monkeypatch: pytest.MonkeyPatch, value: bytes
) -> None:
    e = Env(monkeypatch, import_max_json_bytes=1000)
    out = asgi_post(
        e.app(), [b"{}"], [(b"content-type", b"application/json"), (b"content-length", value)]
    )
    assert 400 <= out["status"] < 500 and e.repo.writes() == []


def test_limit_is_per_route(monkeypatch: pytest.MonkeyPatch) -> None:
    # A rota de importação tem o limite próprio; o de upload (>= 20 MiB) não a afeta e vice-versa.
    e = Env(monkeypatch, import_max_json_bytes=50, upload_max_bytes=10, upload_max_files=1)
    with e.client() as c:
        assert post(c, b"x" * 51).status_code == 413
        r = c.post("/datasets/1/images", content=b"z" * 60_000, headers={"Content-Type": "x/y"})
        assert r.status_code != 413  # limite de upload = 1 * 10 + 64 KiB
        big = c.post("/datasets/1/images", content=b"z" * 70_000, headers={"Content-Type": "x/y"})
        assert big.status_code == 413


def test_middleware_custom_path_only_guards_that_path() -> None:
    import re

    seen: list[str] = []

    async def inner(scope: Any, receive: Any, send: Any) -> None:
        seen.append(scope["path"])
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    statuses: list[int] = []

    async def send(m: Any) -> None:
        if m["type"] == "http.response.start":
            statuses.append(m["status"])

    async def receive() -> Any:
        return {"type": "http.request", "body": b"", "more_body": False}

    mw = RequestBodyLimitMiddleware(inner, 5, re.compile(r"^/x$"))
    for path in ("/x", "/datasets/1/images"):
        scope = {
            "type": "http", "method": "POST", "path": path,
            "headers": [(b"content-length", b"9")],
        }  # fmt: skip
        asyncio.run(mw(scope, receive, send))
    assert statuses == [413, 200] and seen == ["/datasets/1/images"]


# 34
def test_invalid_file_reports_every_error_and_writes_nothing(env: Env) -> None:
    d = doc(
        images=[{"id": 1, "file_name": "a.jpg", "width": 200, "height": 100},
                {"id": 2, "file_name": "faltando.jpg", "width": 10, "height": 10}],
        annotations=[{"id": 1, "image_id": 1, "category_id": 99, "bbox": [1, 1, 5, 5]}],
    )  # fmt: skip
    with env.client() as c:
        r = post(c, json.dumps(d).encode())
    j = r.json()
    assert r.status_code == 422 and j["code"] == "importacao_invalida"
    # defeitos de estrutura (fase do parser) param antes do casamento de imagens
    assert {e["code"] for e in j["erros"]} == {"anotacao_categoria_inexistente"}
    assert j["erros_total"] == len(j["erros"]) and j["erros_truncados"] is False
    assert all(set(e) == {"code", "kind", "id", "index", "message"} for e in j["erros"])
    assert env.repo.writes() == [] and "BEGIN" not in env.repo.calls


def test_missing_category_and_missing_image_are_both_reported(env: Env) -> None:
    d = doc(
        images=[{"id": 1, "file_name": "nao_existe.jpg", "width": 200, "height": 100}],
        categories=[{"id": 3, "name": "gato"}],
        annotations=[{"id": 1, "image_id": 1, "category_id": 3, "bbox": [1, 1, 5, 5]}],
    )
    env.repo.images = [ImportImage(10, "a.jpg", 200, 100)]
    with env.client() as c:
        r = post(c, json.dumps(d).encode(), params={"create_classes": "false"})
    codes = [e["code"] for e in r.json()["erros"]]
    assert sorted(codes) == ["classe_ausente", "imagem_nao_encontrada"]
    assert env.repo.writes() == []


def test_error_list_is_truncated_with_real_total(monkeypatch: pytest.MonkeyPatch) -> None:
    e = Env(monkeypatch, import_max_errors=3)
    images = [{"id": i, "file_name": f"n{i}.jpg", "width": 1, "height": 1} for i in range(10)]
    with e.client() as c:
        r = post(c, json.dumps(doc(images=images, annotations=[])).encode())
    j = r.json()
    assert len(j["erros"]) == 3 and j["erros_total"] == 10 and j["erros_truncados"] is True


@pytest.mark.parametrize("data", [b"", b"{", b"[]", b"\xff\xfe", b'{"images": NaN}'])
def test_unparseable_bodies_are_422_json_invalido(env: Env, data: bytes) -> None:
    with env.client() as c:
        r = post(c, data)
    assert r.status_code == 422 and r.json()["erros"][0]["code"] == "json_invalido"
    assert r.json()["code"] == "importacao_invalida"


def test_structure_and_limits_errors_are_422(monkeypatch: pytest.MonkeyPatch) -> None:
    e = Env(monkeypatch, import_max_images=1, import_max_depth=3)
    two = [{"id": i, "file_name": "a.jpg", "width": 1, "height": 1} for i in (1, 2)]
    with e.client() as c:
        assert post(c, b'{"images": []}').json()["erros"][0]["code"] == "estrutura_invalida"
        assert post(c, json.dumps(doc(images=two)).encode()).json()["erros"][0]["code"] == (
            "limite_excedido"
        )
        assert post(c, b"[" * 200).json()["erros"][0]["code"] in {
            "limite_excedido",
            "json_invalido",
        }


def test_user_data_in_messages_is_bounded(env: Env) -> None:
    name = "q" * 900
    d = doc(images=[{"id": 1, "file_name": name, "width": 200, "height": 100}], annotations=[])
    with env.client() as c:
        r = post(c, json.dumps(d).encode())
    assert r.status_code == 422
    for err in r.json()["erros"]:
        assert "q" * 201 not in err["message"]


# 35
def test_db_unavailable_is_503(env: Env, caplog: pytest.LogCaptureFixture) -> None:
    env.provider.fail = OSError(f"connect {SECRETS[3]} {SECRETS[2]}")
    with caplog.at_level(logging.DEBUG), env.client() as c:
        r = post(c, body(categories=[{"id": 3, "name": JSON_SENTINEL}]))
    assert r.status_code == 503 and r.json()["code"] == "banco_indisponivel"
    _no_leak(r.text, caplog)


def test_acquire_timeout_is_503(
    env: Env, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(imports, "DB_ACQUIRE_TIMEOUT", 0.05)
    env.provider.hang = True
    with caplog.at_level(logging.DEBUG), env.client() as c:
        r = post(c)
    assert r.status_code == 503 and r.json()["code"] == "banco_timeout_conexao"
    _no_leak(r.text, caplog)


def test_lock_timeout_is_503_importacao_em_andamento(
    env: Env, caplog: pytest.LogCaptureFixture
) -> None:
    async def boom(project_id: int) -> None:
        raise LockTimeoutError("tempo esgotado ao obter o bloqueio")

    env.repo.lock_project_for_import = boom  # type: ignore[method-assign]
    with caplog.at_level(logging.DEBUG), env.client() as c:
        r = post(c)
    assert r.status_code == 503 and r.json()["code"] == "importacao_em_andamento"
    assert env.repo.annotations == [] and env.repo.classes == []
    _no_leak(r.text, caplog)


@pytest.mark.parametrize(
    "exc",
    [asyncpg.UniqueViolationError(f"dup {SECRETS[3]} INSERT INTO annotation {JSON_SENTINEL}"),
     asyncpg.ForeignKeyViolationError(SECRETS[2])],
)  # fmt: skip
def test_integrity_violation_is_409(
    env: Env, exc: Exception, caplog: pytest.LogCaptureFixture
) -> None:
    async def boom(*a: Any) -> Any:
        raise exc

    env.repo.create_class = boom  # type: ignore[method-assign]
    with caplog.at_level(logging.DEBUG), env.client() as c:
        r = post(c, body(categories=[{"id": 3, "name": JSON_SENTINEL}]))
    assert r.status_code == 409 and r.json()["code"] == "conflito_integridade"
    _no_leak(r.text, caplog)
    assert env.repo.classes == []


def test_domain_error_in_phase_2_is_409_and_rolled_back(
    env: Env, caplog: pytest.LogCaptureFixture
) -> None:
    env.repo.fail_on_row = 1
    with caplog.at_level(logging.DEBUG), env.client() as c:
        r = post(c)
    assert r.status_code == 409 and r.json()["code"] == "conflito_integridade"
    assert env.repo.classes == [] and env.repo.annotations == []
    _no_leak(r.text, caplog)


def test_dataset_changed_during_import_is_409_dataset_alterado(
    env: Env, caplog: pytest.LogCaptureFixture
) -> None:
    real = env.repo.list_images_for_import

    async def racy(dataset_id: int, limit: int) -> list[ImportImage]:
        out = await real(dataset_id, limit)
        return [] if "lock" in env.repo.calls else out  # imagens mudaram após o plano

    env.repo.list_images_for_import = racy  # type: ignore[method-assign]
    with caplog.at_level(logging.DEBUG), env.client() as c:
        r = post(c)
    assert r.status_code == 409
    j = r.json()
    assert j["code"] == "dataset_alterado"
    assert [e["code"] for e in j["erros"]] == ["dataset_alterado"]
    assert env.repo.classes == [] and env.repo.annotations == []
    _no_leak(r.text, caplog)


def test_database_driver_error_and_unexpected_error(
    env: Env, caplog: pytest.LogCaptureFixture
) -> None:
    async def down(*a: Any) -> Any:
        raise asyncpg.PostgresConnectionError(SECRETS[3])

    async def odd(*a: Any) -> Any:
        raise RuntimeError(f"{SECRETS[0]} {JSON_SENTINEL}")

    with caplog.at_level(logging.DEBUG), env.client() as c:
        env.repo.list_classes = down  # type: ignore[method-assign]
        r1 = post(c, body(categories=[{"id": 3, "name": JSON_SENTINEL}]))
        env.repo.list_classes = odd  # type: ignore[method-assign]
        r2 = post(c, body(categories=[{"id": 3, "name": JSON_SENTINEL}]))
    assert (r1.status_code, r1.json()["code"]) == (503, "banco_indisponivel")
    assert (r2.status_code, r2.json()["code"]) == (500, "erro_interno")
    _no_leak(r1.text + r2.text, caplog)
    assert any("RuntimeError" in rec.getMessage() for rec in caplog.records)


def _no_leak(text: str, caplog: pytest.LogCaptureFixture) -> None:
    blob = text + "\n" + caplog.text + "\n".join(str(r.exc_info) for r in caplog.records)
    for s in [*SECRETS, JSON_SENTINEL, "dbhost", "INSERT INTO", "9333"]:
        assert s not in blob, s


def test_422_does_not_log_the_uploaded_json(env: Env, caplog: pytest.LogCaptureFixture) -> None:
    d = doc(annotations=[{"id": 1, "image_id": 1, "category_id": 99, "bbox": [0, 0, 1, 1]}])
    d["info"] = {"note": JSON_SENTINEL}
    with caplog.at_level(logging.DEBUG), env.client() as c:
        r = post(c, json.dumps(d).encode())
    assert r.status_code == 422 and JSON_SENTINEL not in caplog.text


# 36
def test_route_set_and_no_store_in_registration() -> None:
    import inspect

    assert list(inspect.signature(imports.register_import).parameters) == [
        "app",
        "settings",
        "provider",
    ]
    app = create_app(Settings(postgres_password="x"), checks={})
    paths = {getattr(r, "path", "") for r in app.routes}
    assert "/datasets/{dataset_id}/imports/coco" in paths


def test_get_on_the_route_is_405(env: Env) -> None:
    with env.client() as c:
        assert c.get(URL).status_code == 405


def test_lifespan_still_closes_provider_once(env: Env) -> None:
    with env.client() as c:
        post(c)
    assert env.provider.closed == 1
