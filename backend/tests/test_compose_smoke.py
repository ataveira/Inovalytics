"""Critérios da F0-02 (10 a 14) e da F0-05 (14 a 16). Rodam com `make compose-smoke`."""

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
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}{path}", timeout=5) as r:
            return int(r.status)
    except urllib.error.HTTPError as exc:
        return exc.code


def _wait_status(path: str, expected: int, timeout: float = 60) -> int:
    """Repete a requisição até obter `expected` ou estourar o prazo; devolve o último status."""
    deadline = time.time() + timeout
    status = _status(path)
    while status != expected and time.time() < deadline:
        time.sleep(1)
        status = _status(path)
    return status


# Executado dentro do contêiner da API (o storage não é publicado no host): requisição S3
# anônima (sem assinatura) a "/" (listar buckets) e ao bucket (listar objetos); imprime os status.
_ANON_S3_PROBE = (
    "import urllib.request as u, urllib.error as e\n"
    "for p in ('/', '/inovalytics'):\n"
    "    try:\n"
    "        print(u.urlopen('http://storage:8333' + p, timeout=5).status)\n"
    "    except e.HTTPError as x:\n"
    "        print(x.code)\n"
)


# Executado dentro do contêiner da API: head_bucket autenticado (boto3) no bucket padrão.
_HEAD_BUCKET = (
    "import os, boto3\n"
    "c = boto3.client('s3', endpoint_url=os.environ['S3_ENDPOINT_URL'],\n"
    "    aws_access_key_id=os.environ['S3_ACCESS_KEY'],\n"
    "    aws_secret_access_key=os.environ['S3_SECRET_KEY'], region_name='us-east-1')\n"
    "c.head_bucket(Bucket=os.environ['S3_BUCKET'])\n"
)


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
        # F0-05 (15): o bucket já existe, criado no startup, antes da 1ª chamada a /health/ready.
        head = _dc(repo_root, env_file, "exec", "-T", "api", "python", "-c", _HEAD_BUCKET)
        assert head.returncode == 0, head.stderr
        logs = _dc(repo_root, env_file, "logs", "api").stdout
        assert "bucket inovalytics verificado" in logs
        assert _wait_status("/health/ready", 200, timeout=60) == 200
        # S3 anônimo (sem credenciais) não pode listar nem ler buckets.
        probe = _dc(repo_root, env_file, "exec", "-T", "api", "python", "-c", _ANON_S3_PROBE)
        assert probe.returncode == 0, probe.stderr
        codes = [int(c) for c in probe.stdout.split()]
        assert codes and all(c in (401, 403) for c in codes), codes
        assert _dc(repo_root, env_file, "stop", "db").returncode == 0
        deadline = time.time() + 30
        while _status("/health/ready") != 503 and time.time() < deadline:
            time.sleep(1)
        assert _status("/health/ready") == 503
        ps = _dc(repo_root, env_file, "ps", "api", "--format", "json").stdout
        assert "running" in ps.lower() and json.loads(ps.splitlines()[0])
        assert _dc(repo_root, env_file, "down", "-v").returncode == 0


def test_startup_fails_fast_when_storage_unavailable(
    repo_root: Path, env_file: Path, stack: None
) -> None:
    """F0-05 (16): sem storage a API sai com código != 0 em menos de 30 s, sem vazar segredos."""
    assert _dc(repo_root, env_file, "build", "api").returncode == 0
    up = _dc(repo_root, env_file, "up", "-d", "--wait", "--wait-timeout", "120")
    assert up.returncode == 0, up.stderr
    assert _dc(repo_root, env_file, "stop", "storage").returncode == 0
    # `run` não aplica `restart: unless-stopped`, então o código de saída é observável.
    start = time.time()
    run = _dc(
        repo_root,
        env_file,
        "run",
        "--rm",
        "--no-deps",
        "-e",
        "S3_STARTUP_TIMEOUT=3",
        "-e",
        "S3_ENDPOINT_URL=http://storage:8333",
        "api",
    )
    assert time.time() - start < 30
    assert run.returncode != 0
    out = run.stdout + run.stderr
    assert "storage indisponível" in out
    env_text = env_file.read_text(encoding="utf-8")
    for line in env_text.splitlines():
        key, _, value = line.partition("=")
        if key in ("POSTGRES_PASSWORD", "S3_ACCESS_KEY", "S3_SECRET_KEY"):
            assert value not in out
    # Alternativa do critério: recria a API com o storage parado; ela não pode ficar saudável.
    recreate = _dc(repo_root, env_file, "up", "-d", "--no-deps", "--force-recreate", "api")
    assert recreate.returncode == 0, recreate.stderr
    time.sleep(5)
    assert _status("/health") != 200
