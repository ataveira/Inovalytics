"""F1-06 testes adversariais (JSON hostil, limites, idempotência, rollback, 413, segredos).

Complementa test_coco_parser/service/api_coco_import sem alterá-los. Fixtures montadas em código.
"""

import contextlib
import json
import logging
import time
from typing import Any

import pytest

from inovalytics.db.repository import ImportImage
from inovalytics.imports.coco_parser import CocoLimits, ParsedCoco, parse_coco
from inovalytics.imports.coco_service import import_coco
from inovalytics.imports.errors import CocoImportError
from test_api_coco_import import JSON_H, JSON_SENTINEL, SECRETS, URL, Env, _no_leak, post
from test_api_coco_import import doc as api_doc
from test_coco_parser import LIM, codes, doc, enc, parse, raw_codes, with_ann
from test_coco_service import FakeRepo, ann, coco, errors_of, run


def raw_doc(images: str = "[]", cats: str = "[]", anns: str = "[]") -> bytes:
    return f'{{"images": {images}, "categories": {cats}, "annotations": {anns}}}'.encode()


IMG = '{"id": 1, "file_name": "a.jpg", "width": 200, "height": 100}'
CAT = '{"id": 5, "name": "gato"}'


# ---------------------------------------------------------------- JSON hostil: sintaxe


@pytest.mark.parametrize(
    "data",
    [
        raw_doc(anns='[{"id": 1, "image_id": 1, "category_id": 5, "bbox": [NaN, 1, 1, 1]}]'),
        raw_doc(anns='[{"bbox": [1, 1, Infinity, 1]}]'),
        raw_doc(anns='[{"bbox": [1, 1, -Infinity, 1]}]'),
        raw_doc(anns='[{"bbox": [1, 1, 1e400, 1]}]'),  # overflow para inf
        raw_doc(anns='[{"bbox": [1, 1, -1e400, 1]}]'),
        raw_doc(images='[{"id": NaN}]'),
        b'{"images": [], "categories": [], "annotations": [], "info": {"x": [NaN]}}',
        # chave duplicada em níveis profundos, em itens de lista, com escape unicode
        raw_doc(images='[{"id": 1, "id": 2, "file_name": "a", "width": 1, "height": 1}]'),
        raw_doc(cats='[{"id": 1, "name": "a", "name": "b"}]'),
        raw_doc(anns='[{"bbox": [1, 1, 1, 1], "\\u0062box": [2, 2, 2, 2]}]'),
        b'{"images": [], "images": [], "categories": [], "annotations": []}',
        b'{"info": {"a": {"b": {"c": 1, "c": 1}}}}',
        b'{"a":1,\n"a":1}',
        b"\xef\xbb\xbf\xef\xbb\xbf{}",  # BOM duplo
        b'{"images": [], "categories": [], "annotations": []} {"x": 1}',  # lixo depois
        b'{"images": [], "categories": [], "annotations": []}\x00',
        b"{'images': []}",
        b'{"images": [],}',
        b'{"a": "\xed\xa0\x80"}',  # substituto UTF-8 codificado (inválido)
        b"\x00" * 10,
        b'{"a": ' + b"9" * 5000 + b"}",  # inteiro gigante em qualquer posição
        raw_doc(anns='[{"bbox": [' + "9" * 5000 + ", 1, 1, 1]}]"),
        b'{"a": -' + b"9" * 4301 + b"}",
    ],
)
def test_hostile_json_is_json_invalido_without_traceback(data: bytes) -> None:
    with pytest.raises(CocoImportError) as ei:
        parse_coco(data, LIM)
    assert [e.code for e in ei.value.errors] == ["json_invalido"]
    assert ei.value.total == 1
    for e in ei.value.errors:
        assert "Traceback" not in e.message and len(e.message) < 200


def test_lone_surrogate_escape_in_json_is_handled_without_escaping_exception() -> None:
    # "\ud800" é JSON válido para Python; o resultado precisa ser aceito ou erro de domínio.
    data = raw_doc(images='[{"id": 1, "file_name": "\\ud800", "width": 1, "height": 1}]')
    with contextlib.suppress(CocoImportError):
        parse_coco(data, LIM)


