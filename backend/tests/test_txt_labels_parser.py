"""F1-07b critérios 8 a 18: parser dos rótulos txt e do corpo (puro; fixtures só em código)."""

import ast
import contextlib
import json
import random
import tracemalloc
from pathlib import Path
from typing import Any

import pytest

from _timing import timed
from inovalytics.imports import txt_labels_parser as mod
from inovalytics.imports.errors import ImportRejectedError
from inovalytics.imports.txt_labels_parser import ParsedTxtLabels, TxtLimits, parse_txt_labels

NAMES = ["pessoa", "capacete", "colete"]
LIM = TxtLimits()


def body(labels: Any = None, names: Any = NAMES, **extra: Any) -> bytes:
    obj: dict[str, Any] = {"labels": {} if labels is None else labels}
    if names is not None:
        obj["names"] = names
    obj.update(extra)
    return json.dumps(obj).encode()


def parse(labels: Any = None, lim: TxtLimits = LIM, **kw: Any) -> ParsedTxtLabels:
    return parse_txt_labels(body(labels, **kw), lim)


def refused(data: bytes, lim: TxtLimits = LIM) -> ImportRejectedError:
    with pytest.raises(ImportRejectedError) as ei:
        parse_txt_labels(data, lim)
    return ei.value


def errs(labels: Any, lim: TxtLimits = LIM, **kw: Any) -> list[tuple[str, str | None, int | None]]:
    e = refused(body(labels, **kw), lim)
    return [(x.code, x.file, x.line) for x in e.errors]


def code_of(labels: Any, lim: TxtLimits = LIM, **kw: Any) -> str:
    return errs(labels, lim, **kw)[0][0]


# ------------------------------------------------------------------ 8: corpo e estrutura


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"   ",
        b"{",
        b'{"labels": {}',
        b"\xff\xfe",
        b"[]",
        b'"x"',
        b"1",
        b"null",
        b'{"labels": {}, "names": ["a"], "x": NaN}',
        b'{"labels": {}, "names": ["a"], "x": Infinity}',
        b'{"labels": {}, "names": ["a"], "x": -Infinity}',
        b'{"labels": {}, "labels": {}, "names": ["a"]}',
        b"{" * 100_000,
        b"[" * 100_000,
        b'{"labels": {}, "names": ["a"], "x": 1' + b"0" * 5000 + b"}",
    ],
)
def test_bad_json_is_json_invalido_or_depth(data: bytes) -> None:
    e = refused(data)
    assert e.errors[0].code in {"json_invalido", "limite_excedido"}
    assert len(e.errors) == 1


def test_json_invalido_codes() -> None:
    for data in (b"", b"{", b"\xff", b"[]", b'{"a": 1, "a": 2}'):
        assert refused(data).errors[0].code == "json_invalido"


def test_bom_utf8_is_accepted() -> None:
    p = parse_txt_labels(b"\xef\xbb\xbf" + body({"a.txt": "0 0.5 0.5 0.1 0.1"}), LIM)
    assert p.boxes_count == 1


@pytest.mark.parametrize(
    ("kwargs", "code"),
    [
        ({"labels": None}, "estrutura_invalida"),
        ({"labels": []}, "estrutura_invalida"),
        ({"labels": "x"}, "estrutura_invalida"),
        ({"names": None}, "names_ausente"),
        ({"data_yaml": "names: [a]", "names": ["a"]}, "names_ambiguo"),
        ({"data_yaml": 5, "names": None}, "estrutura_invalida"),
        ({"data_yaml": None, "names": None}, "estrutura_invalida"),
        ({"names": True}, "names_tipo_invalido"),
        ({"names": [1]}, "names_tipo_invalido"),
        ({"data_yaml": "path: x", "names": None}, "names_ausente"),
    ],
)
def test_structure_errors(kwargs: dict[str, Any], code: str) -> None:
    obj: dict[str, Any] = {"labels": {}, "names": ["a"]}
    obj.update(kwargs)
    obj = {k: v for k, v in obj.items() if not (v is None and k == "names" and "names" in kwargs)}
    if kwargs.get("labels", 0) is None:
        del obj["labels"]
    assert refused(json.dumps(obj).encode()).errors[0].code == code


