"""F1-05 (a e b): casos de borda e segurança do núcleo e do serviço de upload.

Complementa `test_upload_inspect/naming/stream/service.py` sem alterar nenhum deles. Tudo em
memória, sem rede e sem Docker. Bugs achados na revisão de testes
(dataset_id com `\\n` final e caracteres Cf/Zl/Zp no nome) foram corrigidos e ficam cobertos aqui.
"""

import asyncio
import hashlib
import io
import logging
import random
import unicodedata
from typing import Any

import pytest

from _imgbytes import jpeg, png
from inovalytics.db.repository import Image, NotFoundError
from inovalytics.uploads.errors import (
    InvalidImageError,
    TooLargeError,
    UnsupportedTypeError,
    UploadError,
)
from inovalytics.uploads.inspect import inspect_image
from inovalytics.uploads.limits import HEADER_BYTES, UploadLimits
from inovalytics.uploads.naming import build_object_key, sanitize_file_name
from inovalytics.uploads.service import UploadItem, upload_images
from inovalytics.uploads.stream import read_limited
from test_upload_service import SECRETS, FakeRepo, FakeStore, item, run

pytestmark = pytest.mark.usefixtures("no_external_network")


# --- inspect: dimensões gigantes, limites exatos ------------------------------------------------


def test_jpeg_sof_65535_square_is_rejected_by_default_limits() -> None:
    with pytest.raises(InvalidImageError):
        inspect_image(jpeg(65535, 65535))


def test_jpeg_sof_max_values_are_read_when_limits_allow() -> None:
    lim = UploadLimits(max_dimension=70_000, max_pixels=10**10)
    info = inspect_image(jpeg(65535, 65535), lim)
    assert (info.width, info.height, info.ext) == (65535, 65535, "jpg")


@pytest.mark.parametrize(
    "w,h", [(2**32 - 1, 1), (1, 2**32 - 1), (2**31, 2**31), (2**32 - 1, 2**32 - 1)]
)
def test_png_ihdr_gigantic_dimensions_rejected(w: int, h: int) -> None:
    with pytest.raises(InvalidImageError):
        inspect_image(png(w, h))


LIM = UploadLimits(max_dimension=100, max_pixels=5000)


@pytest.mark.parametrize("make", [png, jpeg])
def test_exact_limits_pass_and_plus_one_fails(make: Any) -> None:
    assert inspect_image(make(100, 50), LIM)[2:] == (100, 50)  # lado == max e pixels == max
    assert inspect_image(make(50, 100), LIM)[2:] == (50, 100)
    for w, h in [(101, 1), (1, 101), (100, 51), (51, 100)]:
        with pytest.raises(InvalidImageError):
            inspect_image(make(w, h), LIM)


# --- inspect: truncamento em todos os pontos --------------------------------------------------


def test_every_truncated_jpeg_prefix_is_422_until_sof_is_complete() -> None:
    data = jpeg()
    sof_end = data.index(b"\xff\xc0") + 2 + 11
    inspect_image(data[:sof_end])  # o SOF completo basta
    for cut in range(1, sof_end):
        with pytest.raises(InvalidImageError):
            inspect_image(data[:cut])


def test_every_truncated_png_prefix() -> None:
    data = png()
    inspect_image(data[:29])
    with pytest.raises(InvalidImageError):
        inspect_image(b"")
    for cut in range(1, 8):  # assinatura incompleta: 415 ou 422, nunca sucesso nem outra exceção
        with pytest.raises(UploadError):
            inspect_image(data[:cut])
    for cut in range(8, 29):
        with pytest.raises(InvalidImageError):
            inspect_image(data[:cut])


def test_fuzz_never_raises_anything_but_upload_error() -> None:
    rnd = random.Random(20261028)
    seeds = [b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"\xff\xd8\xff\xe0", b"", b"\xff"]
    for _ in range(4000):
        body = bytes(rnd.choice((0xFF, 0xC0, 0xC4, 0xDA, 0x00, 0x11, rnd.randrange(256)))
                     for _ in range(rnd.randrange(0, 80)))  # fmt: skip
        data = rnd.choice(seeds) + body
        try:
            info = inspect_image(data)
        except UploadError:
            continue
        assert info.width > 0 and info.height > 0 and info.ext in {"jpg", "png"}