def test_deep_nesting_variants_are_fast_and_clean() -> None:
    variants = [
        b"[" * 100_000,
        b"]" * 100_000,
        b'{"a":' * 50_000,
        b'{"images": ' + b"[" * 100_000 + b"]" * 100_000 + b"}",
        b'{"a": "\\\\"' + b', "b": ' + b"[" * 200 + b"]" * 200 + b"}",  # barra antes de aspas
        b'{"a": "\\"' + b"[" * 200 + b'"}',  # aspas escapadas: colchetes ainda dentro da string
    ]
    t0 = time.perf_counter()
    for v in variants[:5]:
        with pytest.raises(CocoImportError):
            parse_coco(v, CocoLimits())
    assert time.perf_counter() - t0 < 2.0
    # a última é string com 200 colchetes: não conta profundidade
    with pytest.raises(CocoImportError) as ei:
        parse_coco(variants[5], CocoLimits())
    assert ei.value.errors[0].code != "limite_excedido"


def test_depth_counts_objects_and_arrays_and_keys_with_brackets_are_free() -> None:
    lim = CocoLimits(max_depth=4)
    ok = b'{"images": [], "categories": [], "annotations": [], "[[[[[[[[": 1, "info": [[1]]}'
    parse_coco(ok, lim)  # profundidade 3
    deep = b'{"images": [], "categories": [], "annotations": [], "info": [[[[1]]]]}'  # 5 níveis
    with pytest.raises(CocoImportError) as ei:
        parse_coco(deep, lim)
    assert ei.value.errors[0].code == "limite_excedido"


# ---------------------------------------------------------------- inteiros gigantes e ids


def test_giant_ints_in_ids_are_bounded_in_the_report() -> None:
    big = 10**3000
    d = doc(
        images=[
            {"id": big, "file_name": "a", "width": 1, "height": 1},
            {"id": big, "file_name": "b", "width": 1, "height": 1},
        ],
        annotations=[],
    )
    with pytest.raises(CocoImportError) as ei:
        parse(d)
    e = ei.value.errors[0]
    assert e.code == "imagem_id_duplicado"
    assert e.id is None  # não repete 3000 dígitos no relatório
    assert len(json.dumps([x.message for x in ei.value.errors])) < 500


def test_giant_int_in_bbox_and_dimensions_is_rejected_cleanly() -> None:
    assert codes(with_ann(bbox=[10**300, 0, 1, 1])) == ["bbox_invalida"]
    assert codes(with_ann(bbox=[0, 0, 10**4000, 1])) == ["bbox_invalida"]
    bad = doc(images=[{"id": 1, "file_name": "a", "width": 10**400, "height": 1}], annotations=[])
    assert codes(bad) == ["imagem_dimensao_invalida"]


def test_id_boundaries_for_int64_are_accepted_in_the_file() -> None:
    lim = 2**63
    d = doc(
        images=[{"id": lim, "file_name": "a", "width": 10, "height": 10}],
        categories=[{"id": -lim, "name": "x"}],
        annotations=[{"id": lim * 2, "image_id": lim, "category_id": -lim, "bbox": [0, 0, 5, 5]}],
    )
    p = parse(d)
    assert len(p.boxes) == 1  # ids de arquivo fora de int64 são aceitos (nunca vão ao banco)


@pytest.mark.parametrize(
    ("key", "items", "code"),
    [
        (
            "images",
            [
                {"id": 1, "file_name": "a", "width": 1, "height": 1},
                {"id": 1, "file_name": "b", "width": 1, "height": 1},
                {"id": 1, "file_name": "c", "width": 1, "height": 1},
            ],
            "imagem_id_duplicado",
        ),
        (
            "categories",
            [{"id": 1, "name": "a"}, {"id": 1, "name": "b"}, {"id": 1, "name": "c"}],
            "categoria_id_duplicado",
        ),
    ],
)
def test_repeated_ids_report_every_repetition(key: str, items: list[Any], code: str) -> None:
    d = doc(**{key: items}, annotations=[])
    got = codes(d)
    assert got.count(code) == 2


def test_repeated_annotation_ids_reported_for_each_repetition() -> None:
    anns = [{"id": 7, "image_id": 1, "category_id": 5, "bbox": [0, 0, 5, 5]}] * 3
    assert codes(doc(annotations=anns)) == ["anotacao_id_duplicado"] * 2


def test_float_and_bool_ids_are_not_integers() -> None:
    for bad in (1.0, True, "1", None, [1], {"a": 1}):
        d = doc(images=[{"id": bad, "file_name": "a", "width": 1, "height": 1}], annotations=[])
        assert codes(d) == ["imagem_id_invalido"], bad
    d = doc(categories=[{"id": 1.0, "name": "a"}], annotations=[])
    assert codes(d) == ["categoria_id_invalido"]


