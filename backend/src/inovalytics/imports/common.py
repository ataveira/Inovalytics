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


# Letras/símbolos sem glifo visível (preenchimentos): contam como "vazio" num nome.
_BLANK_FILLERS = frozenset("\u115f\u1160\u2800\u3164\uffa0")
# Categorias recusadas em nomes: controle, substituto, separadores de linha/parágrafo e formato
# (Cf: bidi, largura zero, BOM).
_BAD_NAME_CATEGORIES = frozenset({"Cc", "Cs", "Zl", "Zp", "Cf"})


def name_has_bad_chars(v: str) -> bool:
    """Há controle, substituto, separador de linha/parágrafo ou caractere de formato (Cf)?

    Custo linear no tamanho de `v` (o chamador confere o limite de tamanho antes).
    """
    if v.isprintable():
        return False
    return any(unicodedata.category(c) in _BAD_NAME_CATEGORIES for c in v)


def name_is_blank(v: str) -> bool:
    """Nome vazio, só espaços ou só invisíveis (espaços Unicode, Cf e preenchimentos sem glifo)."""
    if not v.strip():
        return True
    return all(c.isspace() or c in _BLANK_FILLERS or unicodedata.category(c) == "Cf" for c in v)


def check_name(v: Any) -> bool:
    """Texto de até 1024 caracteres que não é só espaço (nomes de arquivo; sem endurecimento)."""
    return isinstance(v, str) and bool(v.strip()) and len(v) <= MAX_NAME_CHARS


def check_class_name(v: Any) -> bool:
    """Nome de classe/categoria: `check_name` mais sem controles, substitutos, separadores de
    linha/parágrafo e Cf (bidi, largura zero, BOM), e não vazio após ignorar invisíveis."""
    return check_name(v) and not name_is_blank(v) and not name_has_bad_chars(v)


def shown(name: str) -> str:
    """Nome do usuário para mensagens: sem controles/separadores e até 200 caracteres.

    Para assim que junta 200 caracteres limpos; nunca olha além de 4096 caracteres de entrada.
    """
    out: list[str] = []
    for c in name[:_SHOWN_SCAN_CHARS]:
        cat = unicodedata.category(c)
        if cat[0] != "C" and cat != "Zl" and cat != "Zp":
            out.append(c)
            if len(out) >= SHOWN_NAME_CHARS:
                break
    return "".join(out)
