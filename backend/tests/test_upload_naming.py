"""F1-05a critérios 6 e 7: chave S3 e nome de arquivo."""

import pytest

from inovalytics.uploads.naming import build_object_key, sanitize_file_name

SHA = "ab" * 32


def test_key_ok() -> None:
    assert build_object_key(5, SHA, "jpg") == f"datasets/5/{SHA}.jpg"
    assert build_object_key(5, SHA, "png").endswith(".png")


@pytest.mark.parametrize("ds", [0, -1, "1", "../x", 1.0, True, None])
def test_key_bad_dataset(ds: object) -> None:
    with pytest.raises(ValueError):
        build_object_key(ds, SHA, "jpg")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "sha",
    ["../x", "a/b", "a\\b", "%2e%2e", "\x00" * 64, "é" * 64, SHA.upper(), SHA[:-1], SHA + "a",
     SHA + "\n", "g" * 64],
)  # fmt: skip
def test_key_bad_sha(sha: str) -> None:
    with pytest.raises(ValueError):
        build_object_key(1, sha, "jpg")


@pytest.mark.parametrize("ext", ["jsp", "php", "jpg/../x", "JPG", "", "jpeg", "jpg\x00"])
def test_key_bad_ext(ext: str) -> None:
    with pytest.raises(ValueError):
        build_object_key(1, SHA, ext)


@pytest.mark.parametrize("name", ["../../etc/passwd", "a b.jpg", "é.png", "x\x00.jpg", "..\\..\\x"])
def test_client_name_never_in_key(name: str) -> None:
    key = build_object_key(1, SHA, "jpg")
    assert name not in key and key.count("/") == 2


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("../../etc/passwd", "passwd"),
        ("C:\\x\\foto.JPG", "foto.JPG"),
        ("a\x00b\x07c\n.png", "abc.png"),
        ("cafe\u0301.jpg", "caf\u00e9.jpg"),
        ("", "imagem"),
        (".", "imagem"),
        ("..", "imagem"),
        ("a/..", "imagem"),
        ("  x.png  ", "x.png"),
        ("dir/", "imagem"),
    ],
)
def test_sanitize(raw: str, expected: str) -> None:
    assert sanitize_file_name(raw) == expected


def test_sanitize_truncates_keeping_extension() -> None:
    out = sanitize_file_name("a" * 400 + ".jpg")
    assert len(out) == 255 and out.endswith(".jpg")
    assert len(sanitize_file_name("b" * 400)) == 255


@pytest.mark.parametrize(
    "raw",
    ["../a.png", "e\u0301" * 300 + ".jpg", "x" * 300, " . ", "\x00", "a" * 254 + "\u0301.png"],
)
def test_sanitize_idempotent(raw: str) -> None:
    once = sanitize_file_name(raw)
    assert sanitize_file_name(once) == once