def test_missing_labels_key() -> None:
    assert refused(b'{"names": ["a"]}').errors[0].code == "estrutura_invalida"


def test_non_string_label_value_names_the_file() -> None:
    e = errs({"b.txt": 5, "a.txt": "0 .5 .5 .1 .1", "c.txt": None, "d.txt": ["0"]})
    assert [(c, f) for c, f, _ in e] == [
        ("rotulo_invalido", n) for n in ("b.txt", "c.txt", "d.txt")
    ]


def test_data_yaml_path_and_extra_keys_ignored() -> None:
    p = parse_txt_labels(
        json.dumps({"labels": {"a.txt": "2 .5 .5 .5 .5"}, "data_yaml": "nc: 3\nnames: [a, b, c]\n",
                    "extra": {"x": [1, 2]}}).encode(), LIM)  # fmt: skip
    assert p.names == ("a", "b", "c") and p.boxes[0].class_id == 2


def test_yaml_error_is_forwarded_with_line() -> None:
    e = refused(json.dumps({"labels": {}, "data_yaml": "names: [a]\nnc: 4\n"}).encode())
    assert (e.errors[0].code, e.errors[0].kind, e.errors[0].line) == (
        "nc_divergente",
        "data_yaml",
        2,
    )


def test_random_bytes_only_raise_import_rejected() -> None:
    rng = random.Random(11)
    seeds = [body({"a.txt": "0 .5 .5 .1 .1\n1 2 3 4 5\n"}), body({"a.txt": "x"}, names=["a"]),
             b'{"labels": {"a.txt": "0 1 1 1 1"}, "data_yaml": "names: [a]"}']  # fmt: skip
    for _ in range(3000):
        b = bytearray(rng.choice(seeds))
        for _ in range(rng.randrange(1, 8)):
            b[rng.randrange(len(b))] = rng.randrange(256)
        with contextlib.suppress(ImportRejectedError):
            parse_txt_labels(bytes(b), LIM)
    for _ in range(1000):
        text = "".join(rng.choice("0123456789 .-+eE\n\r\t#nax\u2028\x0b") for _ in range(60))
        with contextlib.suppress(ImportRejectedError):
            parse_txt_labels(body({"a.txt": text}), LIM)


# ------------------------------------------------------------------ 9: fixture de aceite


def test_acceptance_fixture() -> None:
    yaml = "path: ../ds\ntrain: images/train\nnames:\n  0: pessoa\n  1: capacete\n  2: colete\n"
    labels = {
        "img2.txt": "2 0.1 0.2 0.3 0.4\n",
        "img1.txt": "0 0.5 0.5 0.2 0.2\n1 0.5 0.25 0.2 0.3\n",
    }
    p = parse_txt_labels(json.dumps({"data_yaml": yaml, "labels": labels}).encode(), LIM)
    assert p.names == tuple(NAMES)
    assert [(f.name, f.base_name, f.box_count) for f in p.files] == [
        ("img1.txt", "img1", 2), ("img2.txt", "img2", 1)]  # fmt: skip
    assert (p.files_count, p.boxes_count) == (2, 3)
    expected = [(0, 1, 0, 0.5, 0.5, 0.2, 0.2), (0, 2, 1, 0.5, 0.25, 0.2, 0.3),
                (1, 1, 2, 0.1, 0.2, 0.3, 0.4)]  # fmt: skip
    for got, want in zip(p.boxes, expected, strict=True):
        assert got[:3] == want[:3]
        assert all(abs(a - b) < 1e-6 for a, b in zip(got[3:], want[3:], strict=True))
    assert p.boxes[1].class_id == 1 and p.boxes[1].cx == 0.5 and p.boxes[1].cy == 0.25


def test_blank_lines_and_crlf_do_not_shift_line_numbers() -> None:
    text = "\n  \r\n0 .5 .5 .1 .1\r\n\n\t\n1 .5 .5 .1 .1\r\n"
    p = parse({"a.txt": text})
    assert [(b.line, b.class_id) for b in p.boxes] == [(3, 0), (6, 1)]
    e = errs({"a.txt": "\n\r\n0 .5 .5 .1 .1\r\n\n0 .5 .5\r\n"})
    assert e == [("colunas_insuficientes", "a.txt", 5)]


