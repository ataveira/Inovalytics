"""F1-08b critérios 12 a 20: parser Pascal VOC e corpo (puro; fixtures só em código)."""

from __future__ import annotations

import contextlib
import json
import random
from typing import Any

import pytest

from _timing import timed
from inovalytics.imports import voc_parser as mod
from inovalytics.imports.errors import ImportRejectedError
from inovalytics.imports.voc_parser import ParsedVoc, VocLimits, convert_box, parse_voc

LIM = VocLimits()


def bnd(xmin: Any = 51, ymin: Any = 26, xmax: Any = 150, ymax: Any = 75) -> str:
    def tag(n: str, v: Any) -> str:
        return "" if v is None else f"<{n}>{v}</{n}>"

    inner = tag("xmin", xmin) + tag("ymin", ymin) + tag("xmax", xmax) + tag("ymax", ymax)
    return f"<bndbox>{inner}</bndbox>"


def obj(name: str | None = "pessoa", box: str | None = None, extra: str = "") -> str:
    n = "" if name is None else f"<name>{name}</name>"
    b = bnd() if box is None else box
    return f"<object>{n}{extra}{b}</object>"


def ann(
    *objects: str, filename: str | None = None, size: str | None = None, extra: str = ""
) -> str:
    fn = "" if filename is None else f"<filename>{filename}</filename>"
    return f"<annotation>{fn}{size or ''}{extra}{''.join(objects)}</annotation>"


def size(w: Any = 200, h: Any = 100) -> str:
    return f"<size><width>{w}</width><height>{h}</height><depth>3</depth></size>"


def body(files: Any = None, **extra: Any) -> bytes:
    obj_: dict[str, Any] = {"annotations": {} if files is None else files}
    obj_.update(extra)
    return json.dumps(obj_).encode()


def parse(files: Any = None, lim: VocLimits = LIM, **kw: Any) -> ParsedVoc:
    return parse_voc(body(files), lim, **kw)


def refused(data: bytes, lim: VocLimits = LIM) -> ImportRejectedError:
    with pytest.raises(ImportRejectedError) as ei:
        parse_voc(data, lim)
    return ei.value


def errs(files: Any, lim: VocLimits = LIM) -> list[tuple[str, str, str | None, int | None]]:
    e = refused(body(files), lim)
    return [(x.code, x.kind, x.file, x.line) for x in e.errors]


def code_of(files: Any, lim: VocLimits = LIM) -> str:
    return errs(files, lim)[0][0]


# ------------------------------------------------------------------ 12: corpo e estrutura


@pytest.mark.parametrize(
    "data", [b"", b"   ", b"{", b'{"annotations": {}', b"\xff\xfe", b"[]", b"null"]
)
def test_bad_json_bodies(data: bytes) -> None:
    assert refused(data).errors[0].code == "json_invalido"


@pytest.mark.parametrize(
    "data",
    [b"{}", b'{"annotations": []}', b'{"annotations": "x"}', b'{"annotations": null}'],
)
def test_bad_structure(data: bytes) -> None:
    assert refused(data).errors[0].code == "estrutura_invalida"


@pytest.mark.parametrize("value", [1, None, ["x"], {"a": 1}, True])
def test_non_string_value_is_rotulo_invalido_with_file_name(value: Any) -> None:
    if isinstance(value, (list, dict)):
        data = body({"a.xml": value})
        e = refused(data)  # profundidade 3 do JSON: limite
        assert e.errors[0].code == "limite_excedido"
        return
    e = refused(body({"a.xml": value}))
    x = e.errors[0]
    assert (x.code, x.file, x.kind) == ("rotulo_invalido", "a.xml", "anotacao")


def test_duplicate_json_key_and_nonfinite_constants_are_json_invalido() -> None:
    assert refused(b'{"annotations": {"a.xml": "", "a.xml": ""}}').errors[0].code == "json_invalido"
    assert refused(b'{"annotations": {}, "x": NaN}').errors[0].code == "json_invalido"


def test_extra_keys_are_ignored_and_empty_is_valid() -> None:
    p = parse_voc(body({}, other="x"), LIM)
    assert p.files_count == 0 and p.boxes_count == 0 and p.names == ()


