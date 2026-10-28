# ruff: noqa: E501
"""F0-04: mutações de ponta a ponta. Cada detector é provado nos dois sentidos.

Controle positivo: o repositório fictício limpo passa (código 0). Mutação: uma única alteração
o faz falhar com o código de violação esperado e citando o pacote. Tudo roda sob ``no_network``.
Palavras proibidas são montadas por concatenação (o ``test_no_ultralytics`` varre ``backend/``).
"""

from __future__ import annotations

import ast
import hashlib
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from inovalytics.tools.license_check import main, run
from test_license_check_collectors import (
    build_repo,
    make_dist,
    write_images,
    write_npm,
    write_workflow,
)

VENDOR = "ultra" + "lytics"
TOOLS = Path(__file__).resolve().parents[1] / "src" / "inovalytics" / "tools"
DOC_HEAD = "| Nome | Versão | Licença (SPDX) | URL |\n|---|---|---|---|\n"


@pytest.fixture
def repo(tmp_path: Path, no_network: None) -> tuple[Path, Path]:
    """Repositório fictício limpo, com o registro de transitivas já gerado."""
    root, site = tmp_path / "repo", tmp_path / "site"
    build_repo(root, site)
    assert run(root, [str(site)], write=True)[0] == 0
    assert run(root, [str(site)], write=False)[0] == 0, "controle positivo"
    return root, site


def violations(out: str) -> list[tuple[str, str]]:
    """(código, pacote) de cada linha de violação da saída."""
    found = re.findall(r"^VIOLAÇÃO \[(\w+)\] \S+ (\S+) ", out, flags=re.MULTILINE)
    return [(code, pkg) for code, pkg in found]


def codes_of(out: str) -> set[str]:
    return {c for c, _ in violations(out)}


def replace_dist(site: Path, name: str, **kw: object) -> None:
    for d in site.glob(f"{name.replace('-', '_')}-*.dist-info"):
        shutil.rmtree(d)
    make_dist(site, name, **kw)  # type: ignore[arg-type]


def gate(root: Path, site: Path, *, write: bool = True) -> tuple[int, str]:
    """Roda o gate; por padrão regenera o registro, isolando o detector de política."""
    return run(root, [str(site)], write=write)


DEFAULT_ACTIONS = "| actions/checkout | v5 | MIT | https://x |\n"


def write_doc(root: Path, images: str = "", actions: str = DEFAULT_ACTIONS) -> None:
    (root / "docs" / "licencas.md").write_text(
        DOC_HEAD + "| libx | 1.0 | MIT | https://x |\n\n## Imagens de contêiner\n\n"
        + DOC_HEAD + images + "\n## GitHub Actions\n\n" + DOC_HEAD + actions,
        encoding="utf-8",
    )  # fmt: skip


# ------------------------------------------------------------- licenças Python (metadados)


@pytest.mark.parametrize(
    "fields",
    [
        {"expression": "AGPL-3.0-or-later"},
        {"expression": "gpl-3.0-only"},
        {"expression": "GPL-2.0-or-later"},
        {"expression": "LGPL-2.1-only"},
        {"expression": "SSPL-1.0"},
        {"expression": "BUSL-1.1"},
        {"expression": "MIT AND GPL-3.0-only"},
        {"expression": "GPL-2.0-only OR GPL-3.0-only"},
        {"license_field": "GPL-3.0+"},
        {"license_field": "GNU Affero General Public License v3"},
        {"license_field": "Proprietary"},
        {"license_field": "Non-Commercial Use Only"},
        {"license_field": "", "classifiers": ("License :: OSI Approved :: GNU General Public License v3 (GPLv3)",)},
        {"license_field": "", "classifiers": ("License :: OSI Approved :: GNU Affero General Public License v3 (AGPLv3)",)},
        {"license_field": "", "classifiers": ("License :: OSI Approved :: GNU Lesser General Public License v3 (LGPLv3)",)},
        {"license_field": "", "classifiers": ("License :: Other/Proprietary License",)},
        {"license_field": "MIT", "classifiers": ("License :: OSI Approved :: GNU General Public License v2 or later (GPLv2+)",)},
        {"license_field": "", "classifiers": ("License :: OSI Approved :: MIT License", "License :: OSI Approved :: GNU General Public License v3 (GPLv3)")},
    ],
)  # fmt: skip
def test_forbidden_python_license_is_blocked(
    repo: tuple[Path, Path], fields: dict[str, object]
) -> None:
    root, site = repo
    replace_dist(site, "liby", **{"license_field": None, **fields})
    code, out = gate(root, site)
    assert code == 1, out
    assert ("PROIBIDA", "liby") in violations(out), out


@pytest.mark.parametrize(
    "fields",
    [
        {"license_field": None},
        {"license_field": ""},
        {"license_field": "UNKNOWN"},
        {"license_field": "Termos Estranhos"},
        {"license_field": "Commercial"},
        {"license_field": "x" * 400},
        {"license_field": "Permission is hereby granted, free of charge, to any person obtaining"},
        {"expression": "MIT WITH Classpath-exception-2.0"},
        {"expression": "(MIT OR Apache-2.0"},
        {"expression": "MIT OR"},
        {"expression": "MIT Apache-2.0"},
        {"license_field": "", "classifiers": ("License :: OSI Approved",)},
        {"license_field": "", "classifiers": ("Programming Language :: Python :: 3",)},
    ],
)
def test_missing_or_unreadable_python_license_fails_closed(
    repo: tuple[Path, Path], fields: dict[str, object]
) -> None:
    root, site = repo
    replace_dist(site, "liby", **{"license_field": None, **fields})
    code, out = gate(root, site)
    assert code == 1, out
    assert codes_of(out) <= {"AUSENTE", "DESCONHECIDA"} and codes_of(out), out
    assert ("AUSENTE", "liby") in violations(out) or ("DESCONHECIDA", "liby") in violations(out)


