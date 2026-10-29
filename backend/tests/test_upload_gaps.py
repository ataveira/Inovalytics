"""F1-05: lacunas residuais de borda (JPEG com APPn máximos, polyglots, nomes e ids hostis)."""

import struct

import pytest

from _imgbytes import jpeg, png
from inovalytics.uploads.errors import InvalidImageError, UnsupportedTypeError
from inovalytics.uploads.inspect import inspect_image
from inovalytics.uploads.limits import HEADER_BYTES
from inovalytics.uploads.naming import sanitize_file_name

pytestmark = pytest.mark.usefixtures("no_external_network")


def _sof(w: int, h: int) -> bytes:
    return b"\xff\xc0" + struct.pack(">HBHHB", 11, 8, h, w, 1) + b"\x01\x11\x00"


def test_sof_after_many_maximal_appn_segments_within_header_is_read() -> None:
    app = b"\xff\xe1" + (65535).to_bytes(2, "big") + b"E" * 65533
    n = (HEADER_BYTES - 100) // len(app)
    data = b"\xff\xd8\xff\xe0\x00\x02" + app * n + _sof(7, 5)
    assert len(data) < HEADER_BYTES
    info = inspect_image(data)
    assert (info.width, info.height) == (7, 5)


def test_sof_beyond_header_limit_is_422() -> None:
    app = b"\xff\xe1" + (65535).to_bytes(2, "big") + b"E" * 65533
    data = b"\xff\xd8\xff\xe0\x00\x02" + app * (HEADER_BYTES // len(app) + 1) + _sof(7, 5)
    assert len(data) > HEADER_BYTES
    with pytest.raises(InvalidImageError):
        inspect_image(data)


def test_jpeg_header_followed_by_png_signature_is_judged_as_jpeg() -> None:
    with pytest.raises(InvalidImageError):
        inspect_image(b"\xff\xd8\xff" + png())


def test_png_polyglot_with_trailing_jpeg_is_png() -> None:
    assert inspect_image(png(5, 6) + jpeg(1, 1)).ext == "png"


@pytest.mark.parametrize("data", [b"\xff", b"\xff\xd8", b"\x89PNG", b"\x89PNG\r\n\x1a\n"])
def test_signature_only_prefixes_never_become_valid(data: bytes) -> None:
    with pytest.raises((InvalidImageError, UnsupportedTypeError)):
        inspect_image(data)


@pytest.mark.parametrize("w,h", [(0, 5), (5, 0), (0, 0)])
def test_jpeg_zero_dimensions_422(w: int, h: int) -> None:
    with pytest.raises(InvalidImageError):
        inspect_image(jpeg(w, h))


@pytest.mark.parametrize(
    "name",
    [
        "..",
        "../..",
        "....//",
        "‮.png",
        "\x00../x",
        "%2e%2e/x.png",
        "a/../../b",
        "\\\\srv\\sh\\f.png",
    ],
)
def test_sanitized_names_have_no_separators_or_controls(name: str) -> None:
    out = sanitize_file_name(name)
    assert out and "/" not in out and "\\" not in out and "\x00" not in out
    assert out not in (".", "..")
