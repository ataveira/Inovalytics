"""Teste diferencial: a validação de nomes com tabelas em C é idêntica à definição por caractere.

`_old_*` é a cópia literal da implementação anterior (F1-07, laço Python com `unicodedata`) e serve
de referência. Cobre todos os pontos de código isolados e em contexto, mais fuzz de ZWJ/ZWNJ.
"""

from __future__ import annotations

import random
import unicodedata
from typing import Any

from inovalytics.imports import common
from inovalytics.imports.common import check_class_name, name_has_bad_chars, name_is_blank

_BLANK_FILLERS = frozenset("ᅟᅠ⠀ㅤﾠ")
_BAD_NAME_CATEGORIES = frozenset({"Cc", "Cs", "Zl", "Zp", "Cf"})
_JOINERS = frozenset("‌‍")


def _old_visible(c: str) -> bool:
    return not (
        c.isspace()
        or c in _BLANK_FILLERS
        or c in _JOINERS
        or unicodedata.category(c) in _BAD_NAME_CATEGORIES
    )


def _old_has_bad(v: str) -> bool:
    if v.isprintable():
        return False
    last = len(v) - 1
    for i, c in enumerate(v):
        if c in _JOINERS:
            if 0 < i < last and _old_visible(v[i - 1]) and _old_visible(v[i + 1]):
                continue
            return True
        if unicodedata.category(c) in _BAD_NAME_CATEGORIES:
            return True
    return False


def _old_blank(v: str) -> bool:
    if not v.strip():
        return True
    return all(c.isspace() or c in _BLANK_FILLERS or unicodedata.category(c) == "Cf" for c in v)


def _old_check(v: Any) -> bool:
    ok = isinstance(v, str) and bool(v.strip()) and len(v) <= common.MAX_NAME_CHARS
    return ok and not _old_blank(v) and not _old_has_bad(v)


def _same(v: str) -> None:
    assert name_has_bad_chars(v) == _old_has_bad(v), ascii(v)
    assert name_is_blank(v) == _old_blank(v), ascii(v)
    assert check_class_name(v) == _old_check(v), ascii(v)


def test_every_code_point_alone_and_in_context() -> None:
    for cp in range(0x110000):
        c = chr(cp)
        for v in (c, "a" + c + "a", "a" + c, c + "a"):
            assert name_has_bad_chars(v) == _old_has_bad(v), ascii(v)
            assert name_is_blank(v) == _old_blank(v), ascii(v)
            assert check_class_name(v) == _old_check(v), ascii(v)


def test_every_code_point_next_to_a_joiner() -> None:
    for cp in range(0x110000):
        c = chr(cp)
        for v in ("a‍" + c + "a", "a" + c + "‌a", "a‌" + c, c + "‍"):
            assert name_has_bad_chars(v) == _old_has_bad(v), ascii(v)


def test_joiner_fuzz_at_edges_and_runs() -> None:
    rng = random.Random(20261029)
    alphabet = ["a", "b", "‍", "‌", " ", "ㅤ", "​", "‮", "\n", "😀", "م"]
    for _ in range(60_000):
        n = rng.randint(1, 8)
        _same("".join(rng.choice(alphabet) for _ in range(n)))
    for _ in range(300):
        n = rng.randint(1, 300)
        pool = ["a", "‍", "‌"] if rng.random() < 0.8 else alphabet
        _same("".join(rng.choice(pool) for _ in range(n)))


def test_non_string_and_repeated_calls_are_stable() -> None:
    for v in (None, 1, b"a", ["a"], {"a": 1}):
        assert check_class_name(v) == _old_check(v) is False
    for v in ("ab", "a‍b", "a‮b", "a‍b"):  # repetido: passa pelo cache
        for _ in range(2):
            _same(v)