@pytest.mark.parametrize(
    "fields",
    [
        {"license_field": "MIT OR GPL-3.0-only"},
        {"expression": "BSD-3-Clause OR Apache-2.0"},
        {"expression": "MIT AND Apache-2.0"},
        {"expression": "mit"},
        {"expression": "MIT", "license_field": "GPL-3.0"},  # License-Expression prevalece
        {"license_field": "", "classifiers": ("License :: OSI Approved :: BSD License",)},
        {"license_field": "", "classifiers": ("License :: OSI Approved :: Apache Software License",)},
        {"license_field": "x" * 400, "classifiers": ("License :: OSI Approved :: MIT License",)},
    ],
)  # fmt: skip
def test_permitted_python_license_variants_pass(
    repo: tuple[Path, Path], fields: dict[str, object]
) -> None:
    root, site = repo
    replace_dist(site, "liby", **{"license_field": None, **fields})
    code, out = gate(root, site)
    assert code == 0, out


def test_generic_classifier_is_flagged_in_output(repo: tuple[Path, Path]) -> None:
    root, site = repo
    replace_dist(
        site, "liby", license_field="", classifiers=("License :: OSI Approved :: BSD License",)
    )
    code, out = gate(root, site)
    assert code == 0 and "normalizado de forma genérica" in out and "liby" in out


def test_unregistered_transitive_package_fails_then_sync_registers(
    repo: tuple[Path, Path],
) -> None:
    root, site = repo
    replace_dist(site, "liby", license_field="BSD-3-Clause", requires=("libnovo",))
    make_dist(site, "libnovo", license_field="MIT")
    code, out = gate(root, site, write=False)
    assert code == 1 and ("NAO_REGISTRADO", "libnovo") in violations(out), out
    assert gate(root, site, write=True)[0] == 0
    assert gate(root, site, write=False)[0] == 0


def test_license_change_in_new_version_is_diverge_with_both_values(
    repo: tuple[Path, Path],
) -> None:
    root, site = repo
    replace_dist(site, "liby", version="2.0", license_field="Apache-2.0")
    code, out = gate(root, site, write=False)
    assert code == 1 and ("DIVERGE_DO_REGISTRO", "liby") in violations(out), out
    assert "BSD-3-Clause" in out and "Apache-2.0" in out


def test_direct_dependency_unregistered_in_docs(repo: tuple[Path, Path]) -> None:
    root, site = repo
    (root / "docs" / "licencas.md").write_text(
        (root / "docs" / "licencas.md").read_text("utf-8").replace("| libx | 1.0 | MIT | https://x |\n", ""),
        encoding="utf-8",
    )  # fmt: skip
    code, out = gate(root, site, write=False)
    assert code == 1 and ("NAO_REGISTRADO", "libx") in violations(out), out


def test_forbidden_or_invalid_spdx_in_docs_row_fails(repo: tuple[Path, Path]) -> None:
    root, site = repo
    doc = root / "docs" / "licencas.md"
    original = doc.read_text("utf-8")
    for bad in ("AGPL-3.0-only", "Termos Estranhos"):
        doc.write_text(original.replace("| libx | 1.0 | MIT |", f"| libx | 1.0 | {bad} |"), "utf-8")
        code, out = gate(root, site, write=False)
        assert code == 1 and "libx" in out, (bad, out)


# ------------------------------------------------------------- Ultralytics (Python)


@pytest.mark.parametrize(
    "extra",
    [
        (f"Home-page: https://github.com/{VENDOR}/{VENDOR}",),
        (f"Author: {VENDOR}",),
        (f"Project-URL: Source, https://github.com/{VENDOR}/repo",),
        (f"Author-email: x@{VENDOR}.com",),
    ],
)
def test_vendor_metadata_blocks_even_with_exception(
    repo: tuple[Path, Path], extra: tuple[str, ...]
) -> None:
    root, site = repo
    replace_dist(site, "liby", license_field="BSD-3-Clause", extra=extra)
    (root / "docs" / "licencas-excecoes.toml").write_text(
        '[[excecao]]\necossistema="python"\npacote="liby"\nversoes=">=0"\ngrupo="runtime"\n'
        'licenca_declarada="BSD-3-Clause"\nlicenca_verificada="BSD-3-Clause"\n'
        'evidencia="https://x.example/l"\njustificativa="j"\naprovador="h"\n'
        'data="2026-10-28"\nadr="ADR-004"\n',
        encoding="utf-8",
    )
    code, out = gate(root, site)
    assert code == 1 and ("ULTRALYTICS", "liby") in violations(out), out


