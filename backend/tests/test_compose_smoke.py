"""Critérios 10 a 14 da F0-02. Rodam com `make compose-smoke`; pulados sem Docker."""

import json
import secrets
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest

pytestmark = pytest.mark.docker

PORT = "18000"


def _docker_ready() -> bool:
    if shutil.which("docker") is None:
        return False
    return subprocess.run(["docker", "info"], capture_output=True).returncode == 0


@pytest.fixture(scope="module")
def env_file(repo_root: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    if not _docker_ready():
        pytest.skip("docker indisponível")
    p = tmp_path_factory.mktemp("compose") / "smoke.env"
    p.write_text(
        "\n".join(
            [
                f"POSTGRES_PASSWORD={secrets.token_hex(8)}",
                f"S3_ACCESS_KEY=ak{secrets.token_hex(6)}",
                f"S3_SECRET_KEY={secrets.token_hex(16)}",
                f"API_PORT={PORT}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return p


def _dc(root: Path, env: Path, *args: str) -> subprocess.CompletedProcess[str]:
    cmd = ["docker", "compose", "-p", "inovalytics-smoke", "--env-file", str(env), *args]
    return subprocess.run(cmd, cwd=root, capture_output=True, text=True, timeout=600)


def _status(path: str) -> int:
    try:
        with urllib.request.urlopen(f"http://localhost:{PORT}{path}", timeout=5) as r:
            return int(r.status)
    except urllib.error.HTTPError as exc:
        return exc.code


@pytest.fixture
def stack(repo_root: Path, env_file: Path) -> Iterator[None]:
    yield
    _dc(repo_root, env_file, "down", "-v")


def test_config_build_up_ready_and_down(repo_root: Path, env_file: Path, stack: None) -> None:
    assert _dc(repo_root, env_file, "config", "-q").returncode == 0
    assert _dc(repo_root, env_file, "build", "api").returncode == 0
    for _ in range(2):  # segundo ciclo prova idempotência a partir do zero
        up = _dc(repo_root, env_file, "up", "-d", "--wait", "--wait-timeout", "120")
        assert up.returncode == 0, up.stderr
        assert _status("/health/ready") == 200
        assert _dc(repo_root, env_file, "stop", "db").returncode == 0
        deadline = time.time() + 30
        while _status("/health/ready") != 503 and time.time() < deadline:
            time.sleep(1)
        assert _status("/health/ready") == 503
        ps = _dc(repo_root, env_file, "ps", "api", "--format", "json").stdout
        assert "running" in ps.lower() and json.loads(ps.splitlines()[0])
        assert _dc(repo_root, env_file, "down", "-v").returncode == 0
