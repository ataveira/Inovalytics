"""F1-08d critérios 29 a 33: rota de importação Pascal VOC (TestClient, fakes, sem rede)."""

import json
import logging
import threading
from typing import Any

import asyncpg  # type: ignore[import-untyped]
import pytest
from fastapi.testclient import TestClient

from inovalytics.api import imports
from inovalytics.db.repository import ImportImage, LockTimeoutError
from test_api_coco_import import SECRETS, Env
from test_api_txt_import import asgi_post
from test_voc_parser import ann, bnd, obj, size

pytestmark = pytest.mark.usefixtures("no_external_network")

URL = "/datasets/1/imports/pascal-voc"
JSON_H = {"Content-Type": "application/json"}
SENTINEL = "TEXTO-DO-XML-zz"


def files(**over: str) -> dict[str, str]:
    d = {"a.xml": ann(obj("pessoa"), obj("capacete", box=bnd(1, 1, 200, 100)), size=size())}
    d.update(over)
    return d


def body(f: dict[str, str] | None = None, **extra: Any) -> bytes:
    return json.dumps({"annotations": files() if f is None else f, **extra}).encode()


def post(c: TestClient, data: bytes | None = None, url: str = URL, **kw: Any) -> Any:
    return c.post(url, content=body() if data is None else data, headers=JSON_H, **kw)


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> Env:
    return Env(monkeypatch)


def _no_leak(text: str, caplog: pytest.LogCaptureFixture) -> None:
    blob = text + "\n" + caplog.text + "\n".join(str(r.exc_info) for r in caplog.records)
    for s in [*SECRETS, SENTINEL, "dbhost", "INSERT INTO", "9333"]:
        assert s not in blob, s


# 29
def test_create_report_repeat_and_dry_run(env: Env) -> None:
    with env.client() as c:
        r = post(c)
        assert r.status_code == 201
        assert r.json() == {
            "status": "importado",
            "dataset_id": 1,
            "imagens": {"declaradas": 1, "casadas": 1},
            "classes": {"declaradas": 2, "criadas": 2, "existentes": 0},
            "anotacoes": {"no_arquivo": 2, "importadas": 2, "ja_existentes": 0},
            "imagens_sem_caixas": 0,
            "ignorados": {"difficult": 0, "truncated": 0, "occluded": 0, "pose": 0, "partes": 0},
            "sem_size": 0,
            "ajustes_origem_zero": 0,
            "avisos": [],
            "avisos_total": 0,
            "avisos_truncados": False,
        }
        stored = [(a.x_center, a.y_center, a.width, a.height) for a in env.repo.annotations]
        assert stored == [pytest.approx((0.5, 0.5, 0.5, 0.5)), pytest.approx((0.5, 0.5, 1, 1))]
        r = post(c)
        assert r.status_code == 200 and r.json()["status"] == "sem_alteracoes"
        assert r.json()["anotacoes"]["ja_existentes"] == 2
        assert len(env.repo.annotations) == 2


def test_report_counts_ignored_fields_and_adjustments(env: Env) -> None:
    extra = "<difficult>1</difficult><truncated>1</truncated><occluded>1</occluded><pose>L</pose>"
    f = {"a.xml": ann(obj("pessoa", extra=extra), obj("pessoa", box=bnd(0, 26, 150, 75)))}
    with env.client() as c:
        j = post(c, body(f)).json()
    assert j["ignorados"] == {"difficult": 1, "truncated": 1, "occluded": 1, "pose": 1, "partes": 0}
    assert j["sem_size"] == 1 and j["ajustes_origem_zero"] == 1


def test_warning_carries_file_and_line(env: Env) -> None:
    f = {"a.xml": ann(obj("pessoa", box=bnd(51, 26, 201, 75)))}
    with env.client() as c:
        j = post(c, body(f)).json()
    assert [w["code"] for w in j["avisos"]] == ["caixa_fora_da_imagem"]
    assert j["avisos"][0]["arquivo"] == "a.xml" and j["avisos"][0]["linha"] == 1
    assert j["avisos_total"] == 1


def test_empty_annotations_object_is_a_valid_noop(env: Env) -> None:
    with env.client() as c:
        r = post(c, body({}))
    assert r.status_code == 200 and r.json()["status"] == "sem_alteracoes"


