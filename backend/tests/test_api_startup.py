"""Critérios 1 a 11 da F0-05: bucket criado no startup, /health/ready somente leitura."""

import ast
import asyncio
import inspect
import logging
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from botocore.exceptions import (  # type: ignore[import-untyped]
    ClientError,
    ConnectTimeoutError,
    EndpointConnectionError,
)
from fastapi.testclient import TestClient

from inovalytics.api import main
from inovalytics.api.config import Settings
from inovalytics.api.main import (
    StorageStartupError,
    backoff_delay,
    check_s3,
    create_app,
    ensure_bucket,
)

pytestmark = pytest.mark.usefixtures("no_external_network")

SENT_PG = "pg-sentinela-111"
SENT_AK = "ak-sentinela-222"
SENT_SK = "sk-sentinela-333"
CFG = Settings(
    postgres_password=SENT_PG,
    s3_access_key=SENT_AK,
    s3_secret_key=SENT_SK,
    s3_bucket="meu-bucket",
)


def _client_error(status: int, code: str, op: str = "HeadBucket") -> ClientError:
    return ClientError(
        {"Error": {"Code": code}, "ResponseMetadata": {"HTTPStatusCode": status}}, op
    )


class FakeS3:
    """Cliente S3 fake: `head` é uma lista de resultados (exceção ou None), consumida em ordem."""

    def __init__(self, head: list[Exception | None] | None = None, create: Exception | None = None):
        self.head = list(head or [None])
        self.create_err = create
        self.calls: list[str] = []
        self.exists = False

    def head_bucket(self, **kw: Any) -> None:
        self.calls.append("head_bucket")
        item = self.head.pop(0) if len(self.head) > 1 else self.head[0]
        if item is not None:
            raise item

    def create_bucket(self, **kw: Any) -> None:
        self.calls.append("create_bucket")
        self.created_with = kw
        if self.create_err is not None:
            raise self.create_err
        self.exists = True
        self.head = [None]

    def __getattr__(self, name: str) -> Callable[..., None]:
        # Qualquer outro método (put_object, delete_*, ...) é escrita proibida.
        def _forbidden(*a: object, **k: object) -> None:
            raise AssertionError(f"escrita proibida: {name}")

        return _forbidden


class Sleeps:
    def __init__(self) -> None:
        self.values: list[float] = []

    async def __call__(self, d: float) -> None:
        self.values.append(d)


def _app(fake: FakeS3, sleeps: Sleeps | None = None, cfg: Settings = CFG, **kw: Any) -> Any:
    sl = sleeps or Sleeps()

    async def init() -> None:
        await ensure_bucket(cfg, fake, sleep=sl)

    async def storage() -> None:
        await check_s3(cfg, fake)

    return create_app(cfg, {"storage": storage}, bucket_initializer=init, **kw)


# --- critério 1 e 2 ---------------------------------------------------------------------


def test_ready_is_read_only_503_when_bucket_missing() -> None:
    fake = FakeS3([_client_error(404, "404")])
    app = create_app(CFG, {"storage": lambda: check_s3(CFG, fake)})
    with TestClient(app) as c:
        r = c.get("/health/ready")
    assert r.status_code == 503
    assert r.json()["checks"]["storage"] == "unavailable"
    assert fake.calls == ["head_bucket"]


def test_ready_200_when_head_ok() -> None:
    fake = FakeS3()
    app = create_app(CFG, {"storage": lambda: check_s3(CFG, fake)})
    with TestClient(app) as c:
        assert c.get("/health/ready").status_code == 200
    assert fake.calls == ["head_bucket"]


def _names_in(fn: Callable[..., Any]) -> set[str]:
    tree = ast.parse(inspect.getsource(fn).lstrip())
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    names |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    names |= {
        n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)
    }
    return names


def test_only_ensure_bucket_path_references_create_bucket() -> None:
    assert "create_bucket" not in _names_in(check_s3)
    assert "create_bucket" not in _names_in(main.make_s3_client)
    source = inspect.getsource(main)
    tree = ast.parse(source)
    holders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            direct = [
                n
                for n in ast.walk(node)
                if isinstance(n, ast.Attribute) and n.attr == "create_bucket"
            ]
            if direct:
                holders.append(node.name)
    assert holders == ["_head_or_create"]  # chamado só por ensure_bucket
    assert "_head_or_create" in _names_in(ensure_bucket)
    for fn in (main.create_app, check_s3, main.check_postgres):
        assert "_head_or_create" not in _names_in(fn)
        assert "ensure_bucket" not in _names_in(check_s3)


