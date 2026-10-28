"""Testes adicionais da F0-02: bordas de /health/ready, settings, compose e Dockerfile."""

import asyncio
import logging
import re
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

from inovalytics.api import main
from inovalytics.api.config import Settings
from inovalytics.api.main import Check, create_app

PG_PW = "pg-s3nh4-ultra-secreta"
S3_SK = "s3-chave-ultra-secreta"
S3_AK = "s3-acesso-ultra-secreto"
SECRETS = (PG_PW, S3_SK, S3_AK)
REQUIRED_ENV = {"POSTGRES_PASSWORD": "x", "S3_ACCESS_KEY": "y", "S3_SECRET_KEY": "z"}


def _settings(**kw: Any) -> Settings:
    base: dict[str, Any] = {
        "postgres_host": "127.0.0.1",
        "postgres_port": 1,  # porta fechada: conexão recusada imediatamente
        "postgres_password": PG_PW,
        "s3_endpoint_url": "http://127.0.0.1:1",
        "s3_access_key": S3_AK,
        "s3_secret_key": S3_SK,
        "check_timeout": 0.5,
    }
    base.update(kw)
    return Settings(**base)


async def _ok() -> None:
    return None


def _leaky(name: str) -> Check:
    async def fail() -> None:
        raise ConnectionError(f"{name}: postgresql://u:{PG_PW}@db:5432/x key={S3_SK} ak={S3_AK}")

    return fail


def _assert_no_leak(text: str) -> None:
    for s in SECRETS:
        assert s not in text
    assert "postgresql://" not in text


# ---------- /health/ready ----------


@pytest.mark.parametrize("failing", ["postgres", "storage"])
def test_ready_isolated_failure_keeps_other_ok_and_no_leak_in_body_or_logs(
    failing: str, caplog: pytest.LogCaptureFixture
) -> None:
    checks: dict[str, Check] = {"postgres": _ok, "storage": _ok}
    checks[failing] = _leaky(failing)
    other = "storage" if failing == "postgres" else "postgres"
    with caplog.at_level(logging.DEBUG):
        r = TestClient(create_app(_settings(), checks)).get("/health/ready")
    assert r.status_code == 503
    body = r.json()
    assert body["status"] == "unavailable"
    assert body["checks"] == {failing: "unavailable", other: "ok"}
    _assert_no_leak(r.text)
    _assert_no_leak(caplog.text)
    _assert_no_leak(str(dict(r.headers)))


def test_ready_both_failing_is_503() -> None:
    checks: dict[str, Check] = {"postgres": _leaky("p"), "storage": _leaky("s")}
    r = TestClient(create_app(_settings(), checks)).get("/health/ready")
    assert r.status_code == 503
    assert r.json()["checks"] == {"postgres": "unavailable", "storage": "unavailable"}
    _assert_no_leak(r.text)


def test_ready_hanging_dependency_times_out_with_503() -> None:
    async def hang() -> None:
        await asyncio.sleep(60)

    checks: dict[str, Check] = {"postgres": hang, "storage": _ok}
    client = TestClient(create_app(_settings(check_timeout=0.1), checks))
    r = client.get("/health/ready")
    assert r.status_code == 503
    assert r.json()["checks"]["postgres"] == "unavailable"


def test_ready_real_checks_unreachable_dependencies_do_not_leak() -> None:
    """Sem fakes: conexões reais recusadas devem dar 503 sem expor DSN/senha/chaves."""
    r = TestClient(create_app(_settings())).get("/health/ready")
    assert r.status_code == 503
    assert r.json()["checks"] == {"postgres": "unavailable", "storage": "unavailable"}
    _assert_no_leak(r.text)


