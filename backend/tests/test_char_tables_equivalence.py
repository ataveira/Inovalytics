"""Teste diferencial (F1-08, ADR-010): as versões com tabelas em C de `_key_problem`,
`sanitize_file_name`, `shown` e das próprias tabelas são idênticas às definições por caractere.

`_old_*` são cópias literais das implementações anteriores (laço Python com `unicodedata`).
"""

from __future__ import annotations

import random
import re
import unicodedata

from inovalytics.imports import chartables
from inovalytics.imports.common import shown
from inovalytics.imports.voc_parser import _key_problem
from inovalytics.uploads.naming import sanitize_file_name

ALL = [chr(cp) for cp in range(0x110000)]
cat = unicodedata.category

# ---- cópias das versões anteriores -------------------------------------------------------------
_OLD_CONTROL = frozenset({"Cc", "Cs", "Zl", "Zp"})
MAX_KEY_CHARS = 1024
SUFFIX = ".xml"


def _old_key_problem(key: str) -> tuple[str, str] | None:
    if not key or len(key) > MAX_KEY_CHARS:
        return "rotulo_nome_invalido", "nome do arquivo XML vazio ou maior que 1024"
    if not key.isprintable() and any(unicodedata.category(c) in _OLD_CONTROL for c in key):
        return "rotulo_nome_invalido", "nome do arquivo XML com caractere de controle"
    if not key.endswith(SUFFIX):
        return "rotulo_extensao_invalida", "o arquivo de anotação deve terminar em '.xml'"
    return None


_OLD_DROPPED = frozenset({"Cc", "Cf", "Zl", "Zp"})
_FALLBACK = "imagem"
MAX_NAME = 255


def _old_once(name: str) -> str:
    s = unicodedata.normalize("NFC", name)
    s = re.split(r"[/\\]", s)[-1]
    s = "".join(c for c in s if unicodedata.category(c) not in _OLD_DROPPED).strip()
    if s in ("", ".", ".."):
        return _FALLBACK
    if len(s) > MAX_NAME:
        dot = s.rfind(".")
        ext = s[dot:] if dot > 0 and len(s) - dot <= 16 else ""
        s = s[: MAX_NAME - len(ext)] + ext
        s = s.strip()
        if s in ("", ".", ".."):
            return _FALLBACK
    return s


def _old_sanitize(name: str) -> str:
    s = _old_once(name)
    for _ in range(4):
        t = _old_once(s)
        if t == s:
            break
        s = t
    return s


def _old_shown(name: str) -> str:
    out: list[str] = []
    for c in name[:4096]:
        k = unicodedata.category(c)
        if k[0] != "C" and k != "Zl" and k != "Zp":
            out.append(c)
            if len(out) >= 200:
                break
    return "".join(out)


# ---- tabelas -----------------------------------------------------------------------------------
def test_tables_match_category_for_every_code_point() -> None:
    expected = {
        "CONTROL_CHARS": {"Cc", "Cs", "Zl", "Zp"},
        "DROPPED_CHARS": {"Cc", "Cf", "Zl", "Zp"},
        "BAD_NAME_CHARS": {"Cc", "Cs", "Zl", "Zp", "Cf"},
        "FORMAT_CHARS": {"Cf"},
    }
    for name, cats in expected.items():
        assert getattr(chartables, name) == {c for c in ALL if cat(c) in cats}, name
    assert chartables.SPACE_CHARS == {c for c in ALL if c.isspace()}


# ---- _key_problem ------------------------------------------------------------------------------
def test_key_problem_every_code_point() -> None:
    for c in ALL:
        for k in (c, "a" + c + ".xml"):
            assert _key_problem(k) == _old_key_problem(k), ascii(k)