# --- critérios 3 e 4 --------------------------------------------------------------------


def test_startup_creates_missing_bucket_once() -> None:
    fake = FakeS3([_client_error(404, "404")])
    with TestClient(_app(fake)):
        pass
    assert fake.calls.count("create_bucket") == 1
    assert fake.created_with == {"Bucket": "meu-bucket"}


def test_startup_runs_lifespan_only_with_context_manager() -> None:
    fake = FakeS3()
    client = TestClient(_app(fake))
    assert fake.calls == []  # sem `with`, o lifespan não roda
    with client:
        pass
    assert fake.calls == ["head_bucket"]


def test_startup_existing_bucket_does_not_create() -> None:
    fake = FakeS3()
    with TestClient(_app(fake)):
        pass
    assert "create_bucket" not in fake.calls


def test_two_startups_create_at_most_once() -> None:
    fake = FakeS3([_client_error(404, "404")])
    app = _app(fake)
    for _ in range(2):
        with TestClient(app):
            pass
    assert fake.calls.count("create_bucket") == 1


def test_create_race_owned_by_you_is_success(caplog: pytest.LogCaptureFixture) -> None:
    fake = FakeS3(
        [_client_error(404, "404")], create=_client_error(409, "BucketAlreadyOwnedByYou", "Create")
    )
    with caplog.at_level(logging.INFO, logger="inovalytics.api.main"), TestClient(_app(fake)):
        pass
    assert fake.calls == ["head_bucket", "create_bucket"]
    assert "garantido" in caplog.text
    assert "(criado)" not in caplog.text


def test_create_race_already_exists_accessible_is_success() -> None:
    fake = FakeS3(
        [_client_error(404, "404"), None],
        create=_client_error(409, "BucketAlreadyExists", "Create"),
    )
    with TestClient(_app(fake)):
        pass
    assert fake.calls == ["head_bucket", "create_bucket", "head_bucket"]


@pytest.mark.parametrize("status", [403, 404])
def test_already_exists_with_other_owner_fails_permanently(status: int) -> None:
    fake = FakeS3(
        [_client_error(404, "404"), _client_error(status, "AccessDenied")],
        create=_client_error(409, "BucketAlreadyExists", "Create"),
    )
    sleeps = Sleeps()
    with pytest.raises(StorageStartupError), TestClient(_app(fake, sleeps)):
        pass
    assert fake.calls == ["head_bucket", "create_bucket", "head_bucket"]
    assert sleeps.values == []


def test_created_log_says_criado(caplog: pytest.LogCaptureFixture) -> None:
    fake = FakeS3([_client_error(404, "404")])
    with caplog.at_level(logging.INFO, logger="inovalytics.api.main"):
        asyncio.run(ensure_bucket(CFG, fake, sleep=Sleeps()))
    assert "bucket meu-bucket verificado (criado)" in caplog.text


# --- critério 5 e 6 ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "exc",
    [
        EndpointConnectionError(endpoint_url="http://x"),
        ConnectTimeoutError(endpoint_url="http://x"),
    ],
)
def test_startup_fails_after_exhausting_retries(exc: Exception) -> None:
    fake = FakeS3([exc])
    cfg = Settings(**{**CFG.__dict__, "s3_startup_retries": 4})
    sleeps = Sleeps()
    with pytest.raises(StorageStartupError), TestClient(_app(fake, sleeps, cfg)):
        pass
    assert fake.calls.count("head_bucket") == 4
    assert len(sleeps.values) == 3


@pytest.mark.parametrize(
    ("head", "create"),
    [
        ([_client_error(403, "AccessDenied")], None),
        ([_client_error(403, "InvalidAccessKeyId")], None),
        ([_client_error(404, "404")], _client_error(403, "AccessDenied", "CreateBucket")),
    ],
)
def test_permanent_error_fails_on_first_attempt(
    head: list[Exception | None], create: Exception | None
) -> None:
    fake = FakeS3(head, create)
    sleeps = Sleeps()
    with pytest.raises(StorageStartupError), TestClient(_app(fake, sleeps)):
        pass
    assert fake.calls.count("head_bucket") == 1
    assert sleeps.values == []