# ---------------------------------------------------------------- bbox e dimensões


@pytest.mark.parametrize(
    "bbox",
    [
        [1, 2, -0.0, 4],
        [1, 2, 3, -0.0],
        [1, 2, 0.0, 0.0],
        [1, 2, 1e-400, 3],  # underflow para 0.0
        [1, 2, -1, -1],  # ambos negativos: área positiva, ainda inválida
        [-1e9 - 1, 0, 1, 1],
        [[1], 2, 3, 4],
        [{"a": 1}, 2, 3, 4],
        [],
        [None, None, None, None],
    ],
)
def test_bbox_zero_negative_and_garbage_never_reach_the_database(bbox: Any) -> None:
    assert codes(with_ann(bbox=bbox)) == ["bbox_invalida"]


def test_negative_origin_box_is_warning_not_error_and_negative_zero_is_clean() -> None:
    p = parse(with_ann(bbox=[-0.0, 0, 10, 10]))
    assert p.warnings == () and len(p.boxes) == 1
    p = parse(with_ann(bbox=[-3, 0, 10, 10]))  # centro dentro, mas sai pela esquerda
    assert [w.code for w in p.warnings] == ["caixa_fora_da_imagem"]


def test_denormal_sized_box_is_rejected_cleanly_or_kept_positive() -> None:
    try:
        p = parse(with_ann(bbox=[0, 0, 5e-324, 5e-324]))
    except CocoImportError as exc:
        assert [e.code for e in exc.errors] == ["caixa_nao_representavel"]
    else:  # se aceita, a conversão não pode zerar a largura (CHECK width > 0 do banco)
        assert all(b.w / 200 > 0 and b.h / 100 > 0 for b in p.boxes)


@pytest.mark.parametrize("wh", [(0, 10), (10, 0), (-1, 10), (10, -1), (0, 0)])
def test_zero_or_negative_image_dimensions(wh: tuple[int, int]) -> None:
    d = doc(
        images=[{"id": 1, "file_name": "a", "width": wh[0], "height": wh[1]}],
        annotations=[],
    )
    assert "imagem_dimensao_invalida" in codes(d)


def test_zero_dimension_image_does_not_cause_division_by_zero_in_annotations() -> None:
    d = doc(
        images=[{"id": 1, "file_name": "a", "width": 0, "height": 0}],
        annotations=[{"id": 1, "image_id": 1, "category_id": 5, "bbox": [0, 0, 1, 1]}],
    )
    got = codes(d)  # nenhuma ZeroDivisionError escapa
    assert "imagem_dimensao_invalida" in got


def test_declared_and_stored_dimensions_diverging_in_one_axis_only() -> None:
    repo = FakeRepo(images=[ImportImage(10, "a.jpg", 200, 101)])
    err = errors_of(repo, coco())
    assert [e.code for e in err.errors] == ["dimensoes_divergentes"]
    assert repo.writes() == []
    repo = FakeRepo(images=[ImportImage(10, "a.jpg", 201, 100)])
    assert [e.code for e in errors_of(repo, coco()).errors] == ["dimensoes_divergentes"]


# ---------------------------------------------------------------- file_name hostil


@pytest.mark.parametrize(
    ("declared", "stored"),
    [
        ("../../etc/passwd", "passwd"),
        ("..\\..\\windows\\system32\\a.jpg", "a.jpg"),
        ("/abs/path/a.jpg", "a.jpg"),
        ("train/../a.jpg", "a.jpg"),
        ("a/b/c/d/e.jpg", "e.jpg"),
        ("C:/x/y.jpg", "y.jpg"),
        ("fóto ção.jpg", "fóto ção.jpg"),
        ("日本語.jpg", "日本語.jpg"),
        ("train//a.jpg", "a.jpg"),
    ],
)
def test_hostile_file_names_match_only_by_last_component(declared: str, stored: str) -> None:
    repo = FakeRepo(images=[ImportImage(10, stored, 200, 100)])
    parsed = coco(images=[{"id": 1, "file_name": declared, "width": 200, "height": 100}])
    rep = run(import_coco(repo, 1, parsed))
    assert rep.status == "importado"
    assert [a.image_id for a in repo.annotations] == [10]


