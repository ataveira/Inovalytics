"""F1-06a critérios 1 a 15: parser COCO puro (sem banco, sem rede). Fixtures montadas em código."""

import ast
import contextlib
import json
import random
import time
from pathlib import Path
from typing import Any

import pytest

from inovalytics.imports.coco_parser import CocoLimits, ParsedCoco, parse_coco
from inovalytics.imports.errors import CocoImportError

LIM = CocoLimits()


def doc(**over: Any) -> dict[str, Any]:
    d: dict[str, Any] = {
        "images": [
            {"id": 1, "file_name": "a.jpg", "width": 200, "height": 100},
            {"id": 2, "file_name": "b.jpg", "width": 100, "height": 100},
        ],
        "categories": [{"id": 5, "name": "gato"}, {"id": 7, "name": "cão"}],
        "annotations": [
            {"id": 1, "image_id": 1, "category_id": 5, "bbox": [20, 10, 40, 30]},
            {"id": 2, "image_id": 1, "category_id": 7, "bbox": [0, 0, 200, 100]},
            {"id": 3, "image_id": 2, "category_id": 5, "bbox": [1.5, 2.5, 10.25, 20.5]},
        ],
    }
    d.update(over)
    return d


def enc(d: Any) -> bytes:
    return json.dumps(d).encode()


def parse(d: Any, lim: CocoLimits = LIM) -> ParsedCoco:
    return parse_coco(enc(d), lim)


def codes(d: Any, lim: CocoLimits = LIM) -> list[str]:
    with pytest.raises(CocoImportError) as ei:
        parse(d, lim)
    return [e.code for e in ei.value.errors]


def raw_codes(data: bytes, lim: CocoLimits = LIM) -> list[str]:
    with pytest.raises(CocoImportError) as ei:
        parse_coco(data, lim)
    return [e.code for e in ei.value.errors]


def with_ann(**ann: Any) -> dict[str, Any]:
    base = {"image_id": 1, "category_id": 5, "bbox": [1, 1, 10, 10]}
    base.update(ann)
    return doc(annotations=[base])


# 1, 2
def test_minimal_valid_and_deterministic() -> None:
    p = parse(doc())
    assert [i.id for i in p.images] == [1, 2]
    assert [c.name for c in p.categories] == ["gato", "cão"]
    assert [(b.annotation_id, b.x, b.y, b.w, b.h) for b in p.boxes] == [
        (1, 20, 10, 40, 30),
        (2, 0, 0, 200, 100),
        (3, 1.5, 2.5, 10.25, 20.5),
    ]
    assert parse(doc()) == p
    assert p.segmentations_ignored == 0 and p.warnings == () and p.crowd_ignored == ()


def test_errors_sorted_and_capped() -> None:
    d = doc(annotations=[{"image_id": 9, "category_id": 5, "bbox": [1, 1, 1, 1]}] * 3)
    with pytest.raises(CocoImportError) as ei:
        parse(d, CocoLimits(max_errors=2))
    e = ei.value
    assert e.total == 3 and len(e.errors) == 2 and e.truncated
    keys = [(x.kind, x.index) for x in e.errors]
    assert keys == sorted(keys)


# 3
def test_coordinates_stay_in_pixels_and_decimals_accepted() -> None:
    from inovalytics.imports.coco_parser import to_normalized

    assert to_normalized(20, 10, 40, 30, 200, 100) == pytest.approx((0.2, 0.25, 0.2, 0.3), abs=1e-9)
    assert to_normalized(0, 0, 200, 100, 200, 100) == (0.5, 0.5, 1.0, 1.0)
    assert parse(with_ann(bbox=[0.5, 0.25, 3.75, 4.5])).boxes[0].w == 3.75


# 4
@pytest.mark.parametrize(
    "data",
    [
        b"\xff\xfe\x00{",
        b"\xc3(",
        b'{"images": [',
        b"[]",
        b"123",
        b'{"images": NaN}',
        b'{"images": Infinity}',
        b'{"images": -Infinity}',
        b'{"a": 1, "a": 2}',
        b'{"x": {"b": 1, "b": 1}}',
        b'{"a": ' + b"1" * 4301 + b"}",
        b'{"a": 1e999}',
        b"",
        b"   ",
    ],
)
def test_invalid_json(data: bytes) -> None:
    assert raw_codes(data) == ["json_invalido"]


