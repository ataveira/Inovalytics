"""F1-08c critérios 21 a 27: serviço de importação Pascal VOC (repositório falso)."""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import pytest

from inovalytics.db.repository import ImportImage, InvalidAnnotationError, NotFoundError
from inovalytics.imports import voc_service
from inovalytics.imports.errors import ImportRejectedError
from inovalytics.imports.import_core import ImportReport
from inovalytics.imports.names_yaml import TxtLimits
from inovalytics.imports.txt_labels_parser import parse_txt_labels
from inovalytics.imports.txt_labels_service import import_txt_labels
from inovalytics.imports.voc_parser import VocLimits, convert_box, parse_voc
from inovalytics.imports.voc_service import import_voc
from test_coco_service import FakeRepo, run
from test_voc_parser import ann, bnd, obj, size

pytestmark = pytest.mark.usefixtures("no_external_network")

BOX = obj("pessoa")  # bndbox 51,26,150,75 -> (0.5, 0.5, 0.5, 0.5) numa imagem 200x100


def parsed_of(files: dict[str, str], lim: VocLimits | None = None) -> Any:
    data = json.dumps({"annotations": files}).encode()
    return parse_voc(data, lim or VocLimits(), collect_errors=True)


def imgs(*names: str, w: int = 200, h: int = 100) -> list[ImportImage]:
    return [ImportImage(10 + i, n, w, h) for i, n in enumerate(names)]


def do(repo: FakeRepo, files: dict[str, str], **kw: Any) -> ImportReport:
    return run(import_voc(repo, 1, parsed_of(files), **kw))  # type: ignore[no-any-return]


def rejected(repo: FakeRepo, files: dict[str, str], **kw: Any) -> ImportRejectedError:
    with pytest.raises(ImportRejectedError) as ei:
        run(import_voc(repo, 1, parsed_of(files), **kw))
    return ei.value


def triples(e: ImportRejectedError) -> list[tuple[str, str | None, int | None]]:
    return [(x.code, x.file, x.line) for x in e.errors]


def boxes(repo: FakeRepo) -> list[tuple[int, int, float, float, float, float]]:
    return [(a.image_id, a.class_id, a.x_center, a.y_center, a.width, a.height)
            for a in repo.annotations]  # fmt: skip


# ------------------------------------------------------------------ 21


def test_missing_dataset_raises_before_any_read() -> None:
    repo = FakeRepo(dataset_exists=False)
    with pytest.raises(NotFoundError):
        do(repo, {"a.xml": ann(BOX)})
    assert repo.calls == ["get_dataset"]


def test_service_static_imports(repo_root: Path) -> None:
    path = repo_root / "backend/src/inovalytics/imports/voc_service.py"
    banned = {"fastapi", "starlette", "boto3", "os", "pathlib", "subprocess", "socket",
              "ult" + "ralytics", "asyncpg", "requests", "urllib", "http", "re", "yaml",
              "lxml", "defusedxml"}  # fmt: skip
    tree = ast.parse(path.read_text("utf-8"))
    for node in ast.walk(tree):
        mods: list[str] = []
        if isinstance(node, ast.Import):
            mods = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            mods = [node.module or ""]
        for m in mods:
            assert m.split(".")[0] not in banned, m
            assert not m.startswith("xml.etree") and not m.startswith("xml.dom"), m
        if isinstance(node, ast.Name):
            assert node.id != "open"