@pytest.mark.parametrize(
    "data",
    [
        pytest.param(b"\xff\xd8\xff<html><script>alert(1)</script></html>", id="jpeg-sig+html"),
        pytest.param(b"\xff\xd8\xff\xc4\x00\x04\x00\x00\xff\xd9", id="so-DHT-sem-SOF"),
        pytest.param(b"\xff\xd8\xff\xc8\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00", id="JPG-ext"),
        pytest.param(b"\xff\xd8\xff\xcc\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00", id="DAC"),
        pytest.param(b"\xff\xd8\xff\xc0\x00\x07\x08\x00\x01\x00\x01", id="SOF-curto"),
        pytest.param(b"\xff\xd8\xff\xe0\xff\xff" + b"A" * 100, id="APP-alem-do-fim"),
        pytest.param(b"\xff\xd8\xff\x00\x00\x00", id="marcador-00"),
    ],
)
def test_jpeg_garbage_is_422(data: bytes) -> None:
    with pytest.raises(InvalidImageError):
        inspect_image(data)


def test_jpeg_fill_bytes_and_restart_markers_before_sof() -> None:
    data = b"\xff\xd8\xff\xff\xff\xd0\xff\xd1\xff\xc0\x00\x0b\x08\x00\x07\x00\x09\x01\x01\x11\x00"
    assert inspect_image(data)[2:] == (9, 7)  # SOF: altura 7, largura 9


# --- inspect: tipo falso e polyglots ----------------------------------------------------------


@pytest.mark.parametrize(
    "data",
    [
        pytest.param(
            b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg"/>', id="svg-xml"
        ),
        pytest.param(b'<svg onload="alert(1)"></svg>', id="svg-bare"),
        pytest.param(b"<!DOCTYPE html><html></html>", id="html"),
        pytest.param(b"GIF87a\x01\x00\x01\x00", id="gif87"),
        pytest.param(b"GIF89a" + jpeg(), id="gif+jpeg"),
        pytest.param(b"\xef\xbb\xbf" + png(), id="bom+png"),
        pytest.param(b" \n" + png(), id="espaco+png"),
        pytest.param(b"\x00" + png(), id="nul+png"),
        pytest.param(b"<html>" + png(), id="html+png"),
        pytest.param(b"\x00" + jpeg(), id="nul+jpeg"),
        pytest.param(b"\x89PNG\r\n\x1a", id="sig-png-curta"),
        pytest.param(b"\x89png\r\n\x1a\n" + png()[8:], id="sig-png-minuscula"),
        pytest.param(b"\x89PNG\n\x1a\n\r" + png()[8:], id="sig-png-ordem-errada"),
        pytest.param(b"\x00" * 64, id="zeros"),
        pytest.param(b"%PDF-1.7\n", id="pdf"),
    ],
)
def test_fake_or_shifted_signature_is_415_or_422_never_accepted(data: bytes) -> None:
    with pytest.raises(UploadError) as ei:
        inspect_image(data)
    assert ei.value.status in (415, 422)


def test_signature_must_be_at_offset_zero_is_415() -> None:
    with pytest.raises(UnsupportedTypeError):
        inspect_image(b"<html>" + png())


def test_known_limit_valid_header_with_html_body_is_accepted_as_png() -> None:
    """Caracterização (H-F1-05-2): só o cabeçalho é validado; vale o tipo detectado."""
    info = inspect_image(png() + b"<html><script>alert(1)</script></html>")
    assert info.content_type == "image/png" and info.ext == "png"


# --- naming -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ext",
    ["JPG", "Jpg", " png", "png ", "png\n", "png\x00", "jpeg", "", "jpg/", ".jpg", "p\u0131ng"],
)
def test_key_extension_strict(ext: str) -> None:
    with pytest.raises(ValueError):
        build_object_key(1, "a" * 64, ext)


