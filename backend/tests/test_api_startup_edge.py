"""Casos de borda da F0-05: prazo total, erros permanentes, segredos, somente leitura, settings."""

import asyncio
import logging
import socket
from typing import Any

import pytest
from botocore.exceptions import (  # type: ignore[import-untyped]
    ConnectionClosedError,
    ConnectTimeoutError,
    EndpointConnectionError,
    ReadTimeoutError,
)
from fastapi.testclient import TestClient

from inovalytics.api import main
from inovalytics.api.config import Settings
from inovalytics.api.main import (
    StorageStartupError,
    check_s3,
    create_app,
    ensure_bucket,
)
from test_api_startup import (
    CFG,
    SENT_AK,
    SENT_PG,
    SENT_SK,
    FakeS3,
    Sleeps,
    _client_error,
)

pytestmark = pytest.mark.usefixtures("no_external_network")

BASE_ENV = {"POSTGRES_PASSWORD": "p", "S3_ACCESS_KEY": "a", "S3_SECRET_KEY": "s"}


def _cfg(**kw: Any) -> Settings:
    return Settings(**{**CFG.__dict__, **kw})


class FakeClock:
    """Relógio injetado: só avança quando o `sleep` injetado é chamado."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, d: float) -> None:
        self.sleeps.append(d)
        self.now += d


# --- critério 5 e 6: classificação de erros --------------------------------------------------


@pytest.mark.parametrize(
    "exc",
    [
        ReadTimeoutError(endpoint_url="http://x"),
        ConnectionClosedError(endpoint_url="http://x"),
        ConnectionError("reset"),
        TimeoutError(),
        _client_error(500, "InternalError"),
        _client_error(503, "SlowDown"),
    ],
)
def test_other_transient_errors_are_retried_then_fail(exc: Exception) -> None:
    fake = FakeS3([exc])
    sleeps = Sleeps()
    cfg = _cfg(s3_startup_retries=3)
    with pytest.raises(StorageStartupError) as ei:
        asyncio.run(ensure_bucket(cfg, fake, sleep=sleeps))
    assert fake.calls.count("head_bucket") == 3
    assert len(sleeps.values) == 2
    assert "3/3" in str(ei.value)


def test_transient_create_error_is_retried_and_recovers() -> None:
    sleeps = Sleeps()

    class Flaky(FakeS3):
        def create_bucket(self, **kw: Any) -> None:
            if self.calls.count("create_bucket") >= 1:
                self.create_err = None
            super().create_bucket(**kw)

    fake = Flaky([_client_error(404, "404")], create=_client_error(503, "SlowDown", "CreateBucket"))
    asyncio.run(ensure_bucket(CFG, fake, sleep=sleeps))
    assert fake.calls.count("create_bucket") == 2
    assert len(sleeps.values) == 1


@pytest.mark.parametrize(
    ("status", "code"),
    [(400, "InvalidBucketName"), (401, "Unauthorized"), (403, "SignatureDoesNotMatch")],
)
def test_other_permanent_head_errors_fail_without_retry(status: int, code: str) -> None:
    fake = FakeS3([_client_error(status, code)])
    sleeps = Sleeps()
    with pytest.raises(StorageStartupError):
        asyncio.run(ensure_bucket(CFG, fake, sleep=sleeps))
    assert fake.calls == ["head_bucket"]
    assert sleeps.values == []


def test_permanent_error_on_create_other_than_exists_fails_without_retry() -> None:
    fake = FakeS3([_client_error(404, "404")], create=_client_error(409, "TooManyBuckets"))
    sleeps = Sleeps()
    with pytest.raises(StorageStartupError):
        asyncio.run(ensure_bucket(CFG, fake, sleep=sleeps))
    assert fake.calls == ["head_bucket", "create_bucket"]
    assert sleeps.values == []


def test_unexpected_exception_type_is_permanent_and_wrapped() -> None:
    fake = FakeS3([ValueError("bug")])
    with pytest.raises(StorageStartupError) as ei:
        asyncio.run(ensure_bucket(CFG, fake, sleep=Sleeps()))
    assert fake.calls == ["head_bucket"]
    assert "ValueError" in str(ei.value)


def test_bucket_already_exists_then_startup_succeeds_through_app() -> None:
    fake = FakeS3(
        [_client_error(404, "404")],
        create=_client_error(409, "BucketAlreadyExists", "CreateBucket"),
    )
    sleeps = Sleeps()

    async def init() -> None:
        await ensure_bucket(CFG, fake, sleep=sleeps)

    with TestClient(create_app(CFG, {}, bucket_initializer=init)) as c:
        assert c.get("/health").status_code == 200
    assert fake.calls == ["head_bucket", "create_bucket"]
    assert sleeps.values == []


def test_single_retry_setting_never_sleeps() -> None:
    fake = FakeS3([EndpointConnectionError(endpoint_url="http://x")])
    sleeps = Sleeps()
    with pytest.raises(StorageStartupError) as ei:
        asyncio.run(ensure_bucket(_cfg(s3_startup_retries=1), fake, sleep=sleeps))
    assert fake.calls == ["head_bucket"]
    assert sleeps.values == []
    assert "1/1" in str(ei.value)


# --- critério 7: prazo total ------------------------------------------------------------------


def test_total_deadline_stops_before_retries_are_exhausted() -> None:
    clock = FakeClock()
    fake = FakeS3([EndpointConnectionError(endpoint_url="http://x")])
    cfg = _cfg(s3_startup_timeout=2.0, s3_startup_retries=50)
    with pytest.raises(StorageStartupError):
        asyncio.run(ensure_bucket(cfg, fake, sleep=clock.sleep, clock=clock))
    assert fake.calls.count("head_bucket") < 50
    assert sum(clock.sleeps) <= 2.0 + 1e-9


def test_sleep_never_exceeds_remaining_deadline() -> None:
    clock = FakeClock()
    fake = FakeS3([EndpointConnectionError(endpoint_url="http://x")])
    cfg = _cfg(s3_startup_timeout=0.7, s3_startup_retries=10)
    with pytest.raises(StorageStartupError):
        asyncio.run(ensure_bucket(cfg, fake, sleep=clock.sleep, clock=clock))
    assert clock.sleeps  # houve ao menos uma espera
    assert all(s <= 0.7 for s in clock.sleeps)
    assert sum(clock.sleeps) <= 0.7 + 1e-9


def test_deadline_already_expired_makes_no_call() -> None:
    clock = FakeClock()
    fake = FakeS3()

    async def expire(_: float) -> None:
        clock.now += 100

    class Late:
        """Relógio que já passou do prazo na 2ª leitura."""

        def __init__(self) -> None:
            self.n = 0

        def __call__(self) -> float:
            self.n += 1
            return 0.0 if self.n == 1 else 1e9

    with pytest.raises(StorageStartupError):
        asyncio.run(ensure_bucket(_cfg(s3_startup_timeout=1.0), fake, sleep=expire, clock=Late()))
    assert fake.calls == []


def test_slow_call_through_app_aborts_startup_quickly() -> None:
    import threading
    import time

    release = threading.Event()

    class Slow(FakeS3):
        def head_bucket(self, **kw: Any) -> None:
            release.wait(5)

    cfg = _cfg(s3_startup_timeout=0.2, s3_startup_retries=5)

    elapsed: list[float] = []

    async def init() -> None:
        start = time.monotonic()
        try:
            await ensure_bucket(cfg, Slow(), sleep=Sleeps())
        finally:
            # mede o prazo da própria chamada (o encerramento do loop espera a thread do fake)
            elapsed.append(time.monotonic() - start)

    try:
        with (
            pytest.raises(StorageStartupError),
            TestClient(create_app(cfg, {}, bucket_initializer=init)),
        ):
            pass
    finally:
        release.set()
    assert elapsed and elapsed[0] < 1.0


# --- critério 8: segredos ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "exc",
    [
        _client_error(403, f"AccessDenied {SENT_AK} {SENT_SK} postgresql://u:{SENT_PG}@db/x"),
        ValueError(f"{SENT_AK} {SENT_SK} {SENT_PG}"),
        _client_error(503, f"{SENT_SK}"),
    ],
)
def test_no_secrets_for_non_connection_errors(
    exc: Exception, caplog: pytest.LogCaptureFixture
) -> None:
    fake = FakeS3([exc])
    cfg = _cfg(s3_startup_retries=2)
    with caplog.at_level(logging.DEBUG), pytest.raises(StorageStartupError) as ei:
        asyncio.run(ensure_bucket(cfg, fake, sleep=Sleeps()))
    text = str(ei.value) + caplog.text
    for secret in (SENT_PG, SENT_AK, SENT_SK, cfg.postgres_dsn, cfg.s3_endpoint_url):
        assert secret not in text
    assert "meu-bucket" in text


def test_no_secrets_in_create_failure_logs(caplog: pytest.LogCaptureFixture) -> None:
    fake = FakeS3(
        [_client_error(404, "404")], create=_client_error(403, f"{SENT_SK}", "CreateBucket")
    )
    with caplog.at_level(logging.DEBUG), pytest.raises(StorageStartupError) as ei:
        asyncio.run(ensure_bucket(CFG, fake, sleep=Sleeps()))
    text = str(ei.value) + caplog.text
    assert SENT_SK not in text and SENT_AK not in text and SENT_PG not in text


def test_ready_logs_do_not_leak_secrets(caplog: pytest.LogCaptureFixture) -> None:
    fake = FakeS3([_client_error(403, f"{SENT_SK} {SENT_AK}")])
    app = create_app(CFG, {"storage": lambda: check_s3(CFG, fake)})
    with caplog.at_level(logging.DEBUG), TestClient(app) as c:
        r = c.get("/health/ready")
    assert r.status_code == 503
    for secret in (SENT_PG, SENT_AK, SENT_SK):
        assert secret not in caplog.text
        assert secret not in r.text


def test_settings_repr_hides_secrets() -> None:
    text = repr(CFG)
    for secret in (SENT_PG, SENT_AK, SENT_SK):
        assert secret not in text


def test_startup_failure_via_app_factory_has_no_secrets(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    fake = FakeS3([_client_error(403, "AccessDenied")])
    monkeypatch.setattr(main.boto3, "client", lambda *a, **k: fake)
    for k, v in (
        ("POSTGRES_PASSWORD", SENT_PG),
        ("S3_ACCESS_KEY", SENT_AK),
        ("S3_SECRET_KEY", SENT_SK),
    ):
        monkeypatch.setenv(k, v)
    with (
        caplog.at_level(logging.DEBUG),
        pytest.raises(StorageStartupError) as ei,
        TestClient(main.app_factory()),
    ):
        pass
    text = str(ei.value) + caplog.text
    for secret in (SENT_PG, SENT_AK, SENT_SK):
        assert secret not in text
    assert fake.calls == ["head_bucket"]


# --- critério 1 e 2: somente leitura ---------------------------------------------------------


@pytest.mark.parametrize(
    "err",
    [
        None,
        _client_error(404, "404"),
        _client_error(403, "AccessDenied"),
        EndpointConnectionError(endpoint_url="http://x"),
    ],
)
def test_check_s3_only_calls_head_bucket_with_configured_bucket(err: Exception | None) -> None:
    seen: list[dict[str, Any]] = []

    class Spy(FakeS3):
        def head_bucket(self, **kw: Any) -> None:
            seen.append(kw)
            super().head_bucket(**kw)

    fake = Spy([err])
    if err is None:
        asyncio.run(check_s3(CFG, fake))
    else:
        with pytest.raises(type(err)):
            asyncio.run(check_s3(CFG, fake))
    assert fake.calls == ["head_bucket"]
    assert seen == [{"Bucket": "meu-bucket"}]


def test_ready_called_repeatedly_never_writes() -> None:
    fake = FakeS3([_client_error(404, "404")])
    app = create_app(CFG, {"storage": lambda: check_s3(CFG, fake)})
    with TestClient(app) as c:
        for _ in range(3):
            assert c.get("/health/ready").status_code == 503
    assert fake.calls == ["head_bucket"] * 3


def test_default_checks_ready_does_not_create_bucket(monkeypatch: pytest.MonkeyPatch) -> None:
    """Caminho de produção (checks=None): o startup é injetado, /health/ready só lê."""
    fake = FakeS3([_client_error(404, "404")])
    monkeypatch.setattr(main.boto3, "client", lambda *a, **k: fake)

    async def init() -> None:
        return None

    app = create_app(CFG, bucket_initializer=init)
    # postgres real é inalcançável (sem rede); só o storage importa aqui
    with TestClient(app) as c:
        r = c.get("/health/ready")
    assert r.status_code == 503
    assert r.json()["checks"]["storage"] == "unavailable"
    assert "create_bucket" not in fake.calls


# --- critério 7: settings inválidas ------------------------------------------------------------


@pytest.mark.parametrize("name", ["S3_STARTUP_TIMEOUT", "S3_STARTUP_RETRIES"])
@pytest.mark.parametrize("value", ["inf", "-inf", "-0", "0.0", " ", "1,5", "1e999", "segredo-xyz"])
def test_settings_more_invalid_values(name: str, value: str) -> None:
    with pytest.raises(ValueError, match=name) as ei:
        Settings.from_env({**BASE_ENV, name: value})
    assert "segredo-xyz" not in str(ei.value)
    assert "POSTGRES_PASSWORD" not in str(ei.value)


@pytest.mark.parametrize("value", ["1.5", "2.0", "0.5"])
def test_retries_rejects_non_integer(value: str) -> None:
    with pytest.raises(ValueError, match="S3_STARTUP_RETRIES"):
        Settings.from_env({**BASE_ENV, "S3_STARTUP_RETRIES": value})


def test_timeout_accepts_integer_and_fraction() -> None:
    s = Settings.from_env({**BASE_ENV, "S3_STARTUP_TIMEOUT": "7"})
    assert s.s3_startup_timeout == 7.0
    s = Settings.from_env({**BASE_ENV, "S3_STARTUP_TIMEOUT": "0.25"})
    assert s.s3_startup_timeout == 0.25


def test_invalid_startup_setting_does_not_echo_other_secrets() -> None:
    env = {**BASE_ENV, "POSTGRES_PASSWORD": SENT_PG, "S3_SECRET_KEY": SENT_SK}
    with pytest.raises(ValueError) as ei:
        Settings.from_env({**env, "S3_STARTUP_RETRIES": "abc"})
    for secret in (SENT_PG, SENT_SK):
        assert secret not in str(ei.value)


def test_missing_required_secret_cites_only_name() -> None:
    for name in ("POSTGRES_PASSWORD", "S3_ACCESS_KEY", "S3_SECRET_KEY"):
        env = {**BASE_ENV, name: ""}
        with pytest.raises(ValueError, match=name):
            Settings.from_env(env)


def test_no_real_socket_is_opened(monkeypatch: pytest.MonkeyPatch) -> None:
    """O startup com fake não pode tocar a rede (o conftest bloqueia sockets)."""
    calls: list[object] = []
    orig = socket.socket.connect

    def spy(self: socket.socket, *a: object) -> None:
        calls.append(a)
        orig(self, *a)  # type: ignore[arg-type]

    monkeypatch.setattr(socket.socket, "connect", spy)
    with TestClient(create_app(CFG, {}, bucket_initializer=lambda: _noop())):
        pass
    assert calls == []


async def _noop() -> None:
    return None


def test_connect_timeout_is_classified_transient() -> None:
    assert main._is_transient(ConnectTimeoutError(endpoint_url="http://x"))
    assert not main._is_transient(_client_error(403, "AccessDenied"))
    assert not main._is_transient(_client_error(404, "404"))
