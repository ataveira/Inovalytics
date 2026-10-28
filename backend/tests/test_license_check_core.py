"""F0-04a: núcleo puro da verificação de licenças (dados fictícios, nada é instalado)."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Any

import pytest

from inovalytics.tools import license_policy as lp
from inovalytics.tools.license_policy import (
    DEFAULT_POLICY,
    Component,
    RegEntry,
    Snapshot,
    Violation,
    normalize,
    normalize_classifier,
)

VENDOR = "ultra" + "lytics"
TODAY = date(2026, 10, 28)


def comp(name: str = "pkg", **kw: Any) -> Component:
    base: dict[str, Any] = {
        "ecosystem": "python",
        "name": name,
        "version": "1.0",
        "group": "runtime",
        "needs_registry": False,
    }
    base.update(kw)
    return Component(**base)


def run(
    components: list[Component],
    registry: Mapping[tuple[str, str], RegEntry] | None = None,
    exceptions: list[dict[str, object]] | None = None,
    issues: tuple[Violation, ...] = (),
) -> lp.Report:
    return lp.verify(
        Snapshot(components, issues),
        DEFAULT_POLICY,
        registry or {},
        exceptions or [],
        today=TODAY,
    )


def codes(report: lp.Report) -> list[tuple[str, str]]:
    return [(v.code, v.package) for v in report.violations]


def exc(**kw: Any) -> dict[str, object]:
    base: dict[str, object] = {
        "ecossistema": "python",
        "pacote": "certifi",
        "versoes": ">=2026.1",
        "grupo": "dev",
        "licenca_declarada": "MPL-2.0",
        "licenca_verificada": "MPL-2.0",
        "evidencia": "https://example.org/licenca",
        "justificativa": "copyleft fraco por arquivo, sem modificação",
        "aprovador": "humano",
        "data": "2026-10-28",
        "adr": "ADR-002",
    }
    base.update(kw)
    return base


# 1. pureza e determinismo ---------------------------------------------------------------------


def test_verify_is_deterministic_and_sorted() -> None:
    comps = [comp("zeta", expression="GPL-3.0-only"), comp("alfa", expression="AGPL-3.0-only")]
    r1, r2 = run(comps), run(list(reversed(comps)))
    assert r1 == r2
    assert [v.package for v in r1.violations] == ["alfa", "zeta"]
    v = r1.violations[0]
    assert (v.code, v.ecosystem, v.group, v.version) == (lp.PROIBIDA, "python", "runtime", "1.0")
    assert "alfa" in v.message


# 2. normalização -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("mit", "MIT"),
        ("apache-2.0", "Apache-2.0"),
        ("BSD-3-CLAUSE", "BSD-3-Clause"),
        ("MIT OR Apache-2.0", "(Apache-2.0 OR MIT)"),
        ("(MIT AND ISC) OR Zlib", "((ISC AND MIT) OR Zlib)"),
        ("MIT License", "MIT"),
        ("Apache 2.0", "Apache-2.0"),
        ("Apache License, Version 2.0", "Apache-2.0"),
        ("Python Software Foundation License", "PSF-2.0"),
        ("ISC License (ISCL)", "ISC"),
        ("The Unlicense (Unlicense)", "Unlicense"),
        ("Mozilla Public License 2.0 (MPL 2.0)", "MPL-2.0"),
        ("GNU Lesser General Public License v3 (LGPLv3)", "LGPL-3.0-only"),
        ("GNU General Public License v2 or later (GPLv2+)", "GPL-2.0-or-later"),
        ("Python-2.0", "Python-2.0"),
        ("BlueOak-1.0.0", "BlueOak-1.0.0"),
    ],
)
def test_normalize_to_spdx(text: str, expected: str) -> None:
    n = normalize(text)
    assert n.tree is not None, text
    assert lp.canonical(n.tree) == expected


@pytest.mark.parametrize("text", ["BSD License", "Apache Software License"])
def test_generic_classifiers_are_marked(text: str) -> None:
    n = normalize(text)
    assert n.tree is not None and n.generic
    ids = lp.id_set(n.tree)
    assert ids == {"BSD-3-Clause"} or ids == {"Apache-2.0"}
    report = run([comp("gen", classifiers=(f"License :: OSI Approved :: {text}",))])
    assert report.ok
    assert any(lp.GENERIC_MARK in n for n in report.notes)


@pytest.mark.parametrize(
    "text",
    [None, "", "   ", "UNKNOWN", "unknown", "Dual License", "MIT WITH Classpath-exception-2.0",
     "(MIT", "MIT OR", "x" * 150, "Permission is hereby granted,\nfree of charge, to any person"],
)  # fmt: skip
def test_unknown_inputs(text: str | None) -> None:
    assert normalize(text).tree is None


def test_non_license_classifier_is_ignored() -> None:
    assert normalize_classifier("Programming Language :: Python") is None


# 3. negativos e positivos --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("expr", "code"),
    [
        ("AGPL-3.0", lp.PROIBIDA),
        ("GPL-2.0-only", lp.PROIBIDA),
        ("LGPL-3.0", lp.PROIBIDA),
        ("SSPL-1.0", lp.PROIBIDA),
        ("Proprietary", lp.PROIBIDA),
        ("GPL-3.0 AND MIT", lp.PROIBIDA),
        ("MPL-2.0", lp.PROIBIDA),
        ("Some Strange Terms", lp.DESCONHECIDA),
        ("UNKNOWN", lp.DESCONHECIDA),
        (None, lp.AUSENTE),
    ],
)
def test_failures_cite_package(expr: str | None, code: str) -> None:
    report = run([comp("meupkg", expression=expr)])
    assert [(v.code, v.package) for v in report.violations] == [(code, "meupkg")]
    assert "meupkg" in report.violations[0].message


@pytest.mark.parametrize(
    "expr", ["MIT OR GPL-3.0", "BSD-3-Clause OR Apache-2.0", "MIT AND Apache-2.0"]
)
def test_passing_expressions(expr: str) -> None:
    assert run([comp(expression=expr)]).ok


def test_missing_license_without_any_field_is_ausente() -> None:
    assert codes(run([comp("semlic")])) == [(lp.AUSENTE, "semlic")]


# 4. múltiplas fontes -------------------------------------------------------------------------


def test_expression_prevails_over_field_and_classifiers() -> None:
    c = comp(expression="MIT", license_field="GPL-3.0", classifiers=("License :: GPL",))
    assert run([c]).ok


def test_field_and_classifier_conflict_fails() -> None:
    c = comp(
        "conflito",
        license_field="MIT",
        classifiers=("License :: OSI Approved :: GNU General Public License v3 (GPLv3)",),
    )
    assert codes(run([c])) == [(lp.PROIBIDA, "conflito")]


def test_classifier_passes_with_unreadable_field() -> None:
    c = comp(license_field="x" * 300, classifiers=("License :: OSI Approved :: MIT License",))
    assert run([c]).ok


def test_unreadable_field_alone_fails() -> None:
    c = comp("ilegivel", license_field="Permission is hereby granted,\nfree of charge")
    assert codes(run([c])) == [(lp.DESCONHECIDA, "ilegivel")]


# 5. exceções ---------------------------------------------------------------------------------


def certifi(group: str = "dev", version: str = "2026.10.23") -> Component:
    return comp("certifi", version=version, group=group, license_field="MPL-2.0")


def test_mpl_requires_exception() -> None:
    assert codes(run([certifi()])) == [(lp.PROIBIDA, "certifi")]


def test_valid_exception_applies_and_is_reported() -> None:
    report = run([certifi()], exceptions=[exc()])
    assert report.ok
    assert report.used_exceptions == ("certifi >=2026.1 (python/dev)",)


def test_exception_in_wrong_group_version_or_package_does_not_apply() -> None:
    assert codes(run([certifi("runtime")], exceptions=[exc()])) == [(lp.PROIBIDA, "certifi")]
    assert codes(run([certifi(version="2025.1")], exceptions=[exc()])) == [(lp.PROIBIDA, "certifi")]
    other = comp("pathspec", group="dev", license_field="MPL-2.0")
    assert codes(run([other], exceptions=[exc()])) == [(lp.PROIBIDA, "pathspec")]


def test_exception_requires_matching_declared_license() -> None:
    assert codes(run([certifi()], exceptions=[exc(licenca_declarada="AUSENTE")])) == [
        (lp.PROIBIDA, "certifi")
    ]
    sem = comp("certifi", group="dev", version="2026.10.23")
    assert run([sem], exceptions=[exc(licenca_declarada="AUSENTE")]).ok


@pytest.mark.parametrize(
    "bad",
    [
        {"aprovador": ""},
        {"justificativa": "  "},
        {"data": "2026-13-45"},
        {"data": "28/10/2026"},
        {"data": "2027-01-01"},
        {"versoes": "*"},
        {"versoes": ""},
        {"licenca_verificada": "GPL-3.0"},
        {"licenca_verificada": "Some Strange Terms"},
        {"grupo": "ambos"},
        {"ecossistema": "ruby"},
        {"evidencia": "ftp://x"},
        {"adr": None},
    ],
)
def test_invalid_exception_fails_even_if_unused(bad: dict[str, object]) -> None:
    report = run([], exceptions=[exc(**bad)])
    assert [v.code for v in report.violations] == [lp.EXCECAO_INVALIDA]
    assert "certifi" in report.violations[0].message


def test_exception_missing_field_and_duplicate() -> None:
    incomplete = exc()
    del incomplete["aprovador"]
    assert [v.code for v in run([], exceptions=[incomplete]).violations] == [lp.EXCECAO_INVALIDA]
    dup = run([], exceptions=[exc(), exc()])
    assert [v.code for v in dup.violations] == [lp.EXCECAO_INVALIDA]


def test_exception_for_forbidden_never_applies() -> None:
    gpl = comp("certifi", group="dev", license_field="GPL-3.0")
    report = run([gpl], exceptions=[exc(licenca_declarada="GPL-3.0")])
    assert codes(report) == [(lp.PROIBIDA, "certifi")]
    vend = comp(VENDOR, group="dev", license_field="MPL-2.0")
    e = exc(pacote=VENDOR, licenca_declarada="MPL-2.0")
    assert (lp.ULTRALYTICS, VENDOR) in codes(run([vend], exceptions=[e]))


def test_unused_exception_is_listed_not_failed() -> None:
    report = run([], exceptions=[exc()])
    assert report.ok and report.unused_exceptions == ("certifi >=2026.1 (python/dev)",)


def test_npm_exception_with_semver_clauses() -> None:
    c = comp("estranho", ecosystem="npm", version="1.4.2", group="dev", expression="MPL-2.0")
    e = exc(
        ecossistema="npm", pacote="estranho", versoes=">=1.0.0 <2.0.0", licenca_declarada="MPL-2.0"
    )
    assert run([c], exceptions=[e]).ok
    out = comp("estranho", ecosystem="npm", version="2.0.0", group="dev", expression="MPL-2.0")
    assert not run([out], exceptions=[e]).ok


# 6. Ultralytics ------------------------------------------------------------------------------


@pytest.mark.parametrize("eco", ["python", "npm"])
@pytest.mark.parametrize("name", [VENDOR, VENDOR + "-thop", VENDOR.title() + "_x"])
def test_vendor_name_prefix_fails_in_dev_too(eco: str, name: str) -> None:
    c = comp(name, ecosystem=eco, group="dev", expression="MIT")
    assert (lp.ULTRALYTICS, name) in codes(run([c]))


@pytest.mark.parametrize(
    "ref",
    [
        f"https://github.com/{VENDOR}/{VENDOR}",
        f"{VENDOR}>=8",
        f"github:{VENDOR}/algo",
        f"https://registry.npmjs.org/{VENDOR}/-/x.tgz",
        VENDOR.title() + " Inc.",
    ],
)
def test_vendor_in_references_fails(ref: str) -> None:
    c = comp("inocente", expression="MIT", references=("requests", ref))
    assert codes(run([c])) == [(lp.ULTRALYTICS, "inocente")]


def test_vendor_deep_chain_is_caught_python_and_npm() -> None:
    for eco in ("python", "npm"):
        chain = [
            comp("a", ecosystem=eco, expression="MIT", references=("b",)),
            comp("b", ecosystem=eco, expression="MIT", references=(f"{VENDOR}>=8; extra == 'x'",)),
        ]
        assert codes(run(chain)) == [(lp.ULTRALYTICS, "b")]


def test_vendor_in_image_and_action() -> None:
    img = comp(f"docker.io/{VENDOR}/{VENDOR}", ecosystem="image", expression="MIT")
    act = comp(f"{VENDOR}/setup", ecosystem="action", group="dev", expression="MIT")
    assert [c for c, _ in codes(run([img, act]))] == [lp.ULTRALYTICS, lp.ULTRALYTICS]


# 7. grupo ------------------------------------------------------------------------------------


@pytest.mark.parametrize("group", [None, "prod", ""])
def test_invalid_group(group: str | None) -> None:
    report = run([comp("g", group=group, expression="MIT")])
    assert [v.code for v in report.violations] == [lp.GRUPO_INVALIDO]


def test_counts_include_empty_groups() -> None:
    report = run([comp("a", expression="MIT"), comp("b", group="dev", expression="MIT")])
    assert report.counts[("python", "runtime")] == 1
    assert report.counts[("python", "dev")] == 1
    for eco in ("npm", "image", "action"):
        assert report.counts[(eco, "runtime")] == 0 and report.counts[(eco, "dev")] == 0


# 8. registro ---------------------------------------------------------------------------------


def reg(name: str, spdx: str, group: str | None = None) -> dict[tuple[str, str], RegEntry]:
    return {lp.registry_key("python", name): RegEntry(name, spdx, group)}


def test_unregistered_package_fails() -> None:
    c = comp("novo", expression="MIT", needs_registry=True)
    assert codes(run([c])) == [(lp.NAO_REGISTRADO, "novo")]


def test_registry_match_and_divergence() -> None:
    c = comp("lib_x", expression="MIT", needs_registry=True)
    assert run([c], reg("lib-x", "MIT")).ok
    report = run([c], reg("lib-x", "Apache-2.0"))
    assert [v.code for v in report.violations] == [lp.DIVERGE_DO_REGISTRO]
    assert "MIT" in report.violations[0].message and "Apache-2.0" in report.violations[0].message


def test_registry_dual_license_by_classifiers_compares_id_sets() -> None:
    c = comp(
        "dual",
        needs_registry=True,
        license_field="Dual License",
        classifiers=(
            "License :: OSI Approved :: BSD License",
            "License :: OSI Approved :: Apache Software License",
        ),
    )
    assert run([c], reg("dual", "BSD-3-Clause OR Apache-2.0")).ok


def test_registry_runtime_registered_as_dev_fails() -> None:
    c = comp("rt", expression="MIT", needs_registry=True)
    report = run([c], reg("rt", "MIT", "dev"))
    assert [v.code for v in report.violations] == [lp.DIVERGE_DO_REGISTRO]
    assert run([c], reg("rt", "MIT", "runtime")).ok


@pytest.mark.parametrize(
    ("spdx", "code"), [("GPL-3.0-only", lp.PROIBIDA), ("???", lp.DESCONHECIDA)]
)
def test_registry_with_forbidden_or_invalid_spdx_fails(spdx: str, code: str) -> None:
    assert codes(run([], reg("ruim", spdx))) == [(code, "ruim")]


def test_collector_issues_are_merged() -> None:
    issue = Violation(lp.NAO_REGISTRADO, "image", "runtime", "img", "1", "img: sem linha")
    assert codes(run([], issues=(issue,))) == [(lp.NAO_REGISTRADO, "img")]


@pytest.mark.parametrize(
    "expr",
    [
        "Non Commercial",
        "Non-Commercial Use Only",
        "NonCommercial",
        "Server Side Public License",
        "Server-Side-Public-License v1",
        "server side public license, v 1",
    ],
)
def test_noncommercial_and_sspl_free_text_are_forbidden(expr: str) -> None:
    report = run([comp("meupkg", expression=expr)])
    assert [(v.code, v.package) for v in report.violations] == [(lp.PROIBIDA, "meupkg")]


def test_npm_registry_key_does_not_fold_separators_or_share_python_namespace() -> None:
    keys = {lp.registry_key("npm", n) for n in ("foo-bar", "foo_bar", "foo.bar")}
    assert len(keys) == 3
    assert lp.registry_key("npm", " @Scope/Pkg ") == lp.registry_key("npm", "@scope/pkg")
    assert lp.registry_key("npm", "foo-bar") != lp.registry_key("python", "foo-bar")
    assert lp.registry_key("python", "Foo_Bar") == lp.registry_key("python", "foo-bar")


def test_npm_direct_dep_is_not_satisfied_by_similar_python_registry_entry() -> None:
    reg = {lp.registry_key("python", "foo-bar"): RegEntry("foo-bar", "MIT", None)}
    c = comp("foo_bar", ecosystem="npm", expression="MIT", needs_registry=True)
    assert codes(run([c], reg)) == [(lp.NAO_REGISTRADO, "foo_bar")]
    reg_npm = {lp.registry_key("npm", "foo_bar"): RegEntry("foo_bar", "MIT", None)}
    assert run([c], reg_npm).ok


def test_build_registry_exposes_direct_rows_to_npm_with_exact_names() -> None:
    from inovalytics.tools import license_registry as lr

    doc = lr.LicencasDoc(
        deps=(lr.DocRow("@eslint/js", "9", "MIT", "https://x"),), images=(), actions=()
    )
    reg = lr.build_registry(doc, {})
    assert lp.registry_key("npm", "@eslint/js") in reg
    assert lp.registry_key("npm", "@eslint_js") not in reg
