"""F1-05c: bordas e segurança da rota de upload (complementa `test_api_images.py`)."""

import asyncio
import json
import logging
import re
import socket
from typing import Any

import asyncpg  # type: ignore[import-untyped]
import pytest
from fastapi.testclient import TestClient

from _imgbytes import jpeg, png
from inovalytics.api.images import RequestBodyLimitMiddleware
from test_api_images import URL, Env, asgi_post, post
from test_upload_service import SECRETS

pytestmark = pytest.mark.usefixtures("no_external_network")

KEY_RE = re.compile(r"^datasets/1/[0-9a-f]{64}\.(jpg|png)$")
MP = {"content-type": "multipart/form-data; boundary=b"}


def part(name: str, filename: str | None, data: bytes, ctype: str = "image/png") -> bytes:
    disp = f'form-data; name="{name}"' + ("" if filename is None else f'; filename="{filename}"')
    return (
        f"--b\r\nContent-Disposition: {disp}\r\nContent-Type: {ctype}\r\n\r\n".encode()
        + data
        + b"\r\n"
    )


def body(*parts: bytes, close: bool = True) -> bytes:
    return b"".join(parts) + (b"--b--\r\n" if close else b"")


def mk(mp: pytest.MonkeyPatch, **cfg: Any) -> Env:
    return Env(mp, **cfg)


def forbidden(env: Env) -> list[str]:
    s = env.settings
    return [*SECRETS, s.postgres_dsn, s.s3_endpoint_url, "storage:8333", "postgresql://", "/srv/"]


def assert_clean(env: Env, *texts: str) -> None:
    blob = "\n".join(texts)
    for secret in forbidden(env):
        assert secret not in blob


# --- dataset_id -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    ["abc", "0", "-1", "01", "+1", "1.0", "1e3", "0x1", "1%20", "%201", "%D9%A1", "%EF%BC%91",
     "9223372036854775808", "9" * 5000, "%2e%2e", "1%00", "1;2", "null"],
)  # fmt: skip
def test_dataset_id_not_a_positive_int64_is_422_without_side_effects(env_: Any, raw: str) -> None:
    r = post(env_.client(), [("a.png", png(), "image/png")], url=f"/datasets/{raw}/images")
    assert r.status_code == 422 and r.json()["code"] == "dataset_id_invalido"
    assert env_.store.puts == 0 and env_.provider.acquired == 0


@pytest.fixture
def env_(monkeypatch: pytest.MonkeyPatch) -> Env:
    return Env(monkeypatch)


def test_dataset_id_int64_max_is_a_valid_id_so_404(env_: Env) -> None:
    r = post(env_.client(), [("a.png", png(), "image/png")], url=f"/datasets/{2**63 - 1}/images")
    assert r.status_code == 404 and env_.store.puts == 0


def test_dataset_id_with_trailing_newline_is_rejected(env_: Env) -> None:
    r = post(env_.client(), [("a.png", png(), "image/png")], url="/datasets/1%0A/images")
    assert r.status_code == 422 and env_.store.puts == 0


def test_wrong_methods_and_paths(env_: Env) -> None:
    c = env_.client()
    assert c.get(URL).status_code == 405
    assert c.put(URL, content=b"x").status_code == 405
    assert c.post("/datasets/1/images/extra", content=b"x").status_code == 404
    assert c.post("/datasets//images", content=b"x").status_code in (404, 422)
    assert env_.provider.acquired == 0


# --- limites de quantidade e de bytes ---------------------------------------------------------


def test_max_files_exact_ok_and_plus_one_is_422(monkeypatch: pytest.MonkeyPatch) -> None:
    env = mk(monkeypatch, upload_max_files=3)
    c = env.client()
    r = post(c, [(f"{i}.png", png(i + 1), "image/png") for i in range(3)])
    assert r.status_code == 201 and len(r.json()["images"]) == 3
    puts = env.store.puts
    r = post(c, [(f"{i}.png", png(i + 20), "image/png") for i in range(4)])
    assert r.status_code == 422 and r.json()["code"] == "arquivos_demais"
    assert env.store.puts == puts


