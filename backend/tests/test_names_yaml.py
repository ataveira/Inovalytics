"""F1-07a critérios 2 a 7: nomes de classe (`data_yaml` mínimo e JSON), DoS e guardas estáticas."""

import ast
import random
from pathlib import Path
from typing import Any

import pytest

from _timing import timed
from inovalytics.imports.errors import ImportRejectedError
from inovalytics.imports.names_yaml import TxtLimits, normalize_names, parse_names_yaml

LIM = TxtLimits()
EXPECTED = ["pessoa", "capacete", "colete"]


def fail(text: str, lim: TxtLimits = LIM) -> tuple[str, int | None, str]:
    with pytest.raises(ImportRejectedError) as ei:
        parse_names_yaml(text, lim)
    e = ei.value.errors[0]
    assert e.kind == "data_yaml"
    return e.code, e.line, e.message


def fail_json(raw: Any, lim: TxtLimits = LIM) -> str:
    with pytest.raises(ImportRejectedError) as ei:
        normalize_names(raw, lim)
    assert ei.value.errors[0].kind == "names"
    return ei.value.errors[0].code


# ------------------------------------------------------------------ 3: formas aceitas

OK_FORMS = {
    "flow_list": "names: [pessoa, capacete, colete]\n",
    "flow_map": "names: {2: colete, 0: pessoa, 1: capacete}\n",
    "block_list": "names:\n  - pessoa\n  - capacete\n  - colete\n",
    "block_list_indent0": "names:\n- pessoa\n- capacete\n- colete\nnc: 3\n",
    "block_map": "names:\n  0: pessoa\n  1: capacete\n  2: colete\n",
    "block_map_unordered": "names:\n  2: colete\n  0: pessoa\n  1: capacete\n",
    "with_nc": "nc: 3\nnames: [pessoa, capacete, colete]\n",
    "ignored_keys": (
        "path: ../datasets/x\ntrain: images/train\nval: images/val\n"
        "base: &b\n  a: 1\nroboflow:\n  workspace: w\n  ref: *b\n  tags: [x, y\n"
        "names: [pessoa, capacete, colete]\ntest: *b\n<<: *b\n"
    ),
    "comments": (
        "# cabeçalho\nnames: # classes\n  # comentário\n  - pessoa # a\n\n  - capacete\n"
        "  - colete\n# fim\n"
    ),
    "doc_start": "---\nnames: [pessoa, capacete, colete]\n",
    "doc_start_comment": "--- # início\nnames: [pessoa, capacete, colete]\n",
    "crlf": "names:\r\n  - pessoa\r\n  - capacete\r\n  - colete\r\n",
    "bom": "﻿names: [pessoa, capacete, colete]",
    "quoted": "names: ['pessoa', \"capacete\", colete ]\n",
    "spaced_flow": "names: [ pessoa ,capacete,   colete , ]\n",
    "trailing_comment": "names: [pessoa, capacete, colete] # fim\n",
    "quoted_keys": "names: {'0': pessoa, \"1\": capacete, 2: colete}\n",
}


@pytest.mark.parametrize("text", OK_FORMS.values(), ids=OK_FORMS.keys())
def test_accepted_forms_give_the_same_names(text: str) -> None:
    assert parse_names_yaml(text, LIM) == EXPECTED


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("names: ['it''s', \"a\\\"b\", 'c\\d']\n", ["it's", 'a"b', "c\\d"]),
        ('names: ["a\\\\b"]\n', ["a\\b"]),
        (
            "names: ['dois palavras', 'ação', 'a: b', 'x # y', '[z]']\n",
            ["dois palavras", "ação", "a: b", "x # y", "[z]"],
        ),
        (
            "names:\n  - dois palavras\n  - ação\n  - a:b\n  - std::vector\n",
            ["dois palavras", "ação", "a:b", "std::vector"],
        ),
        (
            "names: [no, on, 1, 1:30, '~', 2024-01-01, true, 0o7, off]\n",
            ["no", "on", "1", "1:30", "~", "2024-01-01", "true", "0o7", "off"],
        ),
        ("names:\n  0: no\n  1: '~'\n  2: 1\n", ["no", "~", "1"]),
        ("names: ['  a  ', b]\n", ["a", "b"]),
        ("names: [A, a]\n", ["A", "a"]),
    ],
)
def test_scalars_are_literal_text(text: str, expected: list[str]) -> None:
    assert parse_names_yaml(text, LIM) == expected


