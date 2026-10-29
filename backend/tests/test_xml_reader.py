"""F1-08a critérios 2, 3, 6, 7, 8 e 9: leitor XML seguro (puro, sem banco, rede nem Docker)."""

from __future__ import annotations

import contextlib
import random

import pytest

import _xml_cases as c
from inovalytics.imports import xml_reader
from inovalytics.imports.xml_reader import XmlLimits, XmlRejectedError, read_xml

LIM = XmlLimits()
BIG = XmlLimits(max_bytes=10**8, max_markup=10**8, max_elements=10**8)


def reject(text: str, lim: XmlLimits = LIM) -> XmlRejectedError:
    with pytest.raises(XmlRejectedError) as ei:
        read_xml(text, lim)
    return ei.value


MINIMAL = (
    "<annotation><filename>a.jpg</filename><size><width>200</width><height>100</height></size>"
    "<object><name>pessoa</name><bndbox><xmin>51</xmin><ymin>26</ymin><xmax>150</xmax>"
    "<ymax>75</ymax></bndbox></object></annotation>"
)


def test_minimal_document_structure_text_and_lines() -> None:
    doc = read_xml(MINIMAL.replace("><", ">\n<"))
    names = [n.name for n in doc.nodes]
    assert names[:4] == ["annotation", "filename", "size", "width"]
    by_name = {n.name: n for n in doc.nodes}
    assert by_name["filename"].text == "a.jpg" and by_name["filename"].line == 2
    assert by_name["annotation"].text == ""  # indentação nunca vira texto
    assert by_name["xmin"].text == "51" and by_name["ymax"].text == "75"
    idx = names.index("xmin")
    assert doc.path(idx) == ("annotation", "object", "bndbox", "xmin")
    assert doc.nodes[idx].depth == 4 and doc.nodes[0].parent == -1


def test_pure_and_deterministic() -> None:
    assert read_xml(MINIMAL) == read_xml(MINIMAL)


def test_bom_crlf_declaration_comments_entities_and_char_refs() -> None:
    text = (
        '\ufeff<?xml version="1.0" encoding="utf-8"?>\r\n<!-- c -->\r\n<annotation>\r\n'
        "  <name> a &amp; b &lt; &#233; &#xE9; </name><!-- fim --></annotation>\r\n<!-- depois -->"
    )
    doc = read_xml(text)
    assert [n.name for n in doc.nodes] == ["annotation", "name"]
    assert doc.nodes[1].text == "a & b < é é"
    assert doc.nodes[1].line == 4


@pytest.mark.parametrize("enc", ["UTF-8", "utf-8", "US-ASCII", "us-ascii"])
def test_allowed_declared_encodings(enc: str) -> None:
    read_xml(f'<?xml version="1.0" encoding="{enc}"?><annotation/>')


def test_declaration_without_encoding_is_accepted() -> None:
    read_xml('<?xml version="1.0"?><annotation/>')


@pytest.mark.parametrize("enc", ["ISO-8859-1", "UTF-16", "UTF-32", "windows-1252", "latin1"])
def test_other_declared_encodings_rejected(enc: str) -> None:
    e = reject(f'<?xml version="1.0" encoding="{enc}"?><annotation/>')
    assert e.code == "encoding_nao_suportado" and e.line == 1


def test_declared_utf16_with_utf8_body_is_rejected_not_reinterpreted() -> None:
    e = reject('<?xml version="1.0" encoding="UTF-16"?><annotation><n>é</n></annotation>')
    assert e.code == "encoding_nao_suportado"


def test_non_ascii_text_roundtrips() -> None:
    doc = read_xml("<annotation><n>açaí 日本 🙂</n></annotation>")
    assert doc.nodes[1].text == "açaí 日本 🙂"


# --- critério 6: instruções, CDATA, raiz ------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "<annotation><?php echo 1; ?></annotation>",
        '<?xml-stylesheet href="a.css"?><annotation/>',
        "<annotation/><?pi x?>",
    ],
)
def test_processing_instructions_rejected(text: str) -> None:
    assert reject(text).code == "instrucao_proibida"


