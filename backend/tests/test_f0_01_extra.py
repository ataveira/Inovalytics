"""Testes complementares da F0-01: critérios 3, 6, 7 e 8, casos de borda e entradas inválidas."""

import importlib.metadata as md
import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

import test_licencas as lic
import test_no_secrets as sec
import test_no_ultralytics as ultra

ULTRA = "ultra" + "lytics"  # evita que o grep do make check case com este arquivo
PW = "pass" + "word"
TK = "tok" + "en"


# ---------- Critério 3: ordem e falha do make check ----------


def _make(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["make", f"PYTHON={sys.executable}", *args],
        cwd=root,
        env={**os.environ, "INOVALYTICS_NESTED": "1"},
        capture_output=True,
        text=True,
    )


def test_check_order_in_makefile(repo_root: Path) -> None:
    out = _make(repo_root, "-n", "check").stdout
    steps = ["ruff check", "ruff format", "mypy", "pytest", "run lint", "run test"]
    pos = [out.find(s) for s in steps]
    assert all(p >= 0 for p in pos), pos
    assert pos == sorted(pos), pos


@pytest.fixture
def repo_copy(repo_root: Path, tmp_path: Path) -> Path:
    if os.environ.get("INOVALYTICS_NESTED"):
        pytest.skip("execução aninhada de make check: evita recursão")
    dst = tmp_path / "repo"
    shutil.copytree(
        repo_root,
        dst,
        ignore=shutil.ignore_patterns(
            ".git", ".venv", "node_modules", "__pycache__", ".*_cache", "*.egg-info"
        ),
    )
    return dst


@pytest.mark.parametrize(
    ("code", "tool"),
    [
        ("import os\n", "ruff"),  # F401: import não usado
        ("def f(x):\n    return x\n", "mypy"),  # strict: sem anotações
        ('x: int = "a"\n', "mypy"),  # erro de tipo
    ],
)
def test_check_fails_on_injected_error(repo_copy: Path, code: str, tool: str) -> None:
    (repo_copy / "backend/src/inovalytics/bad.py").write_text(code, encoding="utf-8")
    res = _make(repo_copy, "check")
    assert res.returncode != 0
    assert tool in (res.stdout + res.stderr).lower()


def test_check_fails_on_unformatted_code(repo_copy: Path) -> None:
    (repo_copy / "backend/src/inovalytics/fmt.py").write_text("x   =  1\n", encoding="utf-8")
    assert _make(repo_copy, "lint-backend").returncode != 0


def test_check_rejects_ultralytics_import(repo_copy: Path) -> None:
    (repo_copy / "backend/src/inovalytics/u.py").write_text(
        "import " + ULTRA + "  # noqa\n", encoding="utf-8"
    )
    res = _make(repo_copy, "check")
    assert res.returncode != 0
    assert "ultralytics" in (res.stdout + res.stderr).lower()


def test_make_targets_exist(repo_root: Path) -> None:
    for target in ("test", "lint", "check"):
        assert _make(repo_root, "-n", target).returncode == 0, target