@pytest.mark.parametrize("declared", ["..", ".", "a/", "../", "\\", "/", "a/.."])
def test_file_names_without_a_usable_component_never_match_or_crash(declared: str) -> None:
    repo = FakeRepo(images=[ImportImage(10, "a.jpg", 200, 100)])
    parsed = coco(images=[{"id": 1, "file_name": declared, "width": 200, "height": 100}])
    err = errors_of(repo, parsed)
    assert err.errors and repo.writes() == []
    assert all("\x00" not in e.message and "\n" not in e.message for e in err.errors)


def test_unicode_normalization_forms_match_like_the_upload_sanitizer() -> None:
    """`sanitize_file_name` da F1-05 normaliza para NFC; o importador herda esse casamento."""
    nfc, nfd = "\u00e9.jpg", "e\u0301.jpg"
    repo = FakeRepo(images=[ImportImage(10, nfc, 200, 100)])
    parsed = coco(images=[{"id": 1, "file_name": nfd, "width": 200, "height": 100}])
    assert run(import_coco(repo, 1, parsed)).status == "importado"


def test_control_characters_and_bidi_are_stripped_from_error_messages() -> None:
    evil = "a\x00\x1b[31m\r\nb\u202e\u2066.jpg"
    repo = FakeRepo(images=[])
    parsed = coco(images=[{"id": 1, "file_name": evil, "width": 200, "height": 100}])
    msg = errors_of(repo, parsed).errors[0].message
    assert "\x00" not in msg and "\x1b" not in msg and "\r" not in msg and "\n" not in msg


def test_two_different_declared_names_reducing_to_the_same_one() -> None:
    repo = FakeRepo()
    imgs = [
        {"id": 1, "file_name": "x/a.jpg", "width": 200, "height": 100},
        {"id": 2, "file_name": "y/a.jpg", "width": 200, "height": 100},
    ]
    parsed = coco(images=imgs, anns=[ann(1, image=1), ann(2, image=2)])
    assert "imagem_nome_duplicado" in [e.code for e in errors_of(repo, parsed).errors]
    assert repo.writes() == []


# ---------------------------------------------------------------- categorias hostis


@pytest.mark.parametrize("name", ["a\x00b", "\x00", "\ud800", "ok\udfffx"])
def test_category_names_the_database_cannot_store_are_rejected_in_the_parser(name: str) -> None:
    d = doc(categories=[{"id": 5, "name": name}], annotations=[])
    assert codes(d) == ["categoria_nome_invalido"]


def test_category_name_surrogate_through_raw_json_escape() -> None:
    data = raw_doc(cats='[{"id": 5, "name": "\\ud800"}]')
    assert raw_codes(data) == ["categoria_nome_invalido"]


def test_category_names_with_unicode_and_whitespace_variants() -> None:
    p = parse(
        doc(categories=[{"id": 1, "name": "  cão  "}, {"id": 2, "name": "CÃO"}], annotations=[])
    )
    assert [c.name for c in p.categories] == ["cão", "CÃO"]
    assert codes(doc(categories=[{"id": 1, "name": "\u3000"}], annotations=[])) == [
        "categoria_nome_invalido"
    ]


def test_category_name_exactly_1024_chars_ok_and_1025_rejected() -> None:
    assert parse(doc(categories=[{"id": 1, "name": "x" * 1024}], annotations=[])).categories
    assert codes(doc(categories=[{"id": 1, "name": "x" * 1025}], annotations=[])) == [
        "categoria_nome_invalido"
    ]


# ---------------------------------------------------------------- iscrowd


@pytest.mark.parametrize("bad", [1.0, 0.0, 2, -1, "0", "false", True, None, [1], {}, 1e0])
def test_iscrowd_non_integer_or_out_of_range_is_error(bad: Any) -> None:
    assert codes(with_ann(iscrowd=bad)) == ["iscrowd_invalido"]


def test_crowd_annotation_with_garbage_bbox_or_segmentation_is_ignored_not_error() -> None:
    for extra in ({}, {"bbox": "lixo"}, {"bbox": [1, 2]}, {"bbox": None}, {"segmentation": "x"}):
        a = {"id": 1, "image_id": 1, "category_id": 5, "iscrowd": 1, **extra}
        p = parse(doc(annotations=[a]))
        assert p.boxes == () and len(p.crowd_ignored) == 1, extra