def test_json_depth_fixed_at_two() -> None:
    e = refused(body({"a.xml": ann(obj())}, extra={"deep": [[1]]}))
    assert e.errors[0].code == "limite_excedido" and "max_depth" in e.errors[0].message


def test_pure_and_deterministic() -> None:
    data = body({"b.xml": ann(obj("x")), "a.xml": ann(obj("y"), obj("x"))})
    assert parse_voc(data, LIM) == parse_voc(data, LIM)


def test_random_bodies_only_raise_import_rejected() -> None:
    rng = random.Random(77)
    pieces = ['{"annotations":', "{", "}", '"a.xml"', ":", ",", '"<annotation/>"', "null", "[", "]",
              '"\\ud800"', "1e999", "\x00", "é"]  # fmt: skip
    for _ in range(1500):
        data = "".join(rng.choice(pieces) for _ in range(rng.randint(0, 14))).encode()
        with contextlib.suppress(ImportRejectedError):
            parse_voc(data, LIM)
    for _ in range(300):
        raw = bytes(rng.randrange(256) for _ in range(rng.randint(0, 80)))
        with contextlib.suppress(ImportRejectedError):
            parse_voc(raw, LIM)


# ------------------------------------------------------------------ 13: fixture de aceite


def two_files() -> dict[str, str]:
    a = ann(
        obj("pessoa"),
        obj("capacete", bnd(1, 1, 200, 100)),
        filename="a.jpg",
        size=size(),
    )
    b = ann(obj("pessoa", bnd(51, 26, 150, 75)), size=size())
    return {"b.xml": b, "a.xml": a}


def test_acceptance_fixture() -> None:
    p = parse(two_files())
    assert p.names == ("pessoa", "capacete")
    assert [f.name for f in p.files] == ["a.xml", "b.xml"]  # chave ordenada
    assert [(b.file_index, b.obj_index, b.class_id) for b in p.boxes] == [
        (0, 0, 0),
        (0, 1, 1),
        (1, 0, 0),
    ]
    assert p.files[0].declared_filename == "a.jpg" and p.files[1].declared_filename is None
    assert p.files[0].declared_size == (200, 100)
    assert [f.box_count for f in p.files] == [2, 1]
    b0, b1 = p.boxes[0], p.boxes[1]
    got = convert_box(b0.xmin, b0.ymin, b0.xmax, b0.ymax, 200, 100)
    assert got is not None and got[1] is False
    assert got[0] == pytest.approx((0.5, 0.5, 0.5, 0.5), abs=1e-6)
    got = convert_box(b1.xmin, b1.ymin, b1.xmax, b1.ymax, 200, 100)
    assert got is not None and got[0] == pytest.approx((0.5, 0.5, 1.0, 1.0), abs=1e-6)


def test_key_path_components_never_touch_disk() -> None:
    p = parse({"../../etc/passwd.xml": ann(obj()), "C:\\x\\y.xml": ann(obj())})
    assert sorted(f.base_name for f in p.files) == ["passwd", "y"]


# ------------------------------------------------------------------ 14: geometria


@pytest.mark.parametrize(
    ("box", "expected", "outside"),
    [
        ((51, 26, 150, 75), (0.5, 0.5, 0.5, 0.5), False),
        ((1, 1, 200, 100), (0.5, 0.5, 1.0, 1.0), False),
        ((0, 0, 200, 100), (0.5, 0.5, 1.0, 1.0), False),  # origem 0: ajustada
        ((10, 10, 10, 10), (19 / 400, 19 / 200, 1 / 200, 1 / 100), False),
        ((100, 1, 200, 100), (299 / 400, 0.5, 101 / 200, 1.0), False),
        ((100, 1, 201, 10), (0.75, 0.05, 102 / 200, 0.1), True),  # xmax = W + 1
        ((1, 50, 100, 101), (0.25, 0.75, 0.5, 52 / 100), True),  # ymax = H + 1
    ],
)
def test_conversion_boundaries(
    box: tuple[float, float, float, float], expected: tuple[float, ...], outside: bool
) -> None:
    got = convert_box(*box, 200, 100)
    assert got is not None
    assert got[0] == pytest.approx(expected, abs=1e-9)
    assert got[1] is outside


