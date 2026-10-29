"""F1-08 guardas estáticas (AST) dos módulos novos do importador Pascal VOC."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

FORBIDDEN_FOR_READER = {
    "xml.etree",
    "xml.dom",
    "xml.sax",
    "xml.parsers.expat.errors",  # só o pacote expat é usado
    "lxml",
    "defusedxml",
    "re",
    "yaml",
    "socket",
    "urllib",
    "http",
    "requests",
    "subprocess",
    "os",
    "pathlib",
    "asyncpg",
    "boto3",
    "ult" + "ralytics",
}
ALLOWED_FOR_READER = {"__future__", "dataclasses", "typing", "xml.parsers"}


def _imports(path: Path) -> list[str]:
    mods: list[str] = []
    for node in ast.walk(ast.parse(path.read_text("utf-8"))):
        if isinstance(node, ast.Import):
            mods += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            mods.append(base)
            mods += [f"{base}.{a.name}" for a in node.names]
    return mods


def _src(repo_root: Path, name: str) -> Path:
    return repo_root / "backend/src/inovalytics/imports" / name


def test_xml_reader_imports_only_stdlib_and_expat(repo_root: Path) -> None:
    mods = _imports(_src(repo_root, "xml_reader.py"))
    for m in mods:
        assert not any(m == f or m.startswith(f + ".") for f in FORBIDDEN_FOR_READER), m
    top_level = {m for m in mods if "." not in m or m.startswith("xml.parsers")}
    assert {m.split(".")[0] if not m.startswith("xml") else "xml.parsers" for m in top_level} <= (
        ALLOWED_FOR_READER
    )
    assert "xml.parsers.expat" in mods or "xml.parsers" in mods


def test_xml_reader_has_no_inovalytics_imports(repo_root: Path) -> None:
    assert not [
        m for m in _imports(_src(repo_root, "xml_reader.py")) if m.startswith("inovalytics")
    ]


def test_xml_reader_does_not_touch_banned_apis(repo_root: Path) -> None:
    tree = ast.parse(_src(repo_root, "xml_reader.py").read_text("utf-8"))
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert not names & {"open", "eval", "exec", "compile", "__import__"}
    assert not attrs & {"etree", "minidom", "sax", "feed", "ParseFile", "SetBase"}


def test_xml_reader_calls_parse_once_and_final(repo_root: Path) -> None:
    tree = ast.parse(_src(repo_root, "xml_reader.py").read_text("utf-8"))
    calls = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "Parse"
    ]
    assert len(calls) == 1
    assert isinstance(calls[0].args[1], ast.Constant) and calls[0].args[1].value is True


@pytest.mark.parametrize("name", ["pyproject.toml", "docs/licencas.md"])
def test_no_new_xml_dependency_registered(repo_root: Path, name: str) -> None:
    text = (repo_root / name).read_text("utf-8").lower()
    assert "defusedxml" not in text and "lxml" not in text
