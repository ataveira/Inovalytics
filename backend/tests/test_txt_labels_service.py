"""F1-07c critérios 19 a 26: serviço de importação dos rótulos txt (repositório falso)."""

import ast
import json
from pathlib import Path
from typing import Any

import pytest

from inovalytics.db.repository import ImportImage, InvalidAnnotationError, NotFoundError
from inovalytics.imports import txt_labels_service
from inovalytics.imports.errors import ErrorCollector, ImportRejectedError, ItemError
from inovalytics.imports.import_core import ImportReport
from inovalytics.imports.names_yaml import TxtLimits
from inovalytics.imports.txt_labels_parser import parse_txt_labels
from inovalytics.imports.txt_labels_service import import_txt_labels
from test_coco_service import FakeRepo, run

pytestmark = pytest.mark.usefixtures("no_external_network")

NAMES = ["pessoa", "capacete", "colete"]
LINE = "0 0.5 0.5 0.2 0.2\n"


def parsed_of(labels: dict[str, str], names: Any = NAMES, lim: TxtLimits | None = None) -> Any:
    data = json.dumps({"names": names, "labels": labels}).encode()
    return parse_txt_labels(data, lim or TxtLimits(), collect_errors=True)


def imgs(*names: str, w: int = 200, h: int = 100) -> list[ImportImage]:
    return [ImportImage(10 + i, n, w, h) for i, n in enumerate(names)]


def do(repo: FakeRepo, labels: dict[str, str], **kw: Any) -> ImportReport:
    names = kw.pop("names", NAMES)
    return run(import_txt_labels(repo, 1, parsed_of(labels, names), **kw))  # type: ignore[no-any-return]


def rejected(repo: FakeRepo, labels: dict[str, str], **kw: Any) -> ImportRejectedError:
    names = kw.pop("names", NAMES)
    with pytest.raises(ImportRejectedError) as ei:
        run(import_txt_labels(repo, 1, parsed_of(labels, names), **kw))
    return ei.value


def triples(e: ImportRejectedError) -> list[tuple[str, str | None, int | None]]:
    return [(x.code, x.file, x.line) for x in e.errors]


def boxes(repo: FakeRepo) -> list[tuple[int, int, float, float, float, float]]:
    return [(a.image_id, a.class_id, a.x_center, a.y_center, a.width, a.height)
            for a in repo.annotations]  # fmt: skip


# ------------------------------------------------------------------ 19


def test_missing_dataset_raises_before_any_read() -> None:
    repo = FakeRepo(dataset_exists=False)
    with pytest.raises(NotFoundError):
        do(repo, {"a.txt": LINE})
    assert repo.calls == ["get_dataset"]


def test_happy_path_report_and_values() -> None:
    repo = FakeRepo(images=imgs("a.jpg", "b.png"))
    r = do(repo, {"a.txt": "1 0.5 0.25 0.2 0.3\n0 .1 .2 .3 .4\n", "b.txt": ""})
    assert (r.status, r.images_declared, r.images_matched) == ("importado", 2, 2)
    assert (r.classes_declared, r.classes_created, r.classes_existing) == (3, 3, 0)
    assert (r.annotations_in_file, r.annotations_imported, r.annotations_already_existing) == (
        2,
        2,
        0,
    )
    assert (r.images_without_boxes, r.crowd_ignored, r.segmentations_ignored) == (1, 0, 0)
    ids = {c.name: c.id for c in repo.classes}
    assert boxes(repo) == [
        (10, ids["capacete"], 0.5, 0.25, 0.2, 0.3),
        (10, ids["pessoa"], 0.1, 0.2, 0.3, 0.4),
    ]


# ------------------------------------------------------------------ 20


def test_matching_by_base_name() -> None:
    repo = FakeRepo(images=imgs("a.jpg", "b.png", "c d.jpg"))
    r = do(repo, {"train/labels/a.txt": LINE, "C:\\x\\b.txt": LINE, "c d.txt": LINE})
    assert r.annotations_imported == 3
    assert sorted(a.image_id for a in repo.annotations) == [10, 11, 12]