def test_dry_run_is_200_and_writes_nothing(env: Env) -> None:
    with env.client() as c:
        r = post(c, params={"dry_run": "true"})
    assert r.status_code == 200 and r.json()["status"] == "simulado"
    assert r.json()["classes"]["criadas"] == 2 and r.json()["anotacoes"]["importadas"] == 2
    assert env.repo.writes() == [] and "BEGIN" not in env.repo.calls


def test_create_classes_false_reports_missing_class(env: Env) -> None:
    with env.client() as c:
        r = post(c, params={"create_classes": "false"})
    assert r.status_code == 422
    assert [e["code"] for e in r.json()["erros"]] == ["classe_ausente"] * 2
    assert env.repo.writes() == []


@pytest.mark.parametrize(
    "params", [{"dry_run": "banana"}, {"create_classes": "talvez"}, {"dry_run": ["true", "false"]}]
)
def test_invalid_query_is_422(env: Env, params: dict[str, Any]) -> None:
    with env.client() as c:
        r = post(c, params=params)
    assert r.status_code == 422 and r.json()["code"] == "query_invalida"
    assert env.provider.acquired == 0


# 30
def test_unknown_dataset_is_404_before_reading_the_body(env: Env) -> None:
    env.repo.dataset_exists = False
    with env.client() as c:
        r = post(c)
    assert r.status_code == 404 and r.json()["code"] == "dataset_nao_encontrado"
    assert env.repo.calls == ["get_dataset"]


@pytest.mark.parametrize("ds", ["abc", "0", "-1", "9223372036854775808", "01", "1.5"])
def test_invalid_dataset_id_is_422(env: Env, ds: str) -> None:
    with env.client() as c:
        r = post(c, url=f"/datasets/{ds}/imports/pascal-voc")
    assert r.status_code == 422 and r.json()["code"] == "dataset_id_invalido"
    assert env.provider.acquired == 0


@pytest.mark.parametrize(
    "ctype",
    ["application/xml", "text/xml", "application/zip", "multipart/form-data; boundary=x",
     "text/plain", None, "application/json; charset=utf-16", "application/jsonx"],
)  # fmt: skip
def test_unsupported_content_type_is_415(env: Env, ctype: str | None) -> None:
    headers = {} if ctype is None else {"Content-Type": ctype}
    with env.client() as c:
        r = c.post(URL, content=files()["a.xml"].encode(), headers=headers)
    assert r.status_code == 415 and r.json()["code"] == "tipo_nao_suportado"
    assert env.provider.acquired == 0


def test_content_length_over_limit_is_413_without_reading(monkeypatch: pytest.MonkeyPatch) -> None:
    e = Env(monkeypatch, import_max_json_bytes=100)
    with e.client() as c:
        r = post(c, b"x" * 101)
        assert r.status_code == 413 and r.json()["code"] == "requisicao_grande_demais"
        assert e.provider.acquired == 0 and e.repo.calls == []
        ok = post(c, b"x" * 100)
        assert ok.status_code == 422 and ok.json()["erros"][0]["code"] == "json_invalido"


def test_chunked_body_over_limit_is_413(monkeypatch: pytest.MonkeyPatch) -> None:
    e = Env(monkeypatch, import_max_json_bytes=1000)

    def gen() -> Any:
        for _ in range(100):
            yield b"x" * 100

    with e.client() as c:
        r = c.post(URL, content=gen(), headers=JSON_H)
    assert r.status_code == 413
    assert r.request.headers.get("transfer-encoding") == "chunked"
    assert e.repo.writes() == []


def test_streaming_aborts_before_accumulating_the_whole_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    e = Env(monkeypatch, import_max_json_bytes=1000)
    out = asgi_post(
        e.app(), [b"x" * 100 for _ in range(500)], [(b"content-type", b"application/json")], URL
    )
    assert out["status"] == 413 and out["pulled"] <= 12
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
        URL,
    )
    assert out["status"] == 413 and e.repo.writes() == []


def test_limit_is_per_route_in_all_directions(monkeypatch: pytest.MonkeyPatch) -> None:
    e = Env(monkeypatch, import_max_json_bytes=50)
    with e.client() as c:
        for url in (URL, "/datasets/1/imports/coco", "/datasets/1/imports/yolo-txt"):
            assert post(c, b"x" * 51, url=url).status_code == 413
        big = c.post("/datasets/1/images", content=b"z" * 100, headers={"Content-Type": "x/y"})
        assert big.status_code != 413