@pytest.mark.parametrize(
    "marker", ["", "; python_version < '2'", "; extra == 'x'", "; sys_platform == 'nonexistent'"]
)
def test_vendor_in_requires_dist_even_if_not_installed(
    repo: tuple[Path, Path], marker: str
) -> None:
    root, site = repo
    replace_dist(site, "liby", license_field="BSD-3-Clause", requires=(f"{VENDOR}>=8{marker}",))
    code, out = gate(root, site)
    assert code == 1 and ("ULTRALYTICS", "liby") in violations(out), out


def test_vendor_four_levels_deep_python(repo: tuple[Path, Path]) -> None:
    root, site = repo
    replace_dist(site, "liby", license_field="BSD-3-Clause", requires=("l1",))
    make_dist(site, "l1", requires=("l2",))
    make_dist(site, "l2", requires=("l3",))
    make_dist(site, "l3", requires=(VENDOR + "-thop",))
    make_dist(site, VENDOR + "-thop", license_field="MIT")
    code, out = gate(root, site)
    assert code == 1, out
    assert ("ULTRALYTICS", "l3") in violations(out)
    assert ("ULTRALYTICS", VENDOR + "-thop") in violations(out)


def test_vendor_as_dev_dependency_blocks(repo: tuple[Path, Path]) -> None:
    root, site = repo
    pyproject = root / "pyproject.toml"
    pyproject.write_text(
        pyproject.read_text("utf-8").replace("dev = []", f'dev = ["{VENDOR}"]'), encoding="utf-8"
    )
    make_dist(site, VENDOR, license_field="MIT")
    code, out = gate(root, site)
    assert code == 1 and ("ULTRALYTICS", VENDOR) in violations(out), out


# ------------------------------------------------------------- exceções (arquivo TOML)

EXC_OK = {
    "ecossistema": "python",
    "pacote": "liby",
    "versoes": ">=1.0,<2",
    "grupo": "runtime",
    "licenca_declarada": "MPL-2.0",
    "licenca_verificada": "MPL-2.0",
    "evidencia": "https://x.example/LICENSE",
    "justificativa": "copyleft fraco por arquivo",
    "aprovador": "humano",
    "data": "2026-10-28",
    "adr": "ADR-004",
}


def write_exc(root: Path, *items: dict[str, str]) -> None:
    text = ""
    for it in items:
        text += "[[excecao]]\n" + "".join(f'{k} = "{v}"\n' for k, v in it.items()) + "\n"
    (root / "docs" / "licencas-excecoes.toml").write_text(text, encoding="utf-8")


def mpl_repo(repo: tuple[Path, Path]) -> tuple[Path, Path]:
    root, site = repo
    replace_dist(site, "liby", license_field="MPL-2.0")
    return root, site


def test_mpl_without_exception_fails_and_with_valid_exception_passes(
    repo: tuple[Path, Path],
) -> None:
    root, site = mpl_repo(repo)
    code, out = gate(root, site)
    assert code == 1 and ("PROIBIDA", "liby") in violations(out), out
    write_exc(root, EXC_OK)
    code, out = gate(root, site)
    assert code == 0 and "Exceções usadas: 1" in out and "liby" in out, out


@pytest.mark.parametrize(
    "override",
    [
        {"grupo": "dev"},
        {"versoes": ">=2"},
        {"versoes": "==1.0.1"},
        {"pacote": "outro"},
        {"ecossistema": "npm"},
        {"licenca_declarada": "AUSENTE"},
    ],
)
def test_exception_for_wrong_group_version_package_or_ecosystem_does_not_cover(
    repo: tuple[Path, Path], override: dict[str, str]
) -> None:
    root, site = mpl_repo(repo)
    write_exc(root, {**EXC_OK, **override})
    code, out = gate(root, site)
    assert code == 1 and ("PROIBIDA", "liby") in violations(out), out


@pytest.mark.parametrize(
    "fields",
    [
        {"expression": "AGPL-3.0-only", "declared": "AGPL-3.0-only"},
        {"expression": "GPL-3.0-only", "declared": "GPL-3.0-only"},
        {"expression": "LGPL-3.0-only", "declared": "LGPL-3.0-only"},
        {"expression": "SSPL-1.0", "declared": "SSPL-1.0"},
        {"expression": "Proprietary", "declared": "Proprietary"},
    ],
)
def test_exception_cannot_cover_forbidden_license(
    repo: tuple[Path, Path], fields: dict[str, str]
) -> None:
    root, site = repo
    replace_dist(site, "liby", license_field=None, expression=fields["expression"])
    for verified in ("MPL-2.0", fields["expression"]):
        write_exc(
            root,
            {**EXC_OK, "licenca_declarada": fields["declared"], "licenca_verificada": verified},
        )
        code, out = gate(root, site)
        assert code == 1 and ("PROIBIDA", "liby") in violations(out), (verified, out)


def test_exception_cannot_cover_vendor_package(repo: tuple[Path, Path]) -> None:
    root, site = repo
    replace_dist(site, "liby", license_field="BSD-3-Clause", requires=(VENDOR,))
    make_dist(site, VENDOR, license_field="MPL-2.0")
    write_exc(root, {**EXC_OK, "pacote": VENDOR, "versoes": ">=0", "grupo": "runtime"})
    code, out = gate(root, site)
    assert code == 1 and ("ULTRALYTICS", VENDOR) in violations(out), out