def test_case_sensitive_and_shown_name() -> None:
    repo = FakeRepo(images=imgs("a.jpg"))
    key = "A" + "x" * 300 + ".txt"
    e = rejected(repo, {"A.txt": LINE, key: LINE})
    assert [c for c, _, _ in triples(e)] == ["imagem_nao_encontrada"] * 2
    assert all(f is not None and len(f) <= 200 for _, f, _ in triples(e))
    assert repo.writes() == []


def test_ambiguous_image_names() -> None:
    repo = FakeRepo(images=imgs("a.jpg", "a.png"))
    assert triples(rejected(repo, {"a.txt": LINE})) == [("imagem_ambigua", "a.txt", None)]
    repo = FakeRepo(images=[ImportImage(10, "a.jpg", 1, 1), ImportImage(11, "a.jpg", 1, 1)])
    assert triples(rejected(repo, {"a.txt": LINE}))[0][0] == "imagem_ambigua"


def test_duplicate_base_names_among_label_keys() -> None:
    repo = FakeRepo(images=imgs("a.jpg"))
    e = rejected(repo, {"train/a.txt": LINE, "val/a.txt": LINE})
    assert triples(e) == [("rotulo_nome_duplicado", "val/a.txt", None)]


def test_last_extension_only_and_no_extension() -> None:
    repo = FakeRepo(images=imgs("x.tar.gz", "semext"))
    r = do(repo, {"x.tar.txt": LINE})
    assert r.annotations_imported == 1
    e = rejected(FakeRepo(images=imgs("semext")), {"semext.txt": LINE})
    assert triples(e)[0][0] == "imagem_nao_encontrada"
    e = rejected(FakeRepo(images=imgs("x.tar.gz")), {"x.txt": LINE})
    assert triples(e)[0][0] == "imagem_nao_encontrada"


def test_classes_txt_is_just_a_missing_image() -> None:
    e = rejected(FakeRepo(images=imgs("a.jpg")), {"classes.txt": "pessoa\n", "a.txt": LINE})
    # a lista de classes vai em 'names'; um arquivo de nomes é só um rótulo sem imagem (e inválido)
    assert triples(e) == [
        ("imagem_nao_encontrada", "classes.txt", None),
        ("colunas_insuficientes", "classes.txt", 1),
    ]


def test_images_not_in_the_body_are_untouched() -> None:
    repo = FakeRepo(images=imgs("a.jpg", "b.jpg"))
    do(repo, {"a.txt": LINE})
    assert {a.image_id for a in repo.annotations} == {10}
    assert [i.id for i in repo.images] == [10, 11]
    assert "list_annotations" in repo.calls and repo.calls.count("list_annotations") == 1


def test_key_is_never_used_as_a_path(monkeypatch: pytest.MonkeyPatch) -> None:
    import builtins

    monkeypatch.setattr(builtins, "open", lambda *a, **k: pytest.fail("não deve abrir arquivos"))
    repo = FakeRepo(images=imgs("passwd.jpg"))
    assert do(repo, {"../../etc/passwd.txt": LINE}).annotations_imported == 1


# ------------------------------------------------------------------ 21


def test_values_do_not_depend_on_image_size() -> None:
    labels = {"a.txt": "0 0.123456789 0.5 0.25 0.75\n"}
    big, small = (
        FakeRepo(images=imgs("a.jpg", w=200, h=100)),
        FakeRepo(images=imgs("a.jpg", w=10, h=10)),
    )
    do(big, labels)
    do(small, labels)
    assert boxes(big)[0][2:] == (0.123456789, 0.5, 0.25, 0.75)
    assert [b[2:] for b in boxes(big)] == [b[2:] for b in boxes(small)]
    p = parsed_of(labels)
    assert boxes(big)[0][2:] == (p.boxes[0].cx, p.boxes[0].cy, p.boxes[0].w, p.boxes[0].h)


