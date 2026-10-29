"""F1-07 (correções do revisor): DoS por item, separadores de coluna, nc e nomes (ADR-009)."""

import json
from typing import Any

import pytest

from _timing import timed
from inovalytics.imports.coco_parser import CocoLimits, parse_coco
from inovalytics.imports.common import check_class_name, check_name
from inovalytics.imports.errors import CocoImportError, ErrorCollector, ImportRejectedError
from inovalytics.imports.names_yaml import TxtLimits, normalize_names, parse_names_yaml
from inovalytics.imports.txt_labels_parser import ParsedTxtLabels, parse_txt_labels

LIM = TxtLimits()
LONG_KEY = "k" * 1020 + ".txt"  # 1024 caracteres
LIMIT_S = 3.0


def _body(labels: dict[str, str], names: Any = ("a",)) -> bytes:
    return json.dumps({"names": list(names), "labels": labels}).encode()


# ---------------------------------------------------------------- DoS: custo por item


def test_dos_300k_warnings_with_1024_char_key_cost_nothing_per_item_when_full() -> None:
    data = _body({LONG_KEY: "0 0.99 0.5 0.5 0.5\n" * 300_000})
    elapsed, r = timed(lambda: parse_txt_labels(data, LIM), 20.0)
    assert isinstance(r, ParsedTxtLabels)
    assert (r.warnings_total, len(r.warnings), r.boxes_count) == (300_000, 100, 300_000)
    assert r.warnings[0].line == 1 and r.warnings[0].file == "k" * 200
    print(f"300k avisos, chave 1024: {elapsed:.2f}s")
    assert elapsed < LIMIT_S


def test_dos_300k_line_errors_with_1024_char_key() -> None:
    data = _body({LONG_KEY: "0 x\n" * 300_000})
    elapsed, r = timed(lambda: parse_txt_labels(data, LIM, collect_errors=True), 20.0)
    assert isinstance(r, ParsedTxtLabels)
    assert (r.errors_total, len(r.errors)) == (300_000, 100)
    assert {e.file for e in r.errors} == {"k" * 200}
    print(f"300k erros por linha, chave 1024: {elapsed:.2f}s")
    assert elapsed < LIMIT_S


def test_dos_300k_line_errors_raise_with_real_total() -> None:
    data = _body({LONG_KEY: "0 x\n" * 300_000})
    elapsed, r = timed(lambda: parse_txt_labels(data, LIM), 20.0)
    assert isinstance(r, ImportRejectedError)
    assert r.total == 300_000 and len(r.errors) == 100 and r.truncated
    assert elapsed < LIMIT_S


def test_collector_never_builds_text_of_items_it_discards() -> None:
    col = ErrorCollector(2)
    calls: list[str] = []

    def lazy(tag: str) -> Any:
        def make() -> str:
            calls.append(tag)
            return tag

        return make

    for i in range(1000):
        col.add("c", "k", None, i, lazy(f"m{i}"), file=lazy(f"f{i}"))
    assert col.total == 1000 and calls == ["m0", "f0", "m1", "f1"]
    assert [(e.message, e.file) for e in col.items()] == [("m0", "f0"), ("m1", "f1")]


def test_many_files_with_full_collector_do_not_call_shown_per_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from inovalytics.imports import txt_labels_parser as mod

    calls = 0
    real = mod.shown

    def counting(name: str) -> str:
        nonlocal calls
        calls += 1
        return real(name)

    monkeypatch.setattr(mod, "shown", counting)
    parse_txt_labels(_body({"a.txt": "0 0.99 .5 .5 .5\n0 x\n" * 5000}), LIM, collect_errors=True)
    assert calls == 1  # uma vez por arquivo, não por aviso/erro


# ---------------------------------------------------------------- separadores de coluna

GOOD = "0 .5 .5 .1 .1"


def _codes(text: str) -> list[tuple[str, int | None]]:
    with pytest.raises(ImportRejectedError) as ei:
        parse_txt_labels(_body({"a.txt": text}), LIM)
    return [(e.code, e.line) for e in ei.value.errors]


