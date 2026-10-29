"""Helpers puros compartilhados pelos importadores (F1-07a). Só biblioteca padrão.

Extraídos do parser COCO sem mudança de comportamento: ganchos de `json.loads` que recusam chave
duplicada e constantes não finitas, percurso iterativo de profundidade e validação de nomes.
"""

from __future__ import annotations

import math
import unicodedata
from typing import Any

MAX_NAME_CHARS = 1024
MAX_INT_DIGITS = 4300
SHOWN_NAME_CHARS = 200
_SHOWN_SCAN_CHARS = 4096  # nunca varre um nome gigante inteiro só para exibir 200 caracteres


def pairs_hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in pairs:
        if k in out:
            raise ValueError("chave duplicada")
        out[k] = v
    return out


def bad_constant(_: str) -> Any:
    raise ValueError("constante não finita")


def parse_int(s: str) -> int:
    if len(s) > MAX_INT_DIGITS:
        raise ValueError("inteiro grande demais")
    return int(s)


def parse_float(s: str) -> float:
    v = float(s)
    if not math.isfinite(v):
        raise ValueError("número não finito")
    return v


def depth_exceeded(root: Any, max_depth: int) -> bool:
    """Aninhamento de `root` (já parseado) acima de `max_depth`? Percurso iterativo, sem recursão.

    A raiz é o nível 1. Só contêineres entram na pilha; o custo é linear no número de nós.
    """
    stack: list[tuple[Any, int]] = [(root, 1)]
    while stack:
        node, depth = stack.pop()
        if not isinstance(node, (dict, list)):
            continue
        if depth > max_depth:
            return True
        children = node.values() if isinstance(node, dict) else node
        for child in children:
            if isinstance(child, (dict, list)):
                stack.append((child, depth + 1))
    return False


def storable(v: str) -> bool:
    """Texto gravável no banco: sem NUL e sem substituto isolado (que não codifica em UTF-8)."""
    if "\x00" in v:
        return False
    try:
        v.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def check_name(v: Any) -> bool:
    return isinstance(v, str) and bool(v.strip()) and len(v) <= MAX_NAME_CHARS


def shown(name: str) -> str:
    """Nome do usuário para mensagens: sem controles/separadores e até 200 caracteres."""
    clean = "".join(
        c
        for c in name[:_SHOWN_SCAN_CHARS]
        if unicodedata.category(c)[0] != "C" and unicodedata.category(c) not in {"Zl", "Zp"}
    )
    return clean[:SHOWN_NAME_CHARS]