def test_happy_path_report_and_values() -> None:
    repo = FakeRepo(images=imgs("a.jpg", "b.png"))
    files = {
        "a.xml": ann(obj("pessoa"), obj("capacete", bnd(1, 1, 200, 100)), size=size()),
        "b.xml": ann(obj("pessoa"), size=size()),
    }
    r = do(repo, files)
    assert (r.status, r.images_declared, r.images_matched) == ("importado", 2, 2)
    assert (r.classes_declared, r.classes_created, r.classes_existing) == (2, 2, 0)
    assert (r.annotations_in_file, r.annotations_imported, r.annotations_already_existing) == (
        3,
        3,
        0,
    )
    assert (r.images_without_boxes, r.without_size, r.origin_zero_adjustments) == (0, 0, 0)
    ids = {c.name: c.id for c in repo.classes}
    got = boxes(repo)
    assert got[0] == (10, ids["pessoa"], 0.5, 0.5, 0.5, 0.5)
    assert got[1][:2] == (10, ids["capacete"])
    assert got[1][2:] == pytest.approx((0.5, 0.5, 1.0, 1.0), abs=1e-6)
    assert got[2] == (11, ids["pessoa"], 0.5, 0.5, 0.5, 0.5)


# ------------------------------------------------------------------ 22


def stored3() -> FakeRepo:
    return FakeRepo(images=imgs("a.jpg", "b.png", "c d.jpg"))


def test_matching_rules_that_succeed() -> None:
    repo = stored3()
    r = do(
        repo,
        {
            "x/a.xml": ann(BOX, filename="a.jpg"),  # <filename> coerente em subpasta
            "b.xml": ann(BOX, filename="b.png"),  # extensão diferente da primeira imagem
            "c d.xml": ann(BOX),  # sem <filename>: nome-base
        },
    )
    assert r.annotations_imported == 3
    assert sorted(a.image_id for a in repo.annotations) == [10, 11, 12]


def test_filename_with_other_stem_is_nome_divergente() -> None:
    e = rejected(stored3(), {"a.xml": ann(BOX, filename="b.jpg")})
    assert triples(e) == [("nome_divergente", "a.xml", None)]


def test_filename_is_case_sensitive() -> None:
    e = rejected(stored3(), {"a.xml": ann(BOX, filename="a.JPG")})
    assert triples(e) == [("imagem_nao_encontrada", "a.xml", None)]
    e = rejected(stored3(), {"A.xml": ann(BOX)})
    assert triples(e)[0][0] == "imagem_nao_encontrada"


def test_ambiguous_by_stem_unless_filename_has_extension() -> None:
    repo = FakeRepo(images=imgs("a.jpg", "a.png"))
    for fn in (None, "a", "", "  "):
        assert triples(rejected(repo, {"a.xml": ann(BOX, filename=fn)})) == [
            ("imagem_ambigua", "a.xml", None)
        ]
    r = do(repo, {"a.xml": ann(BOX, filename="a.png")})
    assert r.annotations_imported == 1 and repo.annotations[0].image_id == 11


def test_two_keys_for_the_same_image() -> None:
    repo = FakeRepo(images=imgs("a.jpg"))
    e = rejected(repo, {"x/a.xml": ann(BOX), "y/a.xml": ann(BOX)})
    assert triples(e) == [("rotulo_nome_duplicado", "y/a.xml", None)]
    e = rejected(repo, {"x/a.xml": ann(BOX), "y/a.xml": ann(BOX, filename="a.jpg")})
    assert triples(e) == [("rotulo_nome_duplicado", "y/a.xml", None)]
    e = rejected(
        repo, {"x/a.xml": ann(BOX, filename="a.jpg"), "y/a.xml": ann(BOX, filename="a.jpg")}
    )
    assert triples(e) == [("rotulo_nome_duplicado", "y/a.xml", None)]
    assert repo.writes() == []


def test_filename_with_path_uses_last_component_only() -> None:
    for fn in ("C:\\x\\a.jpg", "../a.jpg", "/abs/a.jpg", "x/y\\a.jpg"):
        repo = FakeRepo(images=imgs("a.jpg"))
        assert do(repo, {"a.xml": ann(BOX, filename=fn)}).annotations_imported == 1, fn


def test_images_not_in_the_body_are_untouched() -> None:
    repo = FakeRepo(images=imgs("a.jpg", "b.jpg"))
    do(repo, {"a.xml": ann(BOX)})
    assert {a.image_id for a in repo.annotations} == {10}
    assert repo.calls.count("list_annotations") == 1