@pytest.mark.parametrize(
    "sha",
    [
        "a" * 64 + "\n",
        "\n" + "a" * 64,
        "a" * 63 + "g",
        "\u0661" * 64,
        "\uff41" * 64,
        "a" * 64 + "\x00",
        "",
    ],
)
def test_key_sha_strict(sha: str) -> None:
    with pytest.raises(ValueError):
        build_object_key(1, sha, "png")


@pytest.mark.parametrize("ds", ["1", 1.0, None, True, 0, -5, "1/../2", b"1"])
def test_key_dataset_must_be_a_positive_int(ds: Any) -> None:
    with pytest.raises(ValueError):
        build_object_key(ds, "a" * 64, "png")


def test_key_with_max_bigint_dataset() -> None:
    assert build_object_key(2**63 - 1, "0" * 64, "jpg") == f"datasets/{2**63 - 1}/{'0' * 64}.jpg"


@pytest.mark.parametrize(
    "raw",
    [
        "a\x00b.png",
        "\x00",
        "\x00\x00.png",
        "a\r\nb.png",
        "a\tb.png",
        "\x7f\x1b[31m.png",
        "x" * 5000 + ".png",
        "\U0001f600" * 400 + ".jpg",
        "e\u0301" * 300 + ".png",
        "a" * 254 + "e\u0301.png",
        "." * 300,
        "   ",
        "foo/",
        "foo\\",
        "/",
        "\\",
        "....//....//x.png",
        "..\\..\\x.png",
        "C:\\a\\b\\c.JPG",
        "%2e%2e%2fx.png",
        "\u202e" + "gnp.exe",
        "日本語/ファイル.png",
    ],
)
def test_sanitize_invariants(raw: str) -> None:
    out = sanitize_file_name(raw)
    assert out and out not in (".", "..")
    assert "/" not in out and "\\" not in out
    assert len(out) <= 255
    assert not any(unicodedata.category(c) == "Cc" for c in out)
    assert unicodedata.is_normalized("NFC", out)
    assert out == out.strip()
    assert sanitize_file_name(out) == out


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("a\x00b.png", "ab.png"),
        ("foo/", "imagem"),
        ("\x00", "imagem"),
        ("   ", "imagem"),
        ("....", "...."),
        ("e\u0301.png", "\u00e9.png"),
        ("pasta/sub\\x.png", "x.png"),
    ],
)
def test_sanitize_examples(raw: str, expected: str) -> None:
    assert sanitize_file_name(raw) == expected


def test_sanitize_long_name_keeps_extension() -> None:
    out = sanitize_file_name("x" * 5000 + ".png")
    assert len(out) == 255 and out.endswith(".png")


@pytest.mark.parametrize("bad", ["\u202e", "\u200b", "\u2028", "\u2029", "\ufeff", "\u2066"])
def test_sanitize_drops_invisible_format_and_separator_chars(bad: str) -> None:
    out = sanitize_file_name(f"foto{bad}gpj.exe")
    assert not any(unicodedata.category(c) in {"Cf", "Zl", "Zp"} for c in out)


# --- stream -----------------------------------------------------------------------------------


class Drip:
    """Fonte que devolve no máximo `step` bytes por leitura, síncrona ou assíncrona."""

    def __init__(self, data: bytes, step: int, *, is_async: bool = False, over: int = 0) -> None:
        self.buf = io.BytesIO(data)
        self.step = step
        self.is_async = is_async
        self.over = over  # fonte mal-comportada: devolve `over` bytes a mais que o pedido
        self.asked: list[int] = []

    def read(self, n: int) -> Any:
        self.asked.append(n)
        data = self.buf.read(min(n, self.step) + self.over)
        if self.is_async:

            async def _r() -> bytes:
                return data

            return _r()
        return data


@pytest.mark.parametrize("is_async", [False, True])
def test_short_reads_sync_and_async_hash_correctly(is_async: bool) -> None:
    data = bytes(range(256)) * 9
    res = run(read_limited(Drip(data, 1, is_async=is_async), 10_000, chunk=64))
    assert res.sha256 == hashlib.sha256(data).hexdigest() and res.size == len(data)
    assert res.header == data


