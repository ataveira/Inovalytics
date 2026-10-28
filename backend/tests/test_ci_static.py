import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

import pytest
import yaml

from test_no_secrets import PRIVATE_KEY_RE, SECRET_RE

ALLOWED_ACTIONS = {
    "actions/checkout",
    "actions/setup-python",
    "actions/setup-node",
    "actions/upload-artifact",
}
USES_RE = re.compile(r"^(?P<name>[\w.-]+/[\w./-]+)@(?P<ref>.+)$")
MAJOR_RE = re.compile(r"^v\d+$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")


@pytest.fixture(scope="module")
def wf(repo_root: Path) -> dict[Any, Any]:
    path = repo_root / ".github" / "workflows" / "ci.yml"
    assert path.is_file()
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


@pytest.fixture(scope="module")
def raw(repo_root: Path) -> str:
    return (repo_root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")


def _triggers(wf: dict[Any, Any]) -> dict[str, Any]:
    on = wf.get("on", wf.get(True))  # PyYAML (YAML 1.1) lê `on` como True
    assert isinstance(on, dict)
    return {str(k): v for k, v in on.items()}


def _jobs(wf: dict[Any, Any]) -> dict[str, dict[str, Any]]:
    jobs = wf["jobs"]
    assert isinstance(jobs, dict)
    return jobs


def _steps(job: dict[str, Any]) -> list[dict[str, Any]]:
    return list(job["steps"])


def _runs(job: dict[str, Any]) -> list[str]:
    return [s["run"] for s in _steps(job) if "run" in s]


def _uses(wf: dict[Any, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    out = []
    for job in _jobs(wf).values():
        for s in _steps(job):
            if "uses" in s:
                m = USES_RE.match(s["uses"])
                assert m, s["uses"]
                out.append((m.group("name"), m.group("ref"), s))
    return out


def test_triggers(wf: dict[Any, Any]) -> None:
    trig = _triggers(wf)
    assert {"push", "pull_request", "workflow_dispatch"} <= trig.keys()
    assert "pull_request_target" not in trig
    assert set(trig["push"]["branches"]) == {"main", "feat/**"}


def test_jobs_and_commands(wf: dict[Any, Any]) -> None:
    jobs = _jobs(wf)
    assert {"gate", "compose-smoke"} <= jobs.keys()
    gate_runs = _runs(jobs["gate"])
    assert "make check" in [r.strip() for r in gate_runs]
    assert any(r.strip() == "make compose-smoke" for r in _runs(jobs["compose-smoke"]))
    idx = {r.strip(): i for i, r in enumerate(gate_runs)}
    pip = next(i for i, r in enumerate(gate_runs) if 'pip install -e ".[dev]"' in r)
    npm_step = next(
        i for i, s in enumerate(_steps(jobs["gate"])) if s.get("run", "").strip() == "npm ci"
    )
    assert _steps(jobs["gate"])[npm_step]["working-directory"] == "frontend"
    check_step = next(
        i for i, s in enumerate(_steps(jobs["gate"])) if s.get("run", "").strip() == "make check"
    )
    assert npm_step < check_step
    assert pip < idx["make check"]


def test_python_matrix(wf: dict[Any, Any], repo_root: Path) -> None:
    versions = _jobs(wf)["gate"]["strategy"]["matrix"]["python-version"]
    assert all(isinstance(v, str) for v in versions)
    assert {"3.11", "3.12"} <= set(versions)
    proj = tomllib.loads((repo_root / "pyproject.toml").read_text(encoding="utf-8"))
    minimum = tuple(int(x) for x in proj["project"]["requires-python"].lstrip(">=").split("."))
    for job in _jobs(wf).values():
        for s in _steps(job):
            if s.get("uses", "").startswith("actions/setup-python"):
                v = s["with"]["python-version"]
                for item in versions if "matrix" in str(v) else [v]:
                    assert isinstance(item, str)
                    assert tuple(int(x) for x in item.split(".")) >= minimum


def test_permissions(wf: dict[Any, Any]) -> None:
    assert wf["permissions"] == {"contents": "read"}
    for job in _jobs(wf).values():
        assert "permissions" not in job


def test_timeouts_and_concurrency(wf: dict[Any, Any]) -> None:
    jobs = _jobs(wf)
    for name, limit in (("gate", 15), ("compose-smoke", 25)):
        t = jobs[name]["timeout-minutes"]
        assert isinstance(t, int) and not isinstance(t, bool)
        assert 0 < t <= limit
    # Nao cancelar o smoke em main: so cancela fora dela (expressao exata, sem `false` literal).
    group = wf["concurrency"]["group"]
    assert "github.workflow" in group and "github.ref" in group
    assert wf["concurrency"]["cancel-in-progress"] == "${{ github.ref != 'refs/heads/main' }}"
    assert jobs["gate"]["runs-on"] == "ubuntu-24.04"
    assert jobs["compose-smoke"]["runs-on"] == "ubuntu-24.04"


def test_actions_official_and_pinned(wf: dict[Any, Any]) -> None:
    uses = _uses(wf)
    assert {n for n, _, _ in uses} == ALLOWED_ACTIONS
    for name, ref, step in uses:
        assert SHA_RE.match(ref) or MAJOR_RE.match(ref), (name, ref)
        if name == "actions/checkout":
            assert step["with"]["persist-credentials"] is False


def test_caches(wf: dict[Any, Any], repo_root: Path) -> None:
    uses = _uses(wf)
    py = [s for n, _, s in uses if n == "actions/setup-python"]
    node = [s for n, _, s in uses if n == "actions/setup-node"]
    assert py and node
    assert all(s["with"]["cache"] == "pip" for s in py)
    for s in py:
        assert s["with"]["cache-dependency-path"] == "pyproject.toml"
        assert (repo_root / "pyproject.toml").is_file()
    for s in node:
        assert s["with"]["cache"] == "npm"
        dep = s["with"]["cache-dependency-path"]
        assert dep == "frontend/package-lock.json"
        assert (repo_root / dep).is_file()


def test_no_secrets_in_workflow(raw: str) -> None:
    assert "secrets." not in raw
    assert not SECRET_RE.search(raw)
    assert not PRIVATE_KEY_RE.search(raw)
    joined = raw.replace("\\\n", " ")  # junta continuacoes de linha com barra invertida
    assert not re.search(r"(curl|wget)[^\n|]*\|\s*(sudo(\s+-\S+)*\s+)?(ba|z)?sh", joined)
    assert "pull_request_target" not in raw


def test_workflow_env_has_no_literal_credentials(wf: dict[Any, Any]) -> None:
    envs = [wf.get("env", {})]
    for job in _jobs(wf).values():
        envs.append(job.get("env", {}))
        envs += [s.get("env", {}) for s in _steps(job)]
    for env in envs:
        for key, value in env.items():
            if re.search(r"PASSWORD|SECRET|TOKEN|KEY", key.upper()):
                assert str(value).startswith("${{"), key


def test_no_ultralytics_in_workflow(raw: str) -> None:
    assert "ultralytics" not in raw.lower()


def test_github_dir_not_ignored(repo_root: Path) -> None:
    lines = (repo_root / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert not any(line.strip().strip("/") in (".github", ".github/*") for line in lines)


def test_smoke_off_critical_path(wf: dict[Any, Any], raw: str) -> None:
    jobs = _jobs(wf)
    assert "compose-smoke" not in _as_list(jobs["gate"].get("needs"))
    assert "gate" not in _as_list(jobs["compose-smoke"].get("needs"))
    assert "continue-on-error" not in raw


def _as_list(v: Any) -> list[str]:
    if v is None:
        return []
    return [v] if isinstance(v, str) else list(v)


def test_smoke_filter_uses_shell_diff(wf: dict[Any, Any]) -> None:
    runs = "\n".join(_runs(_jobs(wf)["compose-smoke"]))
    assert "git diff --name-only" in runs
    for path in ("docker-compose", "docker/", ".env", "backend/src/"):
        assert path in runs


def test_node_matches_engines(wf: dict[Any, Any], repo_root: Path) -> None:
    pkg = json.loads((repo_root / "frontend" / "package.json").read_text(encoding="utf-8"))
    m = re.match(r">=(\d+)", pkg["engines"]["node"])
    assert m
    for name, _, step in _uses(wf):
        if name == "actions/setup-node":
            assert int(str(step["with"]["node-version"]).split(".")[0]) >= int(m.group(1))


def test_licencas_lists_every_action(wf: dict[Any, Any], repo_root: Path) -> None:
    text = (repo_root / "docs" / "licencas.md").read_text(encoding="utf-8")
    assert "## GitHub Actions" in text
    section = text.split("## GitHub Actions", 1)[1].split("\n## ", 1)[0]
    for name, ref, _ in _uses(wf):
        row = next((ln for ln in section.splitlines() if ln.startswith(f"| {name} ")), None)
        assert row, f"{name} sem linha em docs/licencas.md"
        assert ref in row and "MIT" in row and f"https://github.com/{name}" in row


# --- F0-06: endurecimento do CI ---

SMOKE_PATHS = (
    "docker-compose.yml",
    "docker/Dockerfile",
    ".env.example",
    "backend/src/inovalytics/x.py",
    "pyproject.toml",
    "backend/tests/test_compose_smoke.py",
)


def _smoke_steps(wf: dict[Any, Any]) -> list[dict[str, Any]]:
    return _steps(_jobs(wf)["compose-smoke"])


def _step_named(steps: list[dict[str, Any]], prefix: str) -> dict[str, Any]:
    return next(s for s in steps if str(s.get("name", "")).startswith(prefix))


def test_smoke_filter_regex(wf: dict[Any, Any]) -> None:
    run = _step_named(_smoke_steps(wf), "Decidir")["run"].replace("\\\n", " ")
    m = re.search(r"grep -E '([^']+)'", run)
    assert m
    rx = re.compile(m.group(1))
    for path in SMOKE_PATHS:
        assert rx.search(path), path
    for path in ("README.md", "frontend/src/x.ts"):
        assert not rx.search(path), path
    assert "paths-filter" not in run


def test_smoke_diff_needs_full_history(wf: dict[Any, Any]) -> None:
    steps = _smoke_steps(wf)
    checkout = next(s for s in steps if str(s.get("uses", "")).startswith("actions/checkout"))
    assert checkout["with"]["fetch-depth"] == 0
    assert "dorny" not in str(wf)


def test_smoke_junit_upload(wf: dict[Any, Any]) -> None:
    jobs = _jobs(wf)
    steps = _smoke_steps(wf)
    idx = next(i for i, s in enumerate(steps) if s.get("run", "").strip() == "make compose-smoke")
    up = [
        i
        for i, s in enumerate(steps)
        if str(s.get("uses", "")).startswith("actions/upload-artifact@")
    ]
    assert len(up) == 1 and up[0] > idx
    step = steps[up[0]]
    assert step["if"] == "always() && steps.filter.outputs.run == 'true'"
    w = step["with"]
    assert w["name"] == "smoke-junit" and w["path"] == "smoke.xml"
    assert w["if-no-files-found"] == "ignore"
    days = w["retention-days"]
    assert isinstance(days, int) and not isinstance(days, bool) and 1 <= days <= 30
    assert not any("upload-artifact" in str(s.get("uses", "")) for s in _steps(jobs["gate"]))


def _diag(wf: dict[Any, Any]) -> tuple[dict[str, Any], str]:
    step = _step_named(_smoke_steps(wf), "Diagnóstico")
    m = re.search(r"<<'PY'\n(.*?)\n\s*PY\s*$", step["run"], re.S)
    assert m
    lines = m.group(1).splitlines()
    indent = min(len(ln) - len(ln.lstrip()) for ln in lines if ln.strip())
    return step, "\n".join(ln[indent:] for ln in lines)


def _run_diag(code: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-I", "-c", code], cwd=cwd, capture_output=True, text=True, check=False
    )


def test_diagnostic_only_on_failure(wf: dict[Any, Any]) -> None:
    step, _ = _diag(wf)
    assert step["if"] == "failure() && steps.filter.outputs.run == 'true'"
    assert "secrets." not in str(step) and "env" not in step


def test_diagnostic_without_smoke_xml(wf: dict[Any, Any], tmp_path: Path) -> None:
    _, code = _diag(wf)
    r = _run_diag(code, tmp_path)
    assert r.returncode == 0, r.stderr
    assert "::warning::" in r.stdout and "::error::" not in r.stdout


def test_diagnostic_with_failure_and_truncated_xml(wf: dict[Any, Any], tmp_path: Path) -> None:
    _, code = _diag(wf)
    xml = (
        '<testsuites><testsuite><testcase name="test_boom">'
        '<failure message="falhou 100%">trace</failure></testcase></testsuite></testsuites>'
    )
    (tmp_path / "smoke.xml").write_text(xml, encoding="utf-8")
    r = _run_diag(code, tmp_path)
    assert r.returncode == 0, r.stderr
    assert "::error::test_boom" in r.stdout and "100%25" in r.stdout
    (tmp_path / "smoke.xml").write_text(xml[:60], encoding="utf-8")
    r = _run_diag(code, tmp_path)
    assert r.returncode == 0, r.stderr
    assert "::warning::" in r.stdout


def test_pinning_policy_matches_adr(wf: dict[Any, Any], raw: str, repo_root: Path) -> None:
    adr = (repo_root / "docs" / "decisoes.md").read_text(encoding="utf-8")
    section = adr.split("## ADR-003", 1)[1].split("\n## ADR-004", 1)[0]
    declared = re.findall(r"^Pinagem: (major|SHA)\b", section, re.M)
    assert len(declared) == 1, declared
    for name, ref, _ in _uses(wf):
        assert name in ALLOWED_ACTIONS
        if declared[0] == "major":
            assert MAJOR_RE.match(ref), (name, ref)
        else:
            assert SHA_RE.match(ref), (name, ref)
    if declared[0] == "SHA":
        for line in raw.splitlines():
            if "uses:" in line:
                assert re.search(r"#\s*v\d+\.\d+\.\d+", line), line


def test_all_uses_are_official(raw: str) -> None:
    for m in re.finditer(r"^\s*-?\s*uses:\s*(\S+)", raw, re.M):
        assert m.group(1).startswith("actions/"), m.group(1)
        assert "docker://" not in m.group(1)
    assert not re.search(r"@(main|master)\b", raw)


def test_gate_cannot_be_bypassed(wf: dict[Any, Any], repo_root: Path) -> None:
    steps = _steps(_jobs(wf)["gate"])
    step = next(s for s in steps if s.get("run", "").strip() == "make check")
    assert not {"if", "continue-on-error"} & step.keys()
    for s in steps:
        run = s.get("run", "")
        assert "|| true" not in run and "set +e" not in run
        assert "continue-on-error" not in s
    mk = (repo_root / "Makefile").read_text(encoding="utf-8")
    deps = _make_check_deps(mk)
    assert {
        "lint-backend",
        "typecheck",
        "test",
        "licencas",
        "lint-frontend",
        "test-frontend",
    } <= deps, deps


def _make_logical_lines(mk: str) -> list[str]:
    return mk.replace("\\\n", " ").splitlines()


def _make_check_deps(mk: str) -> set[str]:
    """Dependências do alvo `check`: pré-requisitos na linha do alvo e `$(MAKE) x` na receita."""
    lines = _make_logical_lines(mk)
    start = next(
        (i for i, ln in enumerate(lines) if re.match(r"^check\s*:(?!=)", ln)),
        None,
    )
    assert start is not None, "alvo `check` ausente no Makefile"
    deps = set(lines[start].split(":", 1)[1].split("#", 1)[0].split())
    for ln in lines[start + 1 :]:
        if ln.strip() and not ln.startswith(("\t", " ", "#")):
            break  # próximo alvo ou atribuição
        deps.update(re.findall(r"\$\(MAKE\)\s+([\w.-]+)", ln))
    return deps


def test_check_target_runs_every_tool(repo_root: Path) -> None:
    mk = (repo_root / "Makefile").read_text(encoding="utf-8")
    lines = _make_logical_lines(mk)
    deps = _make_check_deps(mk)
    expected = {
        "lint-backend": "ruff check",
        "typecheck": "mypy",
        "test": "pytest",
        "licencas": "license_check",
        "lint-frontend": "run lint",
        "test-frontend": "run test",
    }
    for dep, tool in expected.items():
        assert dep in deps, dep
        i = next(i for i, ln in enumerate(lines) if re.match(rf"^{re.escape(dep)}\s*:", ln))
        recipe = []
        for ln in lines[i + 1 :]:
            if ln.strip() and not ln.startswith(("\t", " ", "#")):
                break
            recipe.append(ln)
        assert tool in "\n".join(recipe), (dep, tool)


def test_licencas_has_no_extra_action_rows(wf: dict[Any, Any], repo_root: Path) -> None:
    text = (repo_root / "docs" / "licencas.md").read_text(encoding="utf-8")
    section = text.split("## GitHub Actions", 1)[1].split("\n## ", 1)[0]
    rows = {m.group(1) for m in re.finditer(r"^\| (actions/[\w.-]+) ", section, re.M)}
    assert rows == {n for n, _, _ in _uses(wf)}