def test_line_numbers_use_only_lf() -> None:
    odd = "\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029"
    text = f"0 .5 .5 .1 .1{odd}\n0 .5 .5 .1 .1\n"
    # separadores exóticos não quebram linha (numeração só por LF) e, desde o ADR-009, também
    # não separam colunas: colam no último número da linha 1 (`numero_invalido`); a linha 2 segue
    # válida. Antes, `str.split()` os tratava como espaço e a linha 1 era aceita (permissivo,
    # trocado de propósito: só espaço e tab separam).
    assert errs({"a.txt": text}) == [("numero_invalido", "a.txt", 1)]
    assert [
        b.line for b in parse({"a.txt": "0 .5 .5 .1 .1\n" + text.split("\n")[1] + "\n"}).boxes
    ] == [1, 2]
    # linha só com U+2028 agora é uma coluna (inválida), na linha 1; a linha 2 tem 4 colunas
    assert errs({"a.txt": "\u2028\n0 1 1 1\n"}) == [
        ("colunas_insuficientes", "a.txt", 1),
        ("colunas_insuficientes", "a.txt", 2),
    ]


def test_bom_at_file_start_and_empty_file() -> None:
    p = parse({"a.txt": "\ufeff0 .5 .5 .1 .1\n", "b.txt": "", "c.txt": "\n\n"})
    assert [f.box_count for f in p.files] == [1, 0, 0]


def test_repeated_lines_are_kept() -> None:
    assert parse({"a.txt": "0 .5 .5 .1 .1\n" * 3}).boxes_count == 3


def test_labels_empty_is_valid() -> None:
    p = parse({})
    assert p.names == tuple(NAMES) and p.files == () and p.boxes == ()


# ------------------------------------------------------------------ 10: tolerância


def one(cx: str = "0.5", cy: str = "0.5", w: str = "0.1", h: str = "0.1") -> dict[str, str]:
    return {"a.txt": f"0 {cx} {cy} {w} {h}\n"}


@pytest.mark.parametrize(
    ("tok", "clamped"),
    [("-1e-6", 0.0), ("-0.000001", 0.0), ("1.000001", 1.0), ("0", 0.0), ("1", 1.0), ("0.5", 0.5),
     ("5e-1", 0.5), ("1E-1", 0.1), (".5", 0.5)],
)  # fmt: skip
def test_center_accepted_and_clamped(tok: str, clamped: float) -> None:
    for field in ("cx", "cy"):
        b = parse(one(**{field: tok})).boxes[0]
        assert getattr(b, field) == pytest.approx(clamped, abs=1e-12)


@pytest.mark.parametrize(
    "tok", ["-1.1e-6", "-0.0000011", "1.0000011", "1.1", "-0.1", "2", "5.", "1e1"]
)
def test_center_refused_outside_tolerance(tok: str) -> None:
    assert code_of(one(cx=tok)) == "valor_fora_do_intervalo"
    assert code_of(one(cy=tok)) == "valor_fora_do_intervalo"


def test_size_boundaries() -> None:
    assert parse(one(w="1.000001")).boxes[0].w == 1.0
    assert parse(one(h="1.000001")).boxes[0].h == 1.0
    assert parse(one(w="1", h="1")).boxes[0].w == 1.0
    assert parse(one(w="1e-300")).boxes[0].w == 1e-300
    for tok in ("1.0000011", "1.1", "5"):
        assert code_of(one(w=tok)) == "valor_fora_do_intervalo"
        assert code_of(one(h=tok)) == "valor_fora_do_intervalo"
    for tok in ("0", "0.0", "-0.1", "-0", "0e0", "-1e-9"):
        assert code_of(one(w=tok)) == "caixa_area_zero"
        assert code_of(one(h=tok)) == "caixa_area_zero"


def test_scientific_and_short_decimals() -> None:
    b = parse(one(cx="5e-1", cy="5E-1", w=".5", h="1e-1")).boxes[0]
    assert (b.cx, b.cy, b.w, b.h) == (0.5, 0.5, 0.5, 0.1)


# ------------------------------------------------------------------ 11: tokens inválidos


