"""F1-07 lacunas (critérios 1 a 34): entradas hostis para data.yaml, linhas, chaves e serviço."""

import json
from typing import Any

import pytest

from inovalytics.db.repository import ImportImage
from inovalytics.imports.errors import ImportRejectedError
from inovalytics.imports.names_yaml import TxtLimits, normalize_names, parse_names_yaml
from inovalytics.imports.txt_labels_parser import ParsedTxtLabels, parse_txt_labels
from inovalytics.imports.txt_labels_service import import_txt_labels
from test_coco_service import FakeRepo, run

LIM = TxtLimits()
NAMES = ["a", "b", "c"]


def yaml_codes(text: str) -> list[tuple[str, int | None]]:
    with pytest.raises(ImportRejectedError) as ei:
        parse_names_yaml(text, LIM)
    return [(e.code, e.line) for e in ei.value.errors]


def parsed(labels: dict[str, str], names: Any = NAMES) -> ParsedTxtLabels:
    return parse_txt_labels(json.dumps({"names": names, "labels": labels}).encode(), LIM)


def line_err(text: str) -> tuple[str, int | None]:
    with pytest.raises(ImportRejectedError) as ei:
        parsed({"a.txt": text})
    e = ei.value.errors[0]
    return e.code, e.line


# ---------------------------------------------------------------- data_yaml hostil


@pytest.mark.parametrize(
    "text",
    [
        "﻿names: [a, b, c]",
        "names:\r\n  - a\r\n  - b\r\n  - c\r\n",
        "names:\n   - a\n   - b\n   - c\n",  # recuo de 3 espaços
        "names:\n  2: c\n  0: a\n  1: b\n",  # mapa de bloco fora de ordem
        "names: {2: c, 0: a, 1: b}",
        "x: &k 1\ny: *k\nnames: [a, b, c]\n",  # âncora/alias fora de names
        "path: /d\nnames: [a, b, c]\nroboflow:\n  s: &z 1\n  t: *z\n",
        "names: [a, b, c,]",  # vírgula final
        "names: [a, b, c] # comentário\n",
        "nc: 3\nnames: [a, b, c]\n",
    ],
)
def test_hostile_but_valid_yaml_forms(text: str) -> None:
    assert parse_names_yaml(text, LIM) == NAMES


@pytest.mark.parametrize(
    ("text", "code", "line"),
    [
        ("names:\n\t- a\n", "yaml_nao_suportado", 2),
        ("names:\n  - a\n    - b\n", "yaml_nao_suportado", 3),
        ("names:\n  - a\n  0: b\n", "yaml_nao_suportado", 3),  # lista e mapa misturados
        ("names: &x [a]\n", "yaml_nao_suportado", 1),
        ("names: !!str [a]\n", "yaml_nao_suportado", 1),
        ("names: {0: a, 2: b}\n", "names_nao_contiguo", 1),
        ("names:\n  0: a\n  0: b\n", "names_id_duplicado", 3),
        ("names: {-1: a}\n", "names_chave_invalida", 1),
        ("names: {+0: a}\n", "names_chave_invalida", 1),
        ("names: {0x0: a}\n", "names_chave_invalida", 1),
        ("names: {0_0: a}\n", "names_chave_invalida", 1),
        ("names: {٠: a}\n", "names_chave_invalida", 1),  # dígito árabe-índico
        ("names: {99999999999999999999: a}\n", "names_chave_invalida", 1),
        ("names: ['a b']\n", "nome_invalido", 1),
        ("names: [a\x85b]\n", "nome_invalido", 1),
        ("names: ['a\x7fb']\n", "nome_invalido", 1),
        ("nc: 2\nnames: [a]\n", "nc_divergente", 1),
        ("nc: -1\nnames: [a]\n", "nc_invalido", 1),
        ("nc: 1.0\nnames: [a]\n", "nc_invalido", 1),
        ("nc: true\nnames: [a]\n", "nc_invalido", 1),
        ("names: [a,,b]\n", "yaml_invalido", 1),
        ("  names: [a]\n", "yaml_nao_suportado", 1),  # chave com recuo
    ],
)
def test_hostile_yaml_refusals(text: str, code: str, line: int) -> None:
    assert yaml_codes(text)[0] == (code, line)


def test_duplicate_nc_keys_are_refused() -> None:
    assert yaml_codes("names: [a]\nnc: 1\nnc: 1\n")[0][0] == "nc_invalido"


def test_lone_cr_line_endings_never_crash() -> None:
    try:
        out = parse_names_yaml("names:\r  - a\r  - b\r", LIM)
    except ImportRejectedError:
        return
    assert out == ["a", "b"]