def test_second_oversized_file_reports_index_and_writes_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    n = len(png())
    env = mk(monkeypatch, upload_max_bytes=n)
    r = post(env.client(), [("a.png", png(), "image/png"), ("b.png", png() + b"\0", "image/png")])
    assert r.status_code == 413 and r.json()["file_index"] == 1
    assert env.store.puts == 0 and not env.repo.rows


def test_invalid_file_in_batch_rejects_all_with_index(env_: Env) -> None:
    files = [("a.png", png(1), "image/png"), ("b.jpg", jpeg(2), "image/jpeg"),
             ("c.png", b"<svg onload=alert(1)>", "image/png")]  # fmt: skip
    r = post(env_.client(), files)
    assert r.status_code == 415 and r.json()["file_index"] == 2
    assert env_.store.puts == 0 and not env_.repo.rows


# --- middleware: limites exatos, Content-Length mentiroso, chunked ---------------------------


def run_mw(
    limit: int,
    chunks: list[bytes],
    *,
    cl: bytes | None = None,
    method: str = "POST",
    path: str = "/datasets/1/images",
    app_raises: bool = False,
    respond_on_disconnect: bool = True,
) -> dict[str, Any]:
    out: dict[str, Any] = {"status": None, "inner_called": False, "seen": 0, "sent": []}
    queue = [*chunks]

    async def receive() -> dict[str, Any]:
        if queue:
            return {"type": "http.request", "body": queue.pop(0), "more_body": bool(queue)}
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(m: dict[str, Any]) -> None:
        out["sent"].append(m)
        if m["type"] == "http.response.start":
            out["status"] = m["status"]

    async def inner(scope: Any, rcv: Any, snd: Any) -> None:
        out["inner_called"] = True
        while True:
            m = await rcv()
            if m["type"] == "http.disconnect":
                if app_raises:
                    raise RuntimeError("cliente foi embora")
                if not respond_on_disconnect:
                    return
                break
            out["seen"] += len(m["body"])
            if not m.get("more_body"):
                break
        if app_raises:
            raise RuntimeError("falha da app " + SECRETS[0])
        await snd({"type": "http.response.start", "status": 200, "headers": []})
        await snd({"type": "http.response.body", "body": b"ok"})

    headers = [] if cl is None else [(b"content-length", cl)]
    scope = {"type": "http", "method": method, "path": path, "headers": headers}
    asyncio.run(RequestBodyLimitMiddleware(inner, limit)(scope, receive, send))
    return out


def test_mw_body_exactly_at_limit_passes_and_plus_one_is_413() -> None:
    ok = run_mw(100, [b"a" * 60, b"b" * 40])
    assert ok["status"] == 200 and ok["seen"] == 100
    bad = run_mw(100, [b"a" * 60, b"b" * 41])
    assert bad["status"] == 413 and bad["seen"] == 60  # o bloco que estourou não é entregue
    assert json.loads(bad["sent"][-1]["body"])["code"] == "requisicao_grande_demais"


def test_mw_413_even_if_app_ignores_disconnect_or_raises() -> None:
    assert run_mw(10, [b"x" * 11], respond_on_disconnect=False)["status"] == 413
    assert run_mw(10, [b"x" * 11], app_raises=True)["status"] == 413


def test_mw_app_error_without_overflow_is_not_swallowed() -> None:
    with pytest.raises(RuntimeError):
        run_mw(100, [b"x" * 5], app_raises=True)


def test_mw_content_length_boundaries() -> None:
    ok = run_mw(100, [b"a" * 100], cl=b"100")
    assert ok["inner_called"] and ok["status"] == 200
    big = run_mw(100, [b"a" * 101], cl=b"101")
    assert big["status"] == 413 and not big["inner_called"] and big["seen"] == 0


@pytest.mark.parametrize("cl", [b"-1", b"abc", b"", b"1e9", b" 5", b"0x10", b"+9"])
def test_mw_unparseable_or_negative_content_length_falls_back_to_counting(cl: bytes) -> None:
    out = run_mw(100, [b"a" * 60, b"b" * 60], cl=cl)
    assert out["status"] == 413  # contagem real do corpo vale, não o cabeçalho