def test_retry_recovers_on_third_attempt() -> None:
    err = EndpointConnectionError(endpoint_url="http://x")
    fake = FakeS3([err, err, _client_error(404, "404")])
    sleeps = Sleeps()
    with TestClient(_app(fake, sleeps)):
        pass
    assert fake.calls == ["head_bucket"] * 3 + ["create_bucket"]
    assert len(sleeps.values) == 2


# --- critério 7 -------------------------------------------------------------------------


def test_backoff_is_growing_and_capped() -> None:
    seq = [backoff_delay(n) for n in range(1, 12)]
    assert seq == sorted(seq)
    assert seq[0] < seq[1]
    assert max(seq) == main.BACKOFF_MAX


def test_injected_sleeps_follow_backoff() -> None:
    err = EndpointConnectionError(endpoint_url="http://x")
    fake = FakeS3([err])
    sleeps = Sleeps()
    cfg = Settings(**{**CFG.__dict__, "s3_startup_retries": 4})
    with pytest.raises(StorageStartupError):
        asyncio.run(ensure_bucket(cfg, fake, sleep=sleeps))
    assert sleeps.values == [backoff_delay(1), backoff_delay(2), backoff_delay(3)]


def test_settings_defaults_and_env() -> None:
    base = {"POSTGRES_PASSWORD": "p", "S3_ACCESS_KEY": "a", "S3_SECRET_KEY": "s"}
    s = Settings.from_env(base)
    assert (s.s3_startup_timeout, s.s3_startup_retries) == (30.0, 5)
    s = Settings.from_env({**base, "S3_STARTUP_TIMEOUT": "3.5", "S3_STARTUP_RETRIES": "2"})
    assert (s.s3_startup_timeout, s.s3_startup_retries) == (3.5, 2)


@pytest.mark.parametrize("name", ["S3_STARTUP_TIMEOUT", "S3_STARTUP_RETRIES"])
@pytest.mark.parametrize("value", ["0", "-1", "abc", "nan"])
def test_settings_invalid_values_cite_only_name(name: str, value: str) -> None:
    base = {"POSTGRES_PASSWORD": "p", "S3_ACCESS_KEY": "a", "S3_SECRET_KEY": "s", name: value}
    with pytest.raises(ValueError, match=name) as ei:
        Settings.from_env(base)
    assert value not in str(ei.value).replace(name, "")


@pytest.mark.parametrize("name", ["S3_STARTUP_TIMEOUT", "S3_STARTUP_RETRIES"])
def test_settings_empty_value_means_default(name: str) -> None:
    """`.env` com `S3_STARTUP_X=` vazio (compose repassa vazio) usa o padrão, não falha."""
    base = {"POSTGRES_PASSWORD": "p", "S3_ACCESS_KEY": "a", "S3_SECRET_KEY": "s", name: ""}
    s = Settings.from_env(base)
    assert (s.s3_startup_timeout, s.s3_startup_retries) == (30.0, 5)


def test_boto3_client_config_single_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def fake_client(*a: Any, **kw: Any) -> object:
        seen.update(kw)
        return object()

    monkeypatch.setattr(main.boto3, "client", fake_client)
    cfg = Settings(**{**CFG.__dict__, "check_timeout": 2.0})
    main.make_s3_client(cfg)
    conf = seen["config"]
    assert conf.connect_timeout == conf.read_timeout == 2.0
    assert conf.retries["max_attempts"] == 1


def test_total_deadline_interrupts_slow_call() -> None:
    release = threading.Event()

    class Slow(FakeS3):
        def head_bucket(self, **kw: Any) -> None:
            release.wait(5)

    cfg = Settings(**{**CFG.__dict__, "s3_startup_timeout": 0.05, "s3_startup_retries": 3})
    try:
        with pytest.raises(StorageStartupError):
            asyncio.run(ensure_bucket(cfg, Slow(), sleep=Sleeps()))
    finally:
        release.set()


# --- critério 8 -------------------------------------------------------------------------


