"""Leitura de ``docs/licencas.md`` e ``docs/licencas-transitivas.md`` e geração do segundo."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from inovalytics.tools.license_policy import (
    DEFAULT_POLICY,
    Component,
    Policy,
    RegEntry,
    Registry,
    Resolution,
    ValidException,
    find_exception,
    id_set,
    license_fails,
    pep503,
    registry_key,
    render,
    resolve,
)

_ROW4 = re.compile(r"^\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*$")

TRANSITIVAS_HEADER = """# Dependências transitivas Python (licenças)

Arquivo GERADO por `make licencas-sync` (verificador de licenças, F0-04). Não edite à mão sem
conferir os metadados do pacote. Registra só nome, licença normalizada, grupo e quem exige;
sem versão, de propósito (ver ADR-004). Linhas a mais são permitidas (a árvore varia com a
versão do Python); linha faltando, licença divergente ou grupo `runtime` registrado como `dev`
falham o `make check`. O modo de verificação nunca escreve neste arquivo.

"""

TABLE_HEAD = (
    "| Nome | Licença (SPDX) | Grupo | Exigido por |\n"
    "|------|----------------|-------|-------------|\n"
)


@dataclass(frozen=True)
class DocRow:
    name: str
    version: str
    spdx: str
    url: str


@dataclass(frozen=True)
class LicencasDoc:
    deps: tuple[DocRow, ...]
    images: tuple[DocRow, ...]
    actions: tuple[DocRow, ...]


@dataclass(frozen=True)
class TransRow:
    name: str
    spdx: str
    group: str
    required_by: tuple[str, ...]


def _rows(lines: Iterable[str]) -> list[tuple[str, str, str, str]]:
    out = []
    for line in lines:
        m = _ROW4.match(line)
        if m and m.group(1) != "Nome" and not set(m.group(1)) <= {"-"}:
            out.append((m.group(1), m.group(2), m.group(3), m.group(4)))
    return out


def parse_licencas(text: str) -> LicencasDoc:
    """Divide ``licencas.md`` em dependências, imagens de contêiner e GitHub Actions."""
    deps_part, _, rest = text.partition("## Imagens de contêiner")
    img_part, _, act_part = rest.partition("## GitHub Actions")
    img_part = img_part.split("\n## ", 1)[0]
    return LicencasDoc(
        deps=tuple(DocRow(*r) for r in _rows(deps_part.splitlines())),
        images=tuple(DocRow(*r) for r in _rows(img_part.splitlines())),
        actions=tuple(DocRow(*r) for r in _rows(act_part.splitlines())),
    )


def parse_transitivas(text: str) -> dict[str, TransRow]:
    rows: dict[str, TransRow] = {}
    for name, spdx, group, by in _rows(text.splitlines()):
        by_t = tuple(sorted(p.strip() for p in by.split(",") if p.strip() and p.strip() != "-"))
        rows[pep503(name)] = TransRow(pep503(name), spdx, group, by_t)
    return rows


def build_registry(doc: LicencasDoc, trans: Mapping[str, TransRow]) -> Registry:
    reg: dict[tuple[str, str], RegEntry] = {}
    for t in trans.values():
        reg[registry_key("python", t.name)] = RegEntry(t.name, t.spdx, t.group)
    for r in doc.deps:  # a tabela principal prevalece sobre a de transitivas
        reg[registry_key("python", r.name)] = RegEntry(r.name, r.spdx, None)
    for r in doc.images:
        reg[registry_key("image", r.name)] = RegEntry(r.name, r.spdx, "runtime")
    for r in doc.actions:
        reg[registry_key("action", r.name)] = RegEntry(r.name, r.spdx, "dev")
    return reg


def license_text(c: Component, res: Resolution | None = None) -> str:
    """SPDX a registrar para um componente (união de fontes vira ``OR`` de ids)."""
    res = res or resolve(c, DEFAULT_POLICY)
    if res.tree is None:
        return res.status
    if res.by_expression:
        return render(res.tree)
    return " OR ".join(sorted(id_set(res.tree)))


def generate_rows(
    components: Iterable[Component],
    exceptions: Sequence[ValidException] = (),
    policy: Policy = DEFAULT_POLICY,
) -> dict[str, TransRow]:
    """Linhas das transitivas Python (não diretas) a partir dos componentes coletados.

    Com exceção válida cobrindo o componente (e a licença não passando sozinha), grava a
    ``licenca_verificada`` da exceção; sem exceção, grava o que os metadados dizem (falha fechada).
    """
    rows: dict[str, TransRow] = {}
    for c in components:
        if c.ecosystem != "python" or c.direct or not c.installed:
            continue
        res = resolve(c, policy)
        text = license_text(c, res)
        if license_fails(c, res, policy):
            exc = find_exception(exceptions, c, res)
            if exc is not None:
                text = exc.verified
        rows[pep503(c.name)] = TransRow(
            pep503(c.name), text, c.group or "dev", tuple(sorted(c.required_by))
        )
    return rows


def merge_rows(
    existing: Mapping[str, TransRow], generated: Mapping[str, TransRow], *, prune: bool
) -> dict[str, TransRow]:
    """União: nunca apaga linha existente, salvo com ``prune``. ``runtime`` vence ``dev``."""
    merged: dict[str, TransRow] = {} if prune else dict(existing)
    for name, row in generated.items():
        old = existing.get(name)
        if old is not None and not prune:
            group = "runtime" if "runtime" in (old.group, row.group) else row.group
            by = tuple(sorted({*old.required_by, *row.required_by}))
            row = TransRow(name, row.spdx, group, by)
        merged[name] = row
    return merged


def render_transitivas(rows: Mapping[str, TransRow]) -> str:
    body = "".join(
        f"| {r.name} | {r.spdx} | {r.group} | {', '.join(r.required_by) or '-'} |\n"
        for r in sorted(rows.values(), key=lambda r: r.name)
    )
    return TRANSITIVAS_HEADER + TABLE_HEAD + body
