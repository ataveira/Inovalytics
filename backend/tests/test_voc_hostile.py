"""F1-08 (testador): XML hostil, números, nomes, casamento, fronteira de orçamento e vazamentos.

Complementa test_voc_parser/test_voc_service/test_api_voc_import sem tocar neles.
"""

from __future__ import annotations

import json
import logging
import unicodedata
from typing import Any

import pytest

from inovalytics.db.repository import ImportImage
from inovalytics.imports.errors import ImportRejectedError
from inovalytics.imports.voc_parser import VocLimits, parse_voc
from inovalytics.imports.voc_service import import_voc
from test_api_coco_import import SECRETS, Env
from test_api_voc_import import post
from test_coco_service import FakeRepo, run
from test_voc_parser import ann, bnd, obj, size

pytestmark = pytest.mark.usefixtures("no_external_network")

LIM = VocLimits()
A = ann(obj())


def parsed(files: Any, lim: VocLimits = LIM) -> Any:
    return parse_voc(json.dumps({"annotations": files}).encode(), lim, collect_errors=True)


def codes(files: Any, lim: VocLimits = LIM) -> list[str]:
    return [e.code for e in parsed(files, lim).errors]


def one(xml: str) -> list[str]:
    return codes({"a.xml": xml})


def boxes_of(xml: str) -> int:
    return int(parsed({"a.xml": xml}).boxes_count)


def import_(repo: FakeRepo, files: Any, **kw: Any) -> Any:
    return run(import_voc(repo, 1, parsed(files), **kw))


def rejected(repo: FakeRepo, files: Any) -> ImportRejectedError:
    with pytest.raises(ImportRejectedError) as ei:
        run(import_voc(repo, 1, parsed(files), dry_run=False))
    return ei.value


# ------------------------------------------------------------------ declaração e codificação


@pytest.mark.parametrize(
    "prefix",
    [
        "\ufeff",
        '<?xml version="1.0"?>',
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<?xml version="1.0" encoding="utf-8" standalone="yes"?>',
        '<?xml version="1.0" standalone="no"?>\n',
        "<?xml version='1.1'?>",
        '\ufeff<?xml version="1.0" encoding="US-ASCII"?>',
    ],
)
def test_accepted_declarations(prefix: str) -> None:
    assert boxes_of(prefix + A) == 1


@pytest.mark.parametrize(
    ("prefix", "code"),
    [
        ('<?xml version="1.0" encoding="ISO-8859-1"?>', "encoding_nao_suportado"),
        ('<?xml version="1.0" encoding="UTF-16"?>', "encoding_nao_suportado"),
        ('<?xml version="1.0" encoding="utf-16le"?>', "encoding_nao_suportado"),
        ('<?xml version="1.0" encoding="windows-1252"?>', "encoding_nao_suportado"),
        ('<?xml version="1.0" encoding=""?>', "xml_malformado"),
        ('<?xml version="2.0"?>', "xml_malformado"),
        ('<?xml encoding="utf-8"?>', "xml_malformado"),
        ('<?xml version="1.0" standalone="maybe"?>', "xml_malformado"),
        ("<?xml", "xml_malformado"),
        ("<?xml?>", "xml_malformado"),
        ('<?XML version="1.0"?>', "xml_malformado"),
        (' <?xml version="1.0"?>', "xml_malformado"),
        ('\ufeff\ufeff<?xml version="1.0"?>', "xml_malformado"),
        ('<?xml version="1.0"?><?xml version="1.0"?>', "xml_malformado"),
    ],
)
def test_rejected_declarations(prefix: str, code: str) -> None:
    assert one(prefix + A) == [code]


def test_malformed_declaration_is_per_file_and_does_not_hide_other_files() -> None:
    p = parsed(
        {"a.xml": "<?xml" + A, "b.xml": A, "c.xml": '<?xml version="1.0" encoding="x"?>' + A}
    )
    assert [(e.code, e.file) for e in p.errors] == [
        ("xml_malformado", "a.xml"),
        ("encoding_nao_suportado", "c.xml"),
    ]
    assert p.boxes_count == 1


