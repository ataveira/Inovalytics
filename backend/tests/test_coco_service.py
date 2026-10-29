"""F1-06b critérios 16 a 26 e 28: serviço de importação COCO com repositório falso."""

import ast
import asyncio
import copy
import datetime as dt
from collections.abc import Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest

from inovalytics.db.repository import (
    Annotation,
    Dataset,
    DuplicateError,
    ImportImage,
    InvalidAnnotationError,
    LabelClass,
    NewAnnotation,
    NotFoundError,
)
from inovalytics.imports import coco_service
from inovalytics.imports.coco_parser import CocoLimits, parse_coco
from inovalytics.imports.coco_service import import_coco
from inovalytics.imports.errors import CocoImportError

pytestmark = pytest.mark.usefixtures("no_external_network")

NOW = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
WRITES = {"create_class", "insert_annotation_batch"}


def run(coro: Any) -> Any:
    return asyncio.run(coro)


class FakeRepo:
    def __init__(
        self,
        images: Sequence[ImportImage] = (ImportImage(10, "a.jpg", 200, 100),),
        classes: Sequence[tuple[str, int]] = (),
        dataset_exists: bool = True,
    ) -> None:
        self.calls: list[str] = []
        self.images = list(images)
        self.classes = [LabelClass(100 + i, 1, n, p, NOW) for i, (n, p) in enumerate(classes)]
        self.annotations: list[Annotation] = []
        self.dataset_exists = dataset_exists
        self.fail_on_row: int | None = None
        self.rows_seen = 0
        self.next_class = 500
        self.batches: list[int] = []

    def state(self) -> tuple[Any, ...]:
        return (copy.deepcopy(self.classes), copy.deepcopy(self.annotations))

    async def get_dataset(self, dataset_id: int) -> Dataset:
        self.calls.append("get_dataset")
        if not self.dataset_exists:
            raise NotFoundError("dataset não encontrado")
        return Dataset(dataset_id, 1, "d", NOW)

    async def list_images_for_import(self, dataset_id: int, limit: int) -> list[ImportImage]:
        self.calls.append("list_images")
        return self.images[:limit]

    async def list_classes(self, project_id: int) -> list[LabelClass]:
        self.calls.append("list_classes")
        return list(self.classes)

    async def list_annotations_for_images(self, image_ids: Sequence[int]) -> list[Annotation]:
        self.calls.append("list_annotations")
        return [a for a in self.annotations if a.image_id in set(image_ids)]

    @asynccontextmanager
    async def transaction(self) -> Any:
        self.calls.append("BEGIN")
        snap = self.state()
        try:
            yield
        except BaseException:
            self.classes, self.annotations = snap
            self.calls.append("ROLLBACK")
            raise
        self.calls.append("COMMIT")

    async def lock_project_for_import(self, project_id: int) -> None:
        self.calls.append("lock")

    async def create_class(self, project_id: int, name: str, position: int) -> LabelClass:
        self.calls.append("create_class")
        self.next_class += 1
        c = LabelClass(self.next_class, project_id, name, position, NOW)
        self.classes.append(c)
        return c

    async def insert_annotation_batch(
        self, project_id: int, rows: Sequence[tuple[int, NewAnnotation]]
    ) -> int:
        self.calls.append("insert_annotation_batch")
        self.batches.append(len(rows))
        for image_id, b in rows:
            self.rows_seen += 1
            if self.fail_on_row == self.rows_seen:
                raise InvalidAnnotationError("valor rejeitado pelas regras do banco")
            self.annotations.append(
                Annotation(
                    len(self.annotations) + 1,
                    image_id,
                    project_id,
                    b.class_id,
                    b.x_center,
                    b.y_center,
                    b.width,
                    b.height,
                    NOW,
                )  # fmt: skip
            )
        return len(rows)

    def writes(self) -> list[str]:
        return [c for c in self.calls if c in WRITES]


def coco(
    images: list[dict[str, Any]] | None = None,
    cats: list[dict[str, Any]] | None = None,
    anns: list[dict[str, Any]] | None = None,
) -> Any:
    d = {
        "images": images
        if images is not None
        else [{"id": 1, "file_name": "a.jpg", "width": 200, "height": 100}],
        "categories": cats if cats is not None else [{"id": 3, "name": "gato"}],
        "annotations": anns
        if anns is not None
        else [{"id": 1, "image_id": 1, "category_id": 3, "bbox": [20, 10, 40, 30]}],
    }
    import json

    return parse_coco(json.dumps(d).encode(), CocoLimits())