def test_key_and_filename_are_never_used_as_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    import builtins

    monkeypatch.setattr(builtins, "open", lambda *a, **k: pytest.fail("não deve abrir arquivos"))
    repo = FakeRepo(images=imgs("passwd.jpg"))
    assert (
        do(
            repo, {"../../etc/passwd.xml": ann(BOX, filename="/etc/passwd.jpg")}
        ).annotations_imported
        == 1
    )


def test_match_errors_carry_kind_index_and_truncated_name() -> None:
    repo = FakeRepo(images=imgs("a.jpg"))
    e = rejected(repo, {"b" + "x" * 300 + ".xml": ann(BOX)})
    x = e.errors[0]
    assert (x.code, x.kind, x.index) == ("imagem_nao_encontrada", "anotacao", 0)
    assert x.file is not None and len(x.file) <= 200


# ------------------------------------------------------------------ 23


def test_values_use_the_stored_dimensions_per_image() -> None:
    repo = FakeRepo(images=[ImportImage(10, "a.jpg", 200, 100), ImportImage(11, "b.jpg", 400, 300)])
    box = obj("p", bnd(11, 21, 60, 80))
    do(repo, {"a.xml": ann(box, size=size(200, 100)), "b.xml": ann(box, size=size(400, 300))})
    exp_a = convert_box(11, 21, 60, 80, 200, 100)
    exp_b = convert_box(11, 21, 60, 80, 400, 300)
    assert exp_a is not None and exp_b is not None
    assert boxes(repo)[0][2:] == exp_a[0] and boxes(repo)[1][2:] == exp_b[0]
    assert exp_a[0] != exp_b[0]


def test_stored_dimensions_win_without_size() -> None:
    repo = FakeRepo(images=imgs("a.jpg", w=400, h=200))
    r = do(repo, {"a.xml": ann(obj("p", bnd(51, 26, 150, 75)))})
    assert r.without_size == 1
    exp = convert_box(51, 26, 150, 75, 400, 200)
    assert exp is not None and boxes(repo)[0][2:] == exp[0]


@pytest.mark.parametrize("sz", [size(201, 100), size(200, 99), size(100, 200), size(999, 999)])
def test_size_different_from_stored_is_an_error_and_nothing_is_written(sz: str) -> None:
    repo = FakeRepo(images=imgs("a.jpg"))
    e = rejected(repo, {"a.xml": ann(BOX, size=sz)})
    assert triples(e) == [("tamanho_divergente", "a.xml", 1)]
    assert e.errors[0].kind == "anotacao"
    assert repo.writes() == [] and "BEGIN" not in repo.calls


def test_size_zero_is_reported_by_the_parser_through_the_service() -> None:
    e = rejected(FakeRepo(images=imgs("a.jpg")), {"a.xml": ann(BOX, size=size(0, 100))})
    assert e.errors[0].code == "tamanho_divergente"


def test_box_not_representable_and_outside_warning() -> None:
    repo = FakeRepo(images=imgs("a.jpg"))
    far = obj("p", bnd(150, 1, 400, 50))
    e = rejected(repo, {"a.xml": ann(BOX, far)})
    x = e.errors[0]
    assert (x.code, x.kind, x.index, x.file, x.line) == (
        "caixa_nao_representavel", "objeto", 1, "a.xml", 1
    )  # fmt: skip
    r = do(repo, {"a.xml": ann(obj("p", bnd(100, 1, 201, 10)))})
    assert r.warnings_total == 1 and r.warnings[0].code == "caixa_fora_da_imagem"
    assert (r.warnings[0].file, r.warnings[0].kind, r.warnings[0].index) == ("a.xml", "objeto", 0)
    assert boxes(repo)[0][2:] == pytest.approx((0.75, 0.05, 0.51, 0.1))