def test_utf16_with_bom_body_is_json_invalido() -> None:
    raw = json.dumps({"annotations": {"a.xml": A}}).encode("utf-16")
    assert raw[:2] in (b"\xff\xfe", b"\xfe\xff")
    with pytest.raises(ImportRejectedError) as ei:
        parse_voc(raw, LIM)
    assert ei.value.errors[0].code == "json_invalido"


def test_utf8_bom_body_and_latin1_body() -> None:
    raw = json.dumps({"annotations": {"a.xml": A}}).encode()
    assert parse_voc(b"\xef\xbb\xbf" + raw, LIM).boxes_count == 1
    latin = json.dumps({"annotations": {"a.xml": ann(obj("coração"))}}, ensure_ascii=False)
    with pytest.raises(ImportRejectedError) as ei:
        parse_voc(latin.encode("iso-8859-1"), LIM)
    assert ei.value.errors[0].code == "json_invalido"


def test_empty_and_blank_xml_values() -> None:
    assert codes({"a.xml": "", "b.xml": " \n\t"}) == ["xml_malformado", "xml_malformado"]


# ------------------------------------------------------------------ entidades


@pytest.mark.parametrize(
    "ref",
    ["&#0;", "&#x0;", "&#1;", "&#x110000;", "&#xD800;", "&#xDFFF;", "&#xFFFE;", "&#99999999999;"],
)
def test_invalid_char_refs_are_malformed_xml(ref: str) -> None:
    assert one(ann(obj(f"a{ref}b"))) == ["xml_malformado"]
    assert one(A.replace("<annotation>", f"<annotation>{ref}", 1)) == ["xml_malformado"]


@pytest.mark.parametrize("ref", ["&nbsp;", "&copy;", "&foo;", "&amp", "&;", "&#;", "&#x;", "&#xZ;"])
def test_undefined_or_broken_references_are_rejected(ref: str) -> None:
    got = one(ann(obj(f"a{ref}b")))
    assert len(got) == 1 and got[0] in {"entidade_proibida", "xml_malformado"}


def test_predefined_and_numeric_entities_decode_once() -> None:
    p = parsed(
        {"a.xml": ann(obj("a&amp;b"), obj("c&amp;amp;d"), obj("&#65;&#x42;&lt;&gt;&quot;&apos;"))}
    )
    assert p.names == ("a&b", "c&amp;d", "AB<>\"'")  # sem dupla decodificação


def test_bidi_and_control_via_char_ref_in_name_are_invalid_class() -> None:
    for ref in ("&#x202E;", "&#x2066;", "&#x200B;", "&#xFEFF;", "&#x7F;", "&#x85;", "&#x2028;"):
        assert one(ann(obj(f"a{ref}b"))) == ["classe_invalida"], ref


def test_raw_nul_and_control_characters_are_malformed() -> None:
    for ch in ("\x00", "\x01", "\x08", "\x0b", "\x1f"):
        assert one(ann(obj(f"a{ch}b"))) == ["xml_malformado"], repr(ch)
        assert one(ann(obj(), extra=f"<filename>a{ch}</filename>")) == ["xml_malformado"]


def test_filename_with_bidi_controls_is_only_a_comparison_text() -> None:
    xml = ann(obj(), filename="b\u202e.jpg\u200b")
    p = parsed({"a.xml": xml})
    assert p.errors == () and p.files[0].declared_filename == "b\u202e.jpg\u200b"
    e = rejected(FakeRepo(), {"a.xml": xml})
    assert [x.code for x in e.errors] == ["nome_divergente"]
    assert "\u202e" not in e.errors[0].message and "jpg" not in e.errors[0].message


# ------------------------------------------------------------------ namespaces e afins


def test_namespaces_and_prefixes() -> None:
    assert boxes_of(A.replace("<annotation>", '<annotation xmlns="http://x/y">', 1)) == 1
    assert one("<voc:annotation xmlns:voc='u'/>") == ["raiz_invalida"]
    assert one("<x:annotation/>") == ["raiz_invalida"]
    # filho com prefixo não é <name>/<bndbox>: o objeto fica incompleto, nunca uma caixa
    pref = "<object><v:name xmlns:v='u'>pessoa</v:name><v:bndbox/></object>"
    got = one(f"<annotation>{pref}</annotation>")
    assert got == ["nome_ausente", "bndbox_ausente"]


