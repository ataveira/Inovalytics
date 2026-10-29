"""F1-08d critério 28: configuração e compose do limite por XML (testes estáticos)."""

import re
from pathlib import Path

import pytest

from inovalytics.api.config import Settings

BASE = {"POSTGRES_PASSWORD": "p", "S3_ACCESS_KEY": "a", "S3_SECRET_KEY": "s"}
NAME = "IMPORT_MAX_XML_BYTES"


def test_xml_limit_default_and_override() -> None:
    assert Settings.from_env(BASE).import_max_xml_bytes == 262_144
    assert Settings.from_env({**BASE, NAME: ""}).import_max_xml_bytes == 262_144
    assert Settings.from_env({**BASE, NAME: "10"}).import_max_xml_bytes == 10


@pytest.mark.parametrize("bad", ["0", "-1", "abc", "1.5", "inf"])
def test_xml_limit_rejects_bad_values_naming_only_the_variable(bad: str) -> None:
    with pytest.raises(ValueError) as ei:
        Settings.from_env({**BASE, NAME: bad})
    assert NAME in str(ei.value) and bad not in str(ei.value).replace(NAME, "")


def test_compose_and_env_example_carry_the_variable(repo_root: Path) -> None:
    compose = (repo_root / "docker-compose.yml").read_text("utf-8")
    assert f"{NAME}: ${{{NAME}:-262144}}" in compose
    example = (repo_root / ".env.example").read_text("utf-8")
    assert f"# {NAME}=262144\n" in example
    assert not re.search(rf"^{NAME}=", example, re.M)  # só comentada


def test_api_image_and_dependencies_unchanged_by_this_task(repo_root: Path) -> None:
    dockerfile = (repo_root / "docker/api/Dockerfile").read_text("utf-8").lower()
    assert "defusedxml" not in dockerfile and "lxml" not in dockerfile
    pyproject = (repo_root / "pyproject.toml").read_text("utf-8").lower()
    assert "defusedxml" not in pyproject and "lxml" not in pyproject


def test_no_forbidden_names_in_the_voc_modules(repo_root: Path) -> None:
    """Nenhum identificador, nome de módulo, código de erro ou mensagem cita marca de terceiros."""
    import ast

    src = repo_root / "backend/src/inovalytics"
    mods = [src / "imports" / n for n in ("xml_reader.py", "voc_parser.py", "voc_service.py")]
    mods.append(src / "api" / "imports.py")
    for path in mods:
        tree = ast.parse(path.read_text("utf-8"))
        doc_ids = {
            id(n.body[0].value)
            for n in ast.walk(tree)
            if isinstance(n, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
            and n.body
            and isinstance(n.body[0], ast.Expr)
            and isinstance(n.body[0].value, ast.Constant)
        }
        for node in ast.walk(tree):
            texts: list[str] = []
            if isinstance(node, ast.Name):
                texts = [node.id]
            elif isinstance(node, ast.Attribute):
                texts = [node.attr]
            elif isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
                texts = [node.name]
            elif (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and id(node) not in doc_ids
            ):
                texts = [node.value]
            for t in texts:
                low = t.lower()
                if path.name == "imports.py" and t == "/datasets/{dataset_id}/imports/yolo-txt":
                    continue  # rota da F1-07 (ADR-009)
                assert "ultralytics" not in low, (path.name, t)
                assert "yolo" not in low, (path.name, t)
