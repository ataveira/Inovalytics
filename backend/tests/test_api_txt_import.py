"""F1-07d critérios 28 a 31: rota de importação de rótulos txt (TestClient, fakes, sem rede)."""

import asyncio
import json
import logging
from typing import Any

import asyncpg  # type: ignore[import-untyped]
import pytest
from fastapi.testclient import TestClient

from inovalytics.api import imports
from inovalytics.db.repository import ImportImage, LockTimeoutError
from test_api_coco_import import SECRETS, Env

pytestmark = pytest.mark.usefixtures("no_external_network")

URL = "/datasets/1/imports/yolo-txt"
JSON_H = {"Content-Type": "application/json"}
SENTINEL = "TEXTO-DO-ROTULO-zz"
YAML = "path: /x\\nnames:\\n  - pessoa\\n  - capacete\\n"


def doc(**over: Any) -> dict[str, Any]:
    d: dict[str, Any] = {
        "names": ["pessoa", "capacete"],
        "labels": {"a.txt": "0 0.5 0.5 0.2 0.2\n1 0.25 0.25 0.1 0.1\n"},
    }
    d.update(over)
    return d


def body(**over: Any) -> bytes:
    return json.dumps(doc(**over)).encode()


def post(c: TestClient, data: bytes | None = None, url: str = URL, **kw: Any) -> Any:
    return c.post(url, content=body() if data is None else data, headers=JSON_H, **kw)


def asgi_post(
    app: Any, chunks: list[bytes], headers: list[tuple[bytes, bytes]], url: str
) -> dict[str, Any]:
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
        "path": url, "raw_path": url.encode(), "query_string": b"", "root_path": "",
        "scheme": "http", "server": ("t", 80), "client": ("c", 1), "headers": headers,
        "state": {},
    }  # fmt: skip
    asyncio.run(app(scope, receive, send))
    return out


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> Env:
    return Env(monkeypatch)


# 28
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
            "avisos": [],
            "avisos_total": 0,
            "avisos_truncados": False,
        }
        r = post(c)
        assert r.status_code == 200 and r.json()["status"] == "sem_alteracoes"
        assert r.json()["anotacoes"]["ja_existentes"] == 2
        assert len(env.repo.annotations) == 2


def test_data_yaml_body_and_empty_label_file(env: Env) -> None:
    d = {"data_yaml": YAML.replace("\\n", "\n"), "labels": {"a.txt": ""}}
    with env.client() as c:
        r = post(c, json.dumps(d).encode())
    j = r.json()
    assert r.status_code == 201
    assert j["classes"]["criadas"] == 2 and j["imagens_sem_caixas"] == 1
    assert j["anotacoes"]["importadas"] == 0
    assert sorted(c.name for c in env.repo.classes) == ["capacete", "pessoa"]


def test_response_without_crowd_fields(env: Env) -> None:
    with env.client() as c:
        j = post(c).json()
    assert "ignoradas_crowd" not in j["anotacoes"] and "segmentacoes_ignoradas" not in j


def test_warning_carries_file_and_line(env: Env) -> None:
    d = doc(labels={"a.txt": "0 0.99 0.5 0.2 0.2\n"})
    with env.client() as c:
        j = post(c, json.dumps(d).encode()).json()
    assert [w["code"] for w in j["avisos"]] == ["caixa_fora_da_imagem"]
    assert j["avisos"][0]["arquivo"] == "a.txt" and j["avisos"][0]["linha"] == 1
    assert j["avisos_total"] == 1


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


# 29
def test_unknown_dataset_is_404_before_reading_the_body(env: Env) -> None:
    env.repo.dataset_exists = False
    with env.client() as c:
        r = post(c)
    assert r.status_code == 404 and r.json()["code"] == "dataset_nao_encontrado"
    assert env.repo.calls == ["get_dataset"]


@pytest.mark.parametrize("ds", ["abc", "0", "-1", "9223372036854775808", "01", "1.5"])
def test_invalid_dataset_id_is_422(env: Env, ds: str) -> None:
    with env.client() as c:
        r = post(c, url=f"/datasets/{ds}/imports/yolo-txt")
    assert r.status_code == 422 and r.json()["code"] == "dataset_id_invalido"
    assert env.provider.acquired == 0


@pytest.mark.parametrize(
    "ctype",
    ["application/zip", "multipart/form-data; boundary=x", "text/plain", None,
     "application/json; charset=utf-16", "application/jsonx"],
)  # fmt: skip
def test_unsupported_content_type_is_415(env: Env, ctype: str | None) -> None:
    headers = {} if ctype is None else {"Content-Type": ctype}
    with env.client() as c:
        r = c.post(URL, content=body(), headers=headers)
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


def test_limit_is_per_route_in_both_directions(monkeypatch: pytest.MonkeyPatch) -> None:
    e = Env(monkeypatch, import_max_json_bytes=50)
    with e.client() as c:
        assert post(c, b"x" * 51).status_code == 413
        assert post(c, b"x" * 51, url="/datasets/1/imports/coco").status_code == 413
        big = c.post("/datasets/1/images", content=b"z" * 100, headers={"Content-Type": "x/y"})
        assert big.status_code != 413  # o limite da rota de importação não vale para o upload


def test_yaml_limit_is_applied(monkeypatch: pytest.MonkeyPatch) -> None:
    e = Env(monkeypatch, import_max_yaml_bytes=10)
    d = {"data_yaml": "names: [a, b, c, d]\n", "labels": {}}
    with e.client() as c:
        r = post(c, json.dumps(d).encode())
    assert r.status_code == 422 and r.json()["erros"][0]["code"] == "limite_excedido"