def ann(i: int, cat: int = 3, bbox: list[float] | None = None, image: int = 1) -> dict[str, Any]:
    return {"id": i, "image_id": image, "category_id": cat, "bbox": bbox or [20, 10, 40, 30]}


def errors_of(repo: FakeRepo, parsed: Any, **kw: Any) -> CocoImportError:
    with pytest.raises(CocoImportError) as ei:
        run(import_coco(repo, 1, parsed, **kw))
    return ei.value


# 16
def test_missing_dataset_raises_before_any_read() -> None:
    repo = FakeRepo(dataset_exists=False)
    with pytest.raises(NotFoundError):
        run(import_coco(repo, 1, coco()))
    assert repo.calls == ["get_dataset"]


# 17
def test_conversion_uses_stored_dimensions_and_counts() -> None:
    repo = FakeRepo()
    rep = run(import_coco(repo, 1, coco()))
    (a,) = repo.annotations
    assert (a.x_center, a.y_center, a.width, a.height) == pytest.approx(
        (0.2, 0.25, 0.2, 0.3), abs=1e-9
    )
    assert (rep.status, rep.images_declared, rep.images_matched) == ("importado", 1, 1)
    assert (rep.classes_declared, rep.classes_created, rep.classes_existing) == (1, 1, 0)
    assert (rep.annotations_in_file, rep.annotations_imported) == (1, 1)
    assert rep.annotations_already_existing == 0 and rep.dataset_id == 1


def test_fixture_with_multiple_images_and_classes() -> None:
    repo = FakeRepo(
        images=[ImportImage(10, "a.jpg", 200, 100), ImportImage(11, "b.png", 100, 100)],
        classes=[("gato", 0)],
    )
    parsed = coco(
        images=[
            {"id": 1, "file_name": "a.jpg", "width": 200, "height": 100},
            {"id": 2, "file_name": "b.png", "width": 100, "height": 100},
        ],
        cats=[{"id": 3, "name": "gato"}, {"id": 4, "name": "cão"}],
        anns=[ann(1, 3), ann(2, 4, [0, 0, 100, 100], 2), ann(3, 4, [10, 10, 20, 20], 2)],
    )
    rep = run(import_coco(repo, 1, parsed))
    assert (rep.classes_created, rep.classes_existing, rep.annotations_imported) == (1, 1, 3)
    assert len(repo.annotations) == 3
    full = [a for a in repo.annotations if a.image_id == 11][0]
    assert (full.x_center, full.y_center, full.width, full.height) == (0.5, 0.5, 1.0, 1.0)


# 18
@pytest.mark.parametrize("name", ["train/a b.jpg", "C:\\x\\a b.jpg", "a b.jpg", "../../a b.jpg"])
def test_names_reduced_to_last_component(name: str) -> None:
    repo = FakeRepo(images=[ImportImage(10, "a b.jpg", 200, 100)])
    parsed = coco(images=[{"id": 1, "file_name": name, "width": 200, "height": 100}])
    assert run(import_coco(repo, 1, parsed)).annotations_imported == 1


def test_path_traversal_reduces_to_base_name() -> None:
    repo = FakeRepo(images=[ImportImage(10, "passwd", 200, 100)])
    parsed = coco(images=[{"id": 1, "file_name": "../../etc/passwd", "width": 200, "height": 100}])
    assert run(import_coco(repo, 1, parsed)).images_matched == 1


def test_case_difference_does_not_match_and_error_is_safe() -> None:
    repo = FakeRepo(images=[ImportImage(10, "A.JPG", 200, 100)])
    long = "dir/" + "x\x00\x07y" * 100 + ".jpg"
    parsed = coco(images=[{"id": 1, "file_name": long, "width": 200, "height": 100}])
    err = errors_of(repo, parsed)
    (e,) = err.errors
    assert (e.code, e.kind, e.id, e.index) == ("imagem_nao_encontrada", "imagem", 1, 0)
    assert "\x00" not in e.message and "\x07" not in e.message
    assert len(e.message) < 300 and "x" * 201 not in e.message
    err = errors_of(
        repo, coco(images=[{"id": 1, "file_name": "a.jpg", "width": 200, "height": 100}])
    )
    assert err.errors[0].code == "imagem_nao_encontrada"


def test_ambiguous_and_duplicate_names() -> None:
    repo = FakeRepo(images=[ImportImage(10, "a.jpg", 200, 100), ImportImage(11, "a.jpg", 200, 100)])
    assert errors_of(repo, coco()).errors[0].code == "imagem_ambigua"
    repo = FakeRepo()
    parsed = coco(
        images=[
            {"id": 1, "file_name": "x/a.jpg", "width": 200, "height": 100},
            {"id": 2, "file_name": "y\\a.jpg", "width": 200, "height": 100},
        ],
        anns=[],
    )
    err = errors_of(repo, parsed)
    assert [(e.code, e.id, e.index) for e in err.errors] == [("imagem_nome_duplicado", 2, 1)]