def test_nested_names_key_does_not_shadow_top_level() -> None:
    text = "names: [a]\nroboflow:\n  names: [z, y]\n"
    assert parse_names_yaml(text, LIM) == ["a"]


def test_names_that_yaml11_would_convert_stay_literal_in_every_form() -> None:
    expected = ["no", "yes", "1:30", "é", "ね", "0o7", "2024-01-01"]
    flow = "names: [" + ", ".join(repr(n) for n in expected) + "]"
    block = "names:\n" + "".join(f"  - '{n}'\n" for n in expected)
    mapa = "names:\n" + "".join(f"  {i}: '{n}'\n" for i, n in enumerate(expected))
    for text in (flow, block, mapa):
        assert parse_names_yaml(text, LIM) == expected
    assert normalize_names(expected, LIM) == expected


def test_error_lines_count_only_lf_with_crlf() -> None:
    assert yaml_codes("path: x\r\nnames:\r\n\t- a\r\n")[0] == ("yaml_nao_suportado", 3)


def test_json_names_sparse_and_odd_keys_refused() -> None:
    for raw in ({"0": "a", "2": "b"}, {"00": "a"}, {"+0": "a"}, {" 0": "a"}, {"٠": "a"}):
        with pytest.raises(ImportRejectedError):
            normalize_names(raw, LIM)


# ---------------------------------------------------------------- linhas de rótulo


def one_box(text: str) -> tuple[int, float, float, float, float]:
    p = parsed({"a.txt": text})
    assert len(p.boxes) == 1
    b = p.boxes[0]
    return b.class_id, b.cx, b.cy, b.w, b.h


@pytest.mark.parametrize(
    "text",
    [
        "0 .5 .5 .1 .1",
        "0 +0.5 +.5 .1 .1",
        "0\t.5   .5\t\t.1 .1",
        "  0 .5 .5 .1 .1   ",
        "0 .5 .5 .1 .1\r\n",
        "\r\n\r\n0 .5 .5 .1 .1\r\n\r\n",
        "00 .5 .5 .1 .1",  # classe com zero à esquerda
        "0 5e-1 5E-1 1E-1 .1",
    ],
)
def test_accepted_line_spellings(text: str) -> None:
    c, cx, cy, w, h = one_box(text)
    assert c == 0 and (cx, cy) == (pytest.approx(0.5), pytest.approx(0.5))
    assert w == pytest.approx(0.1) and h == pytest.approx(0.1)


def test_leading_zero_class_is_the_same_class() -> None:
    assert one_box("01 .5 .5 .1 .1")[0] == 1


def test_crlf_line_numbers_and_blank_lines_with_whitespace_only() -> None:
    text = "0 .5 .5 .1 .1\r\n   \r\n\t\r\n0 .5 .5 .1 .1 .2\r\n"
    assert line_err(text) == ("coluna_extra", 4)


def test_lone_cr_is_not_a_line_break() -> None:
    # `\r` solto não separa linhas: a linha única fica com colunas demais, sem travar nem fundir
    code, ln = line_err("0 .5 .5 .1 .1\r1 .5 .5 .1 .1")
    assert (code, ln) == ("colunas_excedentes", 1)


def test_whitespace_only_file_is_empty_not_error() -> None:
    p = parsed({"a.txt": "   \n\t\t\n \r\n"})
    assert p.boxes == () and p.files[0].box_count == 0


@pytest.mark.parametrize(
    "tok",
    ["nan", "-nan", "inf", "+inf", "Infinity", "0x1", "0X1p0", "1_0", "١", "0,5", "0.5f", "1e",
     "--1", "+-1", "0.5.5", "1e999", "１"],
)  # fmt: skip
def test_exotic_number_tokens_are_numero_invalido(tok: str) -> None:
    for pos in range(1, 5):
        cols = ["0", ".5", ".5", ".1", ".1"]
        cols[pos] = tok
        code, ln = line_err(" ".join(cols))
        assert (code, ln) == ("numero_invalido", 1), (tok, pos)


@pytest.mark.parametrize("tok", ["1e-7", "-0", "-0.0", "1e-400"])
def test_tiny_and_negative_zero_centres_clamp_to_zero_with_warning(tok: str) -> None:
    p = parsed({"a.txt": f"0 {tok} .5 .1 .1"})
    b = p.boxes[0]
    assert 0.0 <= b.cx <= 1e-6 and b.cx == pytest.approx(0.0, abs=1e-6)
    assert str(b.cx) != "-0.0"
    assert [w.code for w in p.warnings] == ["caixa_fora_da_imagem"]