def test_bom_accepted() -> None:
    assert len(parse_coco(b"\xef\xbb\xbf" + enc(doc()), LIM).boxes) == 3


def test_int_with_4300_digits_is_still_parsed_as_json() -> None:
    big = b'{"images": [], "annotations": [], "categories": [], "x": ' + b"1" * 4300 + b"}"
    assert parse_coco(big, LIM).boxes == ()


# 5a
def test_byte_limit_without_json_loads(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a: object, **k: object) -> None:
        raise AssertionError("json.loads não deve ser chamado")

    monkeypatch.setattr(json, "loads", boom)
    data = b"x" * 101
    assert raw_codes(data, CocoLimits(max_json_bytes=100)) == ["limite_excedido"]


def test_byte_limit_exact_passes() -> None:
    data = enc(doc())
    assert len(parse_coco(data, CocoLimits(max_json_bytes=len(data))).boxes) == 3
    assert raw_codes(data, CocoLimits(max_json_bytes=len(data) - 1)) == ["limite_excedido"]


# 5b
def test_depth_dos_is_fast() -> None:
    t = time.monotonic()
    assert raw_codes(b"[" * 100_000) == ["limite_excedido"]
    assert raw_codes(b'{"a":' * 100_000) == ["limite_excedido"]
    assert time.monotonic() - t < 1


def test_depth_exact_and_brackets_in_strings() -> None:
    lim = CocoLimits(max_depth=4)
    ok = b'{"images":[],"annotations":[],"categories":[],"k":[[[]]],"s":"[[[[[[[\\"[[["}'
    assert parse_coco(ok, lim).images == ()
    deeper = b'{"images":[],"annotations":[],"categories":[],"k":[[[[]]]]}'
    assert raw_codes(deeper, lim) == ["limite_excedido"]


def test_default_depth_handles_polygon_segmentation() -> None:
    d = with_ann(segmentation=[[1, 2, 3, 4, 5, 6]], keypoints=[1, 2, 3])
    assert len(parse(d).boxes) == 1


# 5c
@pytest.mark.parametrize(
    ("field", "name"),
    [
        ("images", "max_images"),
        ("annotations", "max_annotations"),
        ("categories", "max_categories"),
    ],
)
def test_item_limits(field: str, name: str) -> None:
    d: dict[str, Any] = {"images": [], "annotations": [], "categories": []}
    d[field] = [{}] * 3
    with pytest.raises(CocoImportError) as ei:
        parse(d, CocoLimits(**{name: 2}))
    assert [(e.code, e.message) for e in ei.value.errors] == [
        ("limite_excedido", f"limite excedido: {name}")
    ]
    d[field] = [{}] * 2  # no limite exato o limite não dispara (falha por conteúdo, não por limite)
    assert "limite_excedido" not in codes(d, CocoLimits(**{name: 2}))


# 5d
def test_long_names_are_item_errors() -> None:
    long = "x" * 1025
    d = doc(images=[{"id": 1, "file_name": long, "width": 1, "height": 1}], annotations=[])
    assert codes(d) == ["imagem_file_name_invalido"]
    d = doc(categories=[{"id": 1, "name": long}], annotations=[])
    assert codes(d) == ["categoria_nome_invalido"]
    ok = doc(categories=[{"id": 1, "name": "x" * 1024}], annotations=[])
    assert parse(ok).categories[0].name == "x" * 1024


# 6
@pytest.mark.parametrize("key", ["images", "annotations", "categories"])
@pytest.mark.parametrize("bad", ["missing", None, {}, "x", [1], [[]]])
def test_structure(key: str, bad: Any) -> None:
    d = doc()
    if bad == "missing":
        del d[key]
    else:
        d[key] = bad
    assert "estrutura_invalida" in codes(d)