# ------------------------------------------------------------------ 4: recusas

REFUSED = [
    ("names:\n\t- a\n", "yaml_nao_suportado", 2),
    ("names:\n  - a\n\t- b\n", "yaml_nao_suportado", 3),
    ("names: [a]\n---\nnc: 1\n", "yaml_nao_suportado", 2),
    ("---\n---\nnames: [a]\n", "yaml_nao_suportado", 2),
    ("names: [a]\nnames: [b]\n", "names_duplicado", 2),
    ("path: x\n", "names_ausente", None),
    ("names: []\n", "names_ausente", None),
    ("names: &n [a]\n", "yaml_nao_suportado", 1),
    ("names: *n\n", "yaml_nao_suportado", 1),
    ("names: !!seq [a]\n", "yaml_nao_suportado", 1),
    ("names: |\n  a\n", "yaml_nao_suportado", 1),
    ("names: >\n  a\n", "yaml_nao_suportado", 1),
    ("names: [&x a]\n", "yaml_nao_suportado", 1),
    ("names: [*x]\n", "yaml_nao_suportado", 1),
    ("names:\n  - !t a\n", "yaml_nao_suportado", 2),
    ("names:\n  - <<\n", "yaml_nao_suportado", 2),
    ("names:\n  - |\n", "yaml_nao_suportado", 2),
    ("names: [[a]]\n", "yaml_nao_suportado", 1),
    ("names: [a, [b]]\n", "yaml_nao_suportado", 1),
    ("names: [{a: b}]\n", "yaml_nao_suportado", 1),
    ("names:\n  - - a\n", "yaml_nao_suportado", 2),
    ("names:\n  - [a]\n", "yaml_nao_suportado", 2),
    ("names:\n  - a: b\n", "yaml_nao_suportado", 2),
    ("names:\n  0: [a]\n", "yaml_nao_suportado", 2),
    ("names:\n  0:\n    x: y\n", "yaml_invalido", 2),
    ("names: [a,\n  b]\n", "yaml_nao_suportado", 1),
    ("names: {0: a,\n 1: b}\n", "yaml_nao_suportado", 1),
    ("names: [a\n", "yaml_nao_suportado", 1),
    ("names: ['a]\n", "yaml_invalido", 1),
    ('names: ["a]\n', "yaml_invalido", 1),
    ('names: ["a\\n"]\n', "yaml_invalido", 1),
    ('names: ["a\\x41"]\n', "yaml_invalido", 1),
    ("names:\n  - 'a\n", "yaml_invalido", 2),
    ("names: [a, , b]\n", "yaml_invalido", 1),
    ("names: [, a]\n", "yaml_invalido", 1),
    ("names:\n  - \n", "yaml_invalido", 2),
    ("names:\n  -\n", "yaml_invalido", 2),
    ("names:\n  - null\n", "yaml_invalido", 2),
    ("names: [~]\n", "yaml_invalido", 1),
    ("names: [Null]\n", "yaml_invalido", 1),
    ("names: ['a' b]\n", "yaml_invalido", 1),
    ("names:\n  0: a\n  1:\n", "yaml_invalido", 3),
    ("names:\nnc: 1\n", "yaml_invalido", 1),
    ("names:\n", "yaml_invalido", 1),
    ("names: texto\n", "yaml_nao_suportado", 1),
    ("names:\n  pessoa: 0\n", "names_chave_invalida", 2),
    ("names: {pessoa: 0}\n", "names_chave_invalida", 1),
    ("names:\n  01: a\n", "names_chave_invalida", 2),
    ("names:\n  -1: a\n", "names_chave_invalida", 2),
    ("names:\n  1.0: a\n", "names_chave_invalida", 2),
    ("names:\n  0: a\n  0: b\n", "names_id_duplicado", 3),
    ("names: {0: a, 0: b}\n", "names_id_duplicado", 1),
    ("names:\n  0: a\n  2: c\n", "names_nao_contiguo", 3),
    ("names: {1: a, 2: b}\n", "names_nao_contiguo", 1),
    ("names:\n  1: a\n", "names_nao_contiguo", 2),
    ("names:\n  - a\n  1: b\n", "yaml_nao_suportado", 3),
    ("names:\n  0: a\n  - b\n", "yaml_nao_suportado", 3),
    ("names: [a, b, a]\n", "nome_duplicado", 1),
    ("names:\n  - a\n  - ' a '\n", "nome_duplicado", 3),
    ("names: ['  ']\n", "nome_invalido", 1),
    ("names: ['']\n", "nome_invalido", 1),
    ("names: ['a\x01b']\n", "nome_invalido", 1),
    ("names: ['a b']\n", "nome_invalido", 1),
    ("names: ['a\x00b']\n", "yaml_invalido", 1),
    (f"names: ['{'x' * 1025}']\n", "nome_invalido", 1),
    ("names: [a]\nnc: tres\n", "nc_invalido", 2),
    ("names: [a]\nnc: 1.0\n", "nc_invalido", 2),
    ("names: [a]\nnc: '01'\n", "nc_invalido", 2),
    ("names: [a]\nnc: 2\n", "nc_divergente", 2),
    ("nc: 3\nnames: [a]\n", "nc_divergente", 1),
    ("nc:\nnames: [a]\n", "yaml_invalido", 1),
    ("nc: 1\nnc: 1\nnames: [a]\n", "nc_invalido", 2),
    ("  names: [a]\n", "yaml_nao_suportado", 1),
    ("- names\n", "yaml_nao_suportado", 1),
    ("names [a]\n", "yaml_nao_suportado", 1),
    ("names: [a] x\n", "yaml_invalido", 1),
    ("names: [a]: b\n", "yaml_nao_suportado", 1),
]