def test_conversion_decimals_and_tolerance() -> None:
    got = convert_box(10.5, 5.0, 50.5, 25.0, 200, 100)
    assert got is not None and got[0][2] == pytest.approx(41.0 / 200)
    ok = convert_box(1, 1, 200 + 5e-7, 100, 200, 100)  # dentro da tolerância de 1e-6: não é "fora"
    assert ok is None or ok[1] is False


@pytest.mark.parametrize(
    "box",
    [
        (150, 1, 400, 50),  # centro fora de [0, 1]
        (1, 1, 201, 100),  # maior que a imagem (w > 1)
        (1, 1, 200, 101),  # h > 1
        (300, 1, 400, 10),  # inteiramente fora
    ],
)
def test_not_representable_returns_none(box: tuple[float, float, float, float]) -> None:
    assert convert_box(*box, 200, 100) is None


def test_convert_box_uses_the_function_shared_with_coco() -> None:
    from inovalytics.imports import coco_parser, common

    assert coco_parser.to_normalized is common.to_normalized
    assert mod.representable is common.representable


def box_doc(**kw: Any) -> dict[str, str]:
    return {"a.xml": ann(obj(box=bnd(**kw)))}


def test_origin_zero_adjustments_are_counted() -> None:
    p = parse({"a.xml": ann(obj(box=bnd(0, 5, 10, 10)), obj(box=bnd(5, 0, 10, 10)), obj())})
    assert p.origin_zero_adjustments == 2 and p.boxes_count == 3


def test_equal_min_max_is_valid_one_pixel() -> None:
    p = parse(box_doc(xmin=10, xmax=10, ymin=20, ymax=20))
    assert p.boxes_count == 1


@pytest.mark.parametrize(
    "kw",
    [
        {"xmax": 40},  # xmax < xmin
        {"ymax": 20},
        {"xmin": -1},
        {"ymin": -0.5},
        {"xmin": 0, "xmax": 0},  # largura zero após o ajuste
        {"ymin": 0, "ymax": 0},
    ],
)
def test_bbox_invalida(kw: dict[str, Any]) -> None:
    assert errs(box_doc(**kw))[0][:2] == ("bbox_invalida", "objeto")


@pytest.mark.parametrize(
    "tok",
    [
        "nan",
        "inf",
        "-inf",
        "1e999",
        "0x10",
        "1_0",
        "0,5",
        "５０",
        "5 0",
        "abc",
        "1" * 33,
        "--1",
        "1e",
    ],
)
@pytest.mark.parametrize("field", ["xmin", "ymin", "xmax", "ymax"])
def test_bad_numbers(tok: str, field: str) -> None:
    e = refused(body(box_doc(**{field: tok})))
    assert e.errors[0].code == "numero_invalido"
    assert (e.errors[0].file, e.errors[0].line) == ("a.xml", 1)


@pytest.mark.parametrize("field", ["xmin", "ymin", "xmax", "ymax"])
@pytest.mark.parametrize("value", [None, ""])
def test_missing_or_empty_coordinate(field: str, value: Any) -> None:
    kw = {field: value}
    if value == "":
        doc = {"a.xml": ann(obj(box=bnd(**kw).replace(f"<{field}></{field}>", f"<{field}/>")))}
    else:
        doc = box_doc(**kw)
    assert errs(doc)[0][0] == "coordenada_ausente"


@pytest.mark.parametrize("tok", ["10.5", "5e1", "+7", "1E1", "007", ".5"])
def test_decimal_numbers_accepted(tok: str) -> None:
    assert parse(box_doc(xmin=tok, xmax=200, ymin=1, ymax=50)).boxes_count == 1


def test_max_length_number_token_accepted() -> None:
    tok = "1." + "0" * 30  # 32 caracteres
    assert len(tok) == 32
    assert parse(box_doc(xmin=tok)).boxes_count == 1


def test_error_line_is_the_element_line() -> None:
    xml = "\n".join(
        ["<annotation>", "<object>", "<name>p</name>", "<bndbox>", "<xmin>1</xmin><ymin>1</ymin>"]
        + ["<xmax>x</xmax><ymax>5</ymax>", "</bndbox>", "</object>", "</annotation>"]
    )
    x = refused(body({"a.xml": xml})).errors[0]
    assert (x.code, x.line, x.kind, x.index) == ("numero_invalido", 6, "objeto", 0)