def test_ignored_counts_flow_into_the_report() -> None:
    repo = FakeRepo(images=imgs("a.jpg"))
    extra = (
        "<difficult>1</difficult><truncated>1</truncated><occluded>1</occluded><pose>Left</pose>"
    )
    extra += "<part><name>x</name></part>"
    r = do(repo, {"a.xml": ann(obj("p", extra=extra), obj("q", bnd(0, 1, 5, 5)))})
    assert (r.ignored_difficult, r.ignored_truncated, r.ignored_occluded) == (1, 1, 1)
    assert (r.ignored_pose, r.ignored_parts, r.origin_zero_adjustments) == (1, 1, 1)
    assert r.annotations_imported == 2  # difficult=1 também é importado


# ------------------------------------------------------------------ 24


def test_classes_reuse_create_in_first_appearance_order_with_positions() -> None:
    repo = FakeRepo(images=imgs("a.jpg"), classes=[("capacete", 4), ("outra", 9)])
    r = do(repo, {"a.xml": ann(obj("pessoa"), obj("capacete"), obj("colete"))})
    assert (r.classes_created, r.classes_existing, r.classes_declared) == (2, 1, 3)
    created = [c for c in repo.classes if c.id > 500]
    assert [(c.name, c.position) for c in created] == [("pessoa", 10), ("colete", 11)]
    assert boxes(repo)[1][1] == 100  # classe existente reaproveitada
    assert repo.calls.count("create_class") == 2


def test_only_used_classes_are_created_and_names_are_exact() -> None:
    repo = FakeRepo(images=imgs("a.jpg"), classes=[("Pessoa", 0)])
    do(repo, {"a.xml": ann(obj("pessoa"))})
    assert sorted(c.name for c in repo.classes) == ["Pessoa", "pessoa"]


def test_create_classes_false_reports_missing_and_writes_nothing() -> None:
    repo = FakeRepo(images=imgs("a.jpg"), classes=[("pessoa", 0)])
    e = rejected(repo, {"a.xml": ann(obj("pessoa"), obj("capacete"))}, create_classes=False)
    assert [(x.code, x.kind, x.index) for x in e.errors] == [("classe_ausente", "categoria", 1)]
    assert repo.writes() == [] and "BEGIN" not in repo.calls
    ok = FakeRepo(images=imgs("a.jpg"), classes=[("pessoa", 0)])
    assert do(ok, {"a.xml": ann(obj("pessoa"))}, create_classes=False).classes_created == 0


def test_existing_classes_never_updated_or_deleted() -> None:
    repo = FakeRepo(images=imgs("a.jpg"), classes=[("pessoa", 3)])
    before = repo.classes[0]
    do(repo, {"a.xml": ann(BOX)})
    assert repo.classes[0] == before
    assert not [c for c in repo.calls if c.startswith(("update", "delete"))]


def test_file_without_objects_counts_as_image_without_boxes() -> None:
    repo = FakeRepo(images=imgs("a.jpg", "b.jpg"))
    r = do(repo, {"a.xml": ann(), "b.xml": ann(BOX)})
    assert r.images_without_boxes == 1 and r.annotations_imported == 1
    r = do(FakeRepo(images=imgs("a.jpg")), {"a.xml": ann()})
    assert (r.status, r.images_without_boxes, r.classes_created) == ("sem_alteracoes", 1, 0)


# ------------------------------------------------------------------ 25


def test_errors_write_nothing_and_open_no_transaction() -> None:
    repo = FakeRepo(images=imgs("a.jpg"))
    files = {"a.xml": ann(BOX, obj(name=None)), "z.xml": ann(BOX), "w.xml": "<annotation>"}
    e = rejected(repo, files)
    assert [(x.file, x.code) for x in e.errors] == [
        ("w.xml", "xml_malformado"),
        ("z.xml", "imagem_nao_encontrada"),
        ("a.xml", "nome_ausente"),
    ]
    assert e.total == 3
    assert repo.writes() == [] and not {"BEGIN", "lock"} & set(repo.calls)
    assert repo.state() == ([], [])