@pytest.mark.parametrize(
    "bad",
    [
        {"versoes": "*"},
        {"versoes": ""},
        {"versoes": "nao-eh-especificador"},
        {"data": "2999-01-01"},
        {"data": "2026-02-30"},
        {"data": "ontem"},
        {"aprovador": ""},
        {"justificativa": "   "},
        {"grupo": "ambos"},
        {"grupo": "prod"},
        {"licenca_verificada": "GPL-3.0-only"},
        {"licenca_verificada": "Termos Estranhos"},
        {"evidencia": "não é url"},
    ],
)
def test_invalid_exception_entry_fails_gate_even_if_unused(
    repo: tuple[Path, Path], bad: dict[str, str]
) -> None:
    root, site = repo  # nenhuma dependência usa a exceção
    write_exc(root, {**EXC_OK, **bad})
    code, out = gate(root, site, write=False)
    assert code == 1 and "EXCECAO_INVALIDA" in codes_of(out), out


@pytest.mark.parametrize(
    "missing",
    [k for k in EXC_OK if k not in ("ecossistema",)] + ["ecossistema"],
)
def test_exception_missing_required_field_fails_gate(repo: tuple[Path, Path], missing: str) -> None:
    root, site = repo
    write_exc(root, {k: v for k, v in EXC_OK.items() if k != missing})
    code, out = gate(root, site, write=False)
    assert code == 1 and "EXCECAO_INVALIDA" in codes_of(out), (missing, out)


def test_duplicate_exception_fails_gate(repo: tuple[Path, Path]) -> None:
    root, site = repo
    write_exc(root, EXC_OK, EXC_OK)
    code, out = gate(root, site, write=False)
    assert code == 1 and "EXCECAO_INVALIDA" in codes_of(out), out


@pytest.mark.parametrize("lic", ["CC-BY-NC-4.0", "CC-BY-NC-SA-4.0", "CC-BY-NC-ND-4.0"])
def test_exception_cannot_cover_non_commercial_spdx(repo: tuple[Path, Path], lic: str) -> None:
    root, site = repo
    replace_dist(site, "libx", license_field=lic, requires=("liby",))
    write_exc(root, {**EXC_OK, "pacote": "libx", "versoes": ">=0", "licenca_declarada": lic})
    code, out = gate(root, site, write=False)
    assert code == 1, out


def test_valid_exception_rescues_transitive_with_absent_license(repo: tuple[Path, Path]) -> None:
    root, site = repo
    replace_dist(site, "liby", license_field=None)
    write_exc(root, {**EXC_OK, "licenca_declarada": "AUSENTE", "licenca_verificada": "MIT"})
    code, out = gate(root, site, write=True)
    assert code == 0, out


def test_unused_valid_exception_is_listed_not_failed(repo: tuple[Path, Path]) -> None:
    root, site = repo
    write_exc(root, EXC_OK)
    code, out = gate(root, site, write=False)
    assert code == 0 and "Exceções sem uso" in out and "liby" in out, out


@pytest.mark.parametrize(
    "text",
    ["[[excecao\n", "excecao = 3\n", "[excecao]\npacote = 'x'\n", "excecao = [1, 2]\n", "\x00"],
)
def test_malformed_exception_toml_is_input_error(
    repo: tuple[Path, Path], text: str, capsys: pytest.CaptureFixture[str]
) -> None:
    root, site = repo
    (root / "docs" / "licencas-excecoes.toml").write_text(text, encoding="utf-8")
    assert main(["--root", str(root), "--metadata-path", str(site)]) == 2
    assert "Erro de entrada" in capsys.readouterr().err


def test_missing_exception_file_is_input_error(repo: tuple[Path, Path]) -> None:
    root, site = repo
    (root / "docs" / "licencas-excecoes.toml").unlink()
    assert gate(root, site, write=False)[0] == 2


# ------------------------------------------------------------- npm


def npm_run(repo: tuple[Path, Path], packages: dict[str, object], **kw: object) -> tuple[int, str]:
    root, site = repo
    write_npm(root, packages, **kw)  # type: ignore[arg-type]
    return gate(root, site, write=False)


def test_npm_clean_control(repo: tuple[Path, Path]) -> None:
    code, out = npm_run(repo, {"node_modules/a": {"version": "1", "license": "MIT", "dev": True}})
    assert code == 0 and "npm: runtime 0, dev 1" in out


@pytest.mark.parametrize("version", [2, 3])
def test_npm_lockfile_versions_2_and_3_accepted(repo: tuple[Path, Path], version: int) -> None:
    code, _ = npm_run(repo, {}, version=version)
    assert code == 0


@pytest.mark.parametrize("version", [1, 0, 4, "3"])
def test_npm_lockfile_other_versions_are_input_error(
    repo: tuple[Path, Path], version: object
) -> None:
    code, _ = npm_run(repo, {}, version=version)  # type: ignore[arg-type]
    assert code == 2


def test_npm_v1_style_lock_without_packages_is_rejected(repo: tuple[Path, Path]) -> None:
    root, site = repo
    (root / "frontend" / "package-lock.json").write_text(
        '{"lockfileVersion": 1, "dependencies": {"a": {"version": "1"}}}', encoding="utf-8"
    )
    assert gate(root, site, write=False)[0] == 2


