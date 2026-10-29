"""Medição dos casos de DoS por nome de classe do VOC (F1-08, ADR-010). Não roda no `make check`.

Uso: `python backend/tests/_voc_bench.py` (a partir da raiz, com o pacote instalado). Os mesmos
construtores alimentam `test_voc_name_dos.py`, que afirma os tempos em thread com `join`.
"""

from __future__ import annotations

import json
import statistics
import sys
import time
from pathlib import Path

LRM = "‮"  # bidi: torna o nome inválido no último caractere
ZWJ = "‍"
MAX_BODY = 16 * 1024 * 1024
MAX_XML = 256 * 1024


def _object(name: str) -> str:
    return (
        f"<object><name>{name}</name><bndbox><xmin>1</xmin><ymin>1</ymin>"
        "<xmax>5</xmax><ymax>5</ymax></bndbox></object>"
    )


def hostile_name(kind: str, i: int) -> str:
    """Recusados: nome distinto por índice (o cache não ajuda), inválido só no último caractere.
    Aceitos (`valid`, `zwj_valid`): 900 nomes distintos, abaixo de `max_categories`."""
    tag = f"{i % 900:06d}" if kind in ("valid", "zwj_valid") else f"{i:06d}"  # aceitos: <= 1000
    if kind == "plain":  # 999 caracteres comuns + bidi
        return tag + "a" * (999 - len(tag)) + LRM
    if kind == "zwj":  # (a ZWJ)*499 + a + bidi, com o prefixo variando
        return tag + ("a" + ZWJ) * 497 + "a" + LRM
    if kind == "zwj_valid":  # aceito: (a ZWJ)*499 + a, mesma forma sem o bidi
        return tag + ("a" + ZWJ) * 497 + "a"
    if kind == "valid":
        return tag + "a" * (999 - len(tag))
    raise ValueError(kind)


def build_body(kind: str, target: int = 15_500_000) -> tuple[bytes, int, int]:
    """Corpo JSON com `kind` até ~`target` bytes. Devolve (corpo, arquivos, objetos)."""
    sample = len(_object(hostile_name(kind, 0)).encode())
    per_file = max(1, (MAX_XML - 200) // (sample + 8))
    per_file = min(per_file, 200)
    files: dict[str, str] = {}
    total = 0
    n = 0
    while True:
        xml = (
            "<annotation><filename>x.jpg</filename>"
            + "".join(_object(hostile_name(kind, n + k)) for k in range(per_file))
            + "</annotation>"
        )
        size = len(xml.encode())
        if total + size > target or len(files) >= 600 or total + size > MAX_BODY - 1024:
            break
        files[f"f{len(files):05d}.xml"] = xml
        total += size
        n += per_file
    return json.dumps({"annotations": files}, ensure_ascii=False).encode(), len(files), n


def main() -> None:
    from inovalytics.imports.voc_parser import VocLimits, parse_voc

    print("caso|arquivos|objetos|MiB|resultado|mediana_ms|max_ms")
    for kind in ("plain", "zwj", "valid", "zwj_valid"):
        body, nfiles, nobj = build_body(kind)
        ts = []
        outcome = "ok"
        for _ in range(3):
            t = time.perf_counter()
            try:
                parse_voc(body, VocLimits(), collect_errors=True)
            except Exception as exc:  # noqa: BLE001
                errors = getattr(exc, "errors", None)
                outcome = errors[0].code + ":" + errors[0].message if errors else repr(exc)
            ts.append(time.perf_counter() - t)
        mib = len(body) / 1048576
        print(
            f"{kind}|{nfiles}|{nobj}|{mib:.1f}|{outcome}|"
            f"{statistics.median(ts) * 1000:.0f}|{max(ts) * 1000:.0f}"
        )


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).parent))
    main()
