"""F0-04b: coletores (Python, npm, imagens, actions) e registro de transitivas, sem rede."""

from __future__ import annotations

import hashlib
import json
import textwrap
from datetime import date
from pathlib import Path

import pytest

from inovalytics.tools import license_collect as lc
from inovalytics.tools import license_policy as lp
from inovalytics.tools import license_registry as lr
from inovalytics.tools.license_check import run

VENDOR = "ultra" + "lytics"


# ---------------------------------------------------------------- Python


def make_dist(
    site: Path,
    name: str,
    version: str = "1.0",
    requires: tuple[str, ...] = (),
    *,
    license_field: str | None = "MIT",
    expression: str | None = None,
    classifiers: tuple[str, ...] = (),
    extra: tuple[str, ...] = (),
) -> None:
    d = site / f"{name.replace('-', '_')}-{version}.dist-info"
    d.mkdir(parents=True)
    lines = ["Metadata-Version: 2.4", f"Name: {name}", f"Version: {version}"]
    if expression:
        lines.append(f"License-Expression: {expression}")
    if license_field:
        lines.append(f"License: {license_field}")
    lines += [f"Classifier: {c}" for c in classifiers]
    lines += [f"Requires-Dist: {r}" for r in requires]
    lines += list(extra)
    (d / "METADATA").write_text("\n".join(lines) + "\n", encoding="utf-8")


def make_project(root: Path, deps: tuple[str, ...] = (), dev: tuple[str, ...] = ()) -> None:
    root.mkdir(parents=True, exist_ok=True)
    d = ", ".join(f'"{x}"' for x in deps)
    v = ", ".join(f'"{x}"' for x in dev)
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "inovalytics"\nversion = "0.1"\ndependencies = [{d}]\n'
        f"[project.optional-dependencies]\ndev = [{v}]\n",
        encoding="utf-8",
    )


def by_name(col: lc.Collected) -> dict[str, lp.Component]:
    return {c.name: c for c in col.components}


def test_python_closure_groups_and_filters(tmp_path: Path) -> None:
    site, root = tmp_path / "site", tmp_path / "repo"
    make_project(root, deps=("app_lib>=1",), dev=("tool", "app-lib"))
    make_dist(
        site, "app-lib", requires=("typing_extensions>=4", "win-only; sys_platform == 'win32'")
    )
    make_dist(site, "typing-extensions", version="4.0")
    make_dist(site, "tool", requires=("helper", "tool-extra; extra == 'x'"))
    make_dist(site, "helper", requires=("tool",))  # ciclo
    make_dist(site, "pip", version="26")
    make_dist(site, "inovalytics")
    make_dist(site, "unrelated")
    got = by_name(lc.collect_python(root, [str(site)]))
    assert set(got) == {"app-lib", "typing-extensions", "tool", "helper"}
    assert got["app-lib"].group == "runtime" and got["app-lib"].direct
    assert got["typing-extensions"].group == "runtime" and not got["typing-extensions"].direct
    assert got["tool"].group == "dev" and got["helper"].group == "dev"
    assert got["typing-extensions"].required_by == ("app-lib",)


def test_python_missing_required_package_is_reported(tmp_path: Path) -> None:
    site, root = tmp_path / "site", tmp_path / "repo"
    make_project(root, deps=("app",))
    make_dist(site, "app", requires=("ghost>=1", "other; python_version < '2'"))
    col = lc.collect_python(root, [str(site)])
    ghost = by_name(col)["ghost"]
    assert not ghost.installed
    report = lp.verify(
        lp.Snapshot(col.components), lp.DEFAULT_POLICY, {}, [], today=date(2026, 10, 28)
    )
    assert (lp.AUSENTE, "ghost") in [(v.code, v.package) for v in report.violations]
    assert "other" not in by_name(col)


def test_python_extras_pull_extra_dependencies(tmp_path: Path) -> None:
    site, root = tmp_path / "site", tmp_path / "repo"
    make_project(root, deps=("web[fast]",))
    make_dist(site, "web", requires=("speedy; extra == 'fast'", "slow; extra == 'slow'"))
    make_dist(site, "speedy")
    make_dist(site, "slow")
    assert set(by_name(lc.collect_python(root, [str(site)]))) == {"web", "speedy"}