# ------------------------------------------------------------------ 15: <size>


def test_size_variants() -> None:
    p = parse({"a.xml": ann(obj(), size=size(640, 480)), "b.xml": ann(obj()), "c.xml": ann(obj())})
    assert p.files[0].declared_size == (640, 480) and p.files[0].size_line == 1
    assert p.files[1].declared_size is None
    assert p.without_size == 2


@pytest.mark.parametrize(
    ("w", "h", "code"),
    [
        (0, 100, "tamanho_divergente"),
        (200, 0, "tamanho_divergente"),
        (-5, 100, "size_invalido"),
        (200, -1, "size_invalido"),
        (2.5, 100, "size_invalido"),
        ("abc", 100, "size_invalido"),
        ("", 100, "size_invalido"),
        ("1" * 10, 100, "size_invalido"),
        ("２００", 100, "size_invalido"),
    ],
)
def test_bad_size(w: Any, h: Any, code: str) -> None:
    x = refused(body({"a.xml": ann(obj(), size=size(w, h))})).errors[0]
    assert (x.code, x.kind, x.file, x.line) == (code, "anotacao", "a.xml", 1)


def test_size_missing_child_or_duplicated() -> None:
    assert code_of({"a.xml": ann(obj(), size="<size><width>5</width></size>")}) == "size_invalido"
    assert code_of({"a.xml": ann(obj(), size="<size/>")}) == "size_invalido"
    two = "<size><width>5</width><width>6</width><height>5</height></size>"
    assert code_of({"a.xml": ann(obj(), size=two)}) == "elemento_duplicado"
    assert code_of({"a.xml": ann(obj(), size=size() + size())}) == "elemento_duplicado"


def test_nested_size_is_ignored() -> None:
    p = parse({"a.xml": ann(obj(extra=size(1, 1)), extra="<other>" + size(9, 9) + "</other>")})
    assert p.files[0].declared_size is None and p.without_size == 1


# ------------------------------------------------------------------ 16: estrutura do objeto


def test_object_structure_errors() -> None:
    cases = {
        "nome_ausente": ann(obj(name=None)),
        "bndbox_ausente": ann("<object><name>p</name><polygon><x1>1</x1></polygon></object>"),
        "elemento_duplicado": ann(obj(extra="<name>q</name>")),
    }
    for code, xml in cases.items():
        x = refused(body({"a.xml": xml})).errors[0]
        assert (x.code, x.kind, x.index) == (code, "objeto", 0), code
    two_boxes = ann(obj(extra=bnd()))
    assert code_of({"a.xml": two_boxes}) == "elemento_duplicado"
    assert (
        code_of(
            {
                "a.xml": ann(
                    obj(box=bnd().replace("<xmin>51</xmin>", "<xmin>1</xmin><xmin>2</xmin>"))
                )
            }
        )
        == "elemento_duplicado"
    )
    assert (
        code_of({"a.xml": "<annotation><object><name></name>" + bnd() + "</object></annotation>"})
        == "nome_ausente"
    )


@pytest.mark.parametrize(
    "name",
    [
        "a\u202eb",
        "a\u200bb",
        "\u200dab",
        "ab\u200d",
        "\u3164",
        "a\x07b",
        "a" * 1025,
        "a\u2028b",
        "\ufeffa",
    ],
)
def test_bad_class_names(name: str) -> None:
    xml = ann(obj(name=name))
    e = refused(body({"a.xml": xml}))
    expected = {"a\x07b": "xml_malformado", "a" * 1025: "limite_excedido"}.get(
        name, "classe_invalida"
    )
    assert e.errors[0].code == expected


def test_class_names_stripped_and_joiners_between_visible_ok() -> None:
    p = parse({"a.xml": ann(obj(name="  pessoa \n"), obj(name="a\u200db"), obj(name="é"))})
    assert p.names == ("pessoa", "a\u200db", "é")