def test_crowd_does_not_count_as_segmentation_and_false_is_normal() -> None:
    a = {
        "id": 1,
        "image_id": 1,
        "category_id": 5,
        "iscrowd": 1,
        "segmentation": [[0, 0, 1, 1, 2, 2]],
    }
    b = {"id": 2, "image_id": 1, "category_id": 5, "iscrowd": False, "bbox": [0, 0, 5, 5]}
    c = {"id": 3, "image_id": 1, "category_id": 5, "iscrowd": 0, "bbox": [0, 0, 5, 5],
         "segmentation": [[0, 0, 5, 0, 5, 5]]}  # fmt: skip
    p = parse(doc(annotations=[a, b, c]))
    assert len(p.boxes) == 2 and len(p.crowd_ignored) == 1 and p.segmentations_ignored == 1


def test_crowd_only_import_is_sem_alteracoes_and_creates_classes_only_if_missing() -> None:
    parsed = coco(anns=[{"id": 1, "image_id": 1, "category_id": 3, "iscrowd": 1}])
    repo = FakeRepo(classes=[("gato", 0)])
    rep = run(import_coco(repo, 1, parsed))
    assert rep.status == "sem_alteracoes" and rep.crowd_ignored == 1
    assert "insert_annotation_batch" not in repo.calls


# ---------------------------------------------------------------- limites exatos e acumulação


def _big_doc(n_img: int, n_ann: int, n_cat: int) -> bytes:
    return enc(
        {
            "images": [
                {"id": i, "file_name": f"{i}.jpg", "width": 10, "height": 10} for i in range(n_img)
            ],
            "categories": [{"id": i, "name": f"c{i}"} for i in range(n_cat)],
            "annotations": [
                {"id": i, "image_id": 0, "category_id": 0, "bbox": [0, 0, 5, 5]}
                for i in range(n_ann)
            ],
        }
    )


@pytest.mark.parametrize(
    ("kw", "args"),
    [
        ("max_images", (3, 1, 1)),
        ("max_annotations", (1, 3, 1)),
        ("max_categories", (1, 1, 3)),
    ],
)
def test_item_limits_exact_and_plus_one_and_message_names_only_the_limit(
    kw: str, args: tuple[int, int, int]
) -> None:
    parse_coco(_big_doc(*args), CocoLimits(**{kw: 3}))
    over = tuple(a + 1 if a == 3 else a for a in args)
    with pytest.raises(CocoImportError) as ei:
        parse_coco(_big_doc(*over), CocoLimits(**{kw: 3}))
    assert [e.code for e in ei.value.errors] == ["limite_excedido"]
    assert kw in ei.value.errors[0].message and "4" not in ei.value.errors[0].message


def test_limits_hit_before_item_validation_so_invalid_items_do_not_cost_work() -> None:
    items = [{"id": "x"}] * 1000
    with pytest.raises(CocoImportError) as ei:
        parse_coco(enc(doc(annotations=items)), CocoLimits(max_annotations=10))
    assert [e.code for e in ei.value.errors] == ["limite_excedido"]


def test_max_errors_one_keeps_the_first_by_order_and_the_real_total() -> None:
    anns = [{"id": i, "image_id": 99, "category_id": 99, "bbox": [0, 0, 0, 0]} for i in range(50)]
    with pytest.raises(CocoImportError) as ei:
        parse_coco(enc(doc(annotations=anns)), CocoLimits(max_errors=1))
    e = ei.value
    assert len(e.errors) == 1 and e.total >= 150 and e.truncated


def test_parser_is_deterministic_for_hostile_input() -> None:
    data = enc(doc(annotations=[{"id": 1, "image_id": 9, "category_id": 9, "bbox": [0, 0, 0, 0]}]))
    outs = []
    for _ in range(3):
        with pytest.raises(CocoImportError) as ei:
            parse_coco(data, LIM)
        outs.append([(e.code, e.kind, e.id, e.index, e.message) for e in ei.value.errors])
    assert outs[0] == outs[1] == outs[2]


def test_parsed_result_is_not_mutable_through_input() -> None:
    p: ParsedCoco = parse(doc())
    assert isinstance(p.boxes, tuple) and isinstance(p.categories, tuple)


# ---------------------------------------------------------------- idempotência e rollback


