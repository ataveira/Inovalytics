import re
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def dockerfile(repo_root: Path) -> str:
    return (repo_root / "docker" / "api" / "Dockerfile").read_text(encoding="utf-8")


def test_base_image_is_python_slim_with_fixed_tag(dockerfile: str) -> None:
    m = re.search(r"^FROM\s+python:(\d+)\.(\d+)[\w.-]*-slim[\w.-]*\s*$", dockerfile, re.M)
    assert m, "base deve ser python:<versão>-slim com tag fixa"
    assert (int(m.group(1)), int(m.group(2))) >= (3, 11)
    assert "latest" not in dockerfile.lower().split("from", 1)[1].splitlines()[0]


def test_non_root_user_and_healthcheck(dockerfile: str) -> None:
    users = re.findall(r"^USER\s+(\S+)", dockerfile, re.M)
    assert users and users[-1] not in {"root", "0"}
    assert re.search(r"^HEALTHCHECK\b", dockerfile, re.M)


def test_no_env_copy_or_ultralytics(dockerfile: str) -> None:
    for line in dockerfile.splitlines():
        if line.strip().upper().startswith(("COPY", "ADD")):
            assert not re.search(r"(^|[\s/])\.env\b", line), line
    assert "ultra" + "lytics" not in dockerfile.lower()


def test_dockerignore_excludes_sensitive(repo_root: Path) -> None:
    lines = {ln.strip() for ln in (repo_root / ".dockerignore").read_text("utf-8").splitlines()}
    assert {".env", ".git", ".venv"} <= lines