@pytest.mark.parametrize(
    "entry",
    [
        {"version": "1"},
        {"version": "1", "license": ""},
        {"version": "1", "license": "UNKNOWN"},
        {"version": "1", "license": "SEE LICENSE IN LICENSE.md"},
        {"version": "1", "license": {"type": "GPL-3.0"}},
        {"version": "1", "license": ["GPL-3.0", {"type": "AGPL-3.0"}]},
        {"version": "1", "license": 7},
        {"version": "1", "license": "AGPL-3.0-only"},
        {"version": "1", "license": "(MIT AND GPL-3.0-only)"},
        {"version": "1", "license": "MIT", "resolved": "git+https://github.com/x/y.git#abc"},
        {"version": "1", "license": "MIT", "resolved": "file:../local"},
        {"version": "1", "license": "MIT", "resolved": "https://registry.npmjs.org.evil.example/x.tgz"},
        {"version": "1", "license": "MIT", "resolved": "http://registry.npmjs.org/x.tgz"},
    ],
)  # fmt: skip
def test_npm_bad_entries_fail(repo: tuple[Path, Path], entry: dict[str, object]) -> None:
    code, out = npm_run(repo, {"node_modules/bad": {**entry, "dev": True}})
    assert code == 1, out
    assert any(pkg == "bad" for _, pkg in violations(out)), out


@pytest.mark.parametrize(
    "entry",
    [
        {"license": "MIT", "dependencies": {VENDOR: "^8"}},
        {"license": "MIT", "peerDependencies": {VENDOR: "*"}},
        {"license": "MIT", "optionalDependencies": {VENDOR + "-thop": "1"}},
        {"license": "MIT", "resolved": "https://registry.npmjs.org/" + VENDOR + "/-/x-1.tgz"},
    ],
)
def test_npm_vendor_references_fail(repo: tuple[Path, Path], entry: dict[str, object]) -> None:
    code, out = npm_run(repo, {"node_modules/holder": {"version": "1", "dev": True, **entry}})
    assert code == 1 and ("ULTRALYTICS", "holder") in violations(out), out


@pytest.mark.parametrize("key", [VENDOR, "@x/" + VENDOR, "node_modules/a/node_modules/" + VENDOR])
def test_npm_vendor_named_package_fails(repo: tuple[Path, Path], key: str) -> None:
    packages = {
        "node_modules/" + key if not key.startswith("node_modules") else key: {
            "version": "1",
            "license": "MIT",
            "dev": True,
        }
    }
    code, out = npm_run(repo, packages)
    assert code == 1 and "ULTRALYTICS" in codes_of(out), out


def test_npm_vendor_four_levels_deep(repo: tuple[Path, Path]) -> None:
    chain = {
        "node_modules/n1": {"version": "1", "license": "MIT", "dev": True, "dependencies": {"n2": "1"}},
        "node_modules/n2": {"version": "1", "license": "MIT", "dev": True, "dependencies": {"n3": "1"}},
        "node_modules/n3": {"version": "1", "license": "MIT", "dev": True, "dependencies": {"n4": "1"}},
        "node_modules/n4": {"version": "1", "license": "MIT", "dev": True, "peerDependencies": {VENDOR: "1"}},
    }  # fmt: skip
    code, out = npm_run(repo, chain)
    assert code == 1 and violations(out) == [("ULTRALYTICS", "n4")], out


def test_npm_exception_cannot_cover_forbidden_or_vendor(repo: tuple[Path, Path]) -> None:
    root, _ = repo
    exc = {**EXC_OK, "ecossistema": "npm", "pacote": "bad", "grupo": "dev", "versoes": ">=1.0.0"}
    write_exc(root, exc)
    code, out = npm_run(
        repo, {"node_modules/bad": {"version": "1.2.3", "license": "GPL-3.0-only", "dev": True}}
    )
    assert code == 1 and ("PROIBIDA", "bad") in violations(out), out
    code, out = npm_run(
        repo, {"node_modules/bad": {"version": "1.2.3", "license": "MPL-2.0", "dev": True}}
    )
    assert code == 0, out  # controle: a mesma exceção cobre MPL-2.0 em dev
    code, out = npm_run(repo, {"node_modules/bad": {"version": "1.2.3", "license": "MPL-2.0"}})
    assert code == 1, out  # grupo runtime: exceção é dev


def test_npm_package_json_dep_missing_from_lock(repo: tuple[Path, Path]) -> None:
    code, out = npm_run(repo, {}, dependencies={"faltando": "^1"})
    assert code == 1 and ("NAO_REGISTRADO", "faltando") in violations(out), out


def test_npm_runtime_group_is_counted_separately(repo: tuple[Path, Path]) -> None:
    code, out = npm_run(
        repo,
        {
            "node_modules/r": {"version": "1", "license": "MIT"},
            "node_modules/d": {"version": "1", "license": "MIT", "dev": True},
            "node_modules/o": {"version": "1", "license": "MIT", "devOptional": True},
        },
    )
    assert code == 0 and "npm: runtime 1, dev 2" in out, out


# ------------------------------------------------------------- imagens


IMG_ROWS = (
    "| python (imagem base) | 3.12.11 | PSF-2.0 | https://x |\n"
    "| postgres (imagem oficial) | 17.6 | PostgreSQL | https://x |\n"
)
COMPOSE_OK = "services:\n  db:\n    image: postgres:17.6\n"