def test_key_problem_chunks_lengths_and_fuzz() -> None:
    for start in range(0, 0x110000, 700):
        k = "".join(ALL[start : start + 700]) + ".xml"
        assert _key_problem(k) == _old_key_problem(k), start
    for k in ("", "a" * 1024, "a" * 1025, "a" * 1020 + ".xml", "a" * 1021 + ".xml", "x.XML"):
        assert _key_problem(k) == _old_key_problem(k)
    assert _key_problem("a" * 1019 + "‮" + ".xml") == _old_key_problem("a" * 1019 + "‮.xml")
    rng = random.Random(8)
    pool = ["a", "é", ".", "/", "\\", " ", "‮", "‍", "\x00", " ", "\ud800", "\U0001f600", ".xml"]
    for _ in range(20000):
        k = "".join(rng.choice(pool) for _ in range(rng.randint(0, 12)))
        assert _key_problem(k) == _old_key_problem(k), ascii(k)


# ---- sanitize_file_name ------------------------------------------------------------------------
def _interesting() -> list[str]:
    keep = [c for c in ALL if cat(c) in {"Cc", "Cf", "Cs", "Zl", "Zp", "Zs", "Mn"} or c.isspace()]
    return keep + ALL[::7]


def test_sanitize_code_points_in_context() -> None:
    for c in _interesting():
        for v in (c, "a" + c + "b", c + "a", "a" + c, "/" + c, c + "/", " " + c + " "):
            assert sanitize_file_name(v) == _old_sanitize(v), ascii(v)


def test_sanitize_every_code_point_in_chunks() -> None:
    for start in range(0, 0x110000, 200):
        v = "".join(ALL[start : start + 200])
        assert sanitize_file_name(v) == _old_sanitize(v), start
        w = "a/" + v + ".jpg"
        assert sanitize_file_name(w) == _old_sanitize(w), start


def test_sanitize_shortcut_and_edge_cases() -> None:
    cases = [
        "", " ", ".", "..", "...", " . ", "a.jpg", "a b.png", " a", "a ", "a\t", "\ta",
        "a/b", "a\\b", "dir/", "/", "\\", "a" * 255, "a" * 256, "a" * 300 + ".jpg",
        "a" * 252 + ".jpg", "a" * 254 + " b", ("a" * 254 + " ") + "b", "." * 300, " " * 300,
        "a" * 240 + "‮" * 30 + ".png", "é" * 200, "é" * 255, "é" * 256, "~", "\x7f",
        "a" * 10 + "\x00" * 300, "x.jpg" + " " * 5, "﻿name.jpg", "é.png",
        "café.jpg", "a" * 255 + ".", "." + "a" * 300,
    ]  # fmt: skip
    for v in cases:
        assert sanitize_file_name(v) == _old_sanitize(v), ascii(v)


def test_sanitize_fuzz() -> None:
    rng = random.Random(10)
    pool = ["a", "b", ".", "..", "/", "\\", " ", "\t", "‮", "‍", "\x00", " ", "é",
            "é", "́", "\U0001f600", ".jpg", "\ud800", " ", "x" * 100]  # fmt: skip
    for _ in range(30000):
        v = "".join(rng.choice(pool) for _ in range(rng.randint(0, 9)))
        assert sanitize_file_name(v) == _old_sanitize(v), ascii(v)


# ---- shown -------------------------------------------------------------------------------------
def test_shown_every_code_point_and_lengths() -> None:
    for c in ALL:
        for v in (c, "a" + c + "b"):
            assert shown(v) == _old_shown(v), ascii(v)
    for start in range(0, 0x110000, 300):
        v = "".join(ALL[start : start + 300])
        assert shown(v) == _old_shown(v), start
    for v in ("a" * 199, "a" * 200, "a" * 201, "a" * 5000, "a" * 199 + "\x00" + "b" * 10,
              "\x00" * 4100 + "a", "a" * 4090 + "\x00" * 20 + "bcd", "é" * 250):  # fmt: skip
        assert shown(v) == _old_shown(v), ascii(v[:20])