def test_xinclude_xsi_and_extra_attributes_are_inert() -> None:
    xml = (
        "<annotation xmlns:xi='http://www.w3.org/2001/XInclude' "
        "xmlns:xsi='http://www.w3.org/2001/XMLSchema-instance' "
        "xsi:schemaLocation='http://a file:///etc/passwd' xsi:noNamespaceSchemaLocation='x.xsd'>"
        "<xi:include href='file:///etc/passwd' parse='text'/>"
        "<xi:include href='http://127.0.0.1:9/x'><xi:fallback/></xi:include>"
        f"{obj()}</annotation>"
    )
    p = parsed({"a.xml": xml})
    assert p.errors == () and p.boxes_count == 1 and p.names == ("pessoa",)


@pytest.mark.parametrize(
    "xml",
    [
        "<annotation a='1' a='2'/>",
        '<annotation a="1" a="1"/>',
        "<annotation xmlns:p='u' xmlns:p='v'/>",
        "<annotation><object x='1' x='2'/></annotation>",
    ],
)
def test_duplicate_attributes_are_rejected(xml: str) -> None:
    assert one(xml) == ["xml_malformado"]


def test_unicode_and_giant_element_names_do_not_crash() -> None:
    assert boxes_of(ann(obj(), extra="<são/><日本語>x</日本語><a.b-c_d/>")) == 1
    giant = "x" * 200_000
    assert boxes_of(ann(obj(), extra=f"<{giant}/>")) == 1
    assert one(ann(obj(), extra="<1bad/>")) == ["xml_malformado"]
    assert one(ann(obj(), extra="<a b/>")) == ["xml_malformado"]
    assert one(ann(obj(), extra="< a/>")) == ["xml_malformado"]


def test_elements_in_unusual_order_and_whitespace_are_fine() -> None:
    xml = (
        "<annotation>\r\n<object><bndbox><ymax>75</ymax><xmax>150</xmax><ymin>26</ymin>"
        "<xmin>51</xmin></bndbox><name>b</name></object>"
        "<size><depth>3</depth><height>100</height><width>200</width></size>"
        "<filename>a.jpg</filename></annotation>"
    )
    p = parsed({"a.xml": xml})
    assert p.errors == () and p.files[0].declared_size == (200, 100)
    assert (p.boxes[0].xmin, p.boxes[0].ymax) == (51.0, 75.0)


# ------------------------------------------------------------------ números e geometria


@pytest.mark.parametrize(
    "tok", ["10.5", " 10 ", "\t10\n", "+10", "10.", ".5e1", "1E1", "-0", "-0.0", "1e0", "00010"]
)
def test_numeric_tokens_accepted(tok: str) -> None:
    assert boxes_of(ann(obj(box=bnd(tok, 1, 190, 90)))) == 1


@pytest.mark.parametrize(
    "tok",
    [
        "١٠",
        "１０",
        "①",
        "1٫5",
        "1,5",
        "1 0",
        "0b11",
        "0o7",
        "0xA",
        "1e",
        "e1",
        "+",
        "-",
        ".",
        "++1",
        "NaN",
        "Infinity",
        "-Infinity",
        "1__0",
        "10\x0c",
    ],
)
def test_numeric_tokens_rejected(tok: str) -> None:
    got = one(ann(obj(box=bnd(tok, 1, 190, 90))))
    assert got and set(got) <= {"numero_invalido", "xml_malformado"}