@pytest.mark.parametrize("which", ["postgres", "storage"])
def test_ready_real_check_exceptions_with_secrets_are_swallowed(
    which: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async def boom(settings: Settings) -> None:
        raise RuntimeError(f"{settings.postgres_dsn} {settings.s3_secret_key}")

    async def fine(settings: Settings) -> None:
        return None

    monkeypatch.setattr(main, "check_postgres", boom if which == "postgres" else fine)
    monkeypatch.setattr(main, "check_s3", boom if which == "storage" else fine)
    with caplog.at_level(logging.DEBUG):
        r = TestClient(create_app(_settings())).get("/health/ready")
    assert r.status_code == 503
    assert r.json()["checks"][which] == "unavailable"
    _assert_no_leak(r.text)
    _assert_no_leak(caplog.text)


def test_ready_all_ok_via_real_wiring(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fine(settings: Settings) -> None:
        return None

    monkeypatch.setattr(main, "check_postgres", fine)
    monkeypatch.setattr(main, "check_s3", fine)
    r = TestClient(create_app(_settings())).get("/health/ready")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "checks": {"postgres": "ok", "storage": "ok"}}


def test_health_liveness_with_real_wiring_does_not_touch_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def boom(settings: Settings) -> None:
        raise AssertionError("não deve ser chamado")

    monkeypatch.setattr(main, "check_postgres", boom)
    monkeypatch.setattr(main, "check_s3", boom)
    r = TestClient(create_app(_settings())).get("/health")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


def test_health_rejects_wrong_method_and_unknown_path() -> None:
    client = TestClient(create_app(_settings(), {"postgres": _ok, "storage": _ok}))
    assert client.post("/health").status_code == 405
    assert client.post("/health/ready").status_code == 405
    assert client.get("/health/nope").status_code == 404


# ---------- Settings ----------


def test_settings_repr_str_hide_all_secrets() -> None:
    s = _settings()
    for text in (repr(s), str(s)):
        _assert_no_leak(text)


def test_settings_defaults_and_overrides_from_env() -> None:
    s = Settings.from_env(REQUIRED_ENV)
    assert (s.postgres_host, s.postgres_port, s.s3_bucket) == ("db", 5432, "inovalytics")
    s = Settings.from_env(
        {**REQUIRED_ENV, "POSTGRES_PORT": "5433", "S3_BUCKET": "b", "HEALTH_CHECK_TIMEOUT": "1.5"}
    )
    assert (s.postgres_port, s.s3_bucket, s.check_timeout) == (5433, "b", 1.5)


def test_settings_dsn_escapes_special_characters() -> None:
    weird = "a/b:c@d?e#f %"
    s = Settings(postgres_user="u:s@r", postgres_password=weird)
    dsn = s.postgres_dsn
    assert dsn.count("@") == 1
    assert "a%2Fb%3Ac%40d%3Fe%23f%20%25" in dsn and "u%3As%40r" in dsn


@pytest.mark.parametrize("bad", ["abc", "", "5432.5"])
def test_settings_invalid_port_raises_without_secret(bad: str) -> None:
    with pytest.raises(ValueError) as exc:
        Settings.from_env({**REQUIRED_ENV, "POSTGRES_PORT": bad, "POSTGRES_PASSWORD": PG_PW})
    assert PG_PW not in str(exc.value)


def test_settings_invalid_timeout_raises() -> None:
    with pytest.raises(ValueError):
        Settings.from_env({**REQUIRED_ENV, "HEALTH_CHECK_TIMEOUT": "rápido"})


def test_settings_ignores_unrelated_env_secrets() -> None:
    s = Settings.from_env({**REQUIRED_ENV, "AWS_SECRET_ACCESS_KEY": PG_PW, "OUTRA": PG_PW})
    assert PG_PW not in repr(s) and PG_PW not in s.postgres_dsn


@pytest.mark.parametrize("missing", ["POSTGRES_PASSWORD", "S3_ACCESS_KEY", "S3_SECRET_KEY"])
def test_settings_without_required_variables_fails(missing: str) -> None:
    env = {"POSTGRES_PASSWORD": "x", "S3_ACCESS_KEY": "y", "S3_SECRET_KEY": "z"}
    del env[missing]
    with pytest.raises(ValueError) as exc:
        Settings.from_env(env)
    assert missing in str(exc.value)
    assert not any(v in str(exc.value) for v in env.values() if len(v) > 1)


@pytest.mark.parametrize("empty", ["POSTGRES_PASSWORD", "S3_ACCESS_KEY", "S3_SECRET_KEY"])
def test_settings_empty_required_variable_fails(empty: str) -> None:
    with pytest.raises(ValueError) as exc:
        Settings.from_env({**REQUIRED_ENV, empty: ""})
    assert empty in str(exc.value)


def test_create_app_empty_checks_dict_is_respected() -> None:
    r = TestClient(create_app(_settings(), {})).get("/health/ready")
    assert r.json()["checks"] == {}


# ---------- compose (estático, com mutações para provar que o detector funciona) ----------

SENSITIVE = re.compile(r"PASSWORD|SECRET|ACCESS_KEY|TOKEN|KEY", re.I)
REF = re.compile(r"\$\{[A-Z0-9_]+(:\?[^}]*)?\}")


def _env_items(svc: dict[str, Any]) -> list[tuple[str, str]]:
    env = svc.get("environment") or {}
    if isinstance(env, list):
        return [tuple(e.split("=", 1)) if "=" in e else (e, "") for e in env]  # type: ignore[misc]
    return [(k, "" if v is None else str(v)) for k, v in env.items()]


def literal_secrets(compose: dict[str, Any]) -> list[tuple[str, str]]:
    bad = []
    for name, svc in compose["services"].items():
        for k, v in _env_items(svc):
            if SENSITIVE.search(k) and not REF.fullmatch(v):
                bad.append((name, k))
    return bad


def bad_images(compose: dict[str, Any]) -> list[str]:
    bad = []
    for name, svc in compose["services"].items():
        img = svc.get("image", "")
        last = img.rsplit("/", 1)[-1]
        if ":" not in last or last.endswith(":") or last.split(":", 1)[1] == "latest":
            bad.append(name)
    return bad


def published(compose: dict[str, Any], name: str) -> list[Any]:
    svc = compose["services"][name]
    return list(svc.get("ports") or []) + (["host"] if svc.get("network_mode") == "host" else [])


@pytest.fixture(scope="module")
def compose(repo_root: Path) -> dict[str, Any]:
    return yaml.safe_load((repo_root / "docker-compose.yml").read_text("utf-8"))  # type: ignore[no-any-return]


def test_compose_text_never_mentions_latest(repo_root: Path) -> None:
    raw = (repo_root / "docker-compose.yml").read_text("utf-8")
    assert not re.search(r":latest\b", raw)


def test_compose_every_service_has_pinned_image(compose: dict[str, Any]) -> None:
    assert all("image" in s for s in compose["services"].values())
    assert bad_images(compose) == []


def test_image_detector_rejects_bad_tags() -> None:
    for img in ("postgres", "postgres:latest", "postgres:", "reg:5000/postgres"):
        assert bad_images({"services": {"x": {"image": img}}}) == ["x"], img
    assert bad_images({"services": {"x": {"image": "reg:5000/postgres:17.6"}}}) == []


def test_compose_no_literal_secret_in_any_service(compose: dict[str, Any]) -> None:
    assert literal_secrets(compose) == []


def test_secret_detector_flags_literals_and_defaults() -> None:
    for v in ("hunter2", "${POSTGRES_PASSWORD:-hunter2}", "${POSTGRES_PASSWORD-x}", ""):
        c = {"services": {"db": {"environment": {"POSTGRES_PASSWORD": v}}}}
        assert literal_secrets(c) == [("db", "POSTGRES_PASSWORD")], v
    c = {"services": {"db": {"environment": ["S3_SECRET_KEY=abc"]}}}
    assert literal_secrets(c) == [("db", "S3_SECRET_KEY")]


def test_compose_no_secret_in_command_or_healthcheck(compose: dict[str, Any]) -> None:
    for name, svc in compose["services"].items():
        blob = " ".join(
            str(x) for x in (svc.get("command"), svc.get("entrypoint"), svc.get("healthcheck"))
        )
        assert not re.search(r"(password|secret|access[_-]?key)\s*[=:]\s*[^$\s'\"]", blob, re.I), (
            name
        )


def test_compose_required_secrets_use_fail_fast_form(compose: dict[str, Any]) -> None:
    for name, svc in compose["services"].items():
        for k, v in _env_items(svc):
            if re.search(r"PASSWORD|SECRET", k):
                assert ":?" in v, (name, k)


@pytest.mark.parametrize("name", ["db", "storage"])
def test_compose_db_and_storage_not_published(compose: dict[str, Any], name: str) -> None:
    svc = compose["services"][name]
    assert published(compose, name) == []
    assert "ports" not in svc and svc.get("network_mode") != "host"
    # `expose` é aceitável (rede interna); mas nada de binding ao host
    assert not any(str(p).split(":")[0].isdigit() for p in svc.get("ports", []))


def test_compose_only_api_publishes_ports(compose: dict[str, Any]) -> None:
    owners = [n for n in compose["services"] if published(compose, n)]
    assert owners == ["api"]
    (port,) = compose["services"]["api"]["ports"]
    assert str(port).endswith(":8000")


def test_published_detector_flags_ports_and_host_network() -> None:
    c = {"services": {"db": {"ports": ["5432:5432"]}, "s": {"network_mode": "host"}}}
    assert published(c, "db") and published(c, "s")


def test_compose_named_volumes_are_declared(compose: dict[str, Any]) -> None:
    for name in ("db", "storage"):
        sources = [str(v).split(":")[0] for v in compose["services"][name]["volumes"]]
        assert sources and all(s in compose["volumes"] for s in sources)
        assert not any(s.startswith(("/", ".", "~")) for s in sources)


def test_env_example_has_no_filled_secrets(repo_root: Path) -> None:
    for line in (repo_root / ".env.example").read_text("utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            if SENSITIVE.search(key):
                assert value.strip() == "", key


def test_env_example_covers_every_settings_secret(repo_root: Path) -> None:
    text = (repo_root / ".env.example").read_text("utf-8")
    for var in ("POSTGRES_PASSWORD", "S3_ACCESS_KEY", "S3_SECRET_KEY"):
        assert re.search(rf"^{var}=", text, re.M)


# ---------- Dockerfile / .dockerignore ----------


def _logical_lines(text: str) -> list[str]:
    return [ln.strip() for ln in re.sub(r"\\\n", " ", text).splitlines() if ln.strip()]


@pytest.fixture(scope="module")
def dockerfile_lines(repo_root: Path) -> list[str]:
    return _logical_lines((repo_root / "docker" / "api" / "Dockerfile").read_text("utf-8"))


def _final_stage(lines: list[str]) -> list[str]:
    idx = [i for i, ln in enumerate(lines) if ln.upper().startswith("FROM ")]
    return lines[idx[-1] :]


def test_dockerfile_final_stage_runs_as_non_root(dockerfile_lines: list[str]) -> None:
    stage = _final_stage(dockerfile_lines)
    users = [ln.split(None, 1)[1].strip() for ln in stage if ln.upper().startswith("USER ")]
    assert users, "sem USER na última etapa: roda como root"
    last = users[-1].split(":")[0].strip().lower()
    assert last not in {"root", "0"}
    # CMD/HEALTHCHECK/ENTRYPOINT devem vir depois do último USER
    last_user = max(i for i, ln in enumerate(stage) if ln.upper().startswith("USER "))
    for i, ln in enumerate(stage):
        if ln.upper().startswith(("CMD", "ENTRYPOINT")):
            assert i > last_user


def test_dockerfile_numeric_user_matches_created_user(dockerfile_lines: list[str]) -> None:
    text = "\n".join(dockerfile_lines)
    user = re.search(r"^USER\s+(\S+)", text, re.M)
    assert user
    if user.group(1).isdigit():
        assert f"--uid {user.group(1)}" in text or f"-u {user.group(1)}" in text


def test_dockerfile_does_not_copy_env_or_whole_context_unsafely(
    dockerfile_lines: list[str], repo_root: Path
) -> None:
    ignore = (repo_root / ".dockerignore").read_text("utf-8").splitlines()
    for ln in dockerfile_lines:
        if ln.upper().startswith(("COPY ", "ADD ")):
            assert not re.search(r"(^|[\s/])\.env(\.\w+)?(\s|$)", ln), ln
            assert not ln.upper().startswith("ADD "), "prefira COPY a ADD"
            args = [a for a in ln.split()[1:] if not a.startswith("--")]
            if args[:-1] and any(a in {".", "./", "*", "./*"} for a in args[:-1]):
                assert ".env" in ignore and ".env.*" in ignore, ln


def test_dockerfile_no_secrets_in_env_or_arg(dockerfile_lines: list[str]) -> None:
    for ln in dockerfile_lines:
        if ln.upper().startswith(("ENV ", "ARG ")):
            assert not re.search(r"PASSWORD|SECRET|ACCESS_KEY|TOKEN", ln, re.I), ln


def test_dockerfile_base_not_latest_and_no_unpinned_from(dockerfile_lines: list[str]) -> None:
    froms = [ln for ln in dockerfile_lines if ln.upper().startswith("FROM ")]
    assert froms
    for ln in froms:
        image = ln.split()[1]
        assert ":" in image and not image.endswith(":latest"), ln


def test_dockerfile_exposes_only_api_port_and_has_cmd(dockerfile_lines: list[str]) -> None:
    exposed = [ln for ln in dockerfile_lines if ln.upper().startswith("EXPOSE")]
    assert exposed == ["EXPOSE 8000"]
    assert any(ln.upper().startswith("CMD") for ln in dockerfile_lines)


def test_dockerignore_env_variants_but_keeps_example(repo_root: Path) -> None:
    lines = [ln.strip() for ln in (repo_root / ".dockerignore").read_text("utf-8").splitlines()]
    assert ".env" in lines and ".env.*" in lines
    assert lines.index("!.env.example") > lines.index(".env.*")