# 30
def test_invalid_lines_report_file_and_line_and_write_nothing(env: Env) -> None:
    d = doc(
        labels={
            "a.txt": "0 0.5 0.5 0.2 0.2\n9 0.5 0.5 0.2 0.2\n",
            "sem_imagem.txt": "0 .5 .5 .1 .1\n",
        }
    )
    with env.client() as c:
        r = post(c, json.dumps(d).encode())
    j = r.json()
    assert r.status_code == 422 and j["code"] == "importacao_invalida"
    assert j["erros_total"] == len(j["erros"]) == 2 and j["erros_truncados"] is False
    by = {e["code"]: e for e in j["erros"]}
    assert set(by) == {"classe_fora_do_intervalo", "imagem_nao_encontrada"}
    bad = by["classe_fora_do_intervalo"]
    assert {"code", "kind", "id", "index", "message", "arquivo", "linha"} <= set(bad)
    assert (bad["arquivo"], bad["linha"]) == ("a.txt", 2)
    assert by["imagem_nao_encontrada"]["arquivo"] == "sem_imagem.txt"
    assert "linha" not in by["imagem_nao_encontrada"]
    assert env.repo.writes() == [] and "BEGIN" not in env.repo.calls


def test_error_list_is_truncated_with_real_total(monkeypatch: pytest.MonkeyPatch) -> None:
    e = Env(monkeypatch, import_max_errors=3)
    lines = "".join("0 2 2 2 2\n" for _ in range(10))
    with e.client() as c:
        j = post(c, json.dumps(doc(labels={"a.txt": lines})).encode()).json()
    assert len(j["erros"]) == 3 and j["erros_total"] == 10 and j["erros_truncados"] is True


@pytest.mark.parametrize("data", [b"", b"{", b"[]", b"\xff\xfe", b'{"labels": NaN}'])
def test_unparseable_bodies_are_422(env: Env, data: bytes) -> None:
    with env.client() as c:
        r = post(c, data)
    assert r.status_code == 422 and r.json()["code"] == "importacao_invalida"
    assert r.json()["erros"][0]["code"] in {"json_invalido", "estrutura_invalida"}


def test_both_names_and_data_yaml_is_ambiguous(env: Env) -> None:
    with env.client() as c:
        r = post(c, body(data_yaml="names: [a]\n"))
    assert r.status_code == 422 and r.json()["erros"][0]["code"] == "names_ambiguo"


def test_messages_do_not_echo_line_content_or_long_data(env: Env) -> None:
    long_tok = "q" * 900
    d = doc(labels={"a.txt": f"0 {long_tok} 0.5 0.2 0.2\n0 0.1 0.1 {SENTINEL} 0.2 0.2\n"})
    with env.client() as c:
        r = post(c, json.dumps(d).encode())
    assert r.status_code == 422
    for err in r.json()["erros"]:
        assert "q" * 201 not in err["message"] and SENTINEL not in err["message"]
        assert len(err["message"]) <= 300


# 31
def test_db_unavailable_is_503(env: Env, caplog: pytest.LogCaptureFixture) -> None:
    env.provider.fail = OSError(f"connect {SECRETS[3]} {SECRETS[2]}")
    with caplog.at_level(logging.DEBUG), env.client() as c:
        r = post(c, body(names=[SENTINEL, "capacete"]))
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
        r = post(c, body(names=[SENTINEL, "capacete"]))
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
        return [] if "lock" in env.repo.calls else out

    env.repo.list_images_for_import = racy  # type: ignore[method-assign]
    with caplog.at_level(logging.DEBUG), env.client() as c:
        r = post(c)
    assert r.status_code == 409 and r.json()["code"] == "dataset_alterado"
    assert env.repo.classes == [] and env.repo.annotations == []
    _no_leak(r.text, caplog)


def test_database_driver_error_and_unexpected_error(
    env: Env, caplog: pytest.LogCaptureFixture
) -> None:
    async def down(*a: Any) -> Any:
        raise asyncpg.PostgresConnectionError(SECRETS[3])

    async def odd(*a: Any) -> Any:
        raise RuntimeError(f"{SECRETS[0]} {SENTINEL}")

    with caplog.at_level(logging.DEBUG), env.client() as c:
        env.repo.list_classes = down  # type: ignore[method-assign]
        r1 = post(c, body(names=[SENTINEL, "capacete"]))
        env.repo.list_classes = odd  # type: ignore[method-assign]
        r2 = post(c, body(names=[SENTINEL, "capacete"]))
    assert (r1.status_code, r1.json()["code"]) == (503, "banco_indisponivel")
    assert (r2.status_code, r2.json()["code"]) == (500, "erro_interno")
    _no_leak(r1.text + r2.text, caplog)


def test_422_does_not_log_the_uploaded_text(env: Env, caplog: pytest.LogCaptureFixture) -> None:
    d = doc(labels={"a.txt": f"9 0.5 0.5 0.2 0.2 {SENTINEL}\n"}, extra={"note": SENTINEL})
    with caplog.at_level(logging.DEBUG), env.client() as c:
        r = post(c, json.dumps(d).encode())
    assert r.status_code == 422
    assert SENTINEL not in caplog.text and SENTINEL not in r.text


def _no_leak(text: str, caplog: pytest.LogCaptureFixture) -> None:
    blob = text + "\n" + caplog.text + "\n".join(str(r.exc_info) for r in caplog.records)
    for s in [*SECRETS, SENTINEL, "dbhost", "INSERT INTO", "9333"]:
        assert s not in blob, s


def test_get_on_the_route_is_405_and_route_registered(env: Env) -> None:
    with env.client() as c:
        assert c.get(URL).status_code == 405


def test_lifespan_still_closes_provider_once(env: Env) -> None:
    with env.client() as c:
        post(c)
    assert env.provider.closed == 1