def test_huge_and_negative_and_empty_numbers() -> None:
    assert one(ann(obj(box=bnd(-1, 1, 5, 5)))) == ["bbox_invalida"]
    assert one(ann(obj(box=bnd(-1e-9, 1, 5, 5)))) == ["bbox_invalida"]
    assert one(ann(obj(box=bnd(1, 1, -5, -5)))) == ["bbox_invalida"]
    assert one(ann(obj(box=bnd("1e400", 1, 5, 5)))) == ["numero_invalido"]
    assert one(ann(obj(box=bnd("-1e400", 1, 5, 5)))) == ["numero_invalido"]
    assert one(ann(obj(box=bnd(1, 1, "9" * 33, 5)))) == ["numero_invalido"]
    assert one(ann(obj(box=bnd(1, 1, "", 5).replace("<xmax></xmax>", "<xmax/>")))) == [
        "coordenada_ausente"
    ]
    assert one(ann(obj(box=bnd(1, 1, "   ", 5)))) == ["coordenada_ausente"]


def test_all_four_bad_coordinates_are_all_reported_in_order() -> None:
    got = parsed({"a.xml": ann(obj(box=bnd("x", "y", "z", "w")))}).errors
    assert [e.message[:7] for e in got] == ["<xmin>:", "<ymin>:", "<xmax>:", "<ymax>:"]


@pytest.mark.parametrize(
    ("xmin", "ymin", "xmax", "ymax", "ok"),
    [
        (10, 10, 9, 20, False),  # xmin > xmax
        (10, 10, 20, 9, False),
        (10, 10, 10, 10, True),  # 1 pixel
        (0, 0, 0, 0, False),
        (0, 0, 1, 1, True),
        (0, 5, 0, 9, False),
        (1, 1, 1, 1, True),
        ("1e3", 1, "1e3", 5, True),
    ],
)
def test_degenerate_and_inverted_boxes(
    xmin: Any, ymin: Any, xmax: Any, ymax: Any, ok: bool
) -> None:
    p = parsed({"a.xml": ann(obj(box=bnd(xmin, ymin, xmax, ymax)))})
    assert (p.boxes_count == 1) is ok
    assert ok or [e.code for e in p.errors] == ["bbox_invalida"]


def test_absurd_values_become_unrepresentable_not_crashes() -> None:
    repo = FakeRepo()
    files = {
        "a.xml": ann(
            obj(box=bnd(1, 1, "1e308", 5)),
            obj(box=bnd("1e308", 1, "1.7e308", 5)),
            obj(box=bnd(1, 1, 5, "1e300")),
            obj(box=bnd("1e-320", "1e-320", "1e-310", "1e-310")),
        )
    }
    e = rejected(repo, files)
    assert [x.code for x in e.errors] == ["caixa_nao_representavel"] * 3
    assert repo.annotations == [] and "BEGIN" not in repo.calls


def test_box_beyond_the_border_is_warning_and_equal_to_image_is_not() -> None:
    repo = FakeRepo()
    r = import_(repo, {"a.xml": ann(obj(box=bnd(1, 1, 200, 100)), obj(box=bnd(100, 1, 210, 100)))})
    assert r.annotations_imported == 2 and r.warnings_total == 1
    assert r.warnings[0].code == "caixa_fora_da_imagem"


# ------------------------------------------------------------------ estrutura do documento


def test_empty_and_degenerate_objects_ordered_by_position() -> None:
    xml = ann(
        "<object/>", obj(box=bnd(9, 1, 2, 2)), "<object></object>", "<object><name/></object>"
    )
    got = parsed({"a.xml": xml}).errors
    assert [(e.code, e.index) for e in got] == [
        ("nome_ausente", 0),
        ("bndbox_ausente", 0),
        ("bbox_invalida", 1),
        ("nome_ausente", 2),
        ("bndbox_ausente", 2),
        ("nome_ausente", 3),
        ("bndbox_ausente", 3),
    ]


def test_object_without_name_and_nested_objects() -> None:
    assert one(f"<annotation><object>{bnd()}</object></annotation>") == ["nome_ausente"]
    inner = f"<object><name>in</name>{bnd()}</object>"
    outer_ok = f"<annotation><object><name>out</name>{bnd()}{inner}</object></annotation>"
    p = parsed({"a.xml": outer_ok})
    assert p.names == ("out",) and p.boxes_count == 1  # o aninhado nunca vira caixa
    outer_bad = f"<annotation><object><name>out</name>{inner}</object></annotation>"
    assert one(outer_bad) == ["bndbox_ausente"]