def test_reimport_with_crowd_and_warnings_stays_idempotent() -> None:
    anns = [
        ann(1, bbox=[-3, 0, 10, 10]),  # fora da imagem, gravada sem recorte
        {"id": 2, "image_id": 1, "category_id": 3, "iscrowd": 1},
        ann(3, bbox=[20, 10, 40, 30]),
    ]
    parsed = coco(anns=anns)
    repo = FakeRepo()
    first = run(import_coco(repo, 1, parsed))
    assert first.status == "importado" and first.annotations_imported == 2
    n_calls = len(repo.calls)
    second = run(import_coco(repo, 1, parsed))
    assert second.status == "sem_alteracoes" and second.annotations_imported == 0
    assert second.annotations_already_existing == 2 and second.classes_created == 0
    assert repo.writes().count("create_class") == 1 and len(repo.annotations) == 2
    assert len(repo.calls) > n_calls


def test_reimport_after_partial_overlap_inserts_only_the_surplus() -> None:
    repo = FakeRepo()
    run(import_coco(repo, 1, coco(anns=[ann(1), ann(2)])))  # duas caixas idênticas
    rep = run(import_coco(repo, 1, coco(anns=[ann(1), ann(2), ann(3)])))
    assert rep.annotations_imported == 1 and rep.annotations_already_existing == 2
    assert len(repo.annotations) == 3


def test_idempotence_ignores_declared_ids_and_order() -> None:
    repo = FakeRepo()
    a = [ann(1, bbox=[1, 1, 5, 5]), ann(2, bbox=[2, 2, 5, 5])]
    run(import_coco(repo, 1, coco(anns=a)))
    renumbered = [ann(90, bbox=[2, 2, 5, 5]), ann(91, bbox=[1, 1, 5, 5])]
    rep = run(import_coco(repo, 1, coco(anns=renumbered)))
    assert rep.annotations_imported == 0 and len(repo.annotations) == 2


def test_dry_run_never_writes_even_when_everything_is_new_and_errors_still_reported() -> None:
    repo = FakeRepo()
    rep = run(import_coco(repo, 1, coco(anns=[ann(1), ann(2, bbox=[1, 1, 3, 3])]), dry_run=True))
    assert rep.status == "simulado" and repo.writes() == [] and "lock" not in repo.calls
    assert "BEGIN" not in repo.calls and repo.annotations == [] and repo.classes == []
    bad = coco(
        anns=[ann(1, cat=3), {"id": 2, "image_id": 1, "category_id": 3, "bbox": [0, 0, 1, 1]}]
    )
    repo2 = FakeRepo(images=[])
    with pytest.raises(CocoImportError):
        run(import_coco(repo2, 1, bad, dry_run=True))
    assert repo2.writes() == []


@pytest.mark.parametrize("fail_at", [1, 2, 3])
def test_rollback_undoes_created_classes_and_leaves_reimport_possible(fail_at: int) -> None:
    anns = [ann(i, bbox=[i, i, 5, 5]) for i in (1, 2, 3)]
    parsed = coco(
        cats=[{"id": 3, "name": "gato"}, {"id": 4, "name": "cão"}, {"id": 9, "name": "ave"}],
        anns=anns,
    )
    repo = FakeRepo(classes=[("existente", 0)])
    before = repo.state()
    repo.fail_on_row = fail_at
    with pytest.raises(Exception) as ei:
        run(import_coco(repo, 1, parsed))
    assert "INSERT" not in str(ei.value)
    assert repo.state() == before
    assert repo.calls[-1] == "ROLLBACK"
    repo.fail_on_row = None
    rep = run(import_coco(repo, 1, parsed))  # a falha não deixou resíduo que bloqueie a repetição
    assert rep.annotations_imported == 3 and rep.classes_created == 3
    assert sorted(c.position for c in repo.classes) == [0, 1, 2, 3]


def test_failure_in_create_class_rolls_back_earlier_classes() -> None:
    parsed = coco(cats=[{"id": 1, "name": "a"}, {"id": 2, "name": "b"}], anns=[ann(1, cat=1)])
    repo = FakeRepo()
    real = repo.create_class
    n = {"c": 0}

    async def flaky(project_id: int, name: str, position: int) -> Any:
        n["c"] += 1
        if n["c"] == 2:
            raise RuntimeError("falha simulada")
        return await real(project_id, name, position)

    repo.create_class = flaky  # type: ignore[method-assign]
    with pytest.raises(RuntimeError):
        run(import_coco(repo, 1, parsed))
    assert repo.classes == [] and repo.annotations == []