def test_extra_keys_and_ignored_fields_ok() -> None:
    d = doc(info={"x": 1}, licenses=[{"id": 1}], extra=[1, 2])
    d["categories"][0]["supercategory"] = "animal"
    d["images"][0].update(coco_url="http://127.0.0.1:9/", flickr_url="http://x/", license=3)
    d["annotations"][0].update(area=1.0, attributes={"a": 1}, segmentation={"counts": "abc"})
    assert len(parse(d).boxes) == 3


# 7
@pytest.mark.parametrize(
    ("patch", "code"),
    [
        ({"id": True}, "imagem_id_invalido"),
        ({"id": -1}, "imagem_id_invalido"),
        ({"id": 1.0}, "imagem_id_invalido"),
        ({"id": "1"}, "imagem_id_invalido"),
        ({"file_name": "   "}, "imagem_file_name_invalido"),
        ({"file_name": 3}, "imagem_file_name_invalido"),
        ({"file_name": None}, "imagem_file_name_invalido"),
        ({"width": 0}, "imagem_dimensao_invalida"),
        ({"width": True}, "imagem_dimensao_invalida"),
        ({"width": 1.5}, "imagem_dimensao_invalida"),
        ({"width": "10"}, "imagem_dimensao_invalida"),
        ({"height": None}, "imagem_dimensao_invalida"),
        ({"height": 16385}, "imagem_dimensao_invalida"),
    ],
)
def test_image_fields(patch: dict[str, Any], code: str) -> None:
    img = {"id": 1, "file_name": "a.jpg", "width": 10, "height": 10, **patch}
    assert codes(doc(images=[img], annotations=[])) == [code]


def test_image_dimension_max_and_dup_ids_and_id_zero() -> None:
    ok = {"id": 0, "file_name": "a", "width": 16384, "height": 16384}
    assert parse(doc(images=[ok], annotations=[])).images[0].id == 0
    dup = [ok, {**ok, "file_name": "b"}]
    assert codes(doc(images=dup, annotations=[])) == ["imagem_id_duplicado"]


# 8
def test_categories() -> None:
    assert codes(
        doc(categories=[{"id": 1, "name": "a"}, {"id": 1, "name": "b"}], annotations=[])
    ) == ["categoria_id_duplicado"]
    cats = [{"id": 1, "name": "a"}, {"id": 2, "name": " a "}]
    assert codes(doc(categories=cats, annotations=[])) == ["categoria_nome_duplicado"]
    cats = [{"id": 1, "name": "Gato"}, {"id": 2, "name": "gato"}]
    assert len(parse(doc(categories=cats, annotations=[])).categories) == 2
    assert (
        parse(doc(categories=[{"id": 1, "name": " x "}], annotations=[])).categories[0].name == "x"
    )
    assert codes(doc(categories=[{"id": "1", "name": "a"}], annotations=[])) == [
        "categoria_id_invalido"
    ]
    assert codes(doc(categories=[{"id": 1, "name": ""}], annotations=[])) == [
        "categoria_nome_invalido"
    ]


# 9
def test_annotation_references_and_ids() -> None:
    d = doc(
        annotations=[
            {"id": 10, "image_id": 99, "category_id": 5, "bbox": [1, 1, 1, 1]},
            {"id": 11, "image_id": 1, "category_id": 99, "bbox": [1, 1, 1, 1]},
        ]
    )
    with pytest.raises(CocoImportError) as ei:
        parse(d)
    got = [(e.code, e.id, e.index) for e in ei.value.errors]
    assert got == [
        ("anotacao_imagem_inexistente", 10, 0),
        ("anotacao_categoria_inexistente", 11, 1),
    ]
    a = {"image_id": 1, "category_id": 5, "bbox": [1, 1, 1, 1]}
    assert codes(doc(annotations=[{**a, "id": 1}, {**a, "id": 1}])) == ["anotacao_id_duplicado"]
    assert codes(doc(annotations=[{**a, "id": "x"}])) == ["anotacao_id_invalido"]
    p = parse(doc(annotations=[a, a]))
    assert [b.annotation_id for b in p.boxes] == [None, None]
    assert [b.index for b in p.boxes] == [0, 1]
    assert codes(with_ann(image_id=True)) == ["anotacao_imagem_inexistente"]
    assert codes(with_ann(category_id=None)) == ["anotacao_categoria_inexistente"]