def test_mw_lying_small_content_length_is_still_limited() -> None:
    out = run_mw(100, [b"a" * 60, b"b" * 60], cl=b"10")
    assert out["status"] == 413


@pytest.mark.parametrize(
    "method,path",
    [("GET", "/datasets/1/images"), ("PUT", "/datasets/1/images"),
     ("POST", "/datasets/1/images/extra"), ("POST", "/datasets/1/other"),
     ("POST", "/health")],
)  # fmt: skip
def test_mw_only_guards_post_on_upload_path(method: str, path: str) -> None:
    out = run_mw(10, [b"x" * 50], cl=b"50", method=method, path=path)
    assert out["status"] == 200 and out["inner_called"]


def test_mw_non_http_scopes_pass_through() -> None:
    called = []

    async def inner(scope: Any, receive: Any, send: Any) -> None:
        called.append(scope["type"])

    async def nop(*a: Any) -> Any:
        return {}

    for t in ("lifespan", "websocket"):
        asyncio.run(RequestBodyLimitMiddleware(inner, 1)({"type": t}, nop, nop))
    assert called == ["lifespan", "websocket"]


def test_chunked_without_content_length_exact_limit_reaches_the_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = mk(monkeypatch, upload_max_bytes=1000, upload_max_files=1)
    limit = 1000 + 64 * 1024
    p = part("files", "a.png", png() + b"\0" * 100)
    pad = limit - len(body(p)) - len(b'--b\r\nContent-Disposition: form-data; name="x"\r\n\r\n\r\n')
    raw = body(p, b'--b\r\nContent-Disposition: form-data; name="x"\r\n\r\n' + b"x" * pad + b"\r\n")
    assert len(raw) == limit
    status, out = asgi_post(env.client().app, iter([raw[:7000], raw[7000:]]))
    assert status == 201 and out["images"][0]["created"] is True
    status, out = asgi_post(env.client().app, iter([raw[:7000], raw[7000:], b"\n"]))
    assert status == 413 and out["code"] == "requisicao_grande_demais"


def test_chunked_over_limit_with_declared_negative_length(monkeypatch: pytest.MonkeyPatch) -> None:
    env = mk(monkeypatch, upload_max_bytes=1000, upload_max_files=1)
    seen = 0

    def gen() -> Any:
        nonlocal seen
        yield part("files", "a.png", png())[:-2]
        while True:
            seen += 1
            yield b"x" * 8192

    status, out = asgi_post(env.client().app, gen(), with_length=-1)
    assert status == 413 and seen < 20 and env.store.puts == 0


# --- multipart malformado ---------------------------------------------------------------------


def _ct(v: str) -> dict[str, str]:
    return {"content-type": v}


_GOOD = body(part("files", "a.png", png()))
_NO_NAME = b"--b\r\nContent-Disposition: form-data\r\n\r\nx\r\n--b--\r\n"
MALFORMED: dict[str, tuple[dict[str, str], bytes]] = {
    "sem-boundary": (_ct("multipart/form-data"), _GOOD),
    "lixo-binario": (MP, b"\x00\xff\xfe" * 50),
    "vazio": (MP, b""),
    "boundary-errado": (_ct("multipart/form-data; boundary=zzz"), _GOOD),
    "texto-com-png": (_ct("text/plain"), png()),
    "json": (_ct("application/json"), b'{"files": []}'),
    "urlencoded": (_ct("application/x-www-form-urlencoded"), b"split=train"),
    "cortado-no-meio": (MP, body(part("files", "a.png", png()), close=False)[:-30]),
    "campo-com-nome-errado": (MP, body(part("file", "a.png", png()))),
    "parte-vazia": (MP, body(part("files", "a.png", b""))),
    "parte-sem-cabecalhos": (MP, b"--b\r\n\r\n\r\n--b--\r\n"),
    "sem-name": (MP, _NO_NAME),
}


@pytest.mark.parametrize("case", list(MALFORMED))
def test_malformed_multipart_is_422_never_5xx_and_writes_nothing(env_: Env, case: str) -> None:
    headers, content = MALFORMED[case]
    r = env_.client().post(URL, content=content, headers=headers)
    assert r.status_code == 422, r.text
    assert r.json()["code"] and r.json()["detail"]
    assert env_.store.puts == 0 and not env_.repo.rows


