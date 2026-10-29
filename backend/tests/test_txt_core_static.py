"""F1-07d critérios 27 e 32: configuração, compose e guardas estáticas da rota de rótulos txt."""

import ast
import re
from pathlib import Path

import pytest

from inovalytics.api.config import Settings

BASE = {"POSTGRES_PASSWORD": "p", "S3_ACCESS_KEY": "a", "S3_SECRET_KEY": "s"}


def test_yaml_limit_default_and_override() -> None:
    assert Settings.from_env(BASE).import_max_yaml_bytes == 65_536
    assert Settings.from_env({**BASE, "IMPORT_MAX_YAML_BYTES": ""}).import_max_yaml_bytes == 65_536
    assert Settings.from_env({**BASE, "IMPORT_MAX_YAML_BYTES": "10"}).import_max_yaml_bytes == 10


@pytest.mark.parametrize("bad", ["0", "-1", "abc", "1.5", "inf"])
def test_yaml_limit_rejects_bad_values_naming_only_the_variable(bad: str) -> None:
    with pytest.raises(ValueError) as ei:
        Settings.from_env({**BASE, "IMPORT_MAX_YAML_BYTES": bad})
    assert "IMPORT_MAX_YAML_BYTES" in str(ei.value) and bad not in str(ei.value).replace(
        "IMPORT_MAX_YAML_BYTES", ""
    )


def test_compose_and_env_example_carry_the_variable(repo_root: Path) -> None:
    compose = (repo_root / "docker-compose.yml").read_text("utf-8")
    assert "IMPORT_MAX_YAML_BYTES: ${IMPORT_MAX_YAML_BYTES:-65536}" in compose
    example = (repo_root / ".env.example").read_text("utf-8")
    assert "# IMPORT_MAX_YAML_BYTES=65536\n" in example
    assert not re.search(r"^IMPORT_MAX_YAML_BYTES=", example, re.M)  # só comentada


def test_new_modules_forbid_regex_and_yaml_libs(repo_root: Path) -> None:
    base = repo_root / "backend/src/inovalytics/imports"
    for name in ("names_yaml.py", "txt_labels_parser.py", "txt_labels_service.py"):
        for node in ast.walk(ast.parse((base / name).read_text("utf-8"))):
            mods = []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                mods = [node.module or ""]
            for m in mods:
                assert m.split(".")[0] not in {"re", "yaml", "ruamel"}, (name, m)


def test_yolo_only_in_route_path_and_docs(repo_root: Path) -> None:
    """Identificadores, módulos, códigos e mensagens em backend/src não contêm 'yolo'; a única
    ocorrência permitida é o literal do caminho da rota (e comentários/docstrings que a citem)."""
    src = repo_root / "backend/src/inovalytics"
    allowed_literals = {"/datasets/{dataset_id}/imports/yolo-txt"}
    for path in src.rglob("*.py"):
        assert "yolo" not in path.name.lower(), path
        tree = ast.parse(path.read_text("utf-8"))
        docstrings = {
            id(n.body[0].value)
            for n in ast.walk(tree)
            if isinstance(n, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
            and n.body
            and isinstance(n.body[0], ast.Expr)
            and isinstance(n.body[0].value, ast.Constant)
        }
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Name):
                names = [node.id]
            elif isinstance(node, ast.Attribute):
                names = [node.attr]
            elif isinstance(node, ast.arg):
                names = [node.arg]
            elif isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
                names = [node.name]
            elif isinstance(node, ast.alias):
                names = [node.name, node.asname or ""]
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                if id(node) in docstrings:
                    continue
                v = node.value
                if "yolo" in v.lower():
                    assert v in allowed_literals, (path.name, v)
            for n in names:
                assert "yolo" not in n.lower(), (path.name, n)
            if (
                isinstance(node, ast.alias | ast.ImportFrom)
                and "yolo" in (getattr(node, "module", "") or "").lower()
            ):
                raise AssertionError(path.name)


def test_route_exposes_yolo_txt_path() -> None:
    from inovalytics.api.main import create_app

    paths = {getattr(r, "path", "") for r in create_app(Settings(postgres_password="x")).routes}
    assert "/datasets/{dataset_id}/imports/yolo-txt" in paths