def test_negative_zero_size_is_zero_area() -> None:
    assert line_err("0 .5 .5 -0 .1") == ("caixa_area_zero", 1)
    assert line_err("0 .5 .5 .1 0e0") == ("caixa_area_zero", 1)
    assert line_err("0 .5 .5 1e-400 .1") == ("caixa_area_zero", 1)


@pytest.mark.parametrize("tok", ["-0", "+0", "0.0", "0e0", "1.0", "1e0", "0x0", "1_0", "nan", ""])
def test_class_token_variants_are_classe_invalida_or_other_clean_code(tok: str) -> None:
    if tok == "":
        return
    code, ln = line_err(f"{tok} .5 .5 .1 .1")
    assert code == "classe_invalida" and ln == 1


SEG = "segmentacao_nao_suportada"
COLUMN_CASES = [
    (1, "colunas_insuficientes"),
    (4, "colunas_insuficientes"),
    (6, "coluna_extra"),
    (7, SEG),
    (8, "colunas_excedentes"),
    (9, SEG),
    (11, SEG),
    (13, SEG),
]


@pytest.mark.parametrize(("n", "code"), COLUMN_CASES)
def test_column_counts_with_mixed_separators(n: int, code: str) -> None:
    text = "0" + "".join(("\t" if i % 2 else "  ") + ".5" for i in range(n - 1))
    assert line_err(text)[0] == code


def test_only_the_offending_token_may_appear_in_messages() -> None:
    marker = "SEGREDO-NO-ROTULO"
    with pytest.raises(ImportRejectedError) as ei:
        parsed({"a.txt": f"0 .5 .5 .1 {marker}\n"})
    for e in ei.value.errors:
        assert len(e.message) <= 200
        assert "0 .5 .5 .1" not in e.message


# ---------------------------------------------------------------- chaves de labels


def test_traversal_and_windows_keys_become_base_names_only() -> None:
    p = parsed({"../x.txt": "", "..\\..\\y.txt": "", "/etc/z.txt": ""})
    assert sorted(f.base_name for f in p.files) == sorted(["x", "y", "z"])


@pytest.mark.parametrize(
    "key", ["x.TXT", "x.Txt", "x", "x.txt ", "x.txt/", "dir/", "x.txt.bak", "."]
)
def test_bad_extension_keys(key: str) -> None:
    with pytest.raises(ImportRejectedError) as ei:
        parsed({key: ""})
    assert ei.value.errors[0].code in {"rotulo_extensao_invalida", "rotulo_nome_invalido"}


def test_huge_and_control_keys_have_bounded_clean_display_names() -> None:
    for key in ("a" * 5000 + ".txt", "\x00" * 3000 + ".txt", "a\x00b\n.txt", "‮" * 500):
        with pytest.raises(ImportRejectedError) as ei:
            parsed({key: "0 .5 .5 .1 .1"})
        for e in ei.value.errors:
            shown = e.file or ""
            assert len(shown) <= 200 and all(ch.isprintable() for ch in shown)


def test_key_with_dot_txt_twice_keeps_inner_suffix_in_base_name() -> None:
    assert parsed({"a.txt.txt": ""}).files[0].base_name == "a.txt"


# ---------------------------------------------------------------- serviço


def imgs(*names: str) -> list[ImportImage]:
    return [ImportImage(10 + i, n, 200, 100) for i, n in enumerate(names)]


def go(repo: FakeRepo, labels: dict[str, str], **kw: Any) -> Any:
    p = parse_txt_labels(
        json.dumps({"names": NAMES, "labels": labels}).encode(), LIM, collect_errors=True
    )
    return run(import_txt_labels(repo, 1, p, **kw))


def codes(repo: FakeRepo, labels: dict[str, str], **kw: Any) -> list[tuple[str, str | None]]:
    with pytest.raises(ImportRejectedError) as ei:
        go(repo, labels, **kw)
    return [(e.code, e.file) for e in ei.value.errors]


LINE = "0 .5 .5 .2 .2\n"


def test_nfd_and_nfc_keys_collide_after_sanitization() -> None:
    repo = FakeRepo(images=imgs("é.jpg"))
    assert codes(repo, {"é.txt": LINE, "é.txt": LINE}) == [("rotulo_nome_duplicado", "é.txt")]
    assert repo.annotations == []


def test_nfd_key_matches_nfc_image() -> None:
    repo = FakeRepo(images=imgs("é.jpg"))
    assert go(repo, {"é.txt": LINE}).annotations_imported == 1