def test_class_name_is_exact_case_sensitive() -> None:
    assert parse({"a.xml": ann(obj("Pessoa"), obj("pessoa"))}).names == ("Pessoa", "pessoa")


def test_ignored_counts_and_objects_still_imported() -> None:
    extra = (
        "<difficult>1</difficult><truncated>1</truncated><occluded>1</occluded><pose>Left</pose>"
    )
    other = "<difficult>0</difficult><truncated>yes</truncated><occluded>2</occluded>"
    other += "<pose>Unspecified</pose>"
    parts = "<part><name>head</name>" + bnd() + "</part><part><name>hand</name></part>"
    p = parse(
        {"a.xml": ann(obj(extra=extra + parts), obj(extra=other), obj(extra="<pose></pose>"))}
    )
    assert p.boxes_count == 3
    assert (p.ignored_difficult, p.ignored_truncated, p.ignored_occluded) == (1, 1, 1)
    assert (p.ignored_pose, p.ignored_parts) == (1, 2)


def test_object_inside_part_or_other_parents_never_becomes_a_box() -> None:
    inner = "<part><name>head</name>" + obj("dentro") + "</part>"
    other = "<holder>" + obj("fora") + "</holder>"
    p = parse({"a.xml": ann(obj("ok", extra=inner), other)})
    assert p.names == ("ok",) and p.boxes_count == 1 and p.ignored_parts == 1


def test_unknown_elements_and_attributes_are_tolerated() -> None:
    xml = (
        '<annotation verified="yes"><folder>x</folder><path>C:\\a.jpg</path>'
        "<source><database>d</database></source><segmented>0</segmented>"
        + obj(extra="<mystery>1</mystery>")
        + "</annotation>"
    )
    p = parse({"a.xml": xml})
    assert p.boxes_count == 1 and p.errors == ()


def test_declared_filename_only_direct_child() -> None:
    xml = (
        "<annotation><x><filename>no.jpg</filename></x><filename> a.jpg </filename>"
        + obj()
        + "</annotation>"
    )
    assert parse({"a.xml": xml}).files[0].declared_filename == "a.jpg"
    assert (
        parse({"a.xml": ann(obj(), extra="<filename></filename>")}).files[0].declared_filename
        is None
    )
    assert (
        code_of({"a.xml": ann(obj(), filename="a.jpg", extra="<filename>b.jpg</filename>")})
        == "elemento_duplicado"
    )


def test_file_without_objects_is_valid() -> None:
    p = parse({"a.xml": ann()})
    assert p.files[0].box_count == 0 and p.boxes_count == 0 and p.names == ()


# ------------------------------------------------------------------ 17: erros acumulados


def six_defects() -> dict[str, str]:
    return {
        "a.xml": ann(obj(box=bnd(xmax="x")), obj(name=None)) + "\n",
        "b.xml": "<annotation>\n<object>\n<name>p</name></object>\n<bad></annotation>",
        "c.xml": ann(obj(box=bnd(xmin=-1)), obj(name=None), size=size(0, 1)),
    }


def test_six_defects_in_three_files_ordered() -> None:
    e = refused(body(six_defects()))
    got = [(x.kind, x.file, x.line, x.code) for x in e.errors]
    assert e.total == 6 and not e.truncated
    assert got == [
        ("anotacao", "b.xml", 4, "xml_malformado"),
        ("anotacao", "c.xml", 1, "tamanho_divergente"),
        ("objeto", "a.xml", 1, "numero_invalido"),
        ("objeto", "a.xml", 1, "nome_ausente"),
        ("objeto", "c.xml", 1, "bbox_invalida"),
        ("objeto", "c.xml", 1, "nome_ausente"),
    ]


def test_malformed_xml_in_one_key_does_not_hide_the_others() -> None:
    data = {"a.xml": "<annotation>", "b.xml": ann(obj(name=None)), "c.xml": ann(obj())}
    codes = {(x.file, x.code) for x in refused(body(data)).errors}
    assert codes == {("a.xml", "xml_malformado"), ("b.xml", "nome_ausente")}