def test_mypy_is_strict_on_backend_src(repo_root: Path) -> None:
    cfg = tomllib.loads((repo_root / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["mypy"]
    assert cfg["strict"] is True
    assert "backend/src" in cfg["files"]


def test_dev_extra_has_required_tools(repo_root: Path) -> None:
    data = tomllib.loads((repo_root / "pyproject.toml").read_text(encoding="utf-8"))
    dev = " ".join(data["project"]["optional-dependencies"]["dev"]).lower()
    for tool in ("pytest", "ruff", "mypy"):
        assert tool in dev


def test_project_license_not_presumed(repo_root: Path) -> None:
    proj = tomllib.loads((repo_root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert "license" not in proj
    assert not any(c.startswith("License ::") for c in proj.get("classifiers", []))


# ---------- Critério 6: licenciamento ----------


@pytest.mark.parametrize(
    "line",
    [
        f"import {ULTRA}",
        f"from {ULTRA} import YOLO",
        f"  import {ULTRA.title()}",
        f"from {ULTRA}.x import y",
    ],
)
def test_import_regex_detects(line: str) -> None:
    assert ultra.IMPORT_RE.search(line)


@pytest.mark.parametrize("line", [f"import {ULTRA}foo", f"# {ULTRA} em comentário", "x = 1"])
def test_import_regex_ignores_unrelated(line: str) -> None:
    assert not ultra.IMPORT_RE.search(line)


def test_ultralytics_not_installed_transitively() -> None:
    names = {(d.metadata["Name"] or "").lower() for d in md.distributions()}
    assert "ultralytics" not in names
    assert "ultralytics-thop" not in names
    for d in md.distributions():
        if d.metadata["Name"] and d.metadata["Name"].lower() in {"pytest", "ruff", "mypy"}:
            assert not any("ultralytics" in r.lower() for r in (d.requires or []))


def test_package_lock_has_no_ultralytics(repo_root: Path) -> None:
    lock = repo_root / "frontend" / "package-lock.json"
    if lock.exists():
        assert "ultralytics" not in lock.read_text(encoding="utf-8").lower()


def test_no_ultralytics_in_scan_targets_exist(repo_root: Path) -> None:
    targets = ultra._targets(repo_root)
    assert repo_root / "pyproject.toml" in targets
    assert all(t.is_file() for t in targets)


# ---------- Critério 7: licenças ----------


def test_no_stale_rows_in_licencas(repo_root: Path) -> None:
    text = (repo_root / "docs" / "licencas.md").read_text(encoding="utf-8")
    section = text.split("## Imagens de contêiner", 1)[-1].split("\n## ", 1)[0]
    images = {lic._norm(m.group(1)) for m in map(lic.ROW_RE.match, section.splitlines()) if m}
    actions_section = text.split("## GitHub Actions", 1)[-1].split("\n## ", 1)[0]
    actions = {
        lic._norm(m.group(1)) for m in map(lic.ROW_RE.match, actions_section.splitlines()) if m
    }
    stale = lic._rows(repo_root).keys() - lic._declared(repo_root) - images - actions
    # typescript e @eslint/js etc. são declarados; qualquer linha sobrando é lixo.
    assert not stale, stale


def test_licencas_versions_match_installed(repo_root: Path) -> None:
    rows = lic._rows(repo_root)
    # Compara só o major: releases novas não devem quebrar o teste.
    for pkg in ("pytest", "ruff", "mypy"):
        documented = rows[pkg][0].split(".")[0]
        installed = md.version(pkg).split(".")[0]
        assert documented == installed, (pkg, rows[pkg][0], md.version(pkg))


def test_licencas_versions_satisfy_package_json(repo_root: Path) -> None:
    pkg = json.loads((repo_root / "frontend" / "package.json").read_text(encoding="utf-8"))
    rows = lic._rows(repo_root)
    for name, spec in pkg["devDependencies"].items():
        major = re.search(r"\d+", spec)
        assert major, spec
        assert rows[lic._norm(name)][0].split(".")[0] == major.group(0), name


def test_licencas_versions_match_lockfile(repo_root: Path) -> None:
    lock = json.loads((repo_root / "frontend" / "package-lock.json").read_text(encoding="utf-8"))
    rows = lic._rows(repo_root)
    for name in json.loads((repo_root / "frontend" / "package.json").read_text())[
        "devDependencies"
    ]:
        locked = lock["packages"][f"node_modules/{name}"]["version"]
        assert rows[lic._norm(name)][0] == locked, name


def test_licencas_permissive_dev_tools(repo_root: Path) -> None:
    rows = lic._rows(repo_root)
    for tool in ("pytest", "ruff", "mypy", "vitest", "eslint"):
        assert rows[tool][1] == "MIT", tool


@pytest.mark.parametrize("bad", ["", "GNU GPL", "MIT OR", "MIT;Apache", "AGPL-3.0"])
def test_spdx_regex_rejects_invalid(bad: str) -> None:
    ok = bool(lic.SPDX_RE.match(bad)) and not any(f in bad.upper() for f in lic.FORBIDDEN)
    assert not ok


@pytest.mark.parametrize("good", ["MIT", "Apache-2.0", "MIT OR Apache-2.0", "BSD-3-Clause"])
def test_spdx_regex_accepts_valid(good: str) -> None:
    assert lic.SPDX_RE.match(good)


def test_dependency_name_normalization() -> None:
    assert lic._norm("Typescript_ESLint") == "typescript-eslint"
    assert lic._norm("ruff") == lic._norm("RUFF")


# ---------- Critério 8: segredos ----------


@pytest.mark.parametrize(
    "text",
    [
        PW + "=hunter2",
        "POSTGRES_" + PW.upper() + "=s3cr3tvalue",
        "api_" + "key: 'abcd1234'",
        "MINIO_ROOT_" + PW.upper() + " = realpass",
        TK + '="abcdef"',
        "AWS_SECRET_ACCESS_" + "KEY=abcd1234",
    ],
)
def test_secret_regex_detects_real_values(text: str) -> None:
    m = sec.SECRET_RE.search(text)
    assert m and m.group(1).lower() not in sec.PLACEHOLDERS


@pytest.mark.parametrize("text", ["POSTGRES_" + PW.upper() + "=", PW + "=${X}", PW + ": changeme"])
def test_secret_regex_ignores_empty_or_placeholder(text: str) -> None:
    m = sec.SECRET_RE.search(text)
    assert m is None or m.group(1).lower() in sec.PLACEHOLDERS


def test_private_key_detected() -> None:
    assert sec.PRIVATE_KEY_RE.search("-----BEGIN RSA PRIVATE " + "KEY-----")
    assert sec.PRIVATE_KEY_RE.search("-----BEGIN PRIVATE " + "KEY-----")


def test_env_example_has_no_values(repo_root: Path) -> None:
    for line in (repo_root / ".env.example").read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#"):
            key, _, value = line.partition("=")
            assert key and not value.strip(), line


def test_env_not_present_in_repo(repo_root: Path) -> None:
    assert ".env" not in {p.name for p in sec.project_files(repo_root)}


def test_project_files_excludes_ignored(repo_root: Path) -> None:
    parts = {part for p in sec.project_files(repo_root) for part in p.relative_to(repo_root).parts}
    assert "node_modules" not in parts
    assert ".venv" not in parts


# ---------- Critérios 1, 4: estrutura ----------


def test_version_follows_pyproject(repo_root: Path) -> None:
    import inovalytics

    proj = tomllib.loads((repo_root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert inovalytics.__version__ == proj["version"]
    assert re.fullmatch(r"\d+\.\d+\.\d+.*", inovalytics.__version__)


def test_required_files_not_empty(repo_root: Path) -> None:
    for f in ("docs/licencas.md", "docs/decisoes.md", "README.md", "Makefile", "pyproject.toml"):
        assert (repo_root / f).read_text(encoding="utf-8").strip(), f


def test_frontend_skeleton(repo_root: Path) -> None:
    for f in ("package.json", "eslint.config.js", "vitest.config.ts", "src/smoke.test.ts"):
        assert (repo_root / "frontend" / f).is_file(), f
    assert any((repo_root / "docker").iterdir())