@pytest.mark.parametrize(
    "tok",
    ["nan", "NaN", "inf", "-inf", "infinity", "1e999", "-1e999", "0x10", "1_0", "١٢٣", "0,5", "+",
     "--1", "1e", "e", ".", "-", "..5", "1e+", "1.2.3", "0.5f", "1" * 33, "0." + "1" * 31, "٠.٥",
     "5\u00b2"],
)  # fmt: skip
def test_invalid_number_tokens(tok: str) -> None:
    for slot in range(4):
        parts = ["0.5"] * 4
        parts[slot] = tok
        e = errs({"a.txt": "\n0 " + " ".join(parts)})
        assert e == [("numero_invalido", "a.txt", 2)], (tok, slot)


def test_number_token_length_boundary() -> None:
    tok32 = "0." + "0" * 29 + "1"
    assert len(tok32) == 32
    assert parse(one(cx=tok32)).boxes_count == 1
    assert code_of(one(cx=tok32 + "1")) == "numero_invalido"


@pytest.mark.parametrize(
    "tok", ["1.0", "-1", "+1", "1e0", "0x1", "a", "١", "1_0", "0.0", "-0", "nan"]
)
def test_invalid_class_tokens(tok: str) -> None:
    assert errs({"a.txt": f"{tok} .5 .5 .1 .1"}) == [("classe_invalida", "a.txt", 1)]


def test_class_out_of_range() -> None:
    assert code_of({"a.txt": "3 .5 .5 .1 .1"}) == "classe_fora_do_intervalo"
    assert code_of({"a.txt": "1234567890 .5 .5 .1 .1"}) == "classe_fora_do_intervalo"
    assert code_of({"a.txt": "9" * 5000 + " .5 .5 .1 .1"}) in {
        "classe_fora_do_intervalo",
        "linha_longa",
    }
    assert parse({"a.txt": "2 .5 .5 .1 .1"}).boxes[0].class_id == 2
    assert parse({"a.txt": "002 .5 .5 .1 .1"}).boxes[0].class_id == 2
    assert parse({"a.txt": "000000000 .5 .5 .1 .1"}).boxes[0].class_id == 0


def test_class_nine_digits_vs_names_size() -> None:
    names = [f"c{i}" for i in range(1000)]
    assert parse({"a.txt": "999 .5 .5 .1 .1"}, names=names).boxes[0].class_id == 999
    assert code_of({"a.txt": "1000 .5 .5 .1 .1"}, names=names) == "classe_fora_do_intervalo"


# ------------------------------------------------------------------ 12: colunas


@pytest.mark.parametrize(
    ("n", "code"),
    [(1, "colunas_insuficientes"), (2, "colunas_insuficientes"), (4, "colunas_insuficientes"),
     (6, "coluna_extra"), (7, "segmentacao_nao_suportada"), (8, "colunas_excedentes"),
     (9, "segmentacao_nao_suportada"), (10, "colunas_excedentes"),
     (11, "segmentacao_nao_suportada"), (101, "segmentacao_nao_suportada")],
)  # fmt: skip
def test_column_counts(n: int, code: str) -> None:
    line = " ".join(["0"] + ["0.5"] * (n - 1))
    assert errs({"a.txt": line}) == [(code, "a.txt", 1)]


def test_blank_line_and_five_columns() -> None:
    assert parse({"a.txt": "   \t \n0\t.5   .5 .1\t.1"}).boxes_count == 1