def test_image_without_label_and_label_without_image() -> None:
    repo = FakeRepo(images=imgs("a.jpg", "b.jpg"))
    r = go(repo, {"a.txt": LINE})
    assert r.annotations_imported == 1 and len(repo.annotations) == 1
    assert {a.image_id for a in repo.annotations} == {10}
    assert codes(FakeRepo(images=imgs("a.jpg")), {"a.txt": LINE, "zz.txt": LINE}) == [
        ("imagem_nao_encontrada", "zz.txt")
    ]


def test_empty_label_file_and_empty_labels_object_write_nothing() -> None:
    repo = FakeRepo(images=imgs("a.jpg"))
    r = go(repo, {"a.txt": ""})
    assert r.images_without_boxes == 1 and repo.annotations == []  # só classes são criadas
    assert go(FakeRepo(), {}).annotations_imported == 0


def test_idempotent_with_crlf_and_spelling_variants() -> None:
    repo = FakeRepo(images=imgs("a.jpg"))
    first = go(repo, {"a.txt": "0 .5 .5 .2 .2\r\n1 0.25 0.25 0.1 0.1\r\n"})
    assert first.status == "importado" and first.annotations_imported == 2
    again = go(repo, {"a.txt": "0 5e-1 +0.500 0.2 .20\n1 .25 .25 .1 .1\n"})
    assert again.status == "sem_alteracoes" and again.annotations_imported == 0
    assert again.annotations_already_existing == 2 and len(repo.annotations) == 2


def test_rollback_restores_state_when_the_last_row_fails() -> None:
    repo = FakeRepo(images=imgs("a.jpg"))
    before = repo.state()
    repo.fail_on_row = 3
    text = "".join(f"0 .5 .5 .{i} .{i}\n" for i in range(1, 4))
    with pytest.raises(Exception):  # noqa: B017  # erro de domínio ou de integridade
        go(repo, {"a.txt": text})
    assert repo.state() == before


def test_report_truncation_keeps_real_total_with_many_bad_files() -> None:
    small = TxtLimits(max_errors=5)
    labels = {f"f{i:03d}.txt": "0 .5 .5 .1 .1\n" for i in range(50)}
    p = parse_txt_labels(
        json.dumps({"names": NAMES, "labels": labels}).encode(), small, collect_errors=True
    )
    with pytest.raises(ImportRejectedError) as ei:
        run(import_txt_labels(FakeRepo(images=imgs("zzz.jpg")), 1, p, limits=small))
    e = ei.value
    assert len(e.errors) == 5 and e.total == 50 and e.truncated is True


def test_service_never_leaks_label_text_in_errors() -> None:
    marker = "TEXTO-SECRETO-QQ"
    repo = FakeRepo(images=imgs("a.jpg"))
    with pytest.raises(ImportRejectedError) as ei:
        go(repo, {f"{marker}.txt": LINE, "a.txt": f"0 .5 .5 .1 {marker}\n"})
    assert all(len(e.message) <= 200 for e in ei.value.errors)
    assert not any(marker in e.message for e in ei.value.errors)


def test_negative_zero_is_never_stored() -> None:
    repo = FakeRepo(images=imgs("a.jpg"))
    go(repo, {"a.txt": "0 -0 -0.0 .1 .1\n"})
    a = repo.annotations[0]
    assert str(a.x_center) == "0.0" and str(a.y_center) == "0.0"


# ---------------------------------------------------------------- HTTP


def test_api_hostile_data_yaml_is_422_with_line_and_never_echoed(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from test_api_coco_import import Env

    marker = "NOME-SECRETO-YY"
    env = Env(monkeypatch)
    bad = f"names:\r\n\t- {marker}\r\n"
    ok = f"﻿names:\r\n  - {marker}\r\n"
    good = json.dumps({"data_yaml": ok, "labels": {"a.txt": "0 .5 .5 .1 .1\n"}}).encode()
    hdr = {"Content-Type": "application/json"}
    with caplog.at_level("DEBUG"), env.client() as c:
        r = c.post(
            "/datasets/1/imports/yolo-txt",
            content=json.dumps({"data_yaml": bad, "labels": {}}).encode(),
            headers=hdr,
        )
        assert r.status_code == 422 and r.json()["code"] == "importacao_invalida"
        e = r.json()["erros"][0]
        assert e["kind"] == "data_yaml" and e["linha"] == 2
        assert marker not in r.text and marker not in caplog.text
        assert env.repo.writes() == []
        r2 = c.post("/datasets/1/imports/yolo-txt", content=good, headers=hdr)
        assert r2.status_code == 201