def test_parser_and_matching_errors_appear_together() -> None:
    repo = FakeRepo(images=imgs("a.jpg"))
    e = rejected(repo, {"a.xml": ann(obj(name=None)), "q.xml": ann(BOX, size=size(1, 1))})
    codes = {(x.file, x.code) for x in e.errors}
    assert codes == {("a.xml", "nome_ausente"), ("q.xml", "imagem_nao_encontrada")}


def test_total_is_real_when_errors_are_truncated() -> None:
    lim = VocLimits(max_errors=5)
    repo = FakeRepo(images=imgs("a.jpg"))
    files = {f"z{i:02d}.xml": ann(BOX) for i in range(30)}
    p = parsed_of(files, lim)
    with pytest.raises(ImportRejectedError) as ei:
        run(import_voc(repo, 1, p, limits=lim))
    assert ei.value.total == 30 and len(ei.value.errors) == 5 and ei.value.truncated


@pytest.mark.parametrize("fail_at", [1, 3, 6])
def test_injected_failure_restores_state_including_classes(fail_at: int) -> None:
    repo = FakeRepo(images=imgs("a.jpg", "b.jpg"))
    repo.fail_on_row = fail_at
    before = repo.state()
    three = [obj("x"), obj("y", bnd(1, 1, 10, 10)), obj("x", bnd(5, 5, 20, 20))]
    with pytest.raises(InvalidAnnotationError):
        do(repo, {"a.xml": ann(*three), "b.xml": ann(*three)})
    assert repo.state() == before
    assert repo.calls[-1] == "ROLLBACK" and repo.classes == []


# ------------------------------------------------------------------ 26


