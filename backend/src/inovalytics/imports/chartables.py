"""Tabelas de pontos de código por categoria Unicode, calculadas uma vez (F1-08, ADR-010).

Servem de pré-filtro em C (`set.isdisjoint`, `str.translate`) para que nenhum laço Python por
caractere rode sobre texto controlado pelo cliente. São calculadas com o `unicodedata` do próprio
processo, no import do módulo: o comportamento por versão do Python é o mesmo de antes (a tabela
muda junto com o `unicodedata`, como mudava a chamada `unicodedata.category` por caractere).

Só pontos que `str.isprintable` recusa entram na varredura: toda categoria de interesse (Cc, Cf,
Cs, Zl, Zp e os espaços) é não imprimível, exceto o ESPAÇO ASCII (tratado à parte). Os pontos
atribuídos e imprimíveis (a maioria) nunca chegam a `unicodedata.category`. Tudo roda em
`itertools` e `map` (C), sem laço Python por ponto de código.
"""

from __future__ import annotations

import unicodedata
from itertools import compress, filterfalse
from operator import or_

_NONPRINTABLE = list(filterfalse(str.isprintable, map(chr, range(0x110000))))
_CATEGORIES = list(map(unicodedata.category, _NONPRINTABLE))


def _of(*categories: str) -> frozenset[str]:
    wanted = frozenset(categories)
    return frozenset(compress(_NONPRINTABLE, map(wanted.__contains__, _CATEGORIES)))


# Cc, Cs, Zl, Zp: recusados em chaves de arquivo (F1-08).
CONTROL_CHARS = _of("Cc", "Cs", "Zl", "Zp")
# Cc, Cf, Zl, Zp: removidos de nomes de arquivo (`sanitize_file_name`, F1-05).
DROPPED_CHARS = _of("Cc", "Cf", "Zl", "Zp")
# Cc, Cs, Zl, Zp, Cf: recusados em nomes de classe (F1-07).
BAD_NAME_CHARS = _of("Cc", "Cs", "Zl", "Zp", "Cf")
# Formato (bidi, largura zero, BOM).
FORMAT_CHARS = _of("Cf")
# `str.isspace` (o espaço ASCII é imprimível e por isso não está na varredura).
SPACE_CHARS = frozenset(compress(_NONPRINTABLE, map(str.isspace, _NONPRINTABLE))) | {" "}
# Espaços não ASCII (Zs): não imprimíveis, mas mantidos por `visible`.
_SEPARATOR_SPACES = _of("Zs")
_MAX_REPLACES = 8


def visible(text: str) -> str:
    """`text` sem os caracteres de categoria C* (Cc, Cf, Cs, Co, Cn), Zl e Zp; os Zs ficam.

    Sem laço Python por caractere. O conjunto de caracteres distintos e não imprimíveis é achado em
    C; se é pequeno (o caso comum), um `replace` por caractere; se é grande (hostil), uma única
    passada de `map`/`compress`. Custo linear no tamanho de `text`, com constante pequena.
    """
    if text.isprintable():  # imprimível = nenhuma categoria C* nem Zl/Zp
        return text
    bad = set(filterfalse(str.isprintable, set(text))) - _SEPARATOR_SPACES
    if len(bad) <= _MAX_REPLACES:
        for ch in bad:
            text = text.replace(ch, "")
        return text
    keep = map(or_, map(str.isprintable, text), map(_SEPARATOR_SPACES.__contains__, text))
    return "".join(compress(text, keep))