def test_multiple_size_bndbox_name_and_coordinates() -> None:
    s = size()
    assert one(ann(obj(), size=s + s)) == ["elemento_duplicado"]
    assert one(ann(f"<object><name>a</name>{bnd()}{bnd()}</object>")) == ["elemento_duplicado"]
    assert one(ann(f"<object><name>a</name><name>b</name>{bnd()}</object>")) == [
        "elemento_duplicado"
    ]
    dup = bnd().replace("</bndbox>", "<xmin>9</xmin></bndbox>")
    assert one(ann(obj(box=dup))) == ["elemento_duplicado"]
    assert one(ann(obj(), size="<size/>")) == ["size_invalido"]
    assert one(ann(obj(), size="<size><width>1</width></size>")) == ["size_invalido"]


@pytest.mark.parametrize("flag", ["true", "TRUE", "2", "-1", "yes", "", " 1 ", "01"])
def test_difficult_variants_never_drop_or_break_the_box(flag: str) -> None:
    p = parsed({"a.xml": ann(obj(extra=f"<difficult>{flag}</difficult>"))})
    assert p.errors == () and p.boxes_count == 1
    assert p.ignored_difficult == (1 if flag.strip() == "1" else 0)


def test_difficult_one_is_counted_even_if_the_object_is_invalid() -> None:
    p = parsed({"a.xml": ann(obj("x", box=bnd(9, 1, 2, 2), extra="<difficult>1</difficult>"))})
    assert p.ignored_difficult == 1 and p.boxes_count == 0 and len(p.errors) == 1


# ------------------------------------------------------------------ corpo JSON


@pytest.mark.parametrize("val", [None, [], {}, "", "x", 1, 1.5, True])
def test_annotations_of_wrong_type_is_estrutura_invalida(val: Any) -> None:
    if isinstance(val, dict) and not val:
        assert parse_voc(b'{"annotations": {}}', LIM).files_count == 0
        return
    with pytest.raises(ImportRejectedError) as ei:
        parse_voc(json.dumps({"annotations": val}).encode(), LIM)
    assert [e.code for e in ei.value.errors] == ["estrutura_invalida"]


def test_missing_annotations_key_and_non_object_root() -> None:
    for raw in (b"{}", b'{"Annotations": {}}', b"[]", b'"x"', b"1", b"null", b"true"):
        with pytest.raises(ImportRejectedError) as ei:
            parse_voc(raw, LIM)
        assert ei.value.errors[0].code in {"estrutura_invalida", "json_invalido"}


@pytest.mark.parametrize("val", [None, 1, 1.5, True])
def test_non_string_xml_value_is_reported_by_key(val: Any) -> None:
    with pytest.raises(ImportRejectedError) as ei:
        parse_voc(json.dumps({"annotations": {"a.xml": val}}).encode(), LIM)
    assert [(e.code, e.file) for e in ei.value.errors] == [("rotulo_invalido", "a.xml")]


@pytest.mark.parametrize("val", [[], {}, ["<annotation/>"], {"x": A}])
def test_container_xml_value_exceeds_json_depth(val: Any) -> None:
    with pytest.raises(ImportRejectedError) as ei:
        parse_voc(json.dumps({"annotations": {"a.xml": val}}).encode(), LIM)
    assert [e.code for e in ei.value.errors] == ["limite_excedido"]


# ------------------------------------------------------------------ chaves e casamento


def test_hostile_keys_never_become_paths_and_never_match_by_directory() -> None:
    repo = FakeRepo([ImportImage(10, "a.jpg", 200, 100), ImportImage(11, "passwd.jpg", 200, 100)])
    r = import_(repo, {"../../etc/passwd.xml": A, "C:\\x\\a.xml": A})
    assert r.annotations_imported == 2
    assert sorted(a.image_id for a in repo.annotations) == [10, 11]