def test_empty_file_counts_and_inserts_nothing() -> None:
    repo = FakeRepo(images=imgs("a.jpg", "b.jpg"))
    r = do(repo, {"a.txt": "", "b.txt": "\n\n"})
    assert r.images_without_boxes == 2 and r.annotations_imported == 0
    assert r.status == "importado"  # classes criadas
    assert repo.annotations == []
    again = do(repo, {"a.txt": "", "b.txt": ""})
    assert again.status == "sem_alteracoes"


# ------------------------------------------------------------------ 22


def test_classes_reuse_create_in_names_order_with_positions() -> None:
    repo = FakeRepo(images=imgs("a.jpg"), classes=[("capacete", 4), ("outra", 9)])
    r = do(repo, {"a.txt": "1 .5 .5 .1 .1\n"})
    assert (r.classes_created, r.classes_existing) == (2, 1)
    created = [c for c in repo.classes if c.id > 500]
    assert [(c.name, c.position) for c in created] == [("pessoa", 10), ("colete", 11)]
    assert boxes(repo)[0][1] == 100  # classe existente reaproveitada
    assert repo.calls.count("create_class") == 2


def test_unused_classes_are_created_too() -> None:
    repo = FakeRepo(images=imgs("a.jpg"))
    do(repo, {"a.txt": "0 .5 .5 .1 .1\n"})
    assert [c.name for c in repo.classes] == NAMES


def test_create_classes_false_reports_missing_and_writes_nothing() -> None:
    repo = FakeRepo(images=imgs("a.jpg"), classes=[("pessoa", 0)])
    e = rejected(repo, {"a.txt": LINE}, create_classes=False)
    assert [(x.code, x.kind, x.index) for x in e.errors] == [
        ("classe_ausente", "categoria", 1),
        ("classe_ausente", "categoria", 2),
    ]
    assert repo.writes() == [] and "BEGIN" not in repo.calls
    assert do(FakeRepo(images=imgs("a.jpg"), classes=[(n, i) for i, n in enumerate(NAMES)]),
              {"a.txt": LINE}, create_classes=False).classes_created == 0  # fmt: skip


def test_existing_classes_never_updated_or_deleted() -> None:
    repo = FakeRepo(images=imgs("a.jpg"), classes=[("pessoa", 3)])
    before = repo.classes[0]
    do(repo, {"a.txt": LINE})
    assert repo.classes[0] == before
    assert not [c for c in repo.calls if c.startswith(("update", "delete"))]


# ------------------------------------------------------------------ 23


def test_errors_write_nothing_and_open_no_transaction() -> None:
    repo = FakeRepo(images=imgs("a.jpg"))
    labels = {"a.txt": "0 .5 .5 .1 .1\n0 .5 .5\n", "z.txt": LINE}
    e = rejected(repo, labels)
    assert triples(e) == [
        ("colunas_insuficientes", "a.txt", 2),
        ("imagem_nao_encontrada", "z.txt", None),
    ]
    assert repo.writes() == [] and "BEGIN" not in repo.calls and "lock" not in repo.calls
    assert repo.state() == ([], [])


def test_parser_errors_alone_are_reported_without_writes() -> None:
    repo = FakeRepo(images=imgs("a.jpg"))
    e = rejected(repo, {"a.txt": "x 1 2 3 4\n"})
    assert triples(e) == [("classe_invalida", "a.txt", 1)]
    assert e.total == 1 and repo.writes() == []


def test_total_is_real_when_errors_are_truncated() -> None:
    repo = FakeRepo(images=imgs("a.jpg"))
    lim = TxtLimits(max_errors=5)
    labels = {"a.txt": "bad\n" * 20, **{f"z{i}.txt": "" for i in range(10)}}
    p = parsed_of(labels, lim=lim)
    with pytest.raises(ImportRejectedError) as ei:
        run(import_txt_labels(repo, 1, p, limits=lim))
    assert ei.value.total == 30 and len(ei.value.errors) == 5 and ei.value.truncated