def test_xml_limit_is_applied_per_file(monkeypatch: pytest.MonkeyPatch) -> None:
    e = Env(monkeypatch, import_max_xml_bytes=50)
    with e.client() as c:
        r = post(c)
    assert r.status_code == 422 and r.json()["erros"][0]["code"] == "limite_excedido"
    assert e.repo.writes() == []


# 31
def test_errors_report_file_and_line_and_write_nothing(env: Env) -> None:
    f = {
        "a.xml": ann(obj("pessoa"), "\n<object><name>x</name></object>"),
        "sem_imagem.xml": ann(obj("pessoa")),
        "mal.xml": "<annotation>",
    }
    with env.client() as c:
        r = post(c, body(f))
    j = r.json()
    assert r.status_code == 422 and j["code"] == "importacao_invalida"
    assert j["erros_total"] == len(j["erros"]) == 3 and j["erros_truncados"] is False
    by = {e["code"]: e for e in j["erros"]}
    assert "bndbox_ausente" in by and "imagem_nao_encontrada" in by
    bad = by["bndbox_ausente"]
    assert {"code", "kind", "id", "index", "message", "arquivo", "linha"} <= set(bad)
    assert (bad["arquivo"], bad["linha"]) == ("a.xml", 2)
    assert by["imagem_nao_encontrada"]["arquivo"] == "sem_imagem.xml"
    assert "linha" not in by["imagem_nao_encontrada"]
    assert env.repo.writes() == [] and "BEGIN" not in env.repo.calls


def test_size_mismatch_is_422_and_writes_nothing(env: Env) -> None:
    with env.client() as c:
        r = post(c, body({"a.xml": ann(obj(), size=size(640, 480))}))
    assert [e["code"] for e in r.json()["erros"]] == ["tamanho_divergente"]
    assert env.repo.writes() == []


def test_error_list_is_truncated_with_real_total(monkeypatch: pytest.MonkeyPatch) -> None:
    e = Env(monkeypatch, import_max_errors=3)
    f = {"a.xml": ann(*[obj("pessoa", box=bnd(9, 1, 2, 2)) for _ in range(10)])}
    with e.client() as c:
        j = post(c, body(f)).json()
    assert len(j["erros"]) == 3 and j["erros_total"] == 10 and j["erros_truncados"] is True


@pytest.mark.parametrize("data", [b"", b"{", b"[]", b"\xff\xfe", b'{"annotations": NaN}', b"{}"])
def test_unparseable_bodies_are_422(env: Env, data: bytes) -> None:
    with env.client() as c:
        r = post(c, data)
    assert r.status_code == 422 and r.json()["code"] == "importacao_invalida"
    assert r.json()["erros"][0]["code"] in {"json_invalido", "estrutura_invalida"}


def test_messages_do_not_echo_xml_or_long_data(env: Env) -> None:
    long_tok = "q" * 900
    f = {
        "a.xml": ann(
            obj("pessoa", box=bnd(long_tok, 1, 2, 2)),
            obj(SENTINEL, box=bnd(1, 1, SENTINEL, 2)),
            f"<object><name>{long_tok}</name>{bnd()}</object>",
        )
    }
    with env.client() as c:
        r = post(c, body(f))
    assert r.status_code == 422
    for err in r.json()["erros"]:
        assert "q" * 201 not in err["message"] and SENTINEL not in err["message"]
        assert len(err["message"]) <= 300


# 32
def test_db_unavailable_is_503(env: Env, caplog: pytest.LogCaptureFixture) -> None:
    env.provider.fail = OSError(f"connect {SECRETS[3]} {SECRETS[2]}")
    with caplog.at_level(logging.DEBUG), env.client() as c:
        r = post(c)
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
    [asyncpg.UniqueViolationError(f"dup {SECRETS[3]} INSERT INTO annotation {SENTINEL}"),
     asyncpg.ForeignKeyViolationError(SECRETS[2])],
)  # fmt: skip
def test_integrity_violation_is_409(
    env: Env, exc: Exception, caplog: pytest.LogCaptureFixture
) -> None:
    async def boom(*a: Any) -> Any:
        raise exc

    env.repo.create_class = boom  # type: ignore[method-assign]
    with caplog.at_level(logging.DEBUG), env.client() as c:
        r = post(c)
    assert r.status_code == 409 and r.json()["code"] == "conflito_integridade"
    _no_leak(r.text, caplog)
    assert env.repo.classes == []


