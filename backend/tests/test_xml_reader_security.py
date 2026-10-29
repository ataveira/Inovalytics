"""F1-08a critérios 4, 5 e 10: XXE, expansão de entidades e DoS com tamanho real do leitor XML."""

from __future__ import annotations

import socket
import tracemalloc
from pathlib import Path

import pytest

import _xml_cases as c
from _timing import timed
from inovalytics.imports import xml_reader
from inovalytics.imports.xml_reader import XmlLimits, XmlRejectedError, read_xml

SENTINEL = "SEGREDO-UNICO-7f3a9c-nao-pode-vazar"
BIG = XmlLimits(max_markup=10**9)


@pytest.fixture
def sentinel_file(tmp_path: Path) -> Path:
    p = tmp_path / "segredo.txt"
    p.write_text(SENTINEL, "utf-8")
    return p


@pytest.fixture
def spies(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    """Conta aberturas de arquivo e criação de socket durante o teste."""
    import builtins
    import io

    counts = {"open": 0, "socket": 0}
    real_open, real_io_open = builtins.open, io.open

    def spy_open(*a: object, **k: object) -> object:
        counts["open"] += 1
        return real_open(*a, **k)  # type: ignore[call-overload]

    def spy_socket(*a: object, **k: object) -> None:
        counts["socket"] += 1
        raise AssertionError("acesso à rede proibido neste teste")

    monkeypatch.setattr(builtins, "open", spy_open)
    monkeypatch.setattr(io, "open", spy_open)
    monkeypatch.setattr(socket, "socket", spy_socket)
    assert real_io_open  # noqa: S101
    return counts


def reject(text: str) -> XmlRejectedError:
    with pytest.raises(XmlRejectedError) as ei:
        read_xml(text)
    return ei.value


def external(uri: str) -> dict[str, str]:
    ent = f'<!ENTITY xxe SYSTEM "{uri}">'
    return {
        "elemento": f"<!DOCTYPE annotation [{ent}]><annotation>&xxe;</annotation>",
        "atributo": f'<!DOCTYPE annotation [{ent}]><annotation a="&xxe;"/>',
        "parametrica": f'<!DOCTYPE annotation [<!ENTITY % p SYSTEM "{uri}"> %p;]><annotation/>',
        "dtd_externo": f'<!DOCTYPE annotation SYSTEM "{uri}"><annotation/>',
        "dtd_publico": f'<!DOCTYPE annotation PUBLIC "-//X//EN" "{uri}"><annotation/>',
    }


@pytest.mark.parametrize(
    "kind", ["elemento", "atributo", "parametrica", "dtd_externo", "dtd_publico"]
)
@pytest.mark.parametrize("scheme", ["file", "http"])
def test_external_entities_and_dtd_refused_without_io(
    kind: str, scheme: str, sentinel_file: Path, spies: dict[str, int]
) -> None:
    uri = f"file://{sentinel_file}" if scheme == "file" else "http://127.0.0.1:9/x.dtd"
    before = spies["open"]
    e = reject(external(uri)[kind])
    assert e.code == "doctype_proibido" and e.line == 1
    assert spies["socket"] == 0 and spies["open"] == before
    assert SENTINEL not in e.message and str(sentinel_file) not in e.message
    assert SENTINEL not in repr(e) and str(sentinel_file) not in repr(e)


@pytest.mark.usefixtures("no_external_network")
def test_external_http_entity_makes_no_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[object] = []
    monkeypatch.setattr(socket.socket, "connect", lambda *a, **k: calls.append(a))
    reject(external("http://127.0.0.1:9/x")["elemento"])
    assert calls == []


@pytest.mark.parametrize(
    "text",
    [
        "<!DOCTYPE annotation [<!ENTITY % p SYSTEM 'file:///etc/passwd'> %p;]><annotation/>",
        "<!DOCTYPE a SYSTEM 'x.dtd'><annotation/>",
        "<!DOCTYPE a []><annotation/>",
        "<!DOCTYPE a [<!ENTITY e 'x'>]><annotation>&e;</annotation>",
        "<!DOCTYPE a [<!ENTITY e 'x'>]><annotation a='&e;'/>",
        "<!DOCTYPE a [<!ELEMENT a ANY>]><annotation/>",
        "<!DOCTYPE a [<!ATTLIST a x CDATA 'y'>]><annotation/>",
        "<!DOCTYPE a [<!NOTATION n SYSTEM 'x'>]><annotation/>",
        "<!doctype annotation><annotation/>",
        "<annotation/><!DOCTYPE annotation>",
    ],
)
def test_any_doctype_is_refused(text: str) -> None:
    code = reject(text).code
    assert code in {"doctype_proibido", "xml_malformado"}
    if "DOCTYPE" in text and "<annotation/><!" not in text:
        assert code == "doctype_proibido"


def test_doctype_error_has_the_line_of_the_declaration() -> None:
    e = reject("<?xml version='1.0'?>\n\n<!DOCTYPE annotation []>\n<annotation/>")
    assert (e.code, e.line) == ("doctype_proibido", 3)


@pytest.mark.parametrize(
    "text",
    [
        "<annotation>&xxe;</annotation>",
        '<annotation a="&xxe;"/>',
        "<annotation>&e1;&amp;</annotation>",
    ],
)
def test_undeclared_entity_without_doctype(text: str) -> None:
    code = reject(text).code
    assert code in {"entidade_proibida", "xml_malformado"}


def test_entity_declaration_outside_doctype_is_error() -> None:
    assert reject('<annotation><!ENTITY e "x"></annotation>').code == "xml_malformado"


@pytest.mark.parametrize("declare_ns", [True, False])
def test_xinclude_is_just_an_element_and_never_reads_files(
    declare_ns: bool, sentinel_file: Path, spies: dict[str, int]
) -> None:
    ns = ' xmlns:xi="http://www.w3.org/2001/XInclude"' if declare_ns else ""
    text = f'<annotation{ns}><xi:include href="file://{sentinel_file}" parse="text"/></annotation>'
    before = spies["open"]
    try:
        doc = read_xml(text)
    except XmlRejectedError as e:  # sem o prefixo declarado o expat sem namespaces aceita; ou erro
        assert SENTINEL not in e.message
        doc = None
    assert spies["open"] == before and spies["socket"] == 0
    if doc is not None:
        assert [n.name for n in doc.nodes] == ["annotation", "xi:include"]
        assert all(SENTINEL not in n.text and SENTINEL not in n.name for n in doc.nodes)


def test_sentinel_never_in_any_result_or_message(sentinel_file: Path) -> None:
    for text in external(f"file://{sentinel_file}").values():
        e = reject(text)
        assert SENTINEL not in e.message and SENTINEL not in str(e) and SENTINEL not in repr(e)


def test_error_messages_never_echo_document_content() -> None:
    marker = "CONTEUDO-DO-USUARIO-XYZ"
    for text in (
        f"<annotation><{marker}></annotation>",
        f"<annotation a='{marker}' a='{marker}'/>",
        f"<annotation>&{marker};</annotation>",
        f"<?{marker}?><annotation/>",
        f"<!DOCTYPE {marker}><annotation/>",
        f'<?xml version="1.0" encoding="{marker}"?><annotation/>',
        f"<{marker}/>",
    ):
        e = reject(text)
        assert marker not in e.message and marker not in repr(e)


# --- critério 5: expansão exponencial e quadrática ---------------------------------------------

BOMBS = {
    "billion_laughs": c.billion_laughs,
    "billion_laughs_param": c.billion_laughs_param,
    "quadratic_blowup": c.quadratic_blowup,
}


@pytest.mark.parametrize("name", list(BOMBS))
def test_entity_bombs_refused_fast_and_small(name: str) -> None:
    text = BOMBS[name]()
    tracemalloc.start()
    try:
        elapsed, result = timed(lambda: reject(text), 0.5)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert isinstance(result, XmlRejectedError) and result.code == "doctype_proibido"
    assert elapsed < 0.5
    assert peak < 4 * 1024 * 1024  # a expansão declarada (GiB) nunca é materializada


def test_memory_does_not_grow_with_declared_expansion() -> None:
    def peak_of(levels: int) -> int:
        decl = '<!ENTITY l0 "ha">' + "".join(
            f'<!ENTITY l{i} "' + f"&l{i - 1};" * 10 + '">' for i in range(1, levels)
        )
        text = f"<!DOCTYPE annotation [{decl}]><annotation>&l{levels - 1};</annotation>"
        tracemalloc.start()
        try:
            reject(text)
            return tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()

    assert abs(peak_of(10) - peak_of(2)) < 64 * 1024


def test_bomb_is_refused_at_the_first_declaration(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []
    orig = xml_reader._Collector.forbid

    def spy(self: object, code: str) -> object:
        h = orig(self, code)  # type: ignore[arg-type]

        def wrapped(*a: object) -> None:
            seen.append(code)
            h(*a)  # type: ignore[operator]

        return wrapped

    monkeypatch.setattr(xml_reader._Collector, "forbid", spy)
    reject(c.billion_laughs())
    assert seen == ["doctype_proibido"]  # parou no DOCTYPE; nenhuma declaração de entidade lida


# --- critério 10: DoS com tamanho real ---------------------------------------------------------

HOSTILE = {
    "nested": c.nested,
    "repeated_empty": c.repeated_empty,
    "spaces": c.spaces,
    "comments": c.comments,
    "single_text": c.single_text,
    "many_attributes": c.many_attributes,
    "long_attribute": c.long_attribute,
    "huge_name": c.huge_name,
}
BUDGET = 2.0  # orçamento por caso; as medições reais (ADR-010) ficam muito abaixo


@pytest.mark.parametrize("limits", [XmlLimits(), BIG], ids=["padrao", "sem_teto_de_markup"])
@pytest.mark.parametrize("name", list(HOSTILE))
def test_hostile_256k_files_finish_within_budget(name: str, limits: XmlLimits) -> None:
    text = HOSTILE[name]()
    assert len(text) <= c.MAX

    def run() -> object:
        try:
            return read_xml(text, limits)
        except XmlRejectedError as e:
            return e

    elapsed, result = timed(run, BUDGET)
    assert elapsed < BUDGET
    assert isinstance(result, (XmlRejectedError, xml_reader.XmlDoc))


def test_hostile_cases_are_rejected_by_the_expected_limit() -> None:
    expected = {
        "repeated_empty": "max_markup",
        "single_text": "max_text_chars",
        "many_attributes": "max_attributes",
    }
    for name, limit in expected.items():
        with pytest.raises(XmlRejectedError) as ei:
            read_xml(HOSTILE[name]())
        assert ei.value.limit == limit, name
    with pytest.raises(XmlRejectedError) as ei:
        read_xml(HOSTILE["repeated_empty"](), BIG)
    assert ei.value.limit == "max_elements"


def test_thousand_hostile_files_in_sequence_stay_linear() -> None:
    docs = [c.nested(5000), c.repeated_empty(), c.single_text(), c.quadratic_blowup()]

    def run() -> int:
        n = 0
        for i in range(1000):
            try:
                read_xml(docs[i % 4], BIG)
            except XmlRejectedError:
                n += 1
        return n

    elapsed, result = timed(run, 30.0)
    assert result == 1000
    assert elapsed < 10.0


def test_garbage_files_finish_within_budget() -> None:
    def run() -> int:
        n = 0
        for seed in range(5):
            try:
                read_xml(c.garbage(seed, 256 * 1024 - 8))
            except XmlRejectedError:
                n += 1
        return n

    elapsed, result = timed(run, BUDGET * 5)
    assert result == 5 and elapsed < BUDGET * 5