# 19
def test_declared_dimensions_diverging_is_error_not_fixed() -> None:
    repo = FakeRepo(images=[ImportImage(10, "a.jpg", 400, 100)])
    err = errors_of(repo, coco())
    assert [e.code for e in err.errors] == ["dimensoes_divergentes"]
    assert repo.annotations == [] and repo.writes() == []


# 20
def test_existing_class_reused_without_create() -> None:
    repo = FakeRepo(classes=[("gato", 0)])
    run(import_coco(repo, 1, coco()))
    assert "create_class" not in repo.calls
    assert repo.annotations[0].class_id == 100


def test_new_classes_positions_order_and_unused_included() -> None:
    repo = FakeRepo(classes=[("a", 0), ("b", 4)])
    parsed = coco(
        cats=[{"id": 9, "name": "z"}, {"id": 3, "name": "gato"}, {"id": 5, "name": "nunca"}],
        anns=[ann(1, 9), ann(2, 3)],
    )
    rep = run(import_coco(repo, 1, parsed))
    created = [(c.name, c.position) for c in repo.classes[2:]]
    assert created == [("gato", 5), ("nunca", 6), ("z", 7)]  # ordem crescente de id do COCO
    assert (rep.classes_created, rep.classes_existing) == (3, 0)


def test_first_class_gets_position_zero_and_names_are_case_sensitive() -> None:
    repo = FakeRepo(classes=[("Gato", 0)])
    run(import_coco(repo, 1, coco()))
    assert [(c.name, c.position) for c in repo.classes] == [("Gato", 0), ("gato", 1)]
    repo = FakeRepo()
    run(import_coco(repo, 1, coco()))
    assert repo.classes[0].position == 0


def test_create_classes_false_missing_is_error_and_writes_nothing() -> None:
    repo = FakeRepo()
    err = errors_of(repo, coco(), create_classes=False)
    assert [(e.code, e.kind, e.id) for e in err.errors] == [("classe_ausente", "categoria", 3)]
    assert repo.writes() == [] and "BEGIN" not in repo.calls


def test_existing_classes_never_modified() -> None:
    repo = FakeRepo(classes=[("gato", 0)])
    before = copy.deepcopy(repo.classes)
    run(import_coco(repo, 1, coco()))
    assert repo.classes[: len(before)] == before
    assert set(repo.calls) <= {
        "get_dataset", "list_images", "list_classes", "list_annotations", "BEGIN", "lock",
        "insert_annotation_batch", "COMMIT",
    }  # fmt: skip


# 21
def test_phase1_errors_write_nothing_and_report_all() -> None:
    repo = FakeRepo(classes=[("gato", 0)])
    parsed = coco(
        images=[
            {"id": 1, "file_name": "a.jpg", "width": 200, "height": 100},
            {"id": 2, "file_name": "nao_existe.jpg", "width": 10, "height": 10},
        ],
        cats=[{"id": 3, "name": "gato"}, {"id": 4, "name": "novo"}],
        anns=[ann(1, 3, image=1)],
    )
    err = errors_of(repo, parsed, create_classes=False)
    assert sorted(e.code for e in err.errors) == ["classe_ausente", "imagem_nao_encontrada"]
    assert repo.writes() == [] and "BEGIN" not in repo.calls and "lock" not in repo.calls
    assert repo.annotations == [] and len(repo.classes) == 1


def test_error_cap_keeps_total() -> None:
    repo = FakeRepo(images=[])
    imgs = [{"id": i, "file_name": f"{i}.jpg", "width": 1, "height": 1} for i in range(30)]
    err = errors_of(repo, coco(images=imgs, anns=[]), limits=CocoLimits(max_errors=5))
    assert err.total == 30 and len(err.errors) == 5 and err.truncated