# 10
def test_bbox_absent() -> None:
    d = with_ann()
    del d["annotations"][0]["bbox"]
    assert codes(d) == ["bbox_ausente"]


@pytest.mark.parametrize(
    "bbox",
    [
        None,
        "1,2,3,4",
        [1, 2, 3],
        [1, 2, 3, 4, 5],
        [1, 2, 3, "4"],
        [1, 2, 3, True],
        [1, 2, 3, None],
        [1, 2, 0, 4],
        [1, 2, 3, 0],
        [1, 2, -3, 4],
        [1, 2, 3, -4],
        [1e10, 2, 3, 4],
        [1, -1e10, 3, 4],
        [1, 2, 1e9 + 1, 4],
        {"x": 1},
    ],
)
def test_bbox_invalid(bbox: Any) -> None:
    assert codes(with_ann(bbox=bbox)) == ["bbox_invalida"]


# 11
@pytest.mark.parametrize(
    ("bbox", "outcome"),
    [
        ([0, 0, 200, 100], "ok"),
        ([-1e-10, 0, 200, 100], "ok"),  # dentro da tolerância
        ([0, 0, 200 + 1e-10, 100], "bad"),  # w > W
        ([-1, 0, 200, 100], "warn"),
        ([1, 0, 200, 100], "warn"),
        ([0, -1, 10, 10], "warn"),
        ([0, 95, 10, 10], "warn"),
        ([-5, 10, 10, 10], "warn"),  # centro exatamente em 0, mas a caixa sai pela esquerda
        ([190, 90, 20, 20], "warn"),
        ([-5.0001, 10, 10, 10], "bad"),
        ([195, 10, 10, 10], "warn"),  # centro 200 => 1.0
        ([195.0001, 10, 10, 10], "bad"),
        ([10, -5, 10, 10], "warn"),
        ([10, -5.0001, 10, 10], "bad"),
        ([10, 95, 10, 10], "warn"),
        ([10, 95.0001, 10, 10], "bad"),
        ([0, 0, 201, 100], "bad"),
        ([0, 0, 200, 101], "bad"),
    ],
)
def test_box_outside_image(bbox: list[float], outcome: str) -> None:
    d = doc(
        images=[{"id": 1, "file_name": "a", "width": 200, "height": 100}],
        annotations=[{"id": 4, "image_id": 1, "category_id": 5, "bbox": bbox}],
    )
    if outcome == "bad":
        assert codes(d) == ["caixa_nao_representavel"]
        return
    p = parse(d)
    assert len(p.boxes) == 1
    assert (p.boxes[0].x, p.boxes[0].y, p.boxes[0].w, p.boxes[0].h) == tuple(float(v) for v in bbox)
    if outcome == "warn":
        assert [w.code for w in p.warnings] == ["caixa_fora_da_imagem"]
        assert p.warnings[0].id == 4 and p.warnings[0].index == 0 and p.warnings_total == 1
    else:
        assert p.warnings == ()


# 12
def test_segmentations_and_crowd() -> None:
    a = {"image_id": 1, "category_id": 5, "bbox": [1, 1, 5, 5]}
    d = doc(
        annotations=[
            {**a, "id": 1, "segmentation": [[1, 2, 3, 4, 5, 6]]},
            {**a, "id": 2, "segmentation": {"counts": [1, 2], "size": [10, 10]}},
            {**a, "id": 3, "segmentation": []},
            {**a, "id": 4},
            {**a, "id": 5, "iscrowd": 1, "segmentation": {"counts": "x", "size": [1, 1]}},
            {"id": 6, "image_id": 2, "category_id": 7, "iscrowd": 1},  # sem bbox: ok
            {**a, "id": 7, "iscrowd": 0},
            {**a, "id": 8, "iscrowd": False},
        ]
    )
    p = parse(d)
    assert p.segmentations_ignored == 2
    assert [(c.id, c.index) for c in p.crowd_ignored] == [(5, 4), (6, 5)]
    assert [b.annotation_id for b in p.boxes] == [1, 2, 3, 4, 7, 8]


