"""F0-04c: CLI, alvo do Makefile, modo offline e árvore real."""

from __future__ import annotations

import ast
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from inovalytics.tools.license_check import main
from test_license_check_collectors import build_repo, make_dist, write_npm

VENDOR = "ultra" + "lytics"
TOOLS = Path(__file__).resolve().parents[1] / "src" / "inovalytics" / "tools"


def cli(root: Path, site: Path | None = None) -> int:
    args = ["--root", str(root)]
    if site is not None:
        args += ["--metadata-path", str(site)]
    return main(args)


@pytest.fixture
def fake_repo(tmp_path: Path) -> tuple[Path, Path]:
    root, site = tmp_path / "repo", tmp_path / "site"
    build_repo(root, site)
    cli_write = main(["--root", str(root), "--metadata-path", str(site), "--write"])
    assert cli_write == 0
    return root, site


def test_clean_repo_exits_0_with_grouped_output(
    fake_repo: tuple[Path, Path], capsys: pytest.CaptureFixture[str], no_network: None
) -> None:
    root, site = fake_repo
    assert cli(root, site) == 0
    out = capsys.readouterr().out
    assert "python: runtime 2, dev 0" in out
    assert "npm: runtime 0, dev 0" in out
    assert "imagens: runtime 0, dev 0" in out and "actions: runtime 0, dev 1" in out
    assert "Resultado: OK" in out


@pytest.mark.parametrize(
    ("fields", "needle"),
    [
        ({"expression": "AGPL-3.0-only"}, "PROIBIDA"),
        ({"expression": "GPL-2.0-only"}, "PROIBIDA"),
        ({"expression": "LGPL-3.0-only"}, "PROIBIDA"),
        ({"expression": "SSPL-1.0"}, "PROIBIDA"),
        ({"expression": "Proprietary"}, "PROIBIDA"),
        ({"expression": "GPL-3.0 AND MIT"}, "PROIBIDA"),
        ({"license_field": None}, "AUSENTE"),
        ({"license_field": "Termos Estranhos"}, "DESCONHECIDA"),
        ({"license_field": "UNKNOWN"}, "DESCONHECIDA"),
        ({"license_field": "MPL-2.0"}, "PROIBIDA"),
    ],
)
def test_bad_license_makes_cli_fail(
    tmp_path: Path, fields: dict[str, str | None], needle: str
) -> None:
    root, site = tmp_path / "repo", tmp_path / "site"
    build_repo(root, site)
    shutil.rmtree(next(site.glob("liby*")))
    make_dist(site, "liby", **{"license_field": None, **fields})  # type: ignore[arg-type]
    assert cli(root, site) == 1


def test_bad_license_output_cites_package(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root, site = tmp_path / "repo", tmp_path / "site"
    build_repo(root, site)
    shutil.rmtree(next(site.glob("liby*")))
    make_dist(site, "liby", expression="AGPL-3.0-only", license_field=None)
    assert cli(root, site) == 1
    assert "VIOLAÇÃO [PROIBIDA] python/runtime liby" in capsys.readouterr().out


def test_vendor_in_python_chain_fails(tmp_path: Path) -> None:
    root, site = tmp_path / "repo", tmp_path / "site"
    build_repo(root, site)
    make_dist(site, "liby-deep", requires=(VENDOR,))
    shutil.rmtree(next(site.glob("liby-1*")))
    make_dist(site, "liby", requires=("liby-deep",))
    assert cli(root, site) == 1


def test_vendor_in_npm_lock_fails(tmp_path: Path) -> None:
    root, site = tmp_path / "repo", tmp_path / "site"
    build_repo(root, site)
    write_npm(
        root,
        {"node_modules/z": {"version": "1", "license": "MIT", "dependencies": {VENDOR: "1"}}},
    )
    assert cli(root, site) == 1


def test_input_errors_exit_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root, site = tmp_path / "repo", tmp_path / "site"
    build_repo(root, site)
    exc = root / "docs" / "licencas-excecoes.toml"
    exc.write_text("[[excecao\n", encoding="utf-8")
    assert cli(root, site) == 2
    assert "Erro de entrada" in capsys.readouterr().err
    exc.write_text("", encoding="utf-8")
    (root / "frontend" / "package-lock.json").write_text("{", encoding="utf-8")
    assert cli(root, site) == 2
    (root / "frontend" / "package-lock.json").unlink()
    assert cli(root, site) == 2
    (root / "docs" / "licencas.md").unlink()
    assert cli(root, site) == 2


def test_invalid_exception_file_content_fails_gate(tmp_path: Path) -> None:
    root, site = tmp_path / "repo", tmp_path / "site"
    build_repo(root, site)
    (root / "docs" / "licencas-excecoes.toml").write_text(
        '[[excecao]]\npacote = "x"\n', encoding="utf-8"
    )
    assert cli(root, site) == 1


# ---------------------------------------------------------------- Makefile


def _make(
    root: Path, *args: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["make", f"PYTHON={sys.executable}", *args],
        cwd=root,
        # --no-print-directory: o caminho do tmp_path contém "pytest" e confundiria testes de ordem.
        env={
            **os.environ,
            "INOVALYTICS_NESTED": "1",
            "MAKEFLAGS": "--no-print-directory",
            **(env or {}),
        },
        capture_output=True,
        text=True,
    )


def test_make_targets_and_order(repo_root: Path) -> None:
    out = _make(repo_root, "-n", "check").stdout
    steps = ["ruff check", "ruff format", "mypy", "pytest", "license_check", "run lint", "run test"]
    pos = [out.find(s) for s in steps]
    assert all(p >= 0 for p in pos), pos
    assert pos == sorted(pos), pos
    assert "--write" not in out
    assert "--write" in _make(repo_root, "-n", "licencas-sync").stdout


def test_check_fails_with_forbidden_package(repo_root: Path, tmp_path: Path) -> None:
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
    site = tmp_path / "site"
    make_dist(site, "fastapi", version="0.162.0", expression="AGPL-3.0-only", license_field=None)
    res = _make(dst, "check", env={"PYTHONPATH": str(site)})
    assert res.returncode != 0
    assert "[PROIBIDA] python/runtime fastapi" in res.stdout, res.stdout[-2500:]


# ---------------------------------------------------------------- offline e árvore real


def test_module_does_not_import_network_or_subprocess() -> None:
    banned = {"socket", "urllib", "http", "requests", "subprocess", "ultra" + "lytics"}
    for path in TOOLS.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for n in names:
                assert n.split(".")[0] not in banned, (path.name, n)
        text = path.read_text(encoding="utf-8")
        assert "os.system" not in text and "Popen" not in text, path.name


def test_real_tree_is_clean_offline(
    repo_root: Path, capsys: pytest.CaptureFixture[str], no_network: None
) -> None:
    if os.environ.get("INOVALYTICS_NESTED"):
        pytest.skip("árvore alterada de propósito pelo teste de mutação aninhado")
    code = main(["--root", str(repo_root)])
    out = capsys.readouterr().out
    assert code == 0, out
    assert "npm: runtime 0, dev 203" in out
    assert "imagens: runtime 3, dev 0" in out and "actions: runtime 0, dev 3" in out
    assert "Exceções usadas: 2" in out and "certifi" in out and "pathspec" in out
    assert "Exceções sem uso" not in out