@pytest.mark.parametrize("fail_at", [1, 3, 6])
def test_injected_failure_restores_state_including_classes(fail_at: int) -> None:
    repo = FakeRepo(images=imgs("a.jpg", "b.jpg"))
    repo.fail_on_row = fail_at
    before = repo.state()
    with pytest.raises(InvalidAnnotationError):
        do(repo, {"a.txt": LINE * 3, "b.txt": LINE * 3})
    assert repo.state() == before
    assert repo.calls[-1] == "ROLLBACK" and repo.classes == []


# ------------------------------------------------------------------ 24


def test_call_order(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(txt_labels_service, "INSERT_CHUNK", 2)
    repo = FakeRepo(images=imgs("a.jpg"))
    do(repo, {"a.txt": LINE * 5})
    assert repo.calls == [
        "get_dataset", "list_images", "list_classes",
        "BEGIN", "lock", "list_images", "list_classes",
        "create_class", "create_class", "create_class",
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
    e = rejected(repo, {"a.txt": LINE})
    assert e.errors[0].code == "dataset_alterado"
    assert repo.writes() == [] and repo.calls[-1] == "ROLLBACK"


def test_classes_created_by_someone_else_under_the_lock_are_reused() -> None:
    repo = FakeRepo(images=imgs("a.jpg"))
    real = repo.lock_project_for_import

    async def lock_and_create(project_id: int) -> None:
        await real(project_id)
        await repo.create_class(1, "pessoa", 0)

    repo.lock_project_for_import = lock_and_create  # type: ignore[method-assign]
    r = do(repo, {"a.txt": LINE})
    assert r.classes_created == 2 and [c.name for c in repo.classes].count("pessoa") == 1


# ------------------------------------------------------------------ 25


def test_reimport_is_idempotent() -> None:
    repo = FakeRepo(images=imgs("a.jpg"))
    labels = {"a.txt": LINE + "1 .1 .1 .1 .1\n"}
    assert do(repo, labels).status == "importado"
    repo.calls.clear()
    r = do(repo, labels)
    assert (r.status, r.annotations_imported, r.annotations_already_existing) == (
        "sem_alteracoes",
        0,
        2,
    )
    assert r.classes_created == 0 and repo.writes() == []


def test_multiset_semantics() -> None:
    repo = FakeRepo(images=imgs("a.jpg"))
    do(repo, {"a.txt": LINE})
    r = do(repo, {"a.txt": LINE * 2})
    assert (r.annotations_imported, r.annotations_already_existing) == (1, 1)
    assert len(repo.annotations) == 2


def test_tiny_difference_is_a_new_box_and_equal_floats_are_the_same() -> None:
    repo = FakeRepo(images=imgs("a.jpg"))
    do(repo, {"a.txt": "0 0.5 0.5 0.2 0.2\n"})
    r = do(repo, {"a.txt": "0 0.500000001 0.5 0.2 0.2\n"})
    assert r.annotations_imported == 1
    r = do(repo, {"a.txt": "0 0.5 0.5 0.2 0.2\n0 0.500000 0.5 0.20 0.2\n"})
    assert (r.annotations_imported, r.annotations_already_existing) == (1, 1)


# ------------------------------------------------------------------ 26


def test_dry_run_reports_without_writing_or_locking() -> None:
    repo = FakeRepo(images=imgs("a.jpg"), classes=[("pessoa", 0)])
    r = do(repo, {"a.txt": LINE * 2}, dry_run=True)
    assert (r.status, r.classes_created, r.annotations_imported) == ("simulado", 2, 2)
    assert repo.writes() == []
    assert not {"BEGIN", "lock", "COMMIT"} & set(repo.calls)
    assert repo.state()[1] == [] and len(repo.classes) == 1
    real = do(FakeRepo(images=imgs("a.jpg"), classes=[("pessoa", 0)]), {"a.txt": LINE * 2})
    assert (real.classes_created, real.annotations_imported) == (2, 2)


def test_dry_run_counts_existing_and_still_reports_errors() -> None:
    repo = FakeRepo(images=imgs("a.jpg"))
    do(repo, {"a.txt": LINE})
    r = do(repo, {"a.txt": LINE * 2}, dry_run=True)
    assert (r.annotations_imported, r.annotations_already_existing, r.classes_created) == (1, 1, 0)
    assert (
        triples(rejected(repo, {"nada.txt": LINE}, dry_run=True))[0][0] == "imagem_nao_encontrada"
    )


def test_query_count_is_independent_of_the_number_of_boxes() -> None:
    lim = TxtLimits(max_annotations=3000)
    small = FakeRepo(images=imgs("a.jpg"))
    big = FakeRepo(images=imgs("a.jpg"))
    run(import_txt_labels(small, 1, parsed_of({"a.txt": LINE}, lim=lim), limits=lim))
    run(
        import_txt_labels(
            big,
            1,
            parsed_of({"a.txt": LINE.replace("0.5 0.5", "0.5 0.4") * 1} | {}, lim=lim),
            limits=lim,
        )
    )
    many = "".join(f"0 0.5 {i / 4000:.6f} 0.2 0.2\n" for i in range(3000))
    huge = FakeRepo(images=imgs("a.jpg"))
    run(import_txt_labels(huge, 1, parsed_of({"a.txt": many}, lim=lim), limits=lim))
    strip = lambda c: [x for x in c if x != "insert_annotation_batch"]  # noqa: E731
    assert strip(huge.calls) == strip(small.calls)
    assert huge.calls.count("list_annotations") == 1 and len(huge.annotations) == 3000


def test_dataset_image_count_above_max_images_is_an_error_never_truncation() -> None:
    names = [f"{i}.jpg" for i in range(1, 6)]
    repo = FakeRepo(images=imgs(*names))
    p = parsed_of({})
    with pytest.raises(ImportRejectedError) as ei:
        run(import_txt_labels(repo, 1, p, limits=TxtLimits(max_images=4)))
    assert ei.value.errors[0].code == "limite_excedido" and repo.writes() == []
    run(import_txt_labels(repo, 1, p, limits=TxtLimits(max_images=5)))


def test_warnings_are_carried_into_the_report() -> None:
    repo = FakeRepo(images=imgs("a.jpg"))
    r = do(repo, {"a.txt": "0 0.95 0.5 0.2 0.1\n"})
    assert r.warnings_total == 1 and r.warnings[0].code == "caixa_fora_da_imagem"
    assert r.warnings[0].file == "a.txt" and r.warnings[0].line == 1
    assert boxes(repo)[0][2] == 0.95


def test_absorb_keeps_real_total_and_cap() -> None:
    col = ErrorCollector(2)
    items = [ItemError("c", "rotulo", None, i, "m") for i in range(5)]
    col.absorb(items, 50)
    assert col.total == 50 and len(col.items()) == 2


# ------------------------------------------------------------------ 19 (estático)


def test_service_static_imports(repo_root: Path) -> None:
    path = repo_root / "backend/src/inovalytics/imports/txt_labels_service.py"
    banned = {"fastapi", "starlette", "boto3", "os", "pathlib", "subprocess", "socket",
              "ultralytics", "asyncpg", "requests", "urllib", "http", "re", "yaml"}  # fmt: skip
    tree = ast.parse(path.read_text("utf-8"))
    for node in ast.walk(tree):
        mods: list[str] = []
        if isinstance(node, ast.Import):
            mods = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            mods = [node.module or ""]
        for m in mods:
            assert m.split(".")[0] not in banned, m
        if isinstance(node, ast.Name):
            assert node.id != "open"


def test_core_modules_do_not_import_web_or_storage(repo_root: Path) -> None:
    banned = {"fastapi", "starlette", "boto3", "os", "pathlib", "subprocess", "socket",
              "ultralytics", "asyncpg"}  # fmt: skip
    for name in ("import_core.py", "common.py", "errors.py", "coco_service.py"):
        tree = ast.parse((repo_root / "backend/src/inovalytics/imports" / name).read_text("utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                mods = [node.module or ""]
            else:
                continue
            assert all(m.split(".")[0] not in banned for m in mods), (name, mods)