def test_cdata_rejected_with_line() -> None:
    e = reject("<annotation>\n<a><![CDATA[x]]></a></annotation>")
    assert (e.code, e.line) == ("cdata_proibido", 2)


def test_xml_declaration_not_first_is_error_with_line() -> None:
    e = reject('\n<?xml version="1.0"?><annotation/>')
    assert e.code == "xml_malformado" and e.line == 2
    e = reject('<annotation><?xml version="1.0"?></annotation>')
    assert e.code in {"xml_malformado", "instrucao_proibida"}


@pytest.mark.parametrize(
    "text",
    ["<annotation/><annotation/>", "<annotation/>lixo", "<annotation/><a/>", "x<annotation/>"],
)
def test_content_after_root_or_second_document(text: str) -> None:
    assert reject(text).code == "xml_malformado"


def test_wrong_root_rejected_with_line() -> None:
    e = reject("\n\n<outra/>")
    assert (e.code, e.line) == ("raiz_invalida", 3)


def test_empty_and_whitespace_documents() -> None:
    assert reject("").code == "xml_malformado"
    assert reject("   \n").code == "xml_malformado"


# --- critério 7: codificação e bombas de texto -------------------------------------------------


def test_double_bom_rejected_single_accepted() -> None:
    assert reject("\ufeff\ufeff<annotation/>").code == "xml_malformado"
    read_xml("\ufeff<annotation/>")


@pytest.mark.parametrize("text", ["<annotation>\ud800</annotation>", "\udfff<annotation/>"])
def test_lone_surrogate_is_texto_invalido(text: str) -> None:
    assert reject(text).code == "texto_invalido"


@pytest.mark.parametrize("ch", ["\x00", "\x01", "\x0b", "\x1f", "\ufffe", "\uffff"])
def test_forbidden_characters_are_syntax_errors(ch: str) -> None:
    assert reject(f"<annotation>a{ch}b</annotation>").code == "xml_malformado"


def test_cr_and_u2028_are_legal_and_do_not_shift_lines() -> None:
    doc = read_xml("<annotation>\r<a>x\u2028y</a>\r\n<b/></annotation>")
    assert doc.nodes[1].line == 2 and doc.nodes[1].text == "x\u2028y"
    assert doc.nodes[2].line == 3


@pytest.mark.parametrize("text", ["\ufffe<\x00a\x00>", "\ufeff<\x00a\x00>", "<\x00a\x00>"])
def test_imitated_utf16_is_syntax_error(text: str) -> None:
    assert reject(text).code == "xml_malformado"


def test_oversize_str_rejected_without_encoding_or_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a: object, **k: object) -> None:
        raise AssertionError("não deve chegar ao expat")

    monkeypatch.setattr(xml_reader.expat, "ParserCreate", boom)
    text = "<" + "a" * (LIM.max_bytes)  # 256 KiB + 1 caractere
    assert len(text) == LIM.max_bytes + 1
    e = reject(text)
    assert (e.code, e.limit) == ("limite_excedido", "max_xml_bytes")


def test_byte_limit_applies_after_encoding(monkeypatch: pytest.MonkeyPatch) -> None:
    lim = XmlLimits(max_bytes=100)
    pad = "😀" * 25  # 25 caracteres, 100 bytes
    read_xml(f"<annotation>{pad}</annotation>", XmlLimits(max_bytes=200, max_text_chars=100))
    e = reject(f"<annotation>{pad}</annotation>", lim)  # 25+25 caracteres mas 125 bytes
    assert (e.code, e.limit) == ("limite_excedido", "max_xml_bytes")


def test_exact_byte_limit_passes() -> None:
    doc = "<annotation/>"
    read_xml(doc, XmlLimits(max_bytes=len(doc)))
    assert reject(doc, XmlLimits(max_bytes=len(doc) - 1)).limit == "max_xml_bytes"


# --- critério 8: limites estruturais -----------------------------------------------------------


def _deep(n: int) -> str:
    return "<annotation>" + "<a>" * (n - 1) + "</a>" * (n - 1) + "</annotation>"


