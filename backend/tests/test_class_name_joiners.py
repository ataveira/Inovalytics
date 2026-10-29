"""ZWJ/ZWNJ (U+200D/U+200C) só são aceitos isolados entre caracteres visíveis (F1-07)."""

from __future__ import annotations

import pytest

from inovalytics.imports.common import check_class_name
from inovalytics.imports.errors import ImportRejectedError
from inovalytics.imports.names_yaml import TxtLimits, normalize_names

LIM = TxtLimits()
FAMILY = "\U0001f468‍\U0001f469‍\U0001f467"
PERSIAN = "می‌خواهم"

GOOD = [FAMILY, PERSIAN, "a‍b", "a‌b"]
BAD = [
    "‍a",  # início
    "a‍",  # fim
    "a‌",
    "‌",  # só junção
    "‍‍",
    "a‍‍b",  # consecutivos
    "a‍‌b",
    "a ‍b",  # ao lado de espaço
    "a‍ b",
    "a​ b",
    "a​b",  # ZWSP segue recusado
    "a﻿b",  # BOM
    "a‮b",  # bidi
    "aㅤb".replace("b", "‍b"),  # junção ao lado de preenchimento
    "a‍​b",
]


@pytest.mark.parametrize("name", GOOD)
def test_joiners_between_visible_chars_are_accepted(name: str) -> None:
    assert check_class_name(name)
    assert normalize_names([name], LIM) == [name]


@pytest.mark.parametrize("name", BAD)
def test_loose_joiners_and_other_cf_are_refused(name: str) -> None:
    assert not check_class_name(name)
    with pytest.raises(ImportRejectedError) as ei:
        normalize_names([name], LIM)
    assert ei.value.errors[0].code == "nome_invalido"
