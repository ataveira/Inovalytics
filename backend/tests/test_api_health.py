import pytest
from fastapi.testclient import TestClient

from inovalytics.api.config import Settings
from inovalytics.api.main import Check, create_app

LEAK = "s3nh4-super-secreta"


async def _ok() -> None:
    return None


async def _fail() -> None:
    raise RuntimeError(f"postgresql://u:{LEAK}@db:5432/x refused")


def _client(checks: dict[str, Check]) -> TestClient:
    return TestClient(create_app(Settings(postgres_password=LEAK), checks))


def test_health_does_not_touch_dependencies() -> None:
    client = _client({"postgres": _fail, "storage": _fail})
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_ready_ok() -> None:
    r = _client({"postgres": _ok, "storage": _ok}).get("/health/ready")
    assert r.status_code == 200
    assert r.json()["checks"] == {"postgres": "ok", "storage": "ok"}


@pytest.mark.parametrize("failing", ["postgres", "storage"])
def test_ready_503_when_any_fails_without_leaking(failing: str) -> None:
    checks: dict[str, Check] = {"postgres": _ok, "storage": _ok}
    checks[failing] = _fail
    r = _client(checks).get("/health/ready")
    assert r.status_code == 503
    assert r.json()["checks"][failing] == "unavailable"
    assert LEAK not in r.text
    assert "postgresql://" not in r.text


def test_settings_from_env_and_no_secret_in_repr() -> None:
    s = Settings.from_env(
        {
            "POSTGRES_PASSWORD": "p@ss/w",
            "S3_LEAK_KEY": LEAK,
            "S3_ACCESS_KEY": "ak",
            "S3_SECRET_KEY": "sk",
        }
    )
    assert "p%40ss%2Fw" in s.postgres_dsn
    assert s.s3_bucket == "inovalytics"
    assert LEAK not in repr(s)
    assert "p@ss/w" not in repr(s)