def test_dataset_changed_during_import_is_409_dataset_alterado(
    env: Env, caplog: pytest.LogCaptureFixture
) -> None:
    real = env.repo.list_images_for_import

    async def racy(dataset_id: int, limit: int) -> list[ImportImage]:
        out = await real(dataset_id, limit)
        return [] if "lock" in env.repo.calls else out

    env.repo.list_images_for_import = racy  # type: ignore[method-assign]
    with caplog.at_level(logging.DEBUG), env.client() as c:
        r = post(c)
    assert r.status_code == 409 and r.json()["code"] == "dataset_alterado"
    assert env.repo.classes == [] and env.repo.annotations == []
    _no_leak(r.text, caplog)


def test_driver_error_and_unexpected_error(env: Env, caplog: pytest.LogCaptureFixture) -> None:
    async def down(*a: Any) -> Any:
        raise asyncpg.PostgresConnectionError(SECRETS[3])

    async def odd(*a: Any) -> Any:
        raise RuntimeError(f"{SECRETS[0]} {SENTINEL}")

    with caplog.at_level(logging.DEBUG), env.client() as c:
        env.repo.list_classes = down  # type: ignore[method-assign]
        r1 = post(c)
        env.repo.list_classes = odd  # type: ignore[method-assign]
        r2 = post(c)
    assert (r1.status_code, r1.json()["code"]) == (503, "banco_indisponivel")
    assert (r2.status_code, r2.json()["code"]) == (500, "erro_interno")
    _no_leak(r1.text + r2.text, caplog)


def test_unexpected_parser_failure_is_500_without_details(
    env: Env, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def boom(*a: Any, **k: Any) -> Any:
        raise RuntimeError(f"{SECRETS[0]} {SENTINEL}")

    monkeypatch.setattr(imports, "parse_voc", boom)
    with caplog.at_level(logging.DEBUG), env.client() as c:
        r = post(c)
    assert (r.status_code, r.json()["code"]) == (500, "erro_interno")
    _no_leak(r.text, caplog)


def test_422_does_not_log_the_uploaded_xml(env: Env, caplog: pytest.LogCaptureFixture) -> None:
    f = {"a.xml": f"<annotation><note>{SENTINEL}</note><object></annotation>"}
    with caplog.at_level(logging.DEBUG), env.client() as c:
        r = post(c, body(f, extra={"note": SENTINEL}))
    assert r.status_code == 422
    assert SENTINEL not in caplog.text and SENTINEL not in r.text


def test_parse_runs_in_a_thread_and_the_loop_keeps_answering(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    started, release = threading.Event(), threading.Event()
    real = imports.parse_voc
    where: list[str] = []

    def slow(*a: Any, **k: Any) -> Any:
        where.append(threading.current_thread().name)
        started.set()
        assert release.wait(10)
        return real(*a, **k)

    monkeypatch.setattr(imports, "parse_voc", slow)
    out: list[Any] = []
    with env.client() as c:
        t = threading.Thread(target=lambda: out.append(post(c)))
        t.start()
        assert started.wait(10)
        health = c.get("/health")  # concluída enquanto o parse ainda está bloqueado
        assert health.status_code == 200 and not release.is_set()
        release.set()
        t.join(10)
    assert out[0].status_code == 201
    assert where[0] != threading.main_thread().name and where[0] != t.name


def test_big_hostile_file_gets_a_fast_422(env: Env) -> None:
    f = {"a.xml": "<annotation>" + "<a>" * 85_000}
    with env.client() as c:
        r = post(c, body(f))
    assert r.status_code == 422 and r.json()["erros"][0]["code"] == "limite_excedido"


# 33
def test_get_on_the_route_is_405_and_lifespan_closes_once(env: Env) -> None:
    with env.client() as c:
        assert c.get(URL).status_code == 405
        post(c)
    assert env.provider.closed == 1