def test_nothing_is_written_when_any_item_is_invalid_even_the_last_one() -> None:
    anns = [ann(i, bbox=[i, 1, 5, 5]) for i in range(1, 50)]
    anns.append(ann(99, image=1, cat=3, bbox=[0, 0, 1, 1]))
    imgs = [
        {"id": 1, "file_name": "a.jpg", "width": 200, "height": 100},
        {"id": 2, "file_name": "ausente.jpg", "width": 10, "height": 10},  # só a última falha
    ]
    repo = FakeRepo()
    err = errors_of(repo, coco(images=imgs, anns=anns))
    assert [e.code for e in err.errors] == ["imagem_nao_encontrada"]
    assert repo.writes() == [] and repo.classes == [] and repo.annotations == []
    assert "BEGIN" not in repo.calls


# ---------------------------------------------------------------- relatório truncado


def test_warning_list_is_truncated_with_real_total_via_service() -> None:
    anns = [ann(i, bbox=[-1 - (i % 3), 0, 10, 10 + (i % 50)]) for i in range(1, 400)]
    parsed = coco(anns=anns)
    repo = FakeRepo()
    rep = run(import_coco(repo, 1, parsed))
    assert rep.warnings_total == 399 and len(rep.warnings) < 399
    assert rep.warnings_truncated is True


def test_error_report_total_is_real_when_truncated_through_the_service() -> None:
    anns = [ann(i, image=1) for i in range(1, 6)]
    imgs = [{"id": i, "file_name": f"n{i}.jpg", "width": 1, "height": 1} for i in range(1, 301)]
    parsed = coco_with_cap(
        enc(
            {
                "images": imgs,
                "categories": [{"id": 3, "name": "g"}],
                "annotations": [{"id": 1, "image_id": 1, "category_id": 3, "bbox": [0, 0, 1, 1]}],
            }
        ),
    )
    del anns
    repo = FakeRepo(images=[])
    err = errors_of(repo, parsed, limits=CocoLimits(max_errors=7))
    assert len(err.errors) == 7 and err.total == 300 and err.truncated


# ---------------------------------------------------------------- API: 413, corpo, segredos


def coco_with_cap(data: bytes) -> ParsedCoco:
    return parse_coco(data, CocoLimits())


def _headers(extra: list[tuple[bytes, bytes]]) -> list[tuple[bytes, bytes]]:
    return [(b"content-type", b"application/json"), *extra]


def test_413_for_stream_without_content_length_leaves_no_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from test_api_coco_import import asgi_post

    e = Env(monkeypatch, import_max_json_bytes=500)
    out = asgi_post(e.app(), [b"x" * 100 for _ in range(50)], _headers([]))
    assert out["status"] == 413
    assert e.repo.writes() == [] and "list_images" not in e.repo.calls
    assert e.repo.classes == [] and e.repo.annotations == []
    assert json.loads(out["body"])["code"] == "requisicao_grande_demais"


def test_stream_exactly_at_the_limit_passes_the_middleware(monkeypatch: pytest.MonkeyPatch) -> None:
    from test_api_coco_import import asgi_post

    payload = json.dumps(api_doc()).encode()
    e = Env(monkeypatch, import_max_json_bytes=len(payload))
    half = len(payload) // 2
    out = asgi_post(e.app(), [payload[:half], payload[half:]], _headers([]))
    assert out["status"] == 201
    e2 = Env(monkeypatch, import_max_json_bytes=len(payload) - 1)
    out = asgi_post(e2.app(), [payload[:half], payload[half:]], _headers([]))
    assert out["status"] == 413 and e2.repo.annotations == []