def test_no_secret_leak_in_exception_and_logs(caplog: pytest.LogCaptureFixture) -> None:
    leaky = EndpointConnectionError(
        endpoint_url=f"http://{SENT_AK}:{SENT_SK}@h/postgresql://u:{SENT_PG}@db/x"
    )
    fake = FakeS3([leaky])
    cfg = Settings(**{**CFG.__dict__, "s3_startup_retries": 2})
    with caplog.at_level(logging.DEBUG), pytest.raises(StorageStartupError) as ei:
        asyncio.run(ensure_bucket(cfg, fake, sleep=Sleeps()))
    text = str(ei.value) + caplog.text
    for secret in (SENT_PG, SENT_AK, SENT_SK, cfg.postgres_dsn):
        assert secret not in text
    assert "meu-bucket" in text and "EndpointConnectionError" in text and "1/2" in text
    assert ei.value.__cause__ is None
    assert ei.value.__suppress_context__


def test_success_log_has_verified_line(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="inovalytics.api.main"):
        asyncio.run(ensure_bucket(CFG, FakeS3(), sleep=Sleeps()))
    assert "bucket meu-bucket verificado" in caplog.text
    assert SENT_SK not in caplog.text


# --- critério 9 -------------------------------------------------------------------------


def test_postgres_down_does_not_block_startup() -> None:
    async def pg_down() -> None:
        raise OSError("db fora")

    fake = FakeS3()

    async def storage() -> None:
        await check_s3(CFG, fake)

    async def init() -> None:
        await ensure_bucket(CFG, fake, sleep=Sleeps())

    app = create_app(CFG, {"postgres": pg_down, "storage": storage}, bucket_initializer=init)
    with TestClient(app) as c:
        r = c.get("/health/ready")
    assert r.status_code == 503
    assert r.json()["checks"] == {"postgres": "unavailable", "storage": "ok"}


# --- critérios 10 e 11 ------------------------------------------------------------------


def test_injected_checks_do_not_touch_network_on_startup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(*a: object, **k: object) -> None:
        raise AssertionError("boto3.client chamado no startup")

    monkeypatch.setattr(main.boto3, "client", boom)

    async def ok() -> None:
        return None

    with TestClient(create_app(CFG, {"storage": ok})) as c:
        assert c.get("/health").status_code == 200


def test_app_factory_uses_real_initializer(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeS3()
    built: list[dict[str, Any]] = []

    def fake_client(*a: Any, **kw: Any) -> FakeS3:
        built.append(kw)
        return fake

    monkeypatch.setattr(main.boto3, "client", fake_client)
    monkeypatch.setenv("POSTGRES_PASSWORD", "p")
    monkeypatch.setenv("S3_ACCESS_KEY", "a")
    monkeypatch.setenv("S3_SECRET_KEY", "s")
    monkeypatch.setenv("S3_BUCKET", "bucket-do-env")
    with TestClient(main.app_factory()):
        pass
    assert fake.calls == ["head_bucket"]
    assert len(built) == 1


def test_smoke_file_has_startup_steps(repo_root: Path) -> None:
    text = (repo_root / "backend/tests/test_compose_smoke.py").read_text("utf-8")
    assert "bucket" in text and "verificado" in text
    assert "--force-recreate" in text and "stop" in text and "storage" in text


def test_upload_s3_client_has_own_timeouts_and_checksum_when_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    def fake_client(*a: Any, **kw: Any) -> object:
        seen.update(kw)
        return object()

    monkeypatch.setattr(main.boto3, "client", fake_client)
    cfg = Settings(**{**CFG.__dict__, "check_timeout": 2.0, "upload_read_timeout": 45.0})
    main.make_upload_s3_client(cfg)
    conf = seen["config"]
    assert conf.connect_timeout == 3.0 and conf.read_timeout == 45.0
    assert conf.retries["max_attempts"] == 1
    if "request_checksum_calculation" in main.Config.OPTION_DEFAULTS:
        assert conf.request_checksum_calculation == "when_required"
        assert conf.response_checksum_validation == "when_required"
    main.make_s3_client(cfg)  # o cliente de health continua com check_timeout
    assert seen["config"].connect_timeout == seen["config"].read_timeout == 2.0


@pytest.mark.parametrize("bad", ["0", "-1", "abc", "inf"])
def test_upload_read_timeout_validated(bad: str) -> None:
    base = {"POSTGRES_PASSWORD": "p", "S3_ACCESS_KEY": "a", "S3_SECRET_KEY": "s"}
    with pytest.raises(ValueError, match="S3_UPLOAD_READ_TIMEOUT"):
        Settings.from_env({**base, "S3_UPLOAD_READ_TIMEOUT": bad})
    assert Settings.from_env({**base, "S3_UPLOAD_READ_TIMEOUT": ""}).upload_read_timeout == 60.0