@pytest.mark.parametrize(("text", "code", "line"), REFUSED)
def test_refusals_have_stable_code_and_line(text: str, code: str, line: int | None) -> None:
    got_code, got_line, message = fail(text)
    assert (got_code, got_line) == (code, line)
    assert "x" * 100 not in message and len(message) < 200


def test_more_names_than_max_categories_fails_before_building_the_list() -> None:
    lim = TxtLimits(max_categories=3)
    assert parse_names_yaml("names: [a, b, c]", lim) == ["a", "b", "c"]
    assert fail("names: [a, b, c, d]", lim)[0] == "limite_excedido"
    assert fail("names:\n- a\n- b\n- c\n- d\n", lim) == (
        "limite_excedido",
        5,
        "limite excedido: max_categories",
    )
    assert fail("names: {0: a, 1: b, 2: c, 3: d}", lim)[0] == "limite_excedido"


def test_error_never_echoes_user_content() -> None:
    needle = "SEGREDO-123"
    for text in (
        f"names: ['{needle}\n",
        f"names: [{needle}, {needle}]\n",
        f"{needle}\n",
        f"names: {needle}\n",
        f"names: [{needle}: x]\n",
    ):
        with pytest.raises(ImportRejectedError) as ei:
            parse_names_yaml(text, LIM)
        assert needle not in str(ei.value)
        assert all(needle not in e.message for e in ei.value.errors)


# ------------------------------------------------------------------ 5: JSON


def test_json_names_accepted() -> None:
    assert normalize_names(["pessoa", "capacete", "colete"], LIM) == EXPECTED
    assert normalize_names({"2": "colete", "0": "pessoa", "1": "capacete"}, LIM) == EXPECTED
    assert normalize_names([" a ", "A"], LIM) == ["a", "A"]


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        ([True], "names_tipo_invalido"),
        ([1], "names_tipo_invalido"),
        ([None], "names_tipo_invalido"),
        ([["a"]], "names_tipo_invalido"),
        ([{"a": "b"}], "names_tipo_invalido"),
        ({"0": 1}, "names_tipo_invalido"),
        ({"0": None}, "names_tipo_invalido"),
        (None, "names_tipo_invalido"),
        ("a", "names_tipo_invalido"),
        (3, "names_tipo_invalido"),
        (True, "names_tipo_invalido"),
        ([], "names_ausente"),
        ({}, "names_ausente"),
        ({"01": "a"}, "names_chave_invalida"),
        ({"-1": "a"}, "names_chave_invalida"),
        ({"1.0": "a"}, "names_chave_invalida"),
        ({"٠": "a"}, "names_chave_invalida"),
        ({"": "a"}, "names_chave_invalida"),
        ({"0": "a", "2": "c"}, "names_nao_contiguo"),
        ({"1": "a"}, "names_nao_contiguo"),
        (["a", "a"], "nome_duplicado"),
        (["a", " a"], "nome_duplicado"),
        ([""], "nome_invalido"),
        (["   "], "nome_invalido"),
        (["a\x00"], "nome_invalido"),
        (["a\nb"], "nome_invalido"),
        (["\ud800"], "nome_invalido"),
        (["x" * 1025], "nome_invalido"),
    ],
)
def test_json_names_refused(raw: Any, code: str) -> None:
    assert fail_json(raw) == code


