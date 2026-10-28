"""CLI da verificação de licenças: ``python -m inovalytics.tools.license_check``.

Códigos de saída: 0 sem violações, 1 com violações, 2 para erro de entrada.
Offline: não usa rede, ``subprocess``, ``npm`` nem ``pip``. O modo de verificação nunca escreve;
só ``--write`` atualiza ``docs/licencas-transitivas.md`` (união, sem apagar, salvo ``--prune``).
"""

from __future__ import annotations

import argparse
import sys
import tomllib
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Any

from inovalytics.tools import license_collect as collect
from inovalytics.tools.license_collect import InputError
from inovalytics.tools.license_policy import (
    DEFAULT_POLICY,
    ECOSYSTEMS,
    GROUPS,
    Report,
    Snapshot,
    validate_exceptions,
    verify,
)
from inovalytics.tools.license_registry import (
    build_registry,
    generate_rows,
    merge_rows,
    parse_licencas,
    parse_transitivas,
    render_transitivas,
)

LABELS = {"python": "python", "npm": "npm", "image": "imagens", "action": "actions"}


def default_root() -> Path:
    return Path(__file__).resolve().parents[4]


def load_exceptions(path: Path) -> list[dict[str, Any]]:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise InputError(f"não foi possível ler {path}: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise InputError(f"TOML inválido em {path}: {exc}") from exc
    items = data.get("excecao", [])
    if not isinstance(items, list) or not all(isinstance(i, dict) for i in items):
        raise InputError(f"{path}: 'excecao' deve ser uma lista de tabelas [[excecao]]")
    return items


def format_report(report: Report) -> str:
    lines = ["Verificação de licenças (Inovalytics)"]
    for eco in ECOSYSTEMS:
        parts = ", ".join(f"{g} {report.counts.get((eco, g), 0)}" for g in GROUPS)
        lines.append(f"  {LABELS[eco]}: {parts}")
    lines.append(f"Exceções usadas: {len(report.used_exceptions)}")
    lines.extend(f"  - {e}" for e in report.used_exceptions)
    if report.unused_exceptions:
        lines.append(f"Exceções sem uso nesta árvore: {len(report.unused_exceptions)}")
        lines.extend(f"  - {e}" for e in report.unused_exceptions)
    if report.notes:
        lines.append("Notas (auditoria):")
        lines.extend(f"  - {n}" for n in report.notes)
    for v in report.violations:
        where = f"{LABELS.get(v.ecosystem, v.ecosystem)}/{v.group or '-'}"
        lines.append(f"VIOLAÇÃO [{v.code}] {where} {v.package} {v.version}: {v.message}")
    n = len(report.violations)
    lines.append("Resultado: OK" if n == 0 else f"Resultado: FALHOU ({n} violação(ões))")
    return "\n".join(lines)


def run(
    root: Path, metadata_paths: Sequence[str] | None, *, write: bool, prune: bool = False
) -> tuple[int, str]:
    docs = root / "docs"
    try:
        licencas_text = collect._read(docs / "licencas.md")
        trans_path = docs / "licencas-transitivas.md"
        trans_text = collect._read(trans_path) if trans_path.exists() or not write else ""
        exceptions = load_exceptions(docs / "licencas-excecoes.toml")
        doc = parse_licencas(licencas_text)
        py = collect.collect_python(root, metadata_paths)
        npm = collect.collect_npm(root)
        images = collect.collect_images(root, doc.images)
        actions = collect.collect_actions(root, doc.actions)
    except InputError as exc:
        return 2, f"Erro de entrada: {exc}"

    trans = parse_transitivas(trans_text)
    if write:
        valid, _ = validate_exceptions(exceptions, date.today(), DEFAULT_POLICY)
        merged = merge_rows(trans, generate_rows(py.components, valid), prune=prune)
        new_text = render_transitivas(merged)
        if new_text != trans_text:
            trans_path.write_text(new_text, encoding="utf-8")
        trans = merged

    comps = (*py.components, *npm.components, *images.components, *actions.components)
    issues = (*py.issues, *npm.issues, *images.issues, *actions.issues)
    report = verify(
        Snapshot(comps, issues),
        DEFAULT_POLICY,
        build_registry(doc, trans),
        exceptions,
        today=date.today(),
    )
    return (0 if report.ok else 1), format_report(report)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="license_check", description=__doc__)
    ap.add_argument("--root", type=Path, default=default_root())
    ap.add_argument("--metadata-path", action="append", default=None, dest="metadata_paths")
    ap.add_argument("--write", action="store_true", help="atualiza docs/licencas-transitivas.md")
    ap.add_argument("--prune", action="store_true", help="com --write, remove linhas sobrando")
    args = ap.parse_args(argv)
    code, text = run(args.root, args.metadata_paths, write=args.write, prune=args.prune)
    print(text, file=sys.stderr if code == 2 else sys.stdout)
    return code


if __name__ == "__main__":
    sys.exit(main())
