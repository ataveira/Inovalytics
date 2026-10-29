"""F1-06b critério 27: funções do repositório usadas pela importação (conexão falsa)."""

import ast
import asyncio
from pathlib import Path
from typing import Any

import asyncpg  # type: ignore[import-untyped]
import pytest

from inovalytics.db import repository as repo
from inovalytics.db.repository import (
    InvalidAnnotationError,
    LockTimeoutError,
    NewAnnotation,
)
from test_db_repository import NOW, SENTINEL, FakeConn

pytestmark = pytest.mark.usefixtures("no_external_network")


def run(coro: Any) -> Any:
    return asyncio.run(coro)


class RowsConn(FakeConn):
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        super().__init__()
        self.fetch_rows = rows or []

    async def fetch(self, query: str, *args: Any) -> list[Any]:
        self.calls.append((query, args))
        return self.fetch_rows


def test_list_classes_maps_rows() -> None:
    row = {"id": 1, "project_id": 2, "name": "a", "position": 0, "created_at": NOW}
    c = RowsConn([row])
    out = run(repo.list_classes(c, 2))
    assert [(x.id, x.name, x.position) for x in out] == [(1, "a", 0)]
    assert c.calls == [(repo.LIST_CLASSES_SQL, (2,))]


def test_list_images_for_import_uses_limit_param() -> None:
    c = RowsConn([{"id": 4, "file_name": "a.jpg", "width_px": 20, "height_px": 10}])
    out = run(repo.list_images_for_import(c, 3, 51))
    assert out == [repo.ImportImage(4, "a.jpg", 20, 10)]
    assert c.calls == [(repo.LIST_IMAGES_FOR_IMPORT_SQL, (3, 51))]


def test_list_annotations_for_images_single_query() -> None:
    row = {
        "id": 1, "image_id": 4, "project_id": 2, "class_id": 9, "x_center": 0.5, "y_center": 0.5,
        "width": 0.1, "height": 0.2, "created_at": NOW,
    }  # fmt: skip
    c = RowsConn([row])
    out = run(repo.list_annotations_for_images(c, (4, 5, 6)))
    assert out[0].class_id == 9
    assert c.calls == [(repo.LIST_ANNOTATIONS_FOR_IMAGES_SQL, ([4, 5, 6],))]
    assert "ANY($1::bigint[])" in repo.LIST_ANNOTATIONS_FOR_IMAGES_SQL


def test_lock_sets_local_timeout_then_takes_advisory_lock() -> None:
    c = FakeConn()
    run(repo.lock_project_for_import(c, 7, 2500))
    assert c.calls == [
        (repo.SET_LOCAL_CONFIG_SQL, ("lock_timeout", "2500")),
        (repo.LOCK_PROJECT_SQL, (repo.IMPORT_LOCK_NAMESPACE, 7)),
    ]
    assert "pg_advisory_xact_lock" in repo.LOCK_PROJECT_SQL
    assert "true" in repo.SET_LOCAL_CONFIG_SQL  # is_local: vale só na transação


def test_lock_key_fits_int4_for_big_project_ids() -> None:
    c = FakeConn()
    run(repo.lock_project_for_import(c, 2**40 + 5))
    key = c.calls[-1][1][1]
    assert 0 <= key < 2**31
    assert c.calls[0][1][1] == "10000"


def test_lock_timeout_becomes_domain_error_without_leak() -> None:
    c = FakeConn()
    c.raise_on = "execute"
    c.exc = asyncpg.exceptions.LockNotAvailableError(f"{SENTINEL} SELECT pg_advisory_xact_lock")
    with pytest.raises(LockTimeoutError) as ei:
        run(repo.lock_project_for_import(c, 1))
    assert SENTINEL not in repr(ei.value) + repr(ei.value.__context__)


def test_batch_has_no_transaction_and_one_executemany() -> None:
    c = FakeConn()
    rows = [(10, NewAnnotation(1, 0.5, 0.5, 0.2, 0.2)), (11, NewAnnotation(2, 0.1, 0.1, 0.1, 0.1))]
    assert run(repo.insert_annotation_batch(c, 3, rows)) == 2
    assert [q for q, _ in c.calls] == [repo.INSERT_ANNOTATION_SQL]
    assert c.calls[0][1] == ((10, 3, 1, 0.5, 0.5, 0.2, 0.2), (11, 3, 2, 0.1, 0.1, 0.1, 0.1))
    assert run(repo.insert_annotation_batch(c, 3, [])) == 0


def test_batch_errors_map_without_leaks() -> None:
    c = FakeConn()
    with pytest.raises(InvalidAnnotationError) as ei:
        run(repo.insert_annotation_batch(c, 3, [(1, NewAnnotation(1, -1.0, 0.5, 0.2, 0.2))]))
    assert SENTINEL not in repr(ei.value) + repr(ei.value.__context__)


def test_batch_function_never_opens_a_transaction(repo_root: Path) -> None:
    src = (repo_root / "backend/src/inovalytics/db/repository.py").read_text(encoding="utf-8")
    fns = {n.name: n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.AsyncFunctionDef)}
    for name in ("insert_annotation_batch", "lock_project_for_import", "list_classes"):
        attrs = {n.attr for n in ast.walk(fns[name]) if isinstance(n, ast.Attribute)}
        assert "transaction" not in attrs, name


def test_insert_annotations_nested_in_outer_transaction_is_savepoint() -> None:
    c = FakeConn()

    async def go() -> None:
        async with c.transaction():
            await repo.insert_annotations(c, 1, [NewAnnotation(1, 0.5, 0.5, 0.2, 0.2)])

    run(go())
    kinds = [q for q, _ in c.calls if q in {"BEGIN", "COMMIT", "ROLLBACK"}]
    assert kinds == ["BEGIN", "BEGIN", "COMMIT", "COMMIT"]  # a interna é savepoint no asyncpg
    c2 = FakeConn()

    async def bad() -> None:
        async with c2.transaction():
            await repo.insert_annotations(c2, 1, [NewAnnotation(1, -1.0, 0.5, 0.2, 0.2)])

    with pytest.raises(InvalidAnnotationError):
        run(bad())
    assert c2.stored == []  # a externa também desfez


def test_new_functions_are_async_and_typed() -> None:
    import inspect

    for n in (
        "list_classes", "list_images_for_import", "list_annotations_for_images",
        "lock_project_for_import", "insert_annotation_batch",
    ):  # fmt: skip
        fn = getattr(repo, n)
        assert inspect.iscoroutinefunction(fn), n
        sig = inspect.signature(fn)
        assert sig.return_annotation is not inspect.Signature.empty
        assert all(p.annotation is not inspect.Parameter.empty for p in sig.parameters.values())