def test_depth_16_ok_17_rejected() -> None:
    read_xml(_deep(16), LIM)
    e = reject(_deep(17))
    assert (e.code, e.limit) == ("limite_excedido", "max_depth")


def test_100000_open_levels_no_recursion_error() -> None:
    e = reject("<annotation>" + "<a>" * 100_000, BIG)
    assert (e.code, e.limit) == ("limite_excedido", "max_depth")


def _flat(n: int) -> str:
    return "<annotation>" + "<a/>" * (n - 1) + "</annotation>"


def test_elements_20000_ok_20001_rejected() -> None:
    assert len(read_xml(_flat(20_000)).nodes) == 20_000
    e = reject(_flat(20_001))
    assert (e.code, e.limit) == ("limite_excedido", "max_elements")


def test_elements_limit_is_reachable_with_end_tags() -> None:
    """20 000 elementos com marca de fim têm 40 000 '<': o teto de '<' padrão não os impede."""
    text = "<annotation>" + "<a></a>" * 19_999 + "</annotation>"
    assert len(read_xml(text).nodes) == 20_000


def _attrs(n: int) -> str:
    return "<annotation" + "".join(f' k{i}="v"' for i in range(n)) + "/>"


def test_attributes_32_ok_33_rejected() -> None:
    read_xml(_attrs(32))
    e = reject(_attrs(33))
    assert (e.code, e.limit) == ("limite_excedido", "max_attributes")


def test_text_1024_ok_1025_rejected() -> None:
    assert len(read_xml(f"<annotation><a>{'x' * 1024}</a></annotation>").nodes[1].text) == 1024
    e = reject(f"<annotation><a>{'x' * 1025}</a></annotation>")
    assert (e.code, e.limit, e.line) == ("limite_excedido", "max_text_chars", 1)


def test_padded_text_1024_ok_and_runaway_padding_aborts() -> None:
    assert read_xml(f"<annotation><a>  {'x' * 1024}  </a></annotation>").nodes[1].text == "x" * 1024
    assert reject(f"<annotation><a>x{' ' * 5000}</a></annotation>").limit == "max_text_chars"


def test_text_split_across_children_is_counted_per_element() -> None:
    parts = "<b/>".join(["x" * 600, "x" * 600])
    assert reject(f"<annotation>{parts}</annotation>").limit == "max_text_chars"


def test_interior_whitespace_survives_buffer_boundaries() -> None:
    body = "a" * 3000 + " " + "b" * 3000
    doc = read_xml(f"<annotation><a>{body}</a></annotation>", XmlLimits(max_text_chars=10_000))
    assert doc.nodes[1].text == body


def test_indentation_with_many_children_is_never_counted() -> None:
    kids = "\n    ".join("<a/>" for _ in range(2000))
    read_xml(f"<annotation>\n    {kids}\n</annotation>")


def test_markup_count_checked_before_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a: object, **k: object) -> None:
        raise AssertionError("Parse/ParserCreate não deve ser chamado")

    monkeypatch.setattr(xml_reader.expat, "ParserCreate", boom)
    lim = XmlLimits(max_markup=10)
    e = reject("<annotation>" + "<a/>" * 10 + "</annotation>", lim)  # 12 '<'
    assert (e.code, e.limit) == ("limite_excedido", "max_markup")


def test_markup_count_exact_boundary() -> None:
    text = "<annotation>" + "<a/>" * 4 + "</annotation>"  # 6 '<'
    read_xml(text, XmlLimits(max_markup=6))
    assert reject(text, XmlLimits(max_markup=5)).limit == "max_markup"


def test_parse_called_exactly_once(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[int, bool]] = []
    real = xml_reader.expat.ParserCreate

    class Spy:
        def __init__(self) -> None:
            self.p = real(encoding="utf-8")

        def Parse(self, data: bytes, final: bool) -> int:  # noqa: N802
            calls.append((len(data), final))
            return int(self.p.Parse(data, final))

        def __getattr__(self, name: str) -> object:
            return getattr(self.p, name)

        def __setattr__(self, name: str, value: object) -> None:
            if name == "p":
                object.__setattr__(self, name, value)
            else:
                setattr(self.p, name, value)

    monkeypatch.setattr(xml_reader.expat, "ParserCreate", lambda **_: Spy())
    read_xml(MINIMAL)
    assert calls == [(len(MINIMAL.encode()), True)]