def test_object_errors_are_ordered_by_file_then_line() -> None:
    files = {f"{n}.xml": ann(obj(name=None), obj(name=None)) for n in "cab"}
    e = refused(body(files))
    assert [(x.file, x.index) for x in e.errors] == [
        ("a.xml", 0), ("a.xml", 1), ("b.xml", 0), ("b.xml", 1), ("c.xml", 0), ("c.xml", 1)
    ]  # fmt: skip


def test_max_errors_truncates_keeping_real_total() -> None:
    files = {f"f{i:03d}.xml": ann(obj(name=None)) for i in range(50)}
    e = refused(body(files), VocLimits(max_errors=5))
    assert len(e.errors) == 5 and e.total == 50 and e.truncated


def test_collect_errors_defers_instead_of_raising() -> None:
    p = parse(six_defects(), collect_errors=True)
    assert p.errors_total == 6 and len(p.errors) == 6
    with pytest.raises(ImportRejectedError):
        parse(six_defects())


def test_no_item_cost_outside_the_collector_when_full(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"shown": 0}
    real = mod.shown

    def counting(name: str) -> str:
        calls["shown"] += 1
        return real(name)

    monkeypatch.setattr(mod, "shown", counting)
    files = {f"f{i:04d}.xml": ann(obj(name=None), obj(name=None)) for i in range(400)}
    e = refused(body(files), VocLimits(max_errors=3))
    assert e.total == 800 and len(e.errors) == 3
    assert calls["shown"] <= 3


def test_messages_are_fixed_and_never_echo_user_content() -> None:
    marker = "CONTEUDO-UNICO-QWERTY"
    files = {
        f"{marker}.txt": ann(obj()),
        "a.xml": f"<annotation><{marker}></annotation>",
        "b.xml": ann(obj(name=marker + "\u202e")),
        "c.xml": ann(obj(box=bnd(xmin=marker))),
    }
    for x in refused(body(files)).errors:
        assert marker not in x.message


# ------------------------------------------------------------------ 18: chaves


@pytest.mark.parametrize(
    ("key", "code"),
    [
        ("a", "rotulo_extensao_invalida"),
        ("a.XML", "rotulo_extensao_invalida"),
        ("a.xml ", "rotulo_extensao_invalida"),
        ("a.txt", "rotulo_extensao_invalida"),
        ("", "rotulo_nome_invalido"),
        (".xml", "rotulo_nome_invalido"),
        ("dir/.xml", "rotulo_nome_invalido"),
        ("a" * 1025 + ".xml", "rotulo_nome_invalido"),
        ("a\x00b.xml", "rotulo_nome_invalido"),
        ("a\nb.xml", "rotulo_nome_invalido"),
        ("a\u2028b.xml", "rotulo_nome_invalido"),
    ],
)
def test_bad_keys(key: str, code: str) -> None:
    x = refused(body({key: ann(obj())})).errors[0]
    assert (x.code, x.kind) == (code, "anotacao")
    assert x.file is not None and len(x.file) <= 200
    assert all(ord(c) >= 32 and c not in "\u2028\u2029" for c in x.file)


def test_displayed_name_is_truncated_to_200() -> None:
    x = refused(body({"a" * 900 + ".txt": ann(obj())})).errors[0]
    assert x.file == "a" * 200


def test_key_with_valid_tail_after_path() -> None:
    p = parse({"x/y/z.xml": ann(obj()), "k\\w.xml": ann(obj())})
    assert sorted(f.base_name for f in p.files) == ["w", "z"]


# ------------------------------------------------------------------ 19: limites


def exact(ok: bytes, over: bytes, lim: VocLimits, name: str) -> None:
    parse_voc(ok, lim)
    e = refused(over, lim)
    assert (
        e.errors[0].code == "limite_excedido" and e.errors[0].message == f"limite excedido: {name}"
    )
    assert e.total == 1


def test_limit_json_bytes_before_json_loads(monkeypatch: pytest.MonkeyPatch) -> None:
    data = body({"a.xml": ann(obj())})
    exact(data, data + b" ", VocLimits(max_json_bytes=len(data)), "max_json_bytes")

    def boom(*a: object, **k: object) -> None:
        raise AssertionError("json.loads não deve ser chamado")

    monkeypatch.setattr(mod.json, "loads", boom)
    assert (
        refused(data, VocLimits(max_json_bytes=len(data) - 1)).errors[0].code == "limite_excedido"
    )