def test_json_names_limit_and_exact_boundary() -> None:
    lim = TxtLimits(max_categories=2)
    assert normalize_names(["a", "b"], lim) == ["a", "b"]
    assert fail_json(["a", "b", "c"], lim) == "limite_excedido"
    assert fail_json({"0": "a", "1": "b", "2": "c"}, lim) == "limite_excedido"
    assert normalize_names(["x" * 1024], LIM) == ["x" * 1024]


# ------------------------------------------------------------------ 2: robustez e determinismo


def test_random_inputs_only_raise_import_rejected() -> None:
    rng = random.Random(7)
    alphabet = [
        "names",
        ":",
        " ",
        "\n",
        "\r",
        "\t",
        "-",
        "[",
        "]",
        "{",
        "}",
        ",",
        "'",
        '"',
        "\\",
        "#",
        "&",
        "*",
        "!",
        "|",
        ">",
        "~",
        "0",
        "1",
        "a",
        "é",
        "\x00",
        " ",
        "\ud800",
        "---",
        "nc",
        "<<",
        "''",
        "  ",
    ]
    for _ in range(3000):
        text = "".join(rng.choice(alphabet) for _ in range(rng.randrange(0, 40)))
        try:
            first = parse_names_yaml(text, LIM)
        except ImportRejectedError as exc:
            with pytest.raises(ImportRejectedError) as ei:
                parse_names_yaml(text, LIM)
            assert ei.value.errors == exc.errors
        else:
            assert first == parse_names_yaml(text, LIM)
            assert first and all(isinstance(n, str) and n for n in first)
    garbage = "".join(chr(rng.randrange(0, 0x3000)) for _ in range(60_000))
    with pytest.raises(ImportRejectedError):
        parse_names_yaml(garbage, LIM)
    for _ in range(300):
        raw = rng.choice([None, 1, "x", [], {}, [None], {"0": 1}, ["a\x00"], {"x": "y"}, [[]]])
        with pytest.raises(ImportRejectedError):
            normalize_names(raw, LIM)


def test_non_string_input_is_a_clean_error() -> None:
    with pytest.raises(ImportRejectedError):
        parse_names_yaml(b"names: [a]", LIM)  # type: ignore[arg-type]


# ------------------------------------------------------------------ 6: DoS