@pytest.mark.parametrize(
    ("key", "code"),
    [
        ("", "rotulo_nome_invalido"),
        (".xml", "rotulo_nome_invalido"),
        ("dir/.xml", "rotulo_nome_invalido"),
        ("a.XML", "rotulo_extensao_invalida"),
        ("a.xml ", "rotulo_extensao_invalida"),
        ("a.xml/", "rotulo_extensao_invalida"),
        ("a\x00.xml", "rotulo_nome_invalido"),
        ("a\n.xml", "rotulo_nome_invalido"),
        ("a\u2028.xml", "rotulo_nome_invalido"),
        ("x" * 1025 + ".xml", "rotulo_nome_invalido"),
        ("  .xml", "rotulo_nome_invalido"),
    ],
)
def test_bad_keys(key: str, code: str) -> None:
    got = parsed({key: A}).errors
    assert [e.code for e in got] == [code]
    assert all(ord(c) >= 32 and c not in "\u2028\u2029" for c in (got[0].file or ""))
    assert len(got[0].file or "") <= 200


def test_nfc_and_nfd_keys_collide_after_sanitization() -> None:
    nfc, nfd = unicodedata.normalize("NFC", "é.xml"), unicodedata.normalize("NFD", "é.xml")
    assert nfc != nfd
    repo = FakeRepo([ImportImage(10, nfc[:-4] + ".jpg", 200, 100)])
    e = rejected(repo, {nfc: A, nfd: A})
    assert [x.code for x in e.errors] == ["rotulo_nome_duplicado"]
    assert repo.annotations == []
    # sozinha, a chave NFD casa com a imagem NFC
    assert (
        import_(FakeRepo([ImportImage(10, nfc[:-4] + ".jpg", 200, 100)]), {nfd: A}).status
        == "importado"
    )


def test_declared_filename_with_dotdot_or_unicode_forms() -> None:
    nfc = unicodedata.normalize("NFC", "é.jpg")
    nfd = unicodedata.normalize("NFD", "é.jpg")
    repo = FakeRepo([ImportImage(10, nfc, 200, 100)])
    ok = {"é.xml": ann(obj(), filename="../../" + nfd)}
    assert import_(repo, ok).annotations_imported == 1
    bad = {"é.xml": ann(obj(), filename="../outra.jpg")}
    assert [x.code for x in rejected(FakeRepo([ImportImage(10, nfc, 200, 100)]), bad).errors] == [
        "nome_divergente"
    ]


def test_same_image_through_two_keys_and_dir_variants_is_duplicate() -> None:
    e = rejected(FakeRepo(), {"a.xml": A, "dir/a.xml": A, "dir/sub/a.xml": A})
    assert [x.code for x in e.errors] == ["rotulo_nome_duplicado"] * 2
    assert [x.index for x in e.errors] == [1, 2]


def test_class_names_are_exact_no_normalization() -> None:
    nfc, nfd = unicodedata.normalize("NFC", "é"), unicodedata.normalize("NFD", "é")
    p = parsed({"a.xml": ann(obj(nfc), obj(nfd), obj("É"), obj("e"))})
    assert p.names == (nfc, nfd, "É", "e")  # decisão: comparação exata (ADR-006/010)


# ------------------------------------------------------------------ ordenação de erros


def test_object_errors_are_stable_by_file_then_line_regardless_of_key_order() -> None:
    def doc(n: int) -> str:
        return ann(*[obj("x", box=bnd(9, 1, 2, 2)) + "\n" for _ in range(n)])

    keys = ["c.xml", "a.xml", "b.xml", "10.xml", "2.xml"]
    runs = []
    for order in (keys, keys[::-1], sorted(keys)):
        p = parsed({k: doc(3) for k in order})
        runs.append([(e.file, e.index, e.line) for e in p.errors])
    assert runs[0] == runs[1] == runs[2]
    files = [r[0] for r in runs[0]]
    assert files == sorted(files)
    for f in set(files):
        pos = [r[1] for r in runs[0] if r[0] == f]
        assert pos == sorted(pos)


