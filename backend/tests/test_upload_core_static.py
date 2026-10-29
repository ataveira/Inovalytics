"""F1-05a critérios 9 e 10: Settings UPLOAD_* e imports proibidos no pacote `uploads`."""

import ast
from pathlib import Path

import pytest

from inovalytics.api.config import Settings

BASE = {"POSTGRES_PASSWORD": "p", "S3_ACCESS_KEY": "a", "S3_SECRET_KEY": "s"}
NAMES = ["UPLOAD_MAX_BYTES", "UPLOAD_MAX_FILES", "UPLOAD_MAX_DIMENSION", "UPLOAD_MAX_PIXELS"]


def test_defaults_and_overrides() -> None:
    s = Settings.from_env(BASE)
    assert (s.upload_max_bytes, s.upload_max_files) == (20 * 1024 * 1024, 20)
    assert (s.upload_max_dimension, s.upload_max_pixels) == (16384, 100_000_000)
    env = {**BASE, "UPLOAD_MAX_BYTES": "5", "UPLOAD_MAX_FILES": "2",
           "UPLOAD_MAX_DIMENSION": "7", "UPLOAD_MAX_PIXELS": "9"}  # fmt: skip
    s = Settings.from_env(env)
    assert (s.upload_max_bytes, s.upload_max_files, s.upload_max_dimension) == (5, 2, 7)
    assert s.upload_max_pixels == 9


@pytest.mark.parametrize("name", NAMES)
def test_empty_is_default(name: str) -> None:
    assert Settings.from_env({**BASE, name: ""}) == Settings.from_env(BASE)


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("bad", ["0", "-1", "abc", "inf", "nan", "1.5"])
def test_invalid_values_cite_only_name(name: str, bad: str) -> None:
    with pytest.raises(ValueError) as ei:
        Settings.from_env({**BASE, name: bad})
    assert name in str(ei.value) and bad not in str(ei.value).replace(name, "")


def test_env_example_lists_upload_vars(repo_root: Path) -> None:
    text = (repo_root / ".env.example").read_text("utf-8")
    for n in NAMES:
        assert f"# {n}=" in text


BANNED = {"ultralytics", "PIL", "socket", "subprocess", "fastapi", "starlette", "asyncpg"}


def test_uploads_package_imports(repo_root: Path) -> None:
    files = list((repo_root / "backend/src/inovalytics/uploads").glob("*.py"))
    assert len(files) >= 7
    for path in files:
        for node in ast.walk(ast.parse(path.read_text("utf-8"))):
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                mods = [node.module or ""]
            else:
                continue
            assert not {m.split(".")[0] for m in mods} & BANNED, path.name