# 22
@pytest.mark.parametrize("n", [1, 4, 7])
def test_phase2_failure_rolls_back_everything(n: int, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(coco_service, "INSERT_CHUNK", 3)
    repo = FakeRepo(classes=[("existente", 0)])
    parsed = coco(
        cats=[{"id": 3, "name": "gato"}, {"id": 4, "name": "novo"}],
        anns=[ann(i, 3 + i % 2, [i, 1, 10, 10]) for i in range(1, 8)],
    )
    repo.fail_on_row = n
    before = repo.state()
    with pytest.raises((InvalidAnnotationError, DuplicateError)) as ei:
        run(import_coco(repo, 1, parsed))
    assert repo.state() == before  # inclusive as classes criadas na mesma transação
    assert repo.calls[-1] == "ROLLBACK"
    assert "SELECT" not in str(ei.value) and "INSERT" not in str(ei.value)
    assert repo.rows_seen == n


# 23
def test_single_transaction_with_lock_first_and_call_order() -> None:
    repo = FakeRepo()
    run(import_coco(repo, 1, coco()))
    assert repo.calls.count("BEGIN") == 1 and repo.calls.count("COMMIT") == 1
    inside = repo.calls[repo.calls.index("BEGIN") :]
    assert inside == [
        "BEGIN", "lock", "list_images", "list_classes", "create_class", "list_annotations",
        "insert_annotation_batch", "COMMIT",
    ]  # fmt: skip
    assert repo.calls[:3] == ["get_dataset", "list_images", "list_classes"]
    assert all(c not in WRITES for c in repo.calls[: repo.calls.index("BEGIN")])


def test_images_changed_between_plan_and_lock_fail_cleanly_and_write_nothing() -> None:
    class Racy(FakeRepo):
        async def list_images_for_import(self, dataset_id: int, limit: int) -> list[ImportImage]:
            out = await super().list_images_for_import(dataset_id, limit)
            if "lock" in self.calls:
                return []  # o upload/remoção concorrente mudou o conjunto após a fase 1
            return out

    repo = Racy()
    with pytest.raises(CocoImportError) as ei:
        run(import_coco(repo, 1, coco()))
    assert ei.value.errors[0].code == "dataset_alterado"
    assert repo.writes() == [] and repo.calls[-1] == "ROLLBACK"
    assert "create_class" not in repo.calls and "insert_annotation_batch" not in repo.calls


def test_class_vanished_before_lock_is_domain_error_when_not_creating() -> None:
    class Racy(FakeRepo):
        async def list_classes(self, project_id: int) -> list[LabelClass]:
            out = await super().list_classes(project_id)
            if "lock" in self.calls:
                return []  # outra transação removeu a classe entre a fase 1 e o bloqueio
            return out

    repo = Racy(classes=[("gato", 0)])
    with pytest.raises(CocoImportError) as ei:
        run(import_coco(repo, 1, coco(), create_classes=False))
    assert ei.value.errors[0].code == "classe_ausente"
    assert repo.writes() == [] and repo.calls[-1] == "ROLLBACK"


# 24
def test_idempotent_second_run() -> None:
    repo = FakeRepo()
    parsed = coco(anns=[ann(1), ann(2, bbox=[1, 1, 5, 5]), ann(3, bbox=[1, 1, 5, 5])])
    first = run(import_coco(repo, 1, parsed))
    assert first.annotations_imported == 3
    n_classes, n_ann = len(repo.classes), len(repo.annotations)
    repo.calls.clear()
    second = run(import_coco(repo, 1, parsed))
    assert (second.status, second.annotations_imported, second.annotations_already_existing) == (
        "sem_alteracoes", 0, 3,
    )  # fmt: skip
    assert second.classes_created == 0 and second.classes_existing == 1
    assert repo.writes() == []
    assert (len(repo.classes), len(repo.annotations)) == (n_classes, n_ann)


def _seed(repo: FakeRepo, class_id: int, box: tuple[float, ...], times: int) -> None:
    for _ in range(times):
        repo.annotations.append(Annotation(len(repo.annotations) + 1, 10, 1, class_id, *box, NOW))


def test_multiset_cases() -> None:
    box = (0.2, 0.25, 0.2, 0.3)
    # 2 idênticas no arquivo, 1 no banco -> insere 1
    repo = FakeRepo(classes=[("gato", 0)])
    _seed(repo, 100, box, 1)
    rep = run(import_coco(repo, 1, coco(anns=[ann(1), ann(2)])))
    assert (rep.annotations_imported, rep.annotations_already_existing) == (1, 1)
    # 1 no arquivo, 2 no banco -> insere 0
    repo = FakeRepo(classes=[("gato", 0)])
    _seed(repo, 100, box, 2)
    rep = run(import_coco(repo, 1, coco()))
    assert (rep.annotations_imported, rep.status) == (0, "sem_alteracoes")
    assert len(repo.annotations) == 2
    # nova junto com antiga -> insere só a nova
    repo = FakeRepo(classes=[("gato", 0)])
    _seed(repo, 100, box, 1)
    rep = run(import_coco(repo, 1, coco(anns=[ann(1), ann(2, bbox=[50, 10, 40, 30])])))
    assert (rep.annotations_imported, rep.annotations_already_existing) == (1, 1)


def test_other_class_or_slightly_different_box_is_not_duplicate() -> None:
    repo = FakeRepo(classes=[("gato", 0), ("outra", 1)])
    _seed(repo, 101, (0.2, 0.25, 0.2, 0.3), 1)  # classe diferente
    _seed(repo, 100, (0.2000001, 0.25, 0.2, 0.3), 1)  # coordenada diferente
    rep = run(import_coco(repo, 1, coco()))
    assert rep.annotations_imported == 1 and rep.annotations_already_existing == 0
    assert len(repo.annotations) == 3


# 25
def test_dry_run_reports_without_writing() -> None:
    repo = FakeRepo()
    parsed = coco(anns=[ann(1), ann(2, bbox=[1, 1, 5, 5])])
    rep = run(import_coco(repo, 1, parsed, dry_run=True))
    assert (rep.status, rep.classes_created, rep.annotations_imported) == ("simulado", 1, 2)
    assert repo.writes() == [] and "BEGIN" not in repo.calls and "lock" not in repo.calls
    assert repo.classes == [] and repo.annotations == []
    real = run(import_coco(repo, 1, parsed))
    assert (real.classes_created, real.annotations_imported) == (1, 2)
    again = run(import_coco(repo, 1, parsed, dry_run=True))
    got = (again.classes_created, again.annotations_imported, again.annotations_already_existing)
    assert got == (0, 0, 2)


def test_dry_run_still_reports_item_errors() -> None:
    repo = FakeRepo(images=[])
    assert errors_of(repo, coco(), dry_run=True).errors[0].code == "imagem_nao_encontrada"


def test_report_carries_crowd_segmentations_and_warnings() -> None:
    repo = FakeRepo()
    a = ann(1, bbox=[-10, 10, 40, 30])
    a["segmentation"] = [[1, 2, 3, 4, 5, 6]]
    parsed = coco(anns=[a, {"id": 2, "image_id": 1, "category_id": 3, "iscrowd": 1}])
    rep = run(import_coco(repo, 1, parsed))
    assert (rep.crowd_ignored, rep.segmentations_ignored, rep.annotations_in_file) == (1, 1, 2)
    assert rep.warnings_total == 1 and rep.warnings[0].code == "caixa_fora_da_imagem"
    assert not rep.warnings_truncated
    assert repo.annotations[0].x_center == pytest.approx(0.05)  # gravada sem recorte


# 26
def test_query_count_is_independent_of_annotation_count() -> None:
    def calls_for(n: int) -> list[str]:
        repo = FakeRepo(classes=[("gato", 0)])
        parsed = coco(anns=[ann(i, bbox=[i % 150, 1, 5, 5]) for i in range(n)])
        run(import_coco(repo, 1, parsed))
        return repo.calls

    small, big = calls_for(10), calls_for(12_000)
    assert [c for c in big if c != "insert_annotation_batch"] == [
        c for c in small if c != "insert_annotation_batch"
    ]
    assert big.count("insert_annotation_batch") == 3  # 12 000 linhas em blocos de 5 000


def test_chunking_sizes() -> None:
    repo = FakeRepo(classes=[("gato", 0)])
    parsed = coco(anns=[ann(i, bbox=[i % 190, (i // 190) % 90, 5, 5]) for i in range(12_000)])
    run(import_coco(repo, 1, parsed))
    assert repo.batches == [5000, 5000, 2000]


def test_images_over_max_images_is_error_never_truncation() -> None:
    repo = FakeRepo(images=[ImportImage(i, f"{i}.jpg", 1, 1) for i in range(1, 6)])
    err = errors_of(repo, coco(images=[], anns=[]), limits=CocoLimits(max_images=4))
    assert [e.code for e in err.errors] == ["limite_excedido"]
    assert repo.writes() == []
    run(import_coco(repo, 1, coco(images=[], anns=[]), limits=CocoLimits(max_images=5)))


# 28
def test_service_static_imports(repo_root: Path) -> None:
    path = repo_root / "backend/src/inovalytics/imports/coco_service.py"
    banned = {
        "fastapi", "starlette", "boto3", "os", "pathlib", "subprocess", "socket", "ultralytics",
        "pycocotools", "requests", "urllib", "http",
    }  # fmt: skip
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        mods: list[str] = []
        if isinstance(node, ast.Import):
            mods = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            mods = [node.module or ""]
        for m in mods:
            assert m.split(".")[0] not in banned, m
            assert m != "asyncpg", m
        if isinstance(node, ast.Name):
            assert node.id != "open"