@pytest.mark.parametrize("n", [0, 1, 63, 64, 65, 127, 128, 129])
def test_boundary_around_chunk_multiples(n: int) -> None:
    data = b"z" * n
    ok = run(read_limited(io.BytesIO(data), n, chunk=64))
    assert ok.size == n and ok.sha256 == hashlib.sha256(data).hexdigest()
    if n:
        with pytest.raises(TooLargeError):
            run(read_limited(io.BytesIO(data), n - 1, chunk=64))


def test_zero_limit_accepts_empty_and_rejects_one_byte() -> None:
    assert run(read_limited(io.BytesIO(b""), 0)).size == 0
    with pytest.raises(TooLargeError):
        run(read_limited(io.BytesIO(b"x"), 0))


def test_header_is_capped_at_one_mib_but_hash_covers_everything() -> None:
    data = b"\x01" * (HEADER_BYTES + 12345)
    res = run(read_limited(io.BytesIO(data), len(data)))
    assert len(res.header) == HEADER_BYTES and res.size == len(data)
    assert res.sha256 == hashlib.sha256(data).hexdigest()


def test_misbehaving_source_returning_more_than_requested_still_hits_limit() -> None:
    with pytest.raises(TooLargeError):
        run(read_limited(Drip(b"y" * 1000, 10, over=500), 100, chunk=10))


def test_never_asks_more_than_chunk_or_remaining_budget() -> None:
    src = Drip(b"q" * 10_000, 10_000)
    with pytest.raises(TooLargeError):
        run(read_limited(src, 150, chunk=64))
    assert max(src.asked) <= 64 and sum(src.asked) <= 150 + 1


# --- serviço ----------------------------------------------------------------------------------


@pytest.fixture
def env() -> tuple[FakeRepo, FakeStore, list[str]]:
    calls: list[str] = []
    return FakeRepo(calls), FakeStore(calls), calls


def test_file_positioned_at_end_is_still_uploaded_whole(env: Any) -> None:
    repo, store, _ = env
    data = jpeg(7, 5)
    f = io.BytesIO(data)
    f.seek(0, io.SEEK_END)
    (res,) = run(upload_images(repo, store, 1, [UploadItem("a.jpg", f)]))
    assert res.created and next(iter(store.objects.values()))[0] == data
    assert res.image.size_bytes == len(data)


def test_invalid_file_index_is_reported_for_each_position(env: Any) -> None:
    repo, store, _ = env
    for bad_at in range(3):
        files = [item(jpeg(i + 1, 1)) for i in range(3)]
        files[bad_at] = item(b"<html></html>", "x.jpg")
        with pytest.raises(UnsupportedTypeError) as ei:
            run(upload_images(repo, store, 1, files))
        assert ei.value.file_index == bad_at
    assert store.puts == 0 and not repo.rows


def test_too_large_second_file_rejects_whole_batch(env: Any) -> None:
    repo, store, _ = env
    lim = UploadLimits(max_bytes=len(png()) + 1)
    files = [item(png()), item(png() + b"\0\0"), item(png(2))]
    with pytest.raises(TooLargeError) as ei:
        run(upload_images(repo, store, 1, files, lim))
    assert ei.value.file_index == 1 and store.puts == 0 and not repo.rows


def test_exactly_max_bytes_is_accepted(env: Any) -> None:
    repo, store, _ = env
    lim = UploadLimits(max_bytes=len(png()))
    (res,) = run(upload_images(repo, store, 1, [item(png())], lim))
    assert res.created


def test_unknown_dataset_wins_over_file_validation(env: Any) -> None:
    repo, store, _ = env
    with pytest.raises(NotFoundError):
        run(upload_images(repo, store, 99, [item(b"nao e imagem")]))
    assert store.puts == 0


def test_ten_concurrent_identical_uploads(env: Any) -> None:
    repo, store, _ = env
    data = png(11, 13)

    async def go() -> list[Any]:
        return await asyncio.gather(
            *[upload_images(repo, store, 1, [item(data, f"n{i}.png")]) for i in range(10)]
        )

    results = [r[0] for r in run(go())]
    assert sum(r.created for r in results) == 1
    assert len({r.image.id for r in results}) == 1
    assert len(repo.rows) == 1 and len(store.objects) == 1
    assert len({r.image.object_key for r in results}) == 1


