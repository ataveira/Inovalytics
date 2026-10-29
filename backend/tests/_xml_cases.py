"""Documentos hostis sintéticos para o leitor XML (F1-08a). Fixtures só em código."""

from __future__ import annotations

KIB = 1024
MAX = 256 * KIB


def billion_laughs() -> str:
    """Clássico: 10 níveis de 10 referências (10**9 expansões se fosse aceito)."""
    decl = '<!ENTITY l0 "ha">' + "".join(
        f'<!ENTITY l{i} "' + f"&l{i - 1};" * 10 + '">' for i in range(1, 10)
    )
    return f"<?xml version='1.0'?><!DOCTYPE annotation [{decl}]><annotation>&l9;</annotation>"


def billion_laughs_param() -> str:
    decl = '<!ENTITY % p0 "ha">' + "".join(
        f'<!ENTITY % p{i} "' + f"%p{i - 1};" * 10 + '">' for i in range(1, 10)
    )
    return f"<!DOCTYPE annotation [{decl} %p9;]><annotation/>"


def quadratic_blowup() -> str:
    big = "x" * (50 * KIB)
    return (
        f'<!DOCTYPE annotation [<!ENTITY e "{big}">]><annotation>'
        + "&e;" * 50_000
        + "</annotation>"
    )


def nested(n: int = (MAX - 12) // 3) -> str:
    return "<annotation>" + "<a>" * n


def repeated_empty() -> str:
    body = "<a/>" * ((MAX - 40) // 4)
    return f"<annotation>{body}</annotation>"


def spaces() -> str:
    return "<annotation>" + " " * (MAX - 40) + "</annotation>"


def comments() -> str:
    body = "<!---->" * ((MAX - 40) // 7)
    return f"<annotation>{body}</annotation>"


def single_text() -> str:
    return "<annotation><a>" + "x" * (MAX - 60) + "</a></annotation>"


def many_attributes() -> str:
    attrs = "".join(f' a{i}="1"' for i in range((MAX - 40) // 11))
    return f"<annotation{attrs}/>"


def long_attribute() -> str:
    return '<annotation v="' + "v" * (200 * KIB) + '"/>'


def huge_name() -> str:
    return "<annotation><" + "n" * (MAX - 60) + "/></annotation>"


def garbage(seed: int, size: int = MAX) -> str:
    import random

    rnd = random.Random(seed)
    alphabet = "<>/&;\"'=! -?[]\x00\x01\r\n \ud800﻿aé"
    return "".join(rnd.choice(alphabet) for _ in range(size))
