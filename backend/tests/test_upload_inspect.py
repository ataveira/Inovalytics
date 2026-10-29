"""F1-05a critérios 1 a 5: inspeção de JPEG/PNG sem Pillow."""

import time

import pytest

from _imgbytes import jpeg, png
from inovalytics.uploads.errors import InvalidImageError, UnsupportedTypeError
from inovalytics.uploads.inspect import inspect_image
from inovalytics.uploads.limits import UploadLimits


@pytest.mark.parametrize("sof", [0xC0, 0xC1, 0xC2, 0xC3])
def test_jpeg_sof_variants(sof: int) -> None:
    assert tuple(inspect_image(jpeg(640, 480, sof))) == ("image/jpeg", "jpg", 640, 480)


def test_jpeg_with_long_exif_before_sof() -> None:
    assert inspect_image(jpeg(10, 20, exif=60000)).width == 10


@pytest.mark.parametrize("color,depth", [(0, 1), (0, 16), (2, 8), (2, 16), (3, 4), (4, 8), (6, 16)])
def test_png_valid_color_types(color: int, depth: int) -> None:
    assert tuple(inspect_image(png(7, 9, depth, color))) == ("image/png", "png", 7, 9)


FAKES = {
    "texto": b"hello world, isto nao e imagem",
    "html": b"<!doctype html><html></html>",
    "gif": b"GIF89a\x01\x00\x01\x00",
    "webp": b"RIFF\x24\x00\x00\x00WEBPVP8 ",
    "bmp": b"BM\x1e\x00\x00\x00\x00\x00",
    "pdf": b"%PDF-1.7\n",
    "zip": b"PK\x03\x04\x14\x00",
    "svg": b"<svg xmlns='http://www.w3.org/2000/svg'/>",
    "exe": b"MZ\x90\x00\x03\x00",
}


@pytest.mark.parametrize("data", FAKES.values(), ids=FAKES.keys())
def test_fake_types_are_415(data: bytes) -> None:
    with pytest.raises(UnsupportedTypeError) as ei:
        inspect_image(data)
    assert ei.value.status == 415


def test_empty_is_422_not_415() -> None:
    with pytest.raises(InvalidImageError) as ei:
        inspect_image(b"")
    assert ei.value.status == 422


def _bad_png() -> list[bytes]:
    good = png()
    return [
        good[:8] + b"\x00\x00\x00\x0dIDAT" + good[16:],  # sem IHDR
        png(ihdr_len=12),
        png(0, 5),
        png(5, 0),
        png(5, 5, 8, 5),  # tipo de cor
        png(5, 5, 3, 2),  # profundidade
        good[:20],  # truncado
    ]


@pytest.mark.parametrize("data", _bad_png())
def test_png_bad_structure(data: bytes) -> None:
    with pytest.raises(InvalidImageError):
        inspect_image(data)


def _bad_jpeg() -> list[bytes]:
    base = b"\xff\xd8\xff\xe0"
    return [
        jpeg()[:12],  # truncado antes do SOF
        base + b"\x00\x00" + b"\x00" * 20,  # comprimento 0
        base + b"\x00\x01" + b"\x00" * 20,  # comprimento 1
        base + b"\xff\xff" + b"\x00" * 20,  # além do fim
        jpeg(0, 5),
        jpeg(5, 0),
        b"\xff\xd8\xff\xda\x00\x02",  # SOS sem SOF
        b"\xff\xd8",  # só SOI
    ]


@pytest.mark.parametrize("data", _bad_jpeg())
def test_jpeg_bad_structure(data: bytes) -> None:
    with pytest.raises(InvalidImageError):
        inspect_image(data)


def test_dimension_and_pixel_limits() -> None:
    lim = UploadLimits(max_dimension=100, max_pixels=5000)
    assert inspect_image(png(100, 50), lim).width == 100  # exatamente no limite
    assert inspect_image(jpeg(50, 100), lim).height == 100
    with pytest.raises(InvalidImageError, match="dimensão"):
        inspect_image(png(101, 1), lim)
    with pytest.raises(InvalidImageError, match="pixels"):
        inspect_image(png(100, 51), lim)
    with pytest.raises(InvalidImageError):
        inspect_image(jpeg(1, 101), lim)


def test_png_bomb_header_rejected_without_big_allocation() -> None:
    data = png(65535, 65535)
    assert len(data) < 100
    with pytest.raises(InvalidImageError):
        inspect_image(data)


def test_adversarial_megabyte_of_markers_terminates() -> None:
    data = b"\xff\xd8\xff" + (b"\xff\xe0\x00\x02" * (1024 * 1024 // 4))
    t = time.perf_counter()
    with pytest.raises(InvalidImageError):
        inspect_image(data)
    assert time.perf_counter() - t < 5


def test_never_reads_beyond_one_mib() -> None:
    pad = b"\xff\xe1" + (65533).to_bytes(2, "big") + b"x" * 65531
    data = b"\xff\xd8\xff" + pad * 17  # >1 MiB de APP1; o SOF válido ficaria depois do limite
    data += b"\xff\xc0" + (11).to_bytes(2, "big") + b"\x08\x00\x03\x00\x04\x01\x01\x11\x00"
    assert len(data) > 1024 * 1024
    with pytest.raises(InvalidImageError):
        inspect_image(data)