def test_limit_message_names_only_the_limit() -> None:
    e = reject(_attrs(33))
    assert e.message == "limite excedido: max_attributes"
    assert "k0" not in e.message


# --- critério 9: atributos e nomes hostis ------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        '<annotation a="1" a="2"/>',
        "<annotation><1a/></annotation>",
        "<annotation><a b/></annotation>",
        "<annotation><a b=1/></annotation>",
        '<annotation><a b="<"/></annotation>',
        "<annotation><a></b></annotation>",
        "<annotation><a>",
        "<annotation>&;</annotation>",
        "<annotation>&amp</annotation>",
        "<annotation>&#0;</annotation>",
        "<annotation>&#xD800;</annotation>",
        "<annotation>&#99999999;</annotation>",
    ],
)
def test_malformed_documents_are_syntax_errors(text: str) -> None:
    e = reject(text)
    assert e.code == "xml_malformado" and e.line == 1


def test_attribute_with_predefined_entity_is_fine_and_discarded() -> None:
    doc = read_xml('<annotation a="&lt;&amp;" b="x"/>')
    assert doc.nodes[0].text == ""


def test_attribute_with_unknown_entity_rejected() -> None:
    assert reject('<annotation a="&xxe;"/>').code == "entidade_proibida"


def test_long_attribute_value_and_many_attributes_are_bounded() -> None:
    read_xml('<annotation v="' + "v" * (200 * 1024) + '"/>')
    text = "<annotation" + "".join(f' k{i}=""' for i in range(1000)) + "/>"
    assert reject(text).limit == "max_attributes"


def test_real_voc_extras_are_tolerated_and_do_not_change_result() -> None:
    plain = read_xml(MINIMAL)
    extra = MINIMAL.replace(
        "<size>",
        '<source database="x"><annotation2/></source><segmented>0</segmented>'
        "<path>C:\\x\\a.jpg</path><size>",
    ).replace("<annotation>", '<annotation verified="yes">')
    rich = read_xml(extra)
    keep = lambda d: [(n.name, n.text) for n in d.nodes if n.name in {"filename", "xmin", "ymax"}]  # noqa: E731
    assert keep(rich) == keep(plain)


# --- critério 2: nenhuma outra exceção escapa --------------------------------------------------


def test_random_inputs_only_raise_xml_rejected() -> None:
    rnd = random.Random(1234)
    seeds = [
        *("<annotation>", "</annotation>", "<a>", "</a>", "&amp;", "&", "\x00", "\r", "\u2028"),
        *("\ud800", "é", "<!--", "-->", "<?", "?>", "<![CDATA[", "]]>", "<!DOCTYPE a>", " ", "="),
    ]
    for _ in range(3000):
        text = "".join(rnd.choice(seeds) for _ in range(rnd.randint(0, 25)))
        try:
            read_xml(text)
        except XmlRejectedError as e:
            assert e.code and e.message
    for text in ("\x00" * 1000, "\r" * 1000, "\u2028" * 1000, "\ud800" * 1000, "\xff" * 1000):
        with contextlib.suppress(XmlRejectedError):
            read_xml(text)


def test_garbage_256k_and_mojibake() -> None:
    for seed in (1, 2, 3):
        reject(c.garbage(seed))
    moji = "<annotation>" + bytes([0xFF] * 1000).decode("latin-1") + "</annotation>"
    read_xml(moji, XmlLimits(max_text_chars=2000))  # latin-1 -> U+00FF é texto UTF-8 válido


def test_non_string_input_is_rejected() -> None:
    with pytest.raises(XmlRejectedError):
        read_xml(b"<annotation/>")  # type: ignore[arg-type]