def image_run(
    repo: tuple[Path, Path], dockerfile: str, compose: str = "services: {}\n", rows: str = IMG_ROWS
) -> tuple[int, str]:
    root, site = repo
    write_doc(root, images=rows)
    write_images(root, compose, dockerfile)
    return gate(root, site, write=False)


def test_images_clean_control(repo: tuple[Path, Path]) -> None:
    code, out = image_run(repo, "FROM python:3.12.11-slim-bookworm\n", COMPOSE_OK)
    assert code == 0 and "imagens: runtime 2" in out, out


@pytest.mark.parametrize(
    "dockerfile",
    [
        "FROM python\n",
        "FROM python:latest\n",
        "FROM python@sha256:" + "a" * 64 + "\n",
        "ARG PY=3.12.11\nFROM python:${PY}\n",
        "FROM python:$PY\n",
        "FROM --platform=linux/amd64 python\n",
        "FROM python:3.12.11 AS a\nFROM python AS b\n",
        "FROM registry.example.com:5000/python\n",
    ],
)
def test_images_unpinned_forms_fail(repo: tuple[Path, Path], dockerfile: str) -> None:
    code, out = image_run(repo, dockerfile, COMPOSE_OK)
    assert code == 1 and "DESCONHECIDA" in codes_of(out), out


@pytest.mark.parametrize("image", ["postgres", "postgres:latest", "${DB_IMAGE}", "postgres:${V}"])
def test_compose_unpinned_images_fail(repo: tuple[Path, Path], image: str) -> None:
    code, out = image_run(repo, "FROM python:3.12.11\n", f"services:\n  db:\n    image: {image}\n")
    assert code == 1 and "DESCONHECIDA" in codes_of(out), out


@pytest.mark.parametrize(
    "dockerfile",
    [
        "FROM scratch\nFROM python:3.12.11\n",
        "FROM python:3.12.11 AS build\nFROM build AS final\nFROM scratch AS empty\nFROM empty\n",
        "from python:3.12.11 as Build\nFROM build\n",
        "FROM --platform=$BUILDPLATFORM python:3.12.11@sha256:" + "b" * 64 + " AS b\nFROM b\n",
    ],
)
def test_images_multistage_scratch_digest_pass(repo: tuple[Path, Path], dockerfile: str) -> None:
    code, out = image_run(repo, dockerfile, COMPOSE_OK)
    assert code == 0, out


def test_image_not_registered_wrong_tag_unused_row(repo: tuple[Path, Path]) -> None:
    code, out = image_run(repo, "FROM redis:7.2\n", COMPOSE_OK)
    got = violations(out)
    assert ("NAO_REGISTRADO", "redis:7.2") in got, out
    assert any(c == "DIVERGE_DO_REGISTRO" and "python" in p for c, p in got), out
    code, out = image_run(repo, "FROM python:3.11.9\n", COMPOSE_OK)
    assert ("DIVERGE_DO_REGISTRO", "python:3.11.9") in violations(out), out


def test_image_registered_with_forbidden_or_invalid_license_fails(repo: tuple[Path, Path]) -> None:
    for spdx in ("AGPL-3.0-only", "GPL-3.0-only", "SSPL-1.0", "Termos Estranhos"):
        rows = IMG_ROWS.replace("| PSF-2.0 |", f"| {spdx} |")
        code, out = image_run(repo, "FROM python:3.12.11\n", COMPOSE_OK, rows)
        assert code == 1 and any(p.startswith("python") for _, p in violations(out)), (spdx, out)


def test_image_vendor_repository_fails(repo: tuple[Path, Path]) -> None:
    rows = IMG_ROWS + f"| {VENDOR} (imagem) | 8.0 | MIT | https://x |\n"
    code, out = image_run(repo, f"FROM {VENDOR}/{VENDOR}:8.0\n", COMPOSE_OK, rows)
    assert code == 1 and "ULTRALYTICS" in codes_of(out), out


def test_image_ambiguous_row_is_not_accepted(repo: tuple[Path, Path]) -> None:
    rows = IMG_ROWS + "| python (outra) | 3.12.11 | PSF-2.0 | https://x |\n"
    code, out = image_run(repo, "FROM python:3.12.11\n", COMPOSE_OK, rows)
    assert code == 1 and "NAO_REGISTRADO" in codes_of(out), out


def test_image_from_other_namespace_does_not_reuse_official_row(repo: tuple[Path, Path]) -> None:
    """Uma imagem 'evil/python' não pode herdar a linha registrada da imagem oficial 'python'."""
    code, out = image_run(repo, "FROM evil/python:3.12.11\n", COMPOSE_OK)
    assert code == 1, out


@pytest.mark.parametrize(
    ("relpath", "content"),
    [
        ("docker/api/Dockerfile.dev", "FROM evil:1\n"),
        ("docker/api/api.Dockerfile", "FROM evil:1\n"),
        ("compose.yaml", "services:\n  x:\n    image: evil:1\n"),
        ("docker-compose.override.yml", "services:\n  x:\n    image: evil:1\n"),
    ],
)
def test_images_in_other_dockerfile_or_compose_names_are_scanned(
    repo: tuple[Path, Path], relpath: str, content: str
) -> None:
    root, site = repo
    write_doc(root, images=IMG_ROWS)
    write_images(root, COMPOSE_OK, "FROM python:3.12.11\n")
    (root / relpath).write_text(content, encoding="utf-8")
    code, out = gate(root, site, write=False)
    assert code == 1, out