def test_crowd_still_checks_references() -> None:
    d = doc(annotations=[{"image_id": 99, "category_id": 99, "iscrowd": 1}])
    assert codes(d) == ["anotacao_imagem_inexistente", "anotacao_categoria_inexistente"]


@pytest.mark.parametrize("v", [2, "1", True, None, 1.0, [1], -1])
def test_iscrowd_invalid(v: Any) -> None:
    assert codes(with_ann(iscrowd=v)) == ["iscrowd_invalido"]


# 13
def test_all_errors_accumulated() -> None:
    d = {
        "images": [{"id": 1, "file_name": "", "width": 10, "height": 10}],
        "categories": [{"id": 1, "name": "a"}, {"id": 1, "name": "b"}],
        "annotations": [
            {"image_id": 7, "category_id": 1, "bbox": [1, 1, 1, 1]},
            {"image_id": 1, "category_id": 1, "bbox": [1, 1, 0, 1]},
            {"image_id": 1, "category_id": 1},
        ],
    }
    with pytest.raises(CocoImportError) as ei:
        parse(d)
    assert sorted(e.code for e in ei.value.errors) == sorted(
        [
            "imagem_file_name_invalido",
            "categoria_id_duplicado",
            "anotacao_imagem_inexistente",
            "bbox_invalida",
            "bbox_ausente",
        ]
    )
    assert ei.value.total == 5 and not ei.value.truncated


def test_cap_keeps_real_total_with_many_errors() -> None:
    anns = [{"image_id": 1, "category_id": 5} for _ in range(1000)]
    with pytest.raises(CocoImportError) as ei:
        parse(doc(annotations=anns), CocoLimits(max_errors=10))
    assert ei.value.total == 1000 and len(ei.value.errors) == 10 and ei.value.truncated


# 14
@pytest.mark.usefixtures("no_network")
def test_urls_are_never_fetched() -> None:
    d = doc()
    d["images"][0]["coco_url"] = "http://127.0.0.1:9/"
    d["images"][0]["flickr_url"] = "http://127.0.0.1:9/"
    assert len(parse(d).images) == 2


# 1 (robustez)
def test_random_inputs_never_escape() -> None:
    rng = random.Random(1234)
    seeds = [enc(doc()), b'{"images":[{"id":1}],"annotations":[{"bbox":[1,2]}],"categories":[]}']
    for n in range(400):
        if n % 2:
            data = bytes(rng.randrange(256) for _ in range(rng.randrange(0, 80)))
        else:
            b = bytearray(rng.choice(seeds))
            for _ in range(rng.randrange(1, 6)):
                b[rng.randrange(len(b))] = rng.randrange(256)
            data = bytes(b)
        with contextlib.suppress(CocoImportError):
            parse_coco(data, LIM)


# 15
def test_parser_imports_only_stdlib(repo_root: Path) -> None:
    path = repo_root / "backend/src/inovalytics/imports/coco_parser.py"
    banned = {
        "pycocotools", "numpy", "PIL", "ultralytics", "socket", "urllib", "http", "requests",
        "subprocess", "asyncpg", "boto3", "fastapi", "starlette", "os", "pathlib", "inovalytics.db",
    }  # fmt: skip
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        mods: list[str] = []
        if isinstance(node, ast.Import):
            mods = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            mods = [node.module or ""]
        for m in mods:
            assert m not in banned and m.split(".")[0] not in banned - {"inovalytics.db"}, m
            assert m.startswith("inovalytics.imports") or m.split(".")[0] in {
                "__future__", "json", "math", "re", "dataclasses", "typing",
            }, m  # fmt: skip


def test_no_data_files_in_backend(repo_root: Path) -> None:
    for p in (repo_root / "backend").rglob("*.json"):
        assert ".venv" in p.parts or "node_modules" in p.parts, p
