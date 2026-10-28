import tomllib
from pathlib import Path


def test_repo_layout(repo_root: Path) -> None:
    for d in ("backend", "frontend", "docs", "docker"):
        assert (repo_root / d).is_dir(), d
    for f in ("pyproject.toml", "Makefile", "docs/licencas.md", "docs/decisoes.md"):
        assert (repo_root / f).is_file(), f


def test_pyproject_config(repo_root: Path) -> None:
    data = tomllib.loads((repo_root / "pyproject.toml").read_text(encoding="utf-8"))
    assert data["project"]["requires-python"] == ">=3.11"
    assert "ruff" in data["tool"]
    assert "mypy" in data["tool"]
    assert "ini_options" in data["tool"]["pytest"]


def test_env_policy(repo_root: Path) -> None:
    lines = (repo_root / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".env" in (line.strip() for line in lines)
    assert (repo_root / ".env.example").is_file()
