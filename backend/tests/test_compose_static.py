import re
from pathlib import Path
from typing import Any

import pytest
import yaml


@pytest.fixture(scope="module")
def compose(repo_root: Path) -> dict[str, Any]:
    data = yaml.safe_load((repo_root / "docker-compose.yml").read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _services(compose: dict[str, Any]) -> dict[str, Any]:
    services = compose["services"]
    assert isinstance(services, dict)
    return services


def test_exact_services(compose: dict[str, Any]) -> None:
    assert set(_services(compose)) == {"db", "storage", "api"}


def test_healthchecks_and_depends_on(compose: dict[str, Any]) -> None:
    services = _services(compose)
    for name, svc in services.items():
        hc = svc.get("healthcheck", {})
        assert {"test", "interval", "timeout", "retries"} <= set(hc), name
    deps = services["api"]["depends_on"]
    for dep in ("db", "storage"):
        assert deps[dep]["condition"] == "service_healthy"


def test_images_have_fixed_tags(compose: dict[str, Any]) -> None:
    for name, svc in _services(compose).items():
        image = svc["image"]
        assert ":" in image.rsplit("/", 1)[-1], name
        tag = image.rsplit(":", 1)[1]
        assert tag and tag != "latest", name


def test_no_literal_secrets_and_vars_in_env_example(
    compose: dict[str, Any], repo_root: Path
) -> None:
    example = (repo_root / ".env.example").read_text(encoding="utf-8")
    example_vars = set(re.findall(r"^([A-Z][A-Z0-9_]*)=", example, re.M))
    raw = (repo_root / "docker-compose.yml").read_text(encoding="utf-8")
    used = set(re.findall(r"\$\{([A-Z][A-Z0-9_]*)", raw))
    assert used and used <= example_vars, used - example_vars
    sensitive = re.compile(r"PASSWORD|SECRET|ACCESS_KEY|TOKEN", re.I)
    for name, svc in _services(compose).items():
        for key, value in (svc.get("environment") or {}).items():
            if sensitive.search(key):
                v = str(value)
                assert re.fullmatch(r"\$\{[A-Z0-9_]+(:\?[^}]*)?\}", v), (name, key)
    gitignore = (repo_root / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".env" in gitignore


def test_volumes_and_only_api_published(compose: dict[str, Any]) -> None:
    services = _services(compose)
    for name in ("db", "storage"):
        mounts = [v for v in services[name]["volumes"] if isinstance(v, str)]
        assert mounts, name
        assert all(m.split(":")[0] in compose["volumes"] for m in mounts), name
        assert "ports" not in services[name], name
    ports = services["api"]["ports"]
    assert len(ports) == 1 and ports[0].endswith(":8000")