def test_service_orders_file_errors_before_matching_and_object_errors_by_file() -> None:
    repo = FakeRepo([ImportImage(10, "a.jpg", 200, 100), ImportImage(11, "b.jpg", 200, 100)])
    bad = ann(obj("x", box=bnd(9, 1, 2, 2)), obj("y", box=bnd(1, 1, "q", 2)))
    e = rejected(repo, {"b.xml": bad, "a.xml": bad, "z.xml": "<annotation"})
    assert e.errors == rejected(repo, {"z.xml": "<annotation", "a.xml": bad, "b.xml": bad}).errors
    obj_files = [x.file for x in e.errors if x.kind == "objeto"]
    assert obj_files == sorted(obj_files)


# ------------------------------------------------------------------ orçamento max_total_markup


def _budget_files(total: int) -> dict[str, str]:
    """Arquivos sem objetos cujo total de '<' é exatamente `total` (<= 20 000 por arquivo)."""
    per = 20_000
    out: dict[str, str] = {}
    left, i = total, 0
    while left > 0:
        n = min(per, left)
        out[f"f{i:03d}.xml"] = (
            "<annotation/>" if n == 1 else "<annotation>" + "<a/>" * (n - 2) + "</annotation>"
        )
        left -= n
        i += 1
    return out


def test_default_budget_is_600000() -> None:
    assert LIM.max_total_markup == 600_000


@pytest.mark.parametrize(
    ("total", "refused"), [(599_999, False), (600_000, False), (600_001, True)]
)
def test_total_markup_boundary(total: int, refused: bool) -> None:
    files = _budget_files(total)
    assert sum(x.count("<") for x in files.values()) == total
    data = json.dumps({"annotations": files}).encode()
    if refused:
        with pytest.raises(ImportRejectedError) as ei:
            parse_voc(data, LIM)
        e = ei.value.errors[0]
        assert (e.code, e.message) == ("limite_excedido", "limite excedido: max_total_markup")
    else:
        p = parse_voc(data, LIM)
        assert p.files_count == len(files) and p.boxes_count == 0


def test_comments_count_toward_the_budget() -> None:
    lim = VocLimits(max_total_markup=10)
    xml = "<annotation>" + "<!---->" * 3 + "</annotation>"  # 5 '<'
    parse_voc(json.dumps({"annotations": {"a.xml": xml}}).encode(), lim)
    with pytest.raises(ImportRejectedError):
        parse_voc(
            json.dumps({"annotations": {"a.xml": xml, "b.xml": xml, "c.xml": xml}}).encode(), lim
        )


# ------------------------------------------------------------------ idempotência e rollback


def test_hostile_valid_import_is_idempotent_and_dry_run_equals_real() -> None:
    repo = FakeRepo()
    files = {"a.xml": ann(obj("a&amp;b"), obj("é"), extra="<xi:include xmlns:xi='u'/>")}
    dry = import_(repo, files, dry_run=True)
    assert repo.annotations == [] and dry.status == "simulado"
    first = import_(repo, files)
    again = import_(repo, files)
    assert (first.annotations_imported, again.annotations_imported) == (2, 0)
    assert again.status == "sem_alteracoes" and again.annotations_already_existing == 2
    assert [c.name for c in repo.classes] == ["a&b", "é"]


def test_one_bad_file_among_good_ones_writes_nothing_even_classes() -> None:
    repo = FakeRepo()
    e = rejected(repo, {"a.xml": ann(obj("nova")), "b.xml": "<annotation>&#0;</annotation>"})
    assert [x.code for x in e.errors] == ["xml_malformado"]
    assert repo.annotations == [] and repo.classes == []
    assert not {"BEGIN", "lock", "create_class", "insert_annotation_batch"} & set(repo.calls)


def test_truncated_report_keeps_the_real_total_for_mixed_errors() -> None:
    repo = FakeRepo()
    files = {f"{i:02d}.xml": "<annotation>&#0;" for i in range(7)}
    files["z.xml"] = ann(*[obj("x", box=bnd(9, 1, 2, 2)) for _ in range(5)])
    p = parse_voc(
        json.dumps({"annotations": files}).encode(), VocLimits(max_errors=4), collect_errors=True
    )
    assert len(p.errors) == 4 and p.errors_total == 12
    with pytest.raises(ImportRejectedError) as ei:
        run(import_voc(repo, 1, p, limits=VocLimits(max_errors=4)))
    assert len(ei.value.errors) == 4 and ei.value.total == 13  # + imagem_nao_encontrada de z.xml