def test_lying_content_length_with_valid_json_prefix_still_413(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from test_api_coco_import import asgi_post

    payload = json.dumps(api_doc()).encode()
    e = Env(monkeypatch, import_max_json_bytes=len(payload) - 1)
    out = asgi_post(e.app(), [payload[:5], payload[5:]], _headers([(b"content-length", b"3")]))
    assert out["status"] == 413 and e.repo.annotations == []


@pytest.mark.parametrize("cl", [b"-1", b"abc", b"", b"1e3", b"99999999999999999999999"])
def test_malformed_content_length_never_causes_500_or_writes(
    monkeypatch: pytest.MonkeyPatch, cl: bytes
) -> None:
    from test_api_coco_import import asgi_post

    e = Env(monkeypatch, import_max_json_bytes=1000)
    out = asgi_post(e.app(), [b'{"images": []}'], _headers([(b"content-length", cl)]))
    assert out["status"] in (400, 411, 413, 422), out
    assert e.repo.writes() == []


def test_empty_body_and_whitespace_body_are_422_json_invalido(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    e = Env(monkeypatch)
    with e.client() as c:
        for data in (b"", b"  \n"):
            r = c.post(URL, content=data, headers=JSON_H)
            assert r.status_code == 422 and r.json()["erros"][0]["code"] == "json_invalido"
    assert e.repo.writes() == []


def test_hostile_json_over_http_is_422_not_500(monkeypatch: pytest.MonkeyPatch) -> None:
    e = Env(monkeypatch)
    payloads = [
        b"[" * 100_000,
        b'{"a": 1, "a": 2}',
        b'{"images": NaN}',
        b'{"a": ' + b"9" * 5000 + b"}",
        b"\xff\xfe",
    ]
    with e.client() as c:
        for p in payloads:
            r = post(c, p)
            assert r.status_code == 422, (p[:20], r.text[:200])
            assert r.json()["code"] == "importacao_invalida"
    assert e.repo.calls.count("lock") == 0


def test_http_category_with_nul_or_surrogate_is_422_and_writes_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    e = Env(monkeypatch)
    with e.client() as c:
        for name in ("a\\u0000b", "\\ud800"):
            data = raw_doc(
                images='[{"id": 1, "file_name": "a.jpg", "width": 200, "height": 100}]',
                cats='[{"id": 3, "name": "' + name + '"}]',
            )
            r = post(c, data)
            assert r.status_code == 422, r.text
            assert r.json()["erros"][0]["code"] == "categoria_nome_invalido"
    assert e.repo.writes() == [] and e.repo.classes == []


def test_http_error_body_for_giant_id_does_not_echo_thousands_of_digits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    big = "9" * 4000
    img = '{"id": ' + big + ', "file_name": "a.jpg", "width": 1, "height": 1}'
    e = Env(monkeypatch)
    with e.client() as c:
        r = post(c, raw_doc(images=f"[{img}, {img}]"))
    assert r.status_code == 422 and len(r.text) < 2000


def test_secrets_never_leak_on_success_422_and_500_paths(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    e = Env(monkeypatch)
    secret_doc = api_doc(
        info={"note": JSON_SENTINEL, "dsn": SECRETS[3]},
        licenses=[{"name": SECRETS[0]}],
        categories=[{"id": 3, "name": "gato", "supercategory": JSON_SENTINEL}],
    )
    bad = api_doc(
        info={"note": JSON_SENTINEL},
        annotations=[{"id": 1, "image_id": 1, "category_id": 77, "bbox": [0, 0, 1, 1]}],
    )
    with caplog.at_level(logging.DEBUG), e.client() as c:
        r1 = post(c, json.dumps(secret_doc).encode())
        r2 = post(c, json.dumps(bad).encode())
        r3 = post(c, json.dumps(secret_doc).encode() + b"\xff")
        r4 = c.post(URL + "?dry_run=true", content=json.dumps(secret_doc), headers=JSON_H)
    assert (r1.status_code, r2.status_code, r3.status_code, r4.status_code) == (201, 422, 422, 200)
    _no_leak(r1.text + r2.text + r3.text + r4.text, caplog)


def test_unexpected_exception_after_reading_body_does_not_echo_body_or_secrets(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    e = Env(monkeypatch)

    async def odd(*a: Any) -> Any:
        raise RuntimeError(f"{SECRETS[2]} {JSON_SENTINEL} {SECRETS[3]}")

    e.repo.list_images_for_import = odd  # type: ignore[method-assign]
    with caplog.at_level(logging.DEBUG), e.client() as c:
        r = post(c, json.dumps(api_doc(info={"x": JSON_SENTINEL})).encode())
    assert r.status_code == 500 and r.json()["code"] == "erro_interno"
    _no_leak(r.text, caplog)


def test_failure_in_phase_2_leaves_fake_state_untouched_over_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    e = Env(monkeypatch)
    e.repo.fail_on_row = 1
    with e.client() as c:
        r = post(c)
        assert r.status_code == 409
        e.repo.fail_on_row = None
        r = post(c)
        assert r.status_code == 201 and len(e.repo.annotations) == 1