def test_python_metadata_fields_and_vendor_references(tmp_path: Path) -> None:
    site, root = tmp_path / "site", tmp_path / "repo"
    make_project(root, deps=("a",))
    make_dist(site, "a", requires=("b",), license_field=None, expression="MIT")
    make_dist(
        site,
        "b",
        requires=(f"{VENDOR}>=8; extra == 'x'",),
        license_field="UNKNOWN",
        classifiers=("License :: OSI Approved :: MIT License",),
    )
    got = by_name(lc.collect_python(root, [str(site)]))
    assert got["a"].expression == "MIT"
    assert got["b"].license_field == "UNKNOWN" and len(got["b"].classifiers) == 1
    rep = lp.verify(
        lp.Snapshot(list(got.values())), lp.DEFAULT_POLICY, {}, [], today=date(2026, 10, 28)
    )
    got_codes = [(v.code, v.package) for v in rep.violations if v.code != lp.NAO_REGISTRADO]
    assert got_codes == [(lp.ULTRALYTICS, "b")]


def test_python_bad_pyproject_is_input_error(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project\n", encoding="utf-8")
    with pytest.raises(lc.InputError):
        lc.collect_python(tmp_path, [])


# ---------------------------------------------------------------- npm


def write_npm(root: Path, packages: dict[str, object], version: int = 3, **pkg: object) -> None:
    (root / "frontend").mkdir(parents=True, exist_ok=True)
    (root / "frontend" / "package-lock.json").write_text(
        json.dumps({"lockfileVersion": version, "packages": {"": {}, **packages}}),
        encoding="utf-8",
    )
    (root / "frontend" / "package.json").write_text(json.dumps(pkg), encoding="utf-8")


REG = "https://registry.npmjs.org/"


def test_npm_lock_basic(tmp_path: Path) -> None:
    write_npm(
        tmp_path,
        {
            "node_modules/@scope/pk": {
                "version": "1.0.0",
                "license": "MIT",
                "dev": True,
                "resolved": REG + "x.tgz",
            },
            "node_modules/a/node_modules/b": {
                "version": "2.0.0",
                "license": "ISC",
                "devOptional": True,
            },
            "node_modules/rt": {"version": "3.0.0", "license": "Apache-2.0"},
            "node_modules/legacy": {"version": "1", "license": {"type": "MIT", "url": "x"}},
            "node_modules/old": {"version": "1", "license": ["MIT", {"type": "ISC"}]},
            "node_modules/semlic": {"version": "1"},
            "node_modules/weird": {"version": "1", "license": 42},
        },
        devDependencies={"@scope/pk": "^1"},
    )
    got = by_name(lc.collect_npm(tmp_path))
    assert set(got) == {"@scope/pk", "b", "rt", "legacy", "old", "semlic", "weird"}
    assert got["@scope/pk"].group == "dev" and got["@scope/pk"].direct
    assert got["b"].group == "dev" and not got["b"].direct
    assert got["rt"].group == "runtime"
    assert got["legacy"].expression == "MIT" and got["old"].expression == "(MIT OR ISC)"
    rep = lp.verify(
        lp.Snapshot(list(got.values())), lp.DEFAULT_POLICY,
        {lp.registry_key("npm", "@scope/pk"): lp.RegEntry("@scope/pk", "MIT")}, [],
        today=date(2026, 10, 28),
    )  # fmt: skip
    assert sorted((v.code, v.package) for v in rep.violations) == [
        (lp.AUSENTE, "semlic"),
        (lp.DESCONHECIDA, "weird"),
    ]


@pytest.mark.parametrize("version", [1, 4, None])
def test_npm_unsupported_lockfile_version(tmp_path: Path, version: int | None) -> None:
    write_npm(tmp_path, {}, version=version)  # type: ignore[arg-type]
    with pytest.raises(lc.InputError):
        lc.collect_npm(tmp_path)


@pytest.mark.parametrize(
    "resolved",
    ["git+ssh://git@host/x.git", "file:../x", "https://evil.example/x.tgz"],
)
def test_npm_non_registry_source_fails_closed(tmp_path: Path, resolved: str) -> None:
    write_npm(
        tmp_path, {"node_modules/x": {"version": "1", "license": "MIT", "resolved": resolved}}
    )
    col = lc.collect_npm(tmp_path)
    assert [(v.code, v.package) for v in col.issues] == [(lp.DESCONHECIDA, "x")]


def test_npm_declared_dependency_missing_from_lock(tmp_path: Path) -> None:
    write_npm(tmp_path, {}, devDependencies={"faltando": "^1"})
    col = lc.collect_npm(tmp_path)
    assert [(v.code, v.package) for v in col.issues] == [(lp.NAO_REGISTRADO, "faltando")]
    assert "lock desatualizado" in col.issues[0].message


def test_npm_vendor_deep_chain(tmp_path: Path) -> None:
    write_npm(
        tmp_path,
        {
            "node_modules/a": {"version": "1", "license": "MIT", "dependencies": {"b": "^1"}},
            "node_modules/b": {"version": "1", "license": "MIT", "peerDependencies": {VENDOR: "*"}},
        },
    )
    got = list(lc.collect_npm(tmp_path).components)
    rep = lp.verify(lp.Snapshot(got), lp.DEFAULT_POLICY, {}, [], today=date(2026, 10, 28))
    assert [(v.code, v.package) for v in rep.violations] == [(lp.ULTRALYTICS, "b")]


def test_real_npm_lock_is_clean(repo_root: Path) -> None:
    col = lc.collect_npm(repo_root)
    assert len(col.components) == 203 and not col.issues
    assert {c.group for c in col.components} == {"dev"}


# ---------------------------------------------------------------- imagens e actions


def rows(*items: tuple[str, str, str]) -> list[lr.DocRow]:
    return [lr.DocRow(n, v, s, "https://x") for n, v, s in items]


def write_images(root: Path, compose: str, dockerfile: str) -> None:
    (root / "docker" / "api").mkdir(parents=True, exist_ok=True)
    (root / "docker-compose.yml").write_text(textwrap.dedent(compose), encoding="utf-8")
    (root / "docker" / "api" / "Dockerfile").write_text(
        textwrap.dedent(dockerfile), encoding="utf-8"
    )


COMPOSE = """\
services:
  db:
    image: postgres:17.6
  api:
    build: .
    image: inovalytics-api:0.1.0
"""


def test_images_ok_with_stages_platform_scratch_and_digest(tmp_path: Path) -> None:
    write_images(
        tmp_path,
        COMPOSE,
        """\
        FROM --platform=linux/amd64 python:3.12.11-slim-bookworm AS build
        FROM build AS final
        FROM scratch
        FROM postgres:17.6@sha256:abc
        """,
    )
    col = lc.collect_images(
        tmp_path,
        rows(("postgres (imagem oficial)", "17.6", "PostgreSQL"),
             ("python (imagem base)", "3.12.11", "PSF-2.0")),
    )  # fmt: skip
    assert not col.issues
    assert sorted(c.name for c in col.components) == ["postgres", "python"]


@pytest.mark.parametrize(
    ("dockerfile", "fragment"),
    [
        ("FROM python\n", "sem tag"),
        ("FROM python:latest\n", "sem tag"),
        ("FROM python:${PY}\n", "não resolvida"),
    ],
)
def test_images_unpinned_or_unresolved_fail(tmp_path: Path, dockerfile: str, fragment: str) -> None:
    write_images(tmp_path, "services: {}\n", dockerfile)
    col = lc.collect_images(tmp_path, rows(("python", "3.12.11", "PSF-2.0")))
    assert any(fragment in v.message for v in col.issues)


def test_images_unregistered_wrong_tag_and_unused_row(tmp_path: Path) -> None:
    write_images(tmp_path, COMPOSE, "FROM python:3.11.1\n")
    col = lc.collect_images(
        tmp_path, rows(("python", "3.12.11", "PSF-2.0"), ("redis", "7", "BSD-3-Clause"))
    )
    got = sorted((v.code, v.package) for v in col.issues)
    assert got == [
        (lp.DIVERGE_DO_REGISTRO, "python:3.11.1"),
        (lp.DIVERGE_DO_REGISTRO, "redis"),
        (lp.NAO_REGISTRADO, "postgres:17.6"),
    ]


def write_workflow(root: Path, uses: list[str]) -> None:
    wf = root / ".github" / "workflows"
    wf.mkdir(parents=True, exist_ok=True)
    body = "jobs:\n  j:\n    steps:\n" + "".join(f"      - uses: {u}\n" for u in uses)
    (wf / "ci.yml").write_text(body, encoding="utf-8")


def test_actions_ok_and_failures(tmp_path: Path) -> None:
    write_workflow(tmp_path, ["actions/checkout@v5", "actions/checkout@v5", "./local"])
    ok = lc.collect_actions(tmp_path, rows(("actions/checkout", "v5", "MIT")))
    assert not ok.issues and [c.name for c in ok.components] == ["actions/checkout"]
    write_workflow(
        tmp_path, ["evil/act@v1", "actions/cache@v4", "docker://x:1", "actions/checkout@v4"]
    )
    bad = lc.collect_actions(
        tmp_path, rows(("actions/checkout", "v5", "MIT"), ("actions/setup-node", "v6", "MIT"))
    )
    assert sorted(v.package for v in bad.issues) == sorted(
        [
            "evil/act@v1",
            "actions/cache@v4",
            "docker://x:1",
            "actions/checkout@v4",
            "actions/setup-node",
        ]
    ) + ["evil/act@v1"]


# ---------------------------------------------------------------- registro de transitivas


def comps_for_registry() -> list[lp.Component]:
    return [
        lp.Component("python", "alfa", "1", "runtime", expression="MIT", required_by=("app",)),
        lp.Component("python", "beta_x", "1", "dev", expression="Apache-2.0", required_by=("t",)),
        lp.Component("python", "direta", "1", "runtime", expression="MIT", direct=True),
    ]


def verify_with(rows_: dict[str, lr.TransRow], comps: list[lp.Component]) -> lp.Report:
    doc = lr.LicencasDoc((lr.DocRow("direta", "1", "MIT", "https://x"),), (), ())
    return lp.verify(
        lp.Snapshot(comps), lp.DEFAULT_POLICY, lr.build_registry(doc, rows_), [],
        today=date(2026, 10, 28),
    )  # fmt: skip


def test_registry_generation_is_sorted_deterministic_and_round_trips() -> None:
    comps = comps_for_registry()
    rows_ = lr.generate_rows(comps)
    text = lr.render_transitivas(rows_)
    assert text == lr.render_transitivas(lr.generate_rows(list(reversed(comps))))
    assert "| Nome | Licença (SPDX) | Grupo | Exigido por |" in text
    assert text.index("| alfa ") < text.index("| beta-x ")
    assert "direta" not in text
    assert lr.parse_transitivas(text) == rows_
    assert verify_with(lr.parse_transitivas(text), comps).ok


def test_registry_missing_divergent_group_and_extra_rows() -> None:
    comps = comps_for_registry()
    rows_ = lr.generate_rows(comps)
    missing = {k: v for k, v in rows_.items() if k != "alfa"}
    assert [(v.code, v.package) for v in verify_with(missing, comps).violations] == [
        (lp.NAO_REGISTRADO, "alfa")
    ]
    changed = dict(rows_)
    changed["alfa"] = lr.TransRow("alfa", "Apache-2.0", "runtime", ())
    rep = verify_with(changed, comps)
    assert [v.code for v in rep.violations] == [lp.DIVERGE_DO_REGISTRO]
    wrong_group = dict(rows_)
    wrong_group["alfa"] = lr.TransRow("alfa", "MIT", "dev", ())
    assert [v.code for v in verify_with(wrong_group, comps).violations] == [lp.DIVERGE_DO_REGISTRO]
    extra = dict(rows_)
    extra["sobrando"] = lr.TransRow("sobrando", "MIT", "dev", ())
    assert verify_with(extra, comps).ok


def test_merge_never_prunes_without_flag() -> None:
    existing = {"velha": lr.TransRow("velha", "MIT", "dev", ("x",))}
    generated = lr.generate_rows(comps_for_registry())
    assert "velha" in lr.merge_rows(existing, generated, prune=False)
    assert "velha" not in lr.merge_rows(existing, generated, prune=True)
    both = lr.merge_rows(
        {"alfa": lr.TransRow("alfa", "MIT", "dev", ("z",))}, generated, prune=False
    )
    assert both["alfa"].group == "runtime" and both["alfa"].required_by == ("app", "z")


# ---------------------------------------------------------------- repositórios reais e CLI-write


def digest(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def test_committed_files_exist_and_exception_is_only_certifi(repo_root: Path) -> None:
    import tomllib

    data = tomllib.loads((repo_root / "docs" / "licencas-excecoes.toml").read_text("utf-8"))
    assert len(data["excecao"]) == 1
    e = data["excecao"][0]
    assert (e["pacote"], e["grupo"], e["licenca_verificada"], e["adr"], e["data"]) == (
        "certifi", "dev", "MPL-2.0", "ADR-002", "2026-10-28",
    )  # fmt: skip
    assert lp.verify(
        lp.Snapshot([]), lp.DEFAULT_POLICY, {}, data["excecao"], today=date(2026, 10, 28)
    ).ok
    text = (repo_root / "docs" / "licencas-transitivas.md").read_text("utf-8")
    assert "| Nome | Licença (SPDX) | Grupo | Exigido por |" in text
    assert lr.parse_transitivas(text)


def test_packaging_is_declared_and_registered(repo_root: Path) -> None:
    import tomllib

    proj = tomllib.loads((repo_root / "pyproject.toml").read_text("utf-8"))["project"]
    assert any(r.startswith("packaging") for r in proj["optional-dependencies"]["dev"])
    doc = lr.parse_licencas((repo_root / "docs" / "licencas.md").read_text("utf-8"))
    row = next(r for r in doc.deps if r.name == "packaging")
    assert (
        row.spdx == "Apache-2.0 OR BSD-2-Clause" and row.version and row.url.startswith("https://")
    )


def test_certifi_in_runtime_would_fail() -> None:
    c = lp.Component(
        "python", "certifi", "2026.10.23", "runtime", license_field="MPL-2.0", needs_registry=False
    )
    e = {
        "ecossistema": "python", "pacote": "certifi", "versoes": ">=2026.1", "grupo": "dev",
        "licenca_declarada": "MPL-2.0", "licenca_verificada": "MPL-2.0",
        "evidencia": "https://x.example/l", "justificativa": "j", "aprovador": "h",
        "data": "2026-10-28", "adr": "ADR-002",
    }  # fmt: skip
    rep = lp.verify(lp.Snapshot([c]), lp.DEFAULT_POLICY, {}, [e], today=date(2026, 10, 28))
    assert [v.code for v in rep.violations] == [lp.PROIBIDA]


def build_repo(root: Path, site: Path) -> None:
    make_project(root, deps=("libx",))
    make_dist(site, "libx", requires=("liby",))
    make_dist(site, "liby", license_field="BSD-3-Clause")
    write_npm(root, {}, devDependencies={})
    write_images(root, "services: {}\n", "FROM scratch\n")
    write_workflow(root, ["actions/checkout@v5"])
    (root / "docs").mkdir()
    (root / "docs" / "licencas.md").write_text(
        "| Nome | Versão | Licença (SPDX) | URL |\n|---|---|---|---|\n"
        "| libx | 1.0 | MIT | https://x |\n\n## Imagens de contêiner\n\n"
        "| Nome | Versão | Licença (SPDX) | URL |\n|---|---|---|---|\n\n"
        "## GitHub Actions\n\n| Nome | Versão | Licença (SPDX) | URL |\n|---|---|---|---|\n"
        "| actions/checkout | v5 | MIT | https://x |\n",
        encoding="utf-8",
    )
    (root / "docs" / "licencas-excecoes.toml").write_text("", encoding="utf-8")
    (root / "docs" / "licencas-transitivas.md").write_text(
        lr.render_transitivas({}), encoding="utf-8"
    )


def test_write_unions_and_verification_never_writes(tmp_path: Path) -> None:
    root, site = tmp_path / "repo", tmp_path / "site"
    build_repo(root, site)
    trans = root / "docs" / "licencas-transitivas.md"
    before = digest(trans)
    code, out = run(root, [str(site)], write=False)
    assert code == 1 and "liby" in out and digest(trans) == before
    code, _ = run(root, [str(site)], write=True)
    assert code == 0 and digest(trans) != before
    # linha manual extra sobrevive a --write sem --prune e some com --prune
    trans.write_text(trans.read_text("utf-8") + "| velha | MIT | dev | x |\n", encoding="utf-8")
    assert run(root, [str(site)], write=True)[0] == 0 and "velha" in trans.read_text("utf-8")
    after = digest(trans)
    assert run(root, [str(site)], write=False)[0] == 0 and digest(trans) == after
    assert run(root, [str(site)], write=True, prune=True)[0] == 0
    assert "velha" not in trans.read_text("utf-8")