# ------------------------------------------------------------------ API: vazamentos


LEAK = "VAZA-zq9"


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> Env:
    return Env(monkeypatch)


def _hostile_with_sentinel() -> list[dict[str, str]]:
    s = LEAK
    return [
        {f"{s}.xml": A},  # chave sem imagem
        {f"{s}\u202e.xml": A.replace("<annotation>", "<annotation>", 1)},
        {"a.xml": ann(obj(f"{s}\u202e"))},  # classe inválida
        {"a.xml": ann(obj(), filename=f"{s}.jpg")},  # nome divergente
        {
            "a.xml": ann(obj(), extra=f"<{s}/>").replace("<annotation>", f"<annotation {s}='{s}'>")
            + s
        },  # texto depois da raiz
        {"a.xml": f"<annotation>{s}&{s};</annotation>"},  # entidade indefinida
        {"a.xml": f"<!DOCTYPE annotation [<!ENTITY x '{s}'>]><annotation>&x;</annotation>"},
        {"a.xml": f"<?pi {s}?><annotation/>"},
        {"a.xml": f"<annotation><![CDATA[{s}]]></annotation>"},
        {"a.xml": ann(obj(box=bnd(s, 1, 2, 2)))},
        {"a.xml": ann(obj(s, box=bnd(1, 1, 2, 2)), size=size(s, s))},
        {"a.xml": f'<?xml version="1.0" encoding="{s}"?>' + A},
        {"a.xml": ann(obj(), size=size(5, 5))},
    ]


@pytest.mark.parametrize("files", _hostile_with_sentinel())
def test_error_responses_and_logs_never_echo_xml_content_or_secrets(
    env: Env, files: dict[str, str], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    with env.client() as c:
        r = post(c, json.dumps({"annotations": files}).encode())
    assert r.status_code == 422
    blob = r.text + "\n" + caplog.text
    if not any(LEAK in k for k in files):  # a chave é nome do usuário e pode ser citada
        assert LEAK not in blob
    for s in [*SECRETS, "INSERT INTO", "dbhost"]:
        assert s not in blob
    assert env.repo.writes() == []
    for err in r.json()["erros"]:
        assert not any(unicodedata.category(ch).startswith("C") for ch in err["message"])
        if err.get("file"):
            assert not any(unicodedata.category(ch).startswith("C") for ch in err["file"])
            assert len(err["file"]) <= 200


def test_success_response_has_no_xml_content_and_valid_hostile_import_is_201(env: Env) -> None:
    files = {"a.xml": ann(obj(f"{LEAK}&amp;"), filename="a.jpg", extra=f"<{LEAK}>{LEAK}</{LEAK}>")}
    with env.client() as c:
        r = post(c, json.dumps({"annotations": files}).encode())
    assert r.status_code == 201
    assert LEAK not in r.text  # nome de classe também não volta na resposta
    assert env.repo.classes[0].name == f"{LEAK}&"


def test_422_truncated_report_has_real_total_via_http(monkeypatch: pytest.MonkeyPatch) -> None:
    e = Env(monkeypatch, import_max_errors=2)
    files = {f"{i}.xml": "<annotation>&#0;" for i in range(6)}
    with e.client() as c:
        j = post(c, json.dumps({"annotations": files}).encode()).json()
    assert len(j["erros"]) == 2 and j["erros_total"] == 6 and j["erros_truncados"] is True
    assert e.repo.writes() == []


def test_http_hostile_json_shapes(env: Env) -> None:
    with env.client() as c:
        for val in (None, [], "x", 3):
            r = post(c, json.dumps({"annotations": val}).encode())
            assert r.status_code == 422 and r.json()["erros"][0]["code"] == "estrutura_invalida"
        r = post(c, json.dumps({"annotations": {"a.xml": None}}).encode())
        assert r.status_code == 422 and r.json()["erros"][0]["code"] == "rotulo_invalido"
    assert env.repo.writes() == []