def test_call_order(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(voc_service, "INSERT_CHUNK", 2)
    repo = FakeRepo(images=imgs("a.jpg"))
    do(repo, {"a.xml": ann(*[obj("p", bnd(i, 1, 100, 50)) for i in range(1, 6)])})
    assert repo.calls == [
        "get_dataset", "list_images", "list_classes",
        "BEGIN", "lock", "list_images", "list_classes",
        "create_class",
        "list_annotations", "insert_annotation_batch", "insert_annotation_batch",
        "insert_annotation_batch", "COMMIT"]  # fmt: skip
    assert repo.batches == [2, 2, 1]


def test_images_changed_under_the_lock_aborts() -> None:
    repo = FakeRepo(images=imgs("a.jpg"))
    real = repo.lock_project_for_import

    async def lock_and_change(project_id: int) -> None:
        await real(project_id)
        repo.images.append(ImportImage(99, "novo.jpg", 1, 1))

    repo.lock_project_for_import = lock_and_change  # type: ignore[method-assign]
    e = rejected(repo, {"a.xml": ann(BOX)})
    assert e.errors[0].code == "dataset_alterado"
    assert repo.writes() == [] and repo.calls[-1] == "ROLLBACK"


def test_dry_run_reports_without_writing_or_locking() -> None:
    repo = FakeRepo(images=imgs("a.jpg"), classes=[("pessoa", 0)])
    files = {"a.xml": ann(BOX, obj("capacete", bnd(1, 1, 10, 10)))}
    r = do(repo, files, dry_run=True)
    assert (r.status, r.classes_created, r.annotations_imported) == ("simulado", 1, 2)
    assert repo.writes() == [] and not {"BEGIN", "lock", "COMMIT"} & set(repo.calls)
    assert repo.state()[1] == [] and len(repo.classes) == 1
    real = do(FakeRepo(images=imgs("a.jpg"), classes=[("pessoa", 0)]), files)
    assert (real.classes_created, real.annotations_imported) == (1, 2)
    e = rejected(repo, {"nada.xml": ann(BOX)}, dry_run=True)
    assert e.errors[0].code == "imagem_nao_encontrada"


def test_query_count_is_independent_of_the_number_of_boxes() -> None:
    lim = VocLimits(max_annotations=3000, max_xml_bytes=10**7)

    def calls_for(n: int) -> list[str]:
        repo = FakeRepo(images=imgs("a.jpg"))
        many = [obj("p", bnd(1 + i % 60, 1, 100 + i % 90, 50)) for i in range(n)]
        run(import_voc(repo, 1, parsed_of({"a.xml": ann(*many)}, lim), limits=lim))
        return [c for c in repo.calls if c != "insert_annotation_batch"]

    assert calls_for(3) == calls_for(2500)


def test_dataset_image_count_above_max_images_is_an_error_never_truncation() -> None:
    repo = FakeRepo(images=imgs(*[f"{i}.jpg" for i in range(1, 6)]))
    p = parsed_of({})
    with pytest.raises(ImportRejectedError) as ei:
        run(import_voc(repo, 1, p, limits=VocLimits(max_images=4)))
    assert ei.value.errors[0].code == "limite_excedido" and repo.writes() == []
    run(import_voc(repo, 1, p, limits=VocLimits(max_images=5)))


def test_no_per_item_cost_outside_the_collector_when_full(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}
    real = voc_service.shown

    def counting(s: str) -> str:
        calls["n"] += 1
        return real(s)

    monkeypatch.setattr(voc_service, "shown", counting)
    lim = VocLimits(max_errors=3)
    repo = FakeRepo(images=imgs("a.jpg"))
    far = obj("p", bnd(150, 1, 400, 50))
    p = parsed_of(
        {
            "a.xml": ann(*[far] * 500, size=size()),
            **{f"n{i:03d}.xml": ann(BOX) for i in range(200)},
        },
        lim,
    )
    with pytest.raises(ImportRejectedError) as ei:
        run(import_voc(repo, 1, p, limits=lim))
    assert ei.value.total == 700 and len(ei.value.errors) == 3
    assert calls["n"] <= 6  # só os itens guardados (3 por tipo) chamam o nome exibido


# ------------------------------------------------------------------ 27


def test_reimport_is_idempotent() -> None:
    repo = FakeRepo(images=imgs("a.jpg"))
    files = {"a.xml": ann(BOX, obj("capacete", bnd(1, 1, 10, 10)))}
    assert do(repo, files).status == "importado"
    repo.calls.clear()
    r = do(repo, files)
    assert (r.status, r.annotations_imported, r.annotations_already_existing) == (
        "sem_alteracoes", 0, 2
    )  # fmt: skip
    assert r.classes_created == 0 and repo.writes() == []


def test_multiset_semantics_and_one_pixel_difference() -> None:
    repo = FakeRepo(images=imgs("a.jpg"))
    do(repo, {"a.xml": ann(BOX)})
    r = do(repo, {"a.xml": ann(BOX, BOX)})
    assert (r.annotations_imported, r.annotations_already_existing) == (1, 1)
    assert len(repo.annotations) == 2
    r = do(repo, {"a.xml": ann(obj("pessoa", bnd(52, 26, 150, 75)))})
    assert r.annotations_imported == 1 and len(repo.annotations) == 3


def test_boxes_from_other_formats_with_equal_coordinates_count_as_existing() -> None:
    repo = FakeRepo(images=imgs("a.jpg"))
    data = json.dumps({"names": ["pessoa"], "labels": {"a.txt": "0 0.5 0.5 0.5 0.5\n"}}).encode()
    run(import_txt_labels(repo, 1, parse_txt_labels(data, TxtLimits())))
    assert len(repo.annotations) == 1
    r = do(repo, {"a.xml": ann(BOX)})
    assert (r.annotations_imported, r.annotations_already_existing, r.status) == (
        0, 1, "sem_alteracoes"
    )  # fmt: skip