K64 = 64 * 1024
HOSTILE = {
    "brackets": "names: " + "[" * (K64 - 7),
    "dashes": "names:\n  " + "- " * ((K64 - 9) // 2),
    "dash_lines": "names:\n" + "- \n" * ((K64 - 7) // 3),
    "quotes_single": "names: [" + "'" * (K64 - 8),
    "quotes_double": "names: [" + '"' * (K64 - 8),
    "quote_pairs": "names: [" + "'a'," * ((K64 - 9) // 4),
    "backslashes": 'names: ["' + "\\" * (K64 - 10),
    "backslash_escapes": 'names: ["' + "\\\\" * ((K64 - 12) // 2) + '"]',
    "one_line": "names: [" + "a" * (K64 - 9) + "]",
    "one_line_ignored": "x: " + "a" * (K64 - 3),
    "newlines": "\n" * K64,
    "spaces": " " * K64,
    "colons": "names: [" + ":" * (K64 - 8),
    "hashes": "names: " + "# " * ((K64 - 7) // 2),
    "key_line": "k" * (K64 - 1) + ":",
}
BUDGET = 0.5


@pytest.mark.parametrize("text", HOSTILE.values(), ids=HOSTILE.keys())
def test_hostile_64k_inputs_are_fast(text: str) -> None:
    assert len(text) <= K64
    elapsed, result = timed(lambda: parse_names_yaml(text, LIM), 5.0)
    assert elapsed < BUDGET, elapsed
    assert isinstance(result, ImportRejectedError | list)


def test_over_the_byte_limit_fails_without_scanning(monkeypatch: pytest.MonkeyPatch) -> None:
    class Spy(str):
        __slots__ = ()
        calls: list[str] = []

        def split(self, *a: Any, **k: Any) -> list[str]:
            Spy.calls.append("split")
            return super().split(*a, **k)

        def find(self, *a: Any, **k: Any) -> int:
            Spy.calls.append("find")
            return super().find(*a, **k)

        def count(self, *a: Any, **k: Any) -> int:
            Spy.calls.append("count")
            return super().count(*a, **k)

    text = Spy("a" * (K64 + 1))
    assert fail(text)[0] == "limite_excedido"
    assert Spy.calls == []
    assert fail(Spy("é" * (K64 // 2 + 1)))[0] == "limite_excedido"  # em bytes, não caracteres
    assert Spy.calls == []
    ok = "names: [a]\n" + "# " + "x" * (K64 - 14)
    assert parse_names_yaml(ok, LIM) == ["a"]


def test_line_cap_is_checked_before_the_loop() -> None:
    lim = TxtLimits(max_categories=10)
    ok = "names: [a]\n" + "\n" * (10 + 200 - 2)
    assert parse_names_yaml(ok, lim) == ["a"]
    assert fail(ok + "\n", lim)[0] == "limite_excedido"


def test_alias_chain_outside_names_is_never_expanded() -> None:
    chain = ["a0: &a0 [x, x, x, x, x, x, x, x, x]"]
    chain += [f"a{i}: &a{i} [*a{i - 1}, *a{i - 1}, *a{i - 1}]" for i in range(1, 1001)]
    text = "\n".join(chain) + "\nnames: [a, b]\n"
    elapsed, result = timed(lambda: parse_names_yaml(text, TxtLimits(max_yaml_bytes=10**6)), 5.0)
    assert result == ["a", "b"]
    assert elapsed < BUDGET
    nested = "roboflow:\n" + "".join(f"  k{i}: *k{i - 1}\n" for i in range(1000))
    assert parse_names_yaml(nested + "names: [a]\n", TxtLimits(max_yaml_bytes=10**6)) == ["a"]


# ------------------------------------------------------------------ 7: estático

SRC = "backend/src/inovalytics/imports"
BANNED = {
    "re", "yaml", "ruamel", "socket", "urllib", "http", "requests", "subprocess", "os", "pathlib",
    "asyncpg", "boto3", "ultralytics",
}  # fmt: skip


def _imports(path: Path) -> set[str]:
    mods: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text("utf-8"))):
        if isinstance(node, ast.Import):
            mods |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            mods.add(node.module or "")
    return mods


@pytest.mark.parametrize("name", ["names_yaml.py", "common.py"])
def test_pure_modules_do_not_import_banned_packages(repo_root: Path, name: str) -> None:
    mods = _imports(repo_root / SRC / name)
    assert {m.split(".")[0] for m in mods}.isdisjoint(BANNED), mods
    assert all(
        m.startswith("inovalytics.imports")
        or m.split(".")[0]
        in {"__future__", "math", "unicodedata", "dataclasses", "typing", "collections"}
        for m in mods
    ), mods


def test_names_yaml_has_no_recursion_or_regex(repo_root: Path) -> None:
    tree = ast.parse((repo_root / SRC / "names_yaml.py").read_text("utf-8"))
    for fn in (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)):
        for call in (c for c in ast.walk(fn) if isinstance(c, ast.Call)):
            assert not (isinstance(call.func, ast.Name) and call.func.id == fn.name), fn.name


def test_no_new_dependencies_or_license_docs_changes(repo_root: Path) -> None:
    text = (repo_root / "pyproject.toml").read_text("utf-8").lower()
    assert "pyyaml" in text  # só como dev (já registrado); o módulo novo não o usa
    runtime = text.split("[project.optional-dependencies]")[0]
    assert "yaml" not in runtime and "ruamel" not in runtime