@pytest.mark.parametrize(
    "line",
    [
        "0\t.5\t.5\t.1\t.1",
        "  0  .5 \t .5   .1\t\t.1  ",
        "0 .5 .5 .1 .1\r",
        "\t0 .5 .5 .1 .1",
    ],
)
def test_space_and_tab_separate_columns(line: str) -> None:
    r = parse_txt_labels(_body({"a.txt": line + "\n"}), LIM)
    assert r.boxes_count == 1


@pytest.mark.parametrize("sep", [" ", " ", "\x85", "\x1c", "\x1d", "\x1e", "\x1f"])
def test_exotic_separators_do_not_separate_columns(sep: str) -> None:
    # entre colunas: o par colado vira um só campo -> poucas colunas
    assert _codes(f"0{sep}.5{sep}.5{sep}.1{sep}.1") == [("colunas_insuficientes", 1)]
    # colado ao último número: número inválido
    assert _codes(f"0 .5 .5 .1 .1{sep}") == [("numero_invalido", 1)]
    # colado à classe: classe inválida
    assert _codes(f"0{sep} .5 .5 .1 .1") == [("classe_invalida", 1)]
    # linha só com o separador: uma coluna inválida, não linha em branco
    assert _codes(sep) == [("colunas_insuficientes", 1)]


def test_cr_inside_line_is_not_a_separator() -> None:
    assert _codes("0 .5 .5\r.1 .1") == [("colunas_insuficientes", 1)]


# ---------------------------------------------------------------- nc entre aspas


@pytest.mark.parametrize("nc", ['"2"', "'2'", '""', "''"])
def test_quoted_nc_is_refused_with_line(nc: str) -> None:
    with pytest.raises(ImportRejectedError) as ei:
        parse_names_yaml(f"path: x\nnc: {nc}\nnames: [a, b]\n", LIM)
    (e,) = ei.value.errors
    assert (e.code, e.line) == ("nc_invalido", 2)


def test_plain_nc_still_works() -> None:
    assert parse_names_yaml("nc: 2\nnames: [a, b]\n", LIM) == ["a", "b"]


# ---------------------------------------------------------------- nomes de classe

BAD_NAMES = [
    "a‮b",  # bidi override
    "a⁦b",  # isolate
    "a​b",  # zero-width space
    "a‍b",  # zero-width joiner
    "﻿a",  # BOM no início
    "a⁠b",  # word joiner
    "​",  # só invisível
    "​ ⁠",
    "ㅤ",  # preenchimento sem glifo
    "⠀",
    "   ",
]


@pytest.mark.parametrize("name", BAD_NAMES)
def test_names_yaml_rejects_cf_and_blank_names(name: str) -> None:
    for call in (
        lambda: normalize_names([name], LIM),
        lambda: normalize_names({"0": name}, LIM),
        lambda: parse_names_yaml(f'names: ["{name}"]\n', LIM),
        lambda: parse_names_yaml(f"names:\n  - '{name}'\n", LIM),
    ):
        with pytest.raises(ImportRejectedError) as ei:
            call()
        assert ei.value.errors[0].code == "nome_invalido"


@pytest.mark.parametrize("name", ["pessoa", "capacete de segurança", "ね", "é", "a-b_c.1"])
def test_names_yaml_keeps_ordinary_names(name: str) -> None:
    assert normalize_names([name], LIM) == [name]


@pytest.mark.parametrize("name", BAD_NAMES)
def test_coco_category_rejects_cf_and_blank_names(name: str) -> None:
    assert not check_class_name(name)
    d = {
        "images": [{"id": 1, "file_name": "a.jpg", "width": 10, "height": 10}],
        "categories": [{"id": 5, "name": name}],
        "annotations": [],
    }
    with pytest.raises(CocoImportError) as ei:
        parse_coco(json.dumps(d).encode(), CocoLimits())
    assert [e.code for e in ei.value.errors] == ["categoria_nome_invalido"]


@pytest.mark.parametrize("name", ["gato", "cão", "é", "ね"])
def test_coco_category_keeps_ordinary_names(name: str) -> None:
    assert check_class_name(name)


def test_file_name_check_is_unchanged() -> None:
    # decisão registrada no ADR-008/009: file_name continua com a regra da F1-06
    assert check_name("a‮b.jpg") and not check_name("  ") and not check_name("a" * 1025)