def test_concurrent_same_bytes_in_two_datasets_keep_separate_rows(env: Any) -> None:
    repo, store, _ = env

    async def go() -> list[Any]:
        return await asyncio.gather(
            *[upload_images(repo, store, ds, [item(png(3, 3))]) for ds in (1, 2, 1, 2)]
        )

    results = [r[0] for r in run(go())]
    assert sum(r.created for r in results) == 2
    assert {k[0] for k in repo.rows} == {1, 2} and len(store.objects) == 2


def test_concurrent_with_one_put_failure_leaves_consistent_state(env: Any) -> None:
    repo, store, _ = env
    data = png(5, 5)
    real_put = store.put
    first = True

    async def put(key: str, fileobj: Any, content_type: str) -> None:
        nonlocal first
        if first:  # a primeira chamada falha (FakeStore.fail_put_at conta após o await)
            first = False
            await asyncio.sleep(0)
            raise RuntimeError("boom " + SECRETS[0])
        await real_put(key, fileobj, content_type)

    store.put = put  # type: ignore[method-assign]

    async def go() -> list[Any]:
        return await asyncio.gather(
            *[upload_images(repo, store, 1, [item(data)]) for _ in range(4)], return_exceptions=True
        )

    out = run(go())
    errors = [o for o in out if isinstance(o, Exception)]
    ok = [o[0] for o in out if not isinstance(o, Exception)]
    assert len(errors) == 1 and errors[0].status == 503  # type: ignore[attr-defined]
    assert sum(r.created for r in ok) <= 1 and len(repo.rows) == 1 and len(store.objects) == 1


def test_batch_mixing_existing_and_new_only_puts_the_new(env: Any) -> None:
    repo, store, _ = env
    run(upload_images(repo, store, 1, [item(png(1, 1))]))
    res = run(upload_images(repo, store, 1, [item(png(1, 1)), item(png(2, 2))]))
    assert [r.created for r in res] == [False, True] and store.puts == 2


def test_compensation_lookup_failure_never_deletes_and_keeps_original_error(
    env: Any, caplog: pytest.LogCaptureFixture
) -> None:
    repo, store, calls = env
    repo.fail_insert = True
    original = repo.get_image_by_sha
    n = 0

    async def flaky(ds: int, sha: str) -> Image | None:
        nonlocal n
        n += 1
        if n >= 2:  # a releitura da compensação falha
            raise ConnectionResetError("reset " + SECRETS[3])
        return await original(ds, sha)

    repo.get_image_by_sha = flaky  # type: ignore[method-assign]
    with caplog.at_level(logging.DEBUG), pytest.raises(RuntimeError, match="db down"):
        run(upload_images(repo, store, 1, [item(jpeg())]))
    assert "delete" not in calls  # sem saber se há linha, nunca apagar (pode haver referência)
    assert len(store.objects) == 1
    assert not any(s in caplog.text for s in SECRETS)
    assert "ConnectionResetError" in caplog.text and "datasets/1/" in caplog.text


@pytest.mark.parametrize("fail_delete", [False, True])
def test_logs_have_no_tracebacks_or_client_names(
    env: Any, caplog: pytest.LogCaptureFixture, fail_delete: bool
) -> None:
    repo, store, _ = env
    repo.fail_insert = True
    store.fail_delete = fail_delete
    evil = "evil\r\nFORGED LOG LINE /home/u/secret.jpg"
    with caplog.at_level(logging.DEBUG), pytest.raises(RuntimeError):
        run(upload_images(repo, store, 1, [item(jpeg(), evil)]))
    assert caplog.records
    assert all(r.exc_info is None and r.exc_text is None for r in caplog.records)
    assert "FORGED" not in caplog.text and "/home/u" not in caplog.text
    assert not any(s in caplog.text for s in SECRETS)


def test_put_failure_exception_chain_does_not_carry_secrets(env: Any) -> None:
    repo, store, _ = env
    store.fail_put_at = 1
    with pytest.raises(UploadError) as ei:
        run(upload_images(repo, store, 1, [item(png())]))
    assert ei.value.__cause__ is None and ei.value.__context__ is None
    assert not any(s in str(ei.value) for s in SECRETS)