# ------------------------------------------------------------- actions


ACT_ROWS = "| actions/checkout | v5 | MIT | https://x |\n"


def action_run(repo: tuple[Path, Path], uses: list[str], rows: str = ACT_ROWS) -> tuple[int, str]:
    root, site = repo
    write_doc(root, actions=rows)
    write_workflow(root, uses)
    return gate(root, site, write=False)


def test_action_control_and_failures(repo: tuple[Path, Path]) -> None:
    assert action_run(repo, ["actions/checkout@v5"])[0] == 0
    code, out = action_run(repo, ["actions/checkout@v5", "actions/setup-node@v6"])
    assert ("NAO_REGISTRADO", "actions/setup-node@v6") in violations(out), out
    code, out = action_run(repo, ["actions/checkout@v4"])
    assert ("DIVERGE_DO_REGISTRO", "actions/checkout@v4") in violations(out), out
    code, out = action_run(repo, ["actions/checkout"])
    assert code == 1 and "DESCONHECIDA" in codes_of(out), out
    code, out = action_run(repo, [])  # linha sem uso
    assert code == 1 and "DIVERGE_DO_REGISTRO" in codes_of(out), out


def test_action_owner_must_be_official_even_if_registered(repo: tuple[Path, Path]) -> None:
    rows = ACT_ROWS + "| docker/login-action | v3 | Apache-2.0 | https://x |\n"
    code, out = action_run(repo, ["actions/checkout@v5", "docker/login-action@v3"], rows)
    assert code == 1 and ("DESCONHECIDA", "docker/login-action@v3") in violations(out), out


def test_action_vendor_owner_fails(repo: tuple[Path, Path]) -> None:
    rows = ACT_ROWS + f"| {VENDOR}/action | v1 | MIT | https://x |\n"
    code, out = action_run(repo, ["actions/checkout@v5", f"{VENDOR}/action@v1"], rows)
    assert code == 1 and "ULTRALYTICS" in codes_of(out), out


def test_action_registered_with_forbidden_license_fails(repo: tuple[Path, Path]) -> None:
    rows = "| actions/checkout | v5 | AGPL-3.0-only | https://x |\n"
    code, out = action_run(repo, ["actions/checkout@v5"], rows)
    assert code == 1 and any(p.startswith("actions/checkout") for _, p in violations(out)), out


# ------------------------------------------------------------- offline, AST, Makefile, escrita


def test_no_network_fixture_really_blocks_sockets(no_network: None) -> None:
    import socket

    with pytest.raises(AssertionError, match="rede"):
        socket.socket()
    with pytest.raises(AssertionError, match="rede"):
        socket.create_connection(("127.0.0.1", 9))


def test_mutated_tree_runs_without_network_or_subprocess(
    repo: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*a: object, **k: object) -> None:
        raise AssertionError("subprocess proibido")

    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(subprocess, "Popen", boom)
    monkeypatch.setattr(subprocess, "check_output", boom)
    root, site = repo
    replace_dist(site, "liby", license_field="GPL-3.0-only")
    assert gate(root, site)[0] == 1


def _imports(tree: ast.AST) -> list[str]:
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            names.append(node.module or "")
            names += [f"{node.module}.{a.name}" for a in node.names]
    return names


def test_ast_check_detects_banned_imports_in_every_form() -> None:
    """O próprio detector de AST não é vácuo: pega import, from, aninhado e dentro de função."""
    banned = {"socket", "urllib", "http", "requests", "subprocess", VENDOR}
    for src in (
        "import socket",
        "import urllib.request",
        "from urllib import request",
        "from http.client import HTTPConnection",
        "def f():\n    import requests",
        "if True:\n    import subprocess as sp",
        f"from {VENDOR} import YOLO",
    ):
        roots = {n.split(".")[0] for n in _imports(ast.parse(src))}
        assert roots & banned, src


def test_verifier_modules_have_no_banned_imports_or_dynamic_loading() -> None:
    banned = {"socket", "urllib", "http", "requests", "subprocess", "ssl", "ftplib", "smtplib",
              "xmlrpc", "asyncio", "aiohttp", "httpx", "urllib3", "botocore", "boto3", VENDOR}  # fmt: skip
    files = sorted(TOOLS.glob("*.py"))
    assert {f.name for f in files} >= {
        "license_check.py",
        "license_collect.py",
        "license_policy.py",
    }
    for path in files:
        tree = ast.parse(path.read_text("utf-8"))
        for name in _imports(tree):
            assert name.split(".")[0] not in banned, (path.name, name)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                f = node.func
                called = f.id if isinstance(f, ast.Name) else getattr(f, "attr", "")
                assert called not in {"__import__", "import_module", "system", "Popen", "popen"}, (
                    path.name,
                    called,
                )