def test_limit_files_annotations_xml_bytes_markup_are_checked_before_the_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(*a: object, **k: object) -> None:
        raise AssertionError("o laço de parse não deve começar")

    monkeypatch.setattr(mod, "read_xml", boom)
    files = {f"{i}.xml": ann(obj()) for i in range(3)}
    for lim, name in (
        (VocLimits(max_images=2), "max_images"),
        (VocLimits(max_xml_bytes=len(ann(obj())) - 1), "max_xml_bytes"),
        (VocLimits(max_annotations=2), "max_annotations"),
        (VocLimits(max_total_markup=3 * ann(obj()).count("<") - 1), "max_total_markup"),
    ):
        e = refused(body(files), lim)
        assert e.errors[0].message == f"limite excedido: {name}", name


def test_limit_files_exact() -> None:
    mk = lambda n: body({f"{i}.xml": ann(obj()) for i in range(n)})  # noqa: E731
    exact(mk(3), mk(4), VocLimits(max_images=3), "max_images")


def test_limit_objects_exact() -> None:
    mk = lambda n: body({"a.xml": ann(*[obj() for _ in range(n)])})  # noqa: E731
    exact(mk(4), mk(5), VocLimits(max_annotations=4), "max_annotations")


def test_limit_objects_precount_cannot_be_bypassed_by_object_in_comment_or_part() -> None:
    xml = ann(obj(), extra="<!-- <object> <object> -->")
    assert (
        refused(body({"a.xml": xml}), VocLimits(max_annotations=2)).errors[0].code
        == "limite_excedido"
    )


def test_limit_categories_exact() -> None:
    mk = lambda n: body({"a.xml": ann(*[obj(f"c{i}") for i in range(n)])})  # noqa: E731
    exact(mk(3), mk(4), VocLimits(max_categories=3), "max_categories")


def test_limit_xml_bytes_exact_and_in_utf8_bytes() -> None:
    xml = ann(obj())
    exact(
        body({"a.xml": xml}),
        body({"a.xml": xml + " "}),
        VocLimits(max_xml_bytes=len(xml)),
        "max_xml_bytes",
    )
    emoji = ann(obj(), extra="<!--" + "😀" * 30 + "-->")  # poucos caracteres, muitos bytes
    lim = VocLimits(max_xml_bytes=len(emoji))  # passa em caracteres, estoura em bytes
    x = refused(body({"a.xml": emoji}), lim).errors[0]
    assert (x.code, x.kind, x.file) == ("limite_excedido", "anotacao", "a.xml")
    assert x.message == "limite excedido: max_xml_bytes"


def test_limit_total_markup_exact() -> None:
    xml = ann(obj())
    n = xml.count("<")
    mk = lambda k: body({f"{i}.xml": xml for i in range(k)})  # noqa: E731
    exact(mk(2), mk(3), VocLimits(max_total_markup=2 * n), "max_total_markup")


def test_limit_message_names_only_the_limit() -> None:
    e = refused(body({"SEGREDO.xml": ann(obj())}), VocLimits(max_images=0))
    assert "SEGREDO" not in e.errors[0].message


def test_limits_cost_is_linear_on_hostile_bodies() -> None:
    xml = "<annotation>" + "<a/>" * 50_000 + "</annotation>"
    data = body({f"{i}.xml": xml for i in range(40)})

    def run() -> object:
        return refused(data, VocLimits(max_total_markup=100_000))

    elapsed, result = timed(run, 2.0)
    assert isinstance(result, ImportRejectedError) and elapsed < 2.0


def test_real_sized_body_at_the_default_budget_finishes_in_time() -> None:
    xml = ann(*[obj(box=bnd(1, 1, 2, 2)) for _ in range(2400)])
    n = LIM.max_total_markup // xml.count("<")
    data = body({f"f{i:04d}.xml": xml for i in range(n)})

    def run() -> ParsedVoc:
        return parse_voc(data, LIM)

    elapsed, result = timed(run, 8.0)
    assert isinstance(result, ParsedVoc) and result.boxes_count == n * 2400
    assert elapsed < 4.0  # medido ~1 s; margem >= 2x
