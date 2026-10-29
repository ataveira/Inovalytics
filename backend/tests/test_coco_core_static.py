"""F1-06c critério 29 (Settings, .env.example, compose) e guardas do 36."""

import ast
import re
from pathlib import Path

import pytest

from inovalytics.api.config import Settings

BASE = {"POSTGRES_PASSWORD": "p", "S3_ACCESS_KEY": "a", "S3_SECRET_KEY": "s"}
NAMES = [
    "IMPORT_MAX_JSON_BYTES",
    "IMPORT_MAX_IMAGES",
    "IMPORT_MAX_ANNOTATIONS",
    "IMPORT_MAX_CATEGORIES",
    "IMPORT_MAX_DEPTH",
    "IMPORT_MAX_ERRORS",
]


def test_defaults_and_overrides() -> None:
    s = Settings.from_env(BASE)
    assert (s.import_max_json_bytes, s.import_max_images) == (32 * 1024 * 1024, 50_000)
    assert (s.import_max_annotations, s.import_max_categories) == (300_000, 1_000)
    assert (s.import_max_depth, s.import_max_errors) == (12, 100)
    env = {**BASE, **{n: str(i + 1) for i, n in enumerate(NAMES)}}
    s = Settings.from_env(env)
    assert (s.import_max_json_bytes, s.import_max_images, s.import_max_annotations) == (1, 2, 3)
    assert (s.import_max_categories, s.import_max_depth, s.import_max_errors) == (4, 5, 6)


@pytest.mark.parametrize("name", NAMES)
def test_empty_is_default(name: str) -> None:
    assert Settings.from_env({**BASE, name: ""}) == Settings.from_env(BASE)


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("bad", ["0", "-1", "abc", "inf", "nan", "1.5"])
def test_invalid_values_cite_only_name(name: str, bad: str) -> None:
    with pytest.raises(ValueError) as ei:
        Settings.from_env({**BASE, name: bad})
    assert name in str(ei.value) and bad not in str(ei.value).replace(name, "")


def test_env_example_lists_import_vars(repo_root: Path) -> None:
    text = (repo_root / ".env.example").read_text("utf-8")
    for n in NAMES:
        assert re.search(rf"^# {n}=\d+$", text, re.M), n


def test_compose_forwards_import_vars_with_the_defaults(repo_root: Path) -> None:
    text = (repo_root / "docker-compose.yml").read_text("utf-8")
    d = Settings()
    expected = {
        "IMPORT_MAX_JSON_BYTES": d.import_max_json_bytes,
        "IMPORT_MAX_IMAGES": d.import_max_images,
        "IMPORT_MAX_ANNOTATIONS": d.import_max_annotations,
        "IMPORT_MAX_CATEGORIES": d.import_max_categories,
        "IMPORT_MAX_DEPTH": d.import_max_depth,
        "IMPORT_MAX_ERRORS": d.import_max_errors,
    }
    for name, default in expected.items():
        assert f"{name}: ${{{name}:-{default}}}" in text, name
    example = (repo_root / ".env.example").read_text("utf-8")
    for name, default in expected.items():
        assert f"# {name}={default}\n" in example, name


def _db_importers(root: Path) -> set[str]:
    found: set[str] = set()
    for path in (root / "backend/src/inovalytics/api").glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text("utf-8"))):
            if isinstance(node, ast.ImportFrom):
                mods = [node.module or ""] + [f"{node.module}.{a.name}" for a in node.names]
            elif isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            else:
                continue
            if any(m.startswith("inovalytics.db") for m in mods):
                found.add(path.name)
    return found


def test_only_images_and_imports_import_db(repo_root: Path) -> None:
    assert _db_importers(repo_root) == {"images.py", "imports.py"}


def test_imports_route_does_not_touch_s3(repo_root: Path) -> None:
    tree = ast.parse((repo_root / "backend/src/inovalytics/api/imports.py").read_text("utf-8"))
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            mods |= {node.module or ""} | {f"{node.module}.{a.name}" for a in node.names}
    assert not {m for m in mods if m.split(".")[0] in {"boto3", "botocore", "ultralytics"}}
    assert not {m for m in mods if m.startswith("inovalytics.uploads.store")}
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    assert "ObjectStore" not in names