def test_long_line_is_refused_without_split(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    real = mod._fields

    def spy(line: str) -> list[str]:
        calls.append(line)
        return real(line)

    monkeypatch.setattr(mod, "_fields", spy)
    long = "0 .5 .5 .1 .1" + " " * (1025 - 13)
    assert len(long) == 1025
    assert errs({"a.txt": long}) == [("linha_longa", "a.txt", 1)]
    assert calls == []
    ok = "0 .5 .5 .1 .1" + " " * (1024 - 13)
    assert len(ok) == 1024
    assert parse({"a.txt": ok}).boxes_count == 1
    assert len(calls) == 1
    assert errs({"a.txt": " " * 1025}) == [("linha_longa", "a.txt", 1)]
    assert parse({"a.txt": " " * 1024}).boxes_count == 0
    assert errs({"a.txt": "0 .5 .5 .1 .1\r" + " " * 1025}) == [("linha_longa", "a.txt", 1)]


# ------------------------------------------------------------------ 13: avisos


def test_box_outside_image_warns_without_clipping() -> None:
    lines = ["0 0.95 0.5 0.2 0.1", "0 0.9 0.5 0.2 0.1", "0 0.5 0.5 1.0 1.0", "0 0.05 0.05 0.1 0.1",
             "0 0.5 0.98 0.1 0.1"]  # fmt: skip
    p = parse({"a.txt": "\n".join(lines) + "\n"})
    assert [(w.code, w.file, w.line) for w in p.warnings] == [
        ("caixa_fora_da_imagem", "a.txt", 1),
        ("caixa_fora_da_imagem", "a.txt", 5),
    ]
    assert p.warnings_total == 2
    assert p.boxes[0].cx == 0.95 and p.boxes[0].w == 0.2  # sem recorte


def test_tolerance_does_not_warn_and_clamps() -> None:
    p = parse({"a.txt": "0 0.5 0.5 1.000001 0.1\n0 -0.000001 0.5 0.000001 0.1\n"})
    assert p.warnings == () and p.warnings_total == 0
    assert p.boxes[0].w == 1.0


def test_warnings_are_capped_but_total_is_real() -> None:
    lim = TxtLimits(max_errors=3)
    p = parse({"a.txt": "0 0.99 0.5 0.1 0.1\n" * 10}, lim)
    assert len(p.warnings) == 3 and p.warnings_total == 10


# ------------------------------------------------------------------ 14: erros acumulados


def test_five_defects_in_three_files_ordered_by_file_then_line() -> None:
    labels = {
        "c.txt": "0 .5 .5 .1 .1\nx 1 1 1 1\n",
        "a.txt": "0 .5 .5 .1\n\n0 .5 .5 .1 .1\n7 .5 .5 .1 .1\n",
        "b.txt": "0 .5 .5 .1 .1\n0 nan .5 .1 .1\n",
    }
    assert errs(labels) == [
        ("colunas_insuficientes", "a.txt", 1),
        ("classe_fora_do_intervalo", "a.txt", 4),
        ("numero_invalido", "b.txt", 2),
        ("classe_invalida", "c.txt", 2),
    ]
    labels["c.txt"] += "0 .5 .5 0 .1\n"
    e = refused(body(labels))
    assert e.total == 5 and not e.truncated
    assert [(x.index, x.line) for x in e.errors] == [(0, 1), (0, 4), (1, 2), (2, 2), (2, 3)]


def test_max_errors_truncates_but_keeps_real_total() -> None:
    labels = {f"{i:03}.txt": "bad\n" * 5 for i in range(10)}
    e = refused(body(labels), TxtLimits(max_errors=7))
    assert (len(e.errors), e.total, e.truncated) == (7, 50, True)
    assert (e.errors[0].file, e.errors[0].line) == ("000.txt", 1)


def test_error_messages_never_echo_line_content() -> None:
    marker = "ZZ-MARCADOR-ZZ"
    labels = {
        "a.txt": f"{marker} 1 2 3 4\n0 {marker} 1 2 3\n{marker} {marker}\n0 .5 .5 .1 .1 .1 {marker}"
    }
    e = refused(body(labels))
    assert e.total == 4
    for x in e.errors:
        assert marker not in x.message and marker not in (x.file or "")
        assert len(x.message) < 120


# ------------------------------------------------------------------ 15: chaves


@pytest.mark.parametrize(
    ("key", "code"),
    [("a", "rotulo_extensao_invalida"), ("a.TXT", "rotulo_extensao_invalida"),
     ("a.txt ", "rotulo_extensao_invalida"), ("a.txt\n", "rotulo_nome_invalido"),
     ("a.jpg", "rotulo_extensao_invalida"), ("", "rotulo_nome_invalido"),
     (".txt", "rotulo_nome_invalido"), ("dir/.txt", "rotulo_nome_invalido"),
     ("dir\\.txt", "rotulo_nome_invalido"), (" .txt", "rotulo_nome_invalido"),
     ("a" * 1021 + ".txt", "rotulo_nome_invalido"), ("a\x00.txt", "rotulo_nome_invalido"),
     ("a\x07.txt", "rotulo_nome_invalido"), ("a\u2028b.txt", "rotulo_nome_invalido"),
     ("\ud800.txt", "rotulo_nome_invalido")],
)  # fmt: skip
def test_bad_keys(key: str, code: str) -> None:
    e = refused(body({key: "0 .5 .5 .1 .1"}))
    assert [x.code for x in e.errors] == [code]
    shown = e.errors[0].file or ""
    assert len(shown) <= 200 and all(c.isprintable() for c in shown)


def test_key_boundary_1024_is_accepted() -> None:
    key = "a" * 1020 + ".txt"
    assert len(key) == 1024
    p = parse({key: ""})
    assert p.files[0].base_name == "a" * 1020 and len(p.files[0].name) == 200


def test_shown_key_is_truncated_and_clean() -> None:
    e = refused(body({"x\x01" * 300 + ".md": ""}))
    assert e.errors[0].code == "rotulo_nome_invalido"
    assert len(e.errors[0].file or "") <= 200 and "\x01" not in (e.errors[0].file or "")


def test_path_like_keys_reduce_to_base_name_without_touching_disk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import builtins

    def no_open(*a: Any, **k: Any) -> None:
        raise AssertionError("o parser não abre arquivos")

    monkeypatch.setattr(builtins, "open", no_open)
    keys = ["../../etc/passwd.txt", "C:\\x\\b.txt", "train/labels/a.txt", "c d.txt", "a.b.txt",
            "x.jpg.txt"]  # fmt: skip
    p = parse(dict.fromkeys(keys, ""))
    assert {f.name: f.base_name for f in p.files} == {
        "../../etc/passwd.txt": "passwd", "C:\\x\\b.txt": "b", "train/labels/a.txt": "a",
        "c d.txt": "c d", "a.b.txt": "a.b", "x.jpg.txt": "x.jpg"}  # fmt: skip


def test_duplicate_base_names_are_not_rejected_here() -> None:
    p = parse({"train/a.txt": "", "val/a.txt": ""})
    assert [f.base_name for f in p.files] == ["a", "a"]


def test_files_are_in_sorted_key_order() -> None:
    p = parse({"b.txt": "", "a.txt": "", "C.txt": ""})
    assert [f.name for f in p.files] == ["C.txt", "a.txt", "b.txt"]


# ------------------------------------------------------------------ 16: limites


def test_body_bytes_limit_is_checked_before_json(monkeypatch: pytest.MonkeyPatch) -> None:
    data = body({"a.txt": "0 .5 .5 .1 .1"})
    calls: list[int] = []
    real = json.loads
    monkeypatch.setattr(mod.json, "loads", lambda *a, **k: (calls.append(1), real(*a, **k))[1])
    e = refused(data, TxtLimits(max_json_bytes=len(data) - 1))
    assert (e.errors[0].code, e.errors[0].message) == (
        "limite_excedido",
        "limite excedido: max_json_bytes",
    )
    assert calls == []
    assert parse_txt_labels(data, TxtLimits(max_json_bytes=len(data))).boxes_count == 1
    assert calls == [1]


def test_file_count_limit() -> None:
    labels = {f"{i}.txt": "" for i in range(3)}
    assert parse(labels, TxtLimits(max_images=3)).files_count == 3
    e = refused(body(labels), TxtLimits(max_images=2))
    assert e.errors[0].message == "limite excedido: max_images"


def test_box_limit() -> None:
    labels = {"a.txt": "0 .5 .5 .1 .1\n" * 3}
    assert parse(labels, TxtLimits(max_annotations=3)).boxes_count == 3
    e = refused(body(labels), TxtLimits(max_annotations=2))
    assert e.errors[0].message == "limite excedido: max_annotations"


def test_line_limit_counts_newlines_before_any_split(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    real = mod._fields
    monkeypatch.setattr(mod, "_fields", lambda s: (calls.append(s), real(s))[1])
    lim = TxtLimits(max_annotations=3, max_images=2)  # teto de 5 quebras de linha
    ok = {"a.txt": "\n\n\n", "b.txt": "\n\n"}
    assert parse(ok, lim).boxes_count == 0
    calls.clear()
    bad = {"a.txt": "\n\n\n", "b.txt": "\n\n\n"}
    e = refused(body(bad), lim)
    assert e.errors[0].message == "limite excedido: max_linhas"
    bad2 = {"a.txt": "0 .5 .5 .1 .1\n" * 6}
    assert refused(body(bad2), lim).errors[0].message == "limite excedido: max_linhas"
    assert len(calls) == 0


def test_class_name_limit() -> None:
    assert len(parse({}, TxtLimits(max_categories=3)).names) == 3
    assert refused(body({}), TxtLimits(max_categories=2)).errors[0].code == "limite_excedido"


def test_depth_limit_is_4() -> None:
    ok = parse_txt_labels(body({}, extra={"a": {"b": {"c": 1}}}), LIM)  # profundidade 4
    assert ok.boxes_count == 0
    e = refused(body({}, extra={"a": {"b": {"c": {"d": 1}}}}))
    assert e.errors[0].message == "limite excedido: max_depth"
    e = refused(body({}, extra=[[[[[1]]]]]))
    assert e.errors[0].code == "limite_excedido"


# ------------------------------------------------------------------ 17: DoS com tamanho real

MIB16 = 16 * 1024 * 1024


def _raw(labels: dict[str, str], lim: TxtLimits = LIM, names: Any = NAMES) -> Any:
    data = json.dumps({"names": names, "labels": labels}).encode()
    assert len(data) <= lim.max_json_bytes
    return lambda: parse_txt_labels(data, lim)


def _run(fn: Any, budget: float = 2.0) -> tuple[float, Any]:
    elapsed, result = timed(fn, 30.0)
    assert elapsed < budget, elapsed
    return elapsed, result


def test_dos_16mib_of_newlines_is_refused_by_the_count() -> None:
    data = b'{"names": ["a"], "labels": {"a.txt": "' + b"\\n" * (MIB16 // 2 - 40) + b'"}}'
    assert len(data) <= MIB16
    _, r = _run(lambda: parse_txt_labels(data, LIM))
    assert (
        isinstance(r, ImportRejectedError) and r.errors[0].message == "limite excedido: max_linhas"
    )


def test_dos_16mib_single_line() -> None:
    data = b'{"names": ["a"], "labels": {"a.txt": "' + b"0" * (MIB16 - 100) + b'"}}'
    _, r = _run(lambda: parse_txt_labels(data, LIM))
    assert isinstance(r, ImportRejectedError) and r.errors[0].code == "linha_longa" and r.total == 1


def test_dos_16mib_of_lines_is_refused_without_a_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    # 11 bytes por linha já escapada: cerca de 1,52 milhão de linhas cabem em 16 MiB
    text = "0 0 0 0 0\n" * ((MIB16 - 100) // 11)
    data = json.dumps({"names": ["a"], "labels": {"a.txt": text}}).encode()
    assert MIB16 - 100 < len(data) <= MIB16
    calls: list[str] = []
    monkeypatch.setattr(mod, "_fields", lambda s: calls.append(s) or [])
    _, r = _run(lambda: parse_txt_labels(data, LIM))
    assert (
        isinstance(r, ImportRejectedError) and r.errors[0].message == "limite excedido: max_linhas"
    )
    assert calls == []


def test_dos_300k_valid_boxes_time_and_memory() -> None:
    line = "0 0.5 0.5 0.25 0.25\n"
    fn = _raw({"a.txt": line * 150_000, "b.txt": line * 150_000})
    elapsed, r = _run(fn)
    assert isinstance(r, ParsedTxtLabels) and r.boxes_count == 300_000
    tracemalloc.start()
    fn()
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    assert peak < 400 * 1024 * 1024, peak
    print(f"300k caixas: {elapsed:.2f}s, pico {peak / 1e6:.0f} MB")


def test_dos_300k_plus_one_box_is_refused() -> None:
    line = "0 0.5 0.5 0.25 0.25\n"
    _, r = _run(_raw({"a.txt": line * 300_001}))
    assert isinstance(r, ImportRejectedError) and "max_" in r.errors[0].message


def test_dos_50k_one_line_files() -> None:
    labels = {f"{i:05}.txt": "0 0.5 0.5 0.25 0.25" for i in range(50_000)}
    _, r = _run(_raw(labels))
    assert isinstance(r, ParsedTxtLabels) and r.files_count == 50_000 and r.boxes_count == 50_000


def test_dos_16mib_of_spaces() -> None:
    data = b'{"names": ["a"], "labels": {"a.txt": "' + b" " * (MIB16 - 100) + b'"}}'
    _, r = _run(lambda: parse_txt_labels(data, LIM))
    assert isinstance(r, ImportRejectedError) and r.errors[0].code == "linha_longa"


def test_dos_1mib_key() -> None:
    labels = {"k" * (1024 * 1024) + ".txt": "0 .5 .5 .1 .1"}
    _, r = _run(_raw(labels))
    assert isinstance(r, ImportRejectedError) and r.errors[0].code == "rotulo_nome_invalido"
    assert len(r.errors[0].file or "") <= 200


def test_dos_1mib_key_of_control_characters() -> None:
    labels = {"\x01" * (1024 * 1024) + ".md": ""}
    _, r = _run(_raw(labels))
    assert isinstance(r, ImportRejectedError) and (r.errors[0].file or "") == ""


def test_dos_1000_files_with_1000_errors_each_uses_bounded_memory() -> None:
    lim = TxtLimits(max_annotations=2_000_000, max_errors=100)
    labels = {f"{i:04}.txt": "x\n" * 1000 for i in range(1000)}
    fn = _raw(labels, lim)
    elapsed, r = _run(fn, 6.0)
    assert isinstance(r, ImportRejectedError) and r.total == 1_000_000 and len(r.errors) == 100
    tracemalloc.start()
    with contextlib.suppress(ImportRejectedError):
        fn()
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    assert peak < 150 * 1024 * 1024, peak
    print(f"1000x1000 erros: {elapsed:.2f}s, pico {peak / 1e6:.0f} MB")


# ------------------------------------------------------------------ 18: estático

BANNED = {"re", "yaml", "numpy", "PIL", "socket", "urllib", "http", "requests", "subprocess",
          "asyncpg", "boto3", "ultralytics"}  # fmt: skip


def test_parser_imports_only_stdlib(repo_root: Path) -> None:
    path = repo_root / "backend/src/inovalytics/imports/txt_labels_parser.py"
    for node in ast.walk(ast.parse(path.read_text("utf-8"))):
        mods: list[str] = []
        if isinstance(node, ast.Import):
            mods = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            mods = [node.module or ""]
        for m in mods:
            assert m.split(".")[0] not in BANNED, m
            std = {"__future__", "json", "math", "unicodedata", "dataclasses", "typing"}
            assert m.startswith("inovalytics.imports") or m.split(".")[0] in std, m
            assert m not in {"inovalytics.db", "inovalytics.imports.import_core"}


def test_parser_never_uses_splitlines_or_regex(repo_root: Path) -> None:
    text = (repo_root / "backend/src/inovalytics/imports/txt_labels_parser.py").read_text("utf-8")
    tree = ast.parse(text)
    attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert "splitlines" not in attrs and "findall" not in attrs


def test_no_data_files_in_backend(repo_root: Path) -> None:
    for ext in ("*.txt", "*.yaml", "*.yml"):
        for p in (repo_root / "backend").rglob(ext):
            assert any(
                x in p.parts or x.endswith(".egg-info")
                for x in (".venv", "node_modules", "__pycache__")
            ) or p.parent.name.endswith(".egg-info"), p


def test_collect_errors_defers_item_errors_but_not_body_errors() -> None:
    data = body({"b.txt": "x\n", "a.md": "", "c.txt": "0 .5 .5 .1 .1\n"})
    p = parse_txt_labels(data, LIM, collect_errors=True)
    assert p.errors_total == 2 and [(e.code, e.file) for e in p.errors] == [
        ("rotulo_extensao_invalida", "a.md"),
        ("colunas_insuficientes", "b.txt"),
    ]
    assert p.boxes_count == 1
    assert refused(data).total == 2
    with pytest.raises(ImportRejectedError):
        parse_txt_labels(b"{", LIM, collect_errors=True)