def test_third_party_imports_in_tools_are_lazy() -> None:
    """PyYAML e packaging só dentro de funções (o pacote 'tools' vai na imagem da API)."""
    for path in TOOLS.glob("*.py"):
        tree = ast.parse(path.read_text("utf-8"))
        for node in tree.body:
            for name in _imports(node) if isinstance(node, ast.Import | ast.ImportFrom) else []:
                assert name.split(".")[0] not in {"yaml", "packaging"}, (path.name, name)


def test_makefile_licencas_recipes(repo_root: Path) -> None:
    text = (repo_root / "Makefile").read_text("utf-8")
    lines = text.splitlines()

    def recipe(target: str) -> list[str]:
        start = lines.index(f"{target}:") + 1
        body: list[str] = []
        for line in lines[start:]:
            if not line.startswith("\t"):
                break
            body.append(line)
        return body

    licencas, sync = recipe("licencas"), recipe("licencas-sync")
    assert len(licencas) == 1 and "inovalytics.tools.license_check" in licencas[0]
    assert "--write" not in licencas[0] and "--prune" not in licencas[0]
    assert len(sync) == 1 and "--write" in sync[0] and "--prune" not in sync[0]
    for line in (*licencas, *sync):
        assert not line.lstrip("\t").startswith(("-", "@-")), line
        assert "||" not in line and "; true" not in line, line
    check = recipe("check")
    order = [i for i, ln in enumerate(check) if "$(MAKE) licencas" in ln]
    assert len(order) == 1 and "licencas-sync" not in check[order[0]]
    seq = [ln.split("$(MAKE) ")[1] for ln in check if "$(MAKE) " in ln]
    assert seq == [
        "lint-backend",
        "typecheck",
        "test",
        "licencas",
        "lint-frontend",
        "test-frontend",
    ]


def _tree_hash(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


@pytest.mark.parametrize("mutation", ["clean", "agpl", "unregistered", "bad_exception"])
def test_verification_mode_never_writes_anything(repo: tuple[Path, Path], mutation: str) -> None:
    root, site = repo
    if mutation == "agpl":
        replace_dist(site, "liby", license_field="AGPL-3.0-only")
    elif mutation == "unregistered":
        make_dist(site, "libnovo")
        replace_dist(site, "liby", license_field="BSD-3-Clause", requires=("libnovo",))
    elif mutation == "bad_exception":
        write_exc(root, {**EXC_OK, "versoes": "*"})
    before = _tree_hash(root)
    main(["--root", str(root), "--metadata-path", str(site)])
    assert _tree_hash(root) == before


def test_sync_never_deletes_rows_and_is_idempotent(repo: tuple[Path, Path]) -> None:
    root, site = repo
    trans = root / "docs" / "licencas-transitivas.md"
    trans.write_text(
        trans.read_text("utf-8") + "| so-no-312 | MIT | dev | x |\n| zzz | ISC | runtime | y |\n",
        encoding="utf-8",
    )
    for _ in range(2):
        assert gate(root, site, write=True)[0] == 0
        text = trans.read_text("utf-8")
        assert "so-no-312" in text and "zzz" in text and "liby" in text
    once = trans.read_bytes()
    gate(root, site, write=True)
    assert trans.read_bytes() == once
    assert (
        gate(
            root,
            site,
            write=True,
        )[0]
        == 0
    )


def test_sync_does_not_touch_other_files(repo: tuple[Path, Path]) -> None:
    root, site = repo
    before = _tree_hash(root)
    replace_dist(site, "liby", version="2.0", license_field="BSD-3-Clause")
    gate(root, site, write=True)
    after = _tree_hash(root)
    changed = {k for k in after if after[k] != before.get(k)}
    assert changed <= {"docs/licencas-transitivas.md"}, changed


def test_sync_via_cli_flag_prune_is_explicit(repo: tuple[Path, Path]) -> None:
    root, site = repo
    trans = root / "docs" / "licencas-transitivas.md"
    trans.write_text(trans.read_text("utf-8") + "| velha | MIT | dev | x |\n", encoding="utf-8")
    assert main(["--root", str(root), "--metadata-path", str(site), "--write"]) == 0
    assert "velha" in trans.read_text("utf-8")
    assert main(["--root", str(root), "--metadata-path", str(site), "--write", "--prune"]) == 0
    assert "velha" not in trans.read_text("utf-8")


def test_sync_with_forbidden_license_still_fails_and_exit_code_is_1(
    repo: tuple[Path, Path],
) -> None:
    root, site = repo
    replace_dist(site, "liby", license_field="AGPL-3.0-only")
    assert main(["--root", str(root), "--metadata-path", str(site), "--write"]) == 1


def test_module_entrypoint_exit_codes(repo: tuple[Path, Path]) -> None:
    root, site = repo
    base = [sys.executable, "-m", "inovalytics.tools.license_check", "--root", str(root),
            "--metadata-path", str(site)]  # fmt: skip
    ok = subprocess.run(base, capture_output=True, text=True)
    assert ok.returncode == 0 and "Resultado: OK" in ok.stdout
    replace_dist(site, "liby", license_field="AGPL-3.0-only")
    bad = subprocess.run(base, capture_output=True, text=True)
    assert bad.returncode == 1 and "[PROIBIDA]" in bad.stdout
    (root / "docs" / "licencas-excecoes.toml").write_text("[[", encoding="utf-8")
    err = subprocess.run(base, capture_output=True, text=True)
    assert err.returncode == 2 and "Erro de entrada" in err.stderr
