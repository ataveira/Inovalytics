"""Helpers puros compartilhados pelos importadores (F1-07a). Só biblioteca padrão.

Extraídos do parser COCO sem mudança de comportamento: ganchos de `json.loads` que recusam chave
duplicada e constantes não finitas, percurso iterativo de profundidade e validação de nomes.
"""

from __future__ import annotations

import math
import unicodedata
from typing import Any

BORDER_EPS = 1e-9
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

# ZWNJ/ZWJ são legítimos entre caracteres visíveis (emoji compostos, persa, escritas indianas).
_JOINERS = frozenset("\u200c\u200d")


def _build_tables() -> tuple[frozenset[str], frozenset[str], frozenset[str]]:
    """Tabelas por caractere, calculadas uma vez com o `unicodedata` do próprio processo.

    Servem de pré-filtro em C (`set.isdisjoint`, `set <=`): o laço Python por caractere deixa de
    existir no caminho comum. A semântica é a da definição original, caractere a caractere.
    """
    category = unicodedata.category
    bad: list[str] = []  # categoria recusada, exceto ZWJ/ZWNJ (tratados à parte)
    invisible: list[str] = []  # não conta como "visível" ao lado de um ZWJ/ZWNJ
    blank: list[str] = []  # conta como "vazio" num nome
    for cp in range(0x110000):
        c = chr(cp)
        cat = category(c)
        is_bad = cat in _BAD_NAME_CATEGORIES
        space = c.isspace()
        if is_bad and c not in _JOINERS:
            bad.append(c)
        if space or c in _BLANK_FILLERS or c in _JOINERS or is_bad:
            invisible.append(c)
        if space or c in _BLANK_FILLERS or cat == "Cf":
            blank.append(c)
    return frozenset(bad), frozenset(invisible), frozenset(blank)


_BAD_CHARS, _INVISIBLE, _BLANK_CHARS = _build_tables()


def _joiners_misplaced(v: str) -> bool:
    """Algum ZWJ/ZWNJ fora de um par de caracteres visíveis? (só chamado se `v` tem joiner)

    Separa o nome nos joiners (tudo em C): parte vazia = joiner no início, no fim ou colado em
    outro joiner; senão confere só o último caractere de cada parte e o primeiro da seguinte.
    """
    parts = v.replace("\u200c", "\u200d").split("\u200d")
    if "" in parts:
        return True
    edges = {p[-1] for p in parts[:-1]}
    edges.update(p[0] for p in parts[1:])
    return not edges.isdisjoint(_INVISIBLE)


def name_has_bad_chars(v: str) -> bool:
    """Há controle, substituto, separador de linha/parágrafo ou caractere de formato (Cf)?

    Exceção: U+200C e U+200D isolados (no máximo um por posição) entre dois caracteres visíveis.
    Custo em C para nomes comuns (conjunto de caracteres do nome contra a tabela de proibidos); só
    nomes com ZWJ/ZWNJ e nenhum outro proibido passam pelo laço dos joiners.
    """
    if v.isprintable():
        return False
    chars = set(v)
    if not chars.isdisjoint(_BAD_CHARS):
        return True
    if chars.isdisjoint(_JOINERS):
        return False
    return _joiners_misplaced(v)


def name_is_blank(v: str) -> bool:
    """Nome vazio, só espaços ou só invisíveis (espaços Unicode, Cf e preenchimentos sem glifo)."""
    if not v.strip():
        return True
    return _BLANK_CHARS.issuperset(v)


def check_name(v: Any) -> bool:
    """Texto de até 1024 caracteres que não é só espaço (nomes de arquivo; sem endurecimento)."""
    return isinstance(v, str) and bool(v.strip()) and len(v) <= MAX_NAME_CHARS


def check_class_name(v: Any) -> bool:
    """Nome de classe/categoria: `check_name` mais sem controles, substitutos, separadores de
    linha/parágrafo e Cf (bidi, ZWSP, BOM; ZWJ/ZWNJ só entre caracteres visíveis), e não vazio após
    ignorar invisíveis."""
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


def to_normalized(
    x: float, y: float, w: float, h: float, width: int, height: int
) -> tuple[float, float, float, float]:
    """`(x_center, y_center, width, height)` normalizados a partir de pixels e da imagem."""
    return ((x + w / 2) / width, (y + h / 2) / height, w / width, h / height)


def representable(x: float, y: float, w: float, h: float, width: int, height: int) -> bool:
    """A caixa cabe no esquema (centro em [0, 1], largura e altura em (0, 1])?"""
    xc, yc, nw, nh = to_normalized(x, y, w, h, width, height)
    return 0.0 <= xc <= 1.0 and 0.0 <= yc <= 1.0 and 0.0 < nw <= 1.0 and 0.0 < nh <= 1.0


def sticks_out(x: float, y: float, w: float, h: float, width: int, height: int) -> bool:
    """A caixa ultrapassa a borda da imagem (tolerância de 1e-9 no toque exato)?"""
    return (
        x < -BORDER_EPS
        or y < -BORDER_EPS
        or x + w > width + BORDER_EPS
        or y + h > height + BORDER_EPS
    )