def test_invalid_utf8_in_part_headers_is_not_a_500(env_: Env) -> None:
    raw = (b'--b\r\nContent-Disposition: form-data; name="files"; filename="a\xff\xfe.png"\r\n'
           b"Content-Type: image/png\r\n\r\n" + png() + b"\r\n--b--\r\n")  # fmt: skip
    r = env_.client().post(URL, content=raw, headers=MP)
    assert r.status_code in (201, 422)
    if r.status_code == 201:
        name = r.json()["images"][0]["file_name"]
        name.encode("utf-8")  # precisa ser UTF-8 válido para o Postgres
        assert KEY_RE.match(next(iter(env_.store.objects)))


def test_more_parts_than_starlette_allows_is_422(env_: Env) -> None:
    raw = body(*[part("files", f"{i}.png", png(1 + i % 50, 1 + i // 50)) for i in range(1001)])
    r = env_.client().post(URL, content=raw, headers=MP)
    assert r.status_code == 422 and env_.store.puts == 0


def test_too_many_plain_fields_is_422(env_: Env) -> None:
    fields = b"".join(
        b'--b\r\nContent-Disposition: form-data; name="split"\r\n\r\ntrain\r\n' for _ in range(1001)
    )
    r = env_.client().post(URL, content=body(fields, part("files", "a.png", png())), headers=MP)
    assert r.status_code == 422 and env_.store.puts == 0


def test_part_with_empty_filename_gets_default_name(env_: Env) -> None:
    r = env_.client().post(URL, content=body(part("files", "", png())), headers=MP)
    assert r.status_code == 201 and r.json()["images"][0]["file_name"] == "imagem"


def test_files_mixed_with_a_text_value_rejects_whole_request(env_: Env) -> None:
    raw = body(
        part("files", "a.png", png()),
        b'--b\r\nContent-Disposition: form-data; name="files"\r\n\r\ntexto\r\n',
    )
    r = env_.client().post(URL, content=raw, headers=MP)
    assert (
        r.status_code == 422
        and r.json()["code"] == "arquivo_invalido"
        and r.json()["file_index"] == 1
    )
    assert env_.store.puts == 0


# --- split ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    ["TRAIN", "Train", " train", "train ", "train\n", "validation", "x" * 10_000, "tráin", "0"],
)
def test_split_invalid_values(env_: Env, bad: str) -> None:
    r = post(env_.client(), [("a.png", png(), "image/png")], split=bad)
    assert r.status_code == 422 and r.json()["code"] == "split_invalido" and env_.store.puts == 0


@pytest.mark.parametrize("ok", ["train", "val", "test"])
def test_split_valid_values(env_: Env, ok: str) -> None:
    r = post(env_.client(), [("a.png", png(), "image/png")], split=ok)
    assert r.status_code == 201 and r.json()["images"][0]["split"] == ok


def test_empty_split_means_none(env_: Env) -> None:
    r = post(env_.client(), [("a.png", png(), "image/png")], split="")
    assert r.status_code == 201 and r.json()["images"][0]["split"] is None


def test_split_sent_as_a_file_is_rejected(env_: Env) -> None:
    raw = body(part("split", "train.txt", b"train", "text/plain"), part("files", "a.png", png()))
    r = env_.client().post(URL, content=raw, headers=MP)
    assert r.status_code == 422 and r.json()["code"] == "split_invalido"


def test_duplicate_split_fields_do_not_crash_or_smuggle_invalid_value(env_: Env) -> None:
    def f(v: str) -> bytes:
        return f'--b\r\nContent-Disposition: form-data; name="split"\r\n\r\n{v}\r\n'.encode()

    for vals in (("train", "bogus"), ("bogus", "train")):
        raw = body(f(vals[0]), f(vals[1]), part("files", "a.png", png(5 + len(vals[0]))))
        r = env_.client().post(URL, content=raw, headers=MP)
        assert r.status_code in (201, 422)
        if r.status_code == 201:
            assert r.json()["images"][0]["split"] in ("train", "val", "test", None)


# --- tipo falso / nomes -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "name,data,ctype",
    [
        ("a.jpg", png(), "image/jpeg"),
        ("a.png", jpeg(), "image/png"),
        ("a.html", png(), "text/html"),
        ("a.svg", jpeg(), "image/svg+xml"),
        ("a", png(), ""),
    ],
)
def test_declared_type_and_extension_never_decide_stored_type(
    env_: Env, name: str, data: bytes, ctype: str
) -> None:
    r = post(env_.client(), [(name, data, ctype)])
    assert r.status_code == 201
    (key,) = env_.store.objects
    real = "png" if data.startswith(b"\x89PNG") else "jpg"
    assert key.endswith(f".{real}")
    assert env_.store.objects[key][1] == {"png": "image/png", "jpg": "image/jpeg"}[real]
    assert r.json()["images"][0]["content_type"] == env_.store.objects[key][1]


@pytest.mark.parametrize(
    "name,data,ctype",
    [
        ("a.jpg", b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg"/>', "image/jpeg"),
        ("a.png", b"<svg onload=alert(1)>", "image/svg+xml"),
        ("a.jpg", b"GIF89a" + jpeg(), "image/jpeg"),
        ("a.jpg", b"\xef\xbb\xbf" + jpeg(), "image/jpeg"),
        ("a.jpg", b"<html>" + png(), "image/png"),
        ("a.png", b"MZ\x90\x00\x03\x00\x00\x00", "image/png"),
        ("a.png", b"PK\x03\x04", "image/png"),
        ("a.png", b"\x00" * 2048, "image/png"),
    ],
)
def test_fake_content_is_415_without_side_effects(
    env_: Env, name: str, data: bytes, ctype: str
) -> None:
    r = post(env_.client(), [(name, data, ctype)])
    assert r.status_code == 415 and r.json()["code"] == "tipo_nao_suportado"
    assert env_.store.puts == 0 and not env_.repo.rows and env_.provider.acquired == 1


@pytest.mark.parametrize(
    "data,code",
    [
        (png()[:8], "imagem_invalida"),
        (png()[:28], "imagem_invalida"),
        (jpeg()[:3], "imagem_invalida"),
        (jpeg()[:25], "imagem_invalida"),
        (jpeg(65535, 65535), "imagem_invalida"),
        (png(2**32 - 1, 2**32 - 1), "imagem_invalida"),
        (png(0, 5), "imagem_invalida"),
        (png(5, 5, depth=3), "imagem_invalida"),
    ],
)
def test_corrupt_or_giant_headers_are_422(env_: Env, data: bytes, code: str) -> None:
    r = post(env_.client(), [("a.png", data, "image/png")])
    assert r.status_code == 422 and r.json()["code"] == code and env_.store.puts == 0


@pytest.mark.parametrize(
    "name",
    ["..", ".", "../../../etc/passwd", "/etc/passwd", "..\\..\\windows\\system32\\cmd.exe.png",
     "C:\\Windows\\x.png", "....//....//x.png", "%2e%2e%2f%2e%2e%2fx.png", "a/../../b.png",
     "x" * 3000 + ".png", "ñ/é/日本.png", "con.png", "a\u202eb.png", "name\twith\ttabs.png",
     "\U0001f600.png", "'; DROP TABLE image;--.png", '"><script>alert(1)</script>.png'],
)  # fmt: skip
def test_hostile_client_names_never_reach_the_key_and_are_sanitized(env_: Env, name: str) -> None:
    r = post(env_.client(), [(name, png(), "image/png")])
    assert r.status_code == 201, r.text
    (key,) = env_.store.objects
    assert KEY_RE.match(key)
    out = r.json()["images"][0]["file_name"]
    assert out and out not in (".", "..") and "/" not in out and "\\" not in out and len(out) <= 255
    assert out == env_.repo.inserts[-1]["file_name"]


def test_name_longer_than_header_limit_is_rejected_not_crashing(env_: Env) -> None:
    r = post(env_.client(), [("x" * 100_000 + ".png", png(), "image/png")])
    assert r.status_code in (201, 422)
    assert all(KEY_RE.match(k) for k in env_.store.objects)


def test_raw_nul_in_filename_is_removed(env_: Env) -> None:
    raw = body(part("files", "a\x00b.png", png()))
    r = env_.client().post(URL, content=raw, headers=MP)
    assert r.status_code == 201 and r.json()["images"][0]["file_name"] == "ab.png"


# --- idempotência -----------------------------------------------------------------------------


def test_resend_with_other_name_returns_original_row_and_200(env_: Env) -> None:
    c = env_.client()
    first = post(c, [("original.png", png(), "image/png")]).json()["images"][0]
    r = post(c, [("outro-nome.png", png(), "image/png")])
    img = r.json()["images"][0]
    assert r.status_code == 200 and img["created"] is False
    assert img["id"] == first["id"] and img["file_name"] == "original.png"
    assert env_.store.puts == 1 and len(env_.repo.rows) == 1


def test_mixed_batch_is_201_with_per_item_flags_and_duplicates_inside_request(env_: Env) -> None:
    c = env_.client()
    post(c, [("a.png", png(1), "image/png")])
    r = post(c, [("a2.png", png(1), "image/png"), ("b.png", png(2), "image/png"),
                 ("b2.png", png(2), "image/png")])  # fmt: skip
    assert r.status_code == 201
    assert [i["created"] for i in r.json()["images"]] == [False, True, False]
    assert env_.store.puts == 2 and len(env_.repo.rows) == 2


def test_same_bytes_in_other_dataset_is_created_again(env_: Env) -> None:
    env_.repo.datasets.add(2)
    c = env_.client()
    assert post(c, [("a.png", png(), "image/png")]).status_code == 201
    r = post(c, [("a.png", png(), "image/png")], url="/datasets/2/images")
    assert r.status_code == 201 and len(env_.store.objects) == 2


def test_s3_failure_on_second_file_keeps_first_and_retry_is_safe(env_: Env) -> None:
    c = env_.client()
    real_put = env_.store.put
    count = 0

    async def put(key: str, fileobj: Any, content_type: str) -> None:
        nonlocal count
        count += 1
        if count == 2:
            raise OSError("s3 down " + SECRETS[0])
        await real_put(key, fileobj, content_type)

    env_.store.put = put  # type: ignore[method-assign]
    files = [("a.png", png(1), "image/png"), ("b.png", png(2), "image/png")]
    r = post(c, files)
    assert r.status_code == 503 and len(env_.repo.rows) == 1
    assert_clean(env_, r.text)
    r2 = post(c, files)
    assert r2.status_code == 201
    assert [i["created"] for i in r2.json()["images"]] == [False, True]


# --- falhas de banco / S3 e compensação -------------------------------------------------------

DB_EXCS = [
    asyncpg.exceptions.CannotConnectNowError("starting up postgresql://u:PGPASS-xyz@db/x"),
    asyncpg.exceptions.TooManyConnectionsError("too many"),
    asyncpg.InterfaceError("pool is closed"),
    ConnectionResetError("reset by peer db:5432"),
    socket.gaierror("Name or service not known: db"),
    TimeoutError(),
]


@pytest.mark.parametrize("exc", DB_EXCS, ids=lambda e: type(e).__name__)
@pytest.mark.parametrize("where", ["acquire", "get_dataset", "get_image_by_sha"])
def test_db_failures_are_503_without_put_and_without_leaks(
    env_: Env, caplog: pytest.LogCaptureFixture, exc: Exception, where: str
) -> None:
    async def boom(*a: Any, **k: Any) -> Any:
        raise exc

    if where == "acquire":
        env_.provider.fail = exc
    else:
        setattr(env_.repo, where, boom)
    with caplog.at_level(logging.DEBUG):
        r = post(env_.client(), [("a.png", png(), "image/png")])
    assert r.status_code == 503 and r.json()["code"] == "banco_indisponivel"
    assert env_.store.puts == 0 and not env_.repo.rows
    assert_clean(env_, r.text, str(dict(r.headers)), caplog.text)
    assert all(rec.exc_info is None for rec in caplog.records)


@pytest.mark.parametrize("exc", DB_EXCS, ids=lambda e: type(e).__name__)
def test_insert_failure_is_503_and_object_is_compensated(
    env_: Env, caplog: pytest.LogCaptureFixture, exc: Exception
) -> None:
    async def boom(*a: Any, **k: Any) -> Any:
        raise exc

    env_.repo.insert_image = boom  # type: ignore[method-assign]
    with caplog.at_level(logging.DEBUG):
        r = post(env_.client(), [("a.png", png(), "image/png")])
    assert r.status_code == 503
    assert env_.store.puts == 1 and not env_.store.objects
    assert_clean(env_, r.text, caplog.text)


def test_insert_and_compensation_both_fail_object_is_orphan_and_reported_only_by_key(
    env_: Env, caplog: pytest.LogCaptureFixture
) -> None:
    env_.repo.fail_insert = (
        True  # RuntimeError com DSN na mensagem: não é erro de banco "conhecido"
    )
    env_.store.fail_delete = True
    c = TestClient(env_.client().app, raise_server_exceptions=False)
    with caplog.at_level(logging.DEBUG):
        r = c.post(URL, files=[("files", ("a.png", png(), "image/png"))])
    assert r.status_code == 500
    assert_clean(env_, r.text, str(dict(r.headers)), caplog.text)
    assert len(env_.store.objects) == 1  # órfão assumido (H-F1-05-7)
    assert re.search(r"datasets/1/[0-9a-f]{64}\.png", caplog.text)
    assert all(rec.exc_info is None for rec in caplog.records)


def test_unexpected_error_after_put_is_compensated_and_not_leaked(env_: Env) -> None:
    env_.repo.fail_insert = True
    c = TestClient(env_.client().app, raise_server_exceptions=False)
    r = c.post(URL, files=[("files", ("a.png", png(), "image/png"))])
    assert r.status_code == 500 and not env_.store.objects
    assert_clean(env_, r.text)


def test_put_failure_with_hostile_filename_does_not_log_the_name(
    env_: Env, caplog: pytest.LogCaptureFixture
) -> None:
    env_.store.fail_put_at = 1
    evil = "evil\r\nFORGED-LOG-LINE.png"
    with caplog.at_level(logging.DEBUG):
        r = post(env_.client(), [(evil, png(), "image/png")])
    assert r.status_code == 503 and r.json()["code"] == "armazenamento_indisponivel"
    assert "FORGED" not in caplog.text and "FORGED" not in r.text
    assert_clean(env_, r.text, caplog.text)


def test_error_bodies_never_echo_client_input(env_: Env) -> None:
    marker = "ECHO-MARKER-123"
    c = env_.client()
    rs = [
        post(c, [(f"{marker}.jpg", f"{marker}".encode(), f"x/{marker}")]),
        post(c, [(f"{marker}.png", png(), "image/png")], split=marker),
        post(c, [("a.png", png(), "image/png")], url=f"/datasets/{marker}/images"),
        post(c, [(f"{marker}.png", png(0, 1), "image/png")]),
    ]
    assert [r.status_code for r in rs] == [415, 422, 422, 422]
    assert all(marker not in r.text for r in rs)


def test_settings_secrets_are_not_in_repr_or_in_any_health_response(env_: Env) -> None:
    for secret in (*SECRETS, env_.settings.postgres_dsn):
        assert secret not in repr(env_.settings) and secret not in str(env_.settings)
    c = env_.client()
    for path in ("/health", "/health/ready"):
        assert_clean(env_, c.get(path).text)


# --- vazamento: o objeto gravado é exatamente o enviado ---------------------------------------


def test_stored_bytes_equal_uploaded_bytes_and_size_matches(env_: Env) -> None:
    data = png(30, 20) + bytes(range(256)) * 40
    r = post(env_.client(), [("a.png", data, "image/png")])
    assert r.status_code == 201
    (key,) = env_.store.objects
    assert env_.store.objects[key][0] == data
    assert r.json()["images"][0]["size_bytes"] == len(data)
    assert r.json()["images"][0]["sha256"] in key


def test_image_json_never_exposes_object_key_or_paths(env_: Env) -> None:
    r = post(env_.client(), [("a.png", png(), "image/png")])
    img = r.json()["images"][0]
    assert "object_key" not in img and "datasets/" not in r.text
