"""F1-04c critérios 23 a 28: repositório asyncpg com conexão falsa (sem Docker, sem rede)."""

import ast
import asyncio
import datetime as dt
import inspect
from collections.abc import Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import asyncpg  # type: ignore[import-untyped]
import pytest

from inovalytics.db import repository as repo
from inovalytics.db.repository import (
    DuplicateError,
    InvalidAnnotationError,
    InvalidValueError,
    NewAnnotation,
    NotFoundError,
)

pytestmark = pytest.mark.usefixtures("no_external_network")

SENTINEL = "pw-sentinela-4d2c1b"
NOW = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
SHA = "a" * 64


class FakeConn:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.rows: list[Any] = []  # fila de respostas de fetchrow
        self.fetchval_result: Any = 7
        self.exec_status = "DELETE 1"
        self.raise_on: str | None = None
        self.exc: BaseException | None = None
        self.stored: list[Any] = []
        self.stored_snapshot: list[Any] = []

    def _maybe_raise(self, name: str) -> None:
        if self.raise_on == name and self.exc is not None:
            raise self.exc

    async def execute(self, query: str, *args: Any) -> str:
        self.calls.append((query, args))
        self._maybe_raise("execute")
        return self.exec_status

    async def executemany(self, query: str, args: Sequence[Sequence[Any]]) -> None:
        self.calls.append((query, tuple(tuple(a) for a in args)))
        for a in args:
            self.stored.append(tuple(a))
            if a[3] < 0:  # caixa inválida simulada
                raise asyncpg.exceptions.CheckViolationError(f"x {SENTINEL} INSERT INTO annotation")

    async def fetch(self, query: str, *args: Any) -> list[Any]:
        self.calls.append((query, args))
        return []

    async def fetchrow(self, query: str, *args: Any) -> Any:
        self.calls.append((query, args))
        self._maybe_raise("fetchrow")
        return self.rows.pop(0) if self.rows else None

    async def fetchval(self, query: str, *args: Any) -> Any:
        self.calls.append((query, args))
        return self.fetchval_result

    @asynccontextmanager
    async def transaction(self) -> Any:
        self.calls.append(("BEGIN", ()))
        self.stored_snapshot = list(self.stored)
        try:
            yield
        except BaseException:
            self.stored = self.stored_snapshot
            self.calls.append(("ROLLBACK", ()))
            raise
        self.calls.append(("COMMIT", ()))


def _img_row() -> dict[str, Any]:
    return {
        "id": 1, "dataset_id": 2, "project_id": 3, "object_key": "k", "file_name": "f.jpg",
        "sha256": SHA, "size_bytes": 10, "width_px": 5, "height_px": 5, "split": None,
        "created_at": NOW,
    }  # fmt: skip


def run(coro: Any) -> Any:
    return asyncio.run(coro)


# 23
def test_public_api_is_async_and_typed() -> None:
    names = [
        "create_project", "get_project", "list_projects", "delete_project", "create_dataset",
        "create_class", "insert_image", "insert_annotations", "list_annotations",
        "get_dataset", "get_image_by_sha",
    ]  # fmt: skip
    for n in names:
        fn = getattr(repo, n)
        assert inspect.iscoroutinefunction(fn), n
        sig = inspect.signature(fn)
        assert sig.return_annotation is not inspect.Signature.empty
        assert all(p.annotation is not inspect.Parameter.empty for p in sig.parameters.values())


def test_create_get_list_delete_project() -> None:
    c = FakeConn()
    c.rows = [{"id": 1, "name": "p", "created_at": NOW}] * 2
    p = run(repo.create_project(c, "p"))
    assert (p.id, p.name) == (1, "p")
    assert run(repo.get_project(c, 1)) == p
    assert run(repo.list_projects(c)) == []
    c.rows = []
    with pytest.raises(NotFoundError):
        run(repo.get_project(c, 99))
    run(repo.delete_project(c, 1))
    c.exec_status = "DELETE 0"
    with pytest.raises(NotFoundError):
        run(repo.delete_project(c, 1))


def test_all_values_go_as_parameters() -> None:
    c = FakeConn()
    evil = "x'; DROP TABLE project; --"
    c.rows = [{"id": 1, "name": evil, "created_at": NOW}]
    run(repo.create_project(c, evil))
    query, args = c.calls[0]
    assert evil not in query and args == (evil,) and "$1" in query


# 25
def test_insert_image_idempotent() -> None:
    c = FakeConn()
    c.rows = [_img_row()]
    img, created = run(repo.insert_image(c, 2, **_kw()))
    assert created is True and img.id == 1
    c.rows = [None, _img_row()]  # conflito: DO NOTHING devolve nada, depois lê o existente
    img2, created2 = run(repo.insert_image(c, 2, **_kw()))
    assert created2 is False and img2 == img
    c.rows = [None, None]
    with pytest.raises(NotFoundError):
        run(repo.insert_image(c, 2, **_kw()))


def _kw() -> dict[str, Any]:
    return {"object_key": "k", "file_name": "f.jpg", "sha256": SHA, "size_bytes": 10,
            "width_px": 5, "height_px": 5}  # fmt: skip


# 26
def test_batch_is_transactional_all_or_nothing() -> None:
    c = FakeConn()
    boxes = [NewAnnotation(1, 0.5, 0.5, 0.2, 0.2), NewAnnotation(1, -0.5, 0.5, 0.2, 0.2)]
    with pytest.raises(InvalidAnnotationError):
        run(repo.insert_annotations(c, 10, boxes))
    names = [q for q, _ in c.calls]
    assert names[0] == "BEGIN" and names[-1] == "ROLLBACK" and "COMMIT" not in names
    assert c.stored == []
    c2 = FakeConn()
    assert run(repo.insert_annotations(c2, 10, boxes[:1])) == 1
    assert [q for q, _ in c2.calls][-1] == "COMMIT"


def test_batch_unknown_image() -> None:
    c = FakeConn()
    c.fetchval_result = None
    with pytest.raises(NotFoundError):
        run(repo.insert_annotations(c, 10, [NewAnnotation(1, 0.5, 0.5, 0.2, 0.2)]))
    assert [q for q, _ in c.calls][-1] == "ROLLBACK"


# 27
@pytest.mark.parametrize(
    ("exc", "expected", "call"),
    [
        (asyncpg.exceptions.UniqueViolationError, DuplicateError, "create_project"),
        (asyncpg.exceptions.ForeignKeyViolationError, NotFoundError, "create_dataset"),
        (asyncpg.exceptions.CheckViolationError, InvalidValueError, "create_class"),
        (asyncpg.exceptions.ForeignKeyViolationError, DuplicateError, "delete_project"),
    ],
)
def test_asyncpg_errors_become_domain_errors_without_leaks(
    exc: type[Exception], expected: type[Exception], call: str
) -> None:
    c = FakeConn()
    c.raise_on = "execute" if call == "delete_project" else "fetchrow"
    c.exc = exc(f"dsn senha={SENTINEL} SELECT secret FROM t")
    args: dict[str, tuple[Any, ...]] = {
        "create_project": ("p",),
        "create_dataset": (1, "d"),
        "create_class": (1, "c", 0),
        "delete_project": (1,),
    }
    with pytest.raises(expected) as ei:
        run(getattr(repo, call)(c, *args[call]))
    text = repr(ei.value) + str(ei.value) + repr(ei.value.__cause__) + repr(ei.value.__context__)
    assert SENTINEL not in text and "SELECT" not in text and "INSERT" not in text
    assert ei.value.__context__ is None
    if expected is InvalidValueError:  # CHECK fora de anotações não vira erro de anotação
        assert not isinstance(ei.value, InvalidAnnotationError)


def test_unmapped_errors_propagate() -> None:
    c = FakeConn()
    c.raise_on, c.exc = "fetchrow", asyncpg.exceptions.DataError("x")
    with pytest.raises(asyncpg.exceptions.DataError):
        run(repo.create_project(c, "p"))


def test_batch_errors_map_without_leaks() -> None:
    c = FakeConn()
    boxes = [NewAnnotation(1, -1.0, 0.5, 0.2, 0.2)]
    with pytest.raises(InvalidAnnotationError) as ei:
        run(repo.insert_annotations(c, 1, boxes))
    assert SENTINEL not in repr(ei.value) + repr(ei.value.__context__)


# 24
def _repo_tree(repo_root: Path) -> ast.Module:
    path = repo_root / "backend/src/inovalytics/db/repository.py"
    return ast.parse(path.read_text(encoding="utf-8"))


_QUERY_METHODS = {"execute", "executemany", "fetch", "fetchrow", "fetchval"}


def test_sql_never_built_dynamically(repo_root: Path) -> None:
    tree = _repo_tree(repo_root)
    consts: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if name.endswith("_SQL"):
                assert isinstance(node.value, ast.Constant), f"{name} não é literal puro"
                assert isinstance(node.value.value, str)
                consts[name] = node.value.value
    assert len(consts) >= 10
    for sql in consts.values():
        assert "%" not in sql and "{" not in sql and "'" not in sql
    for node in ast.walk(tree):
        assert not isinstance(node, ast.JoinedStr), "f-string proibida"
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mod)):
            pytest.fail(f"concatenação ou % na linha {node.lineno}")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr not in {"format", "join", "replace"}, node.lineno
            if node.func.attr in _QUERY_METHODS:
                first = node.args[0]
                assert isinstance(first, ast.Name) and first.id in consts, node.lineno


def test_sql_uses_numbered_params_only() -> None:
    for name, val in vars(repo).items():
        if name.endswith("_SQL"):
            assert "$" in val or name in {"LIST_PROJECTS_SQL"}, name


def test_no_orm_imports(repo_root: Path) -> None:
    banned = ("sqlalchemy", "peewee", "tortoise", "psycopg", "databases", "ultralytics")
    for node in ast.walk(_repo_tree(repo_root)):
        if isinstance(node, ast.Import):
            mods = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            mods = [node.module or ""]
        else:
            continue
        assert not any(m.split(".")[0] in banned for m in mods)


# 28
def test_api_does_not_reference_db_package(repo_root: Path) -> None:
    # F1-05c/F1-06c: só api/images.py (upload) e api/imports.py (importação) importam
    # inovalytics.db; main.py não.
    for path in (repo_root / "backend/src/inovalytics/api").glob("*.py"):
        if path.name in {"images.py", "imports.py"}:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            mods: list[str] = []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                mods = [node.module or ""] + [f"{node.module}.{a.name}" for a in node.names]
            for m in mods:
                assert not m.startswith("inovalytics.db"), (path.name, m)
                assert "repository" not in m, (path.name, m)


def test_no_new_routes() -> None:
    from inovalytics.api.config import Settings
    from inovalytics.api.main import create_app

    paths = {getattr(r, "path", "") for r in create_app(Settings(postgres_password="x")).routes}
    assert {"/health", "/health/ready"} <= paths
    # F1-05c, F1-06c e F1-07d liberam somente estas rotas POST.
    extra = {p for p in paths if p.startswith(("/projects", "/datasets", "/images"))}
    assert extra <= {
        "/datasets/{dataset_id}/images",
        "/datasets/{dataset_id}/imports/coco",
        "/datasets/{dataset_id}/imports/yolo-txt",
    }


# F1-05: get_dataset e get_image_by_sha
def test_get_dataset_found_and_missing() -> None:
    c = FakeConn()
    c.rows = [{"id": 2, "project_id": 3, "name": "d", "created_at": NOW}]
    assert run(repo.get_dataset(c, 2)).project_id == 3
    assert c.calls[0] == (repo.SELECT_DATASET_SQL, (2,))
    with pytest.raises(NotFoundError):
        run(repo.get_dataset(c, 2))


def test_get_image_by_sha_found_and_none() -> None:
    c = FakeConn()
    c.rows = [_img_row()]
    assert run(repo.get_image_by_sha(c, 2, SHA)).sha256 == SHA
    assert c.calls[0] == (repo.SELECT_IMAGE_BY_SHA_SQL, (2, SHA))
    assert run(repo.get_image_by_sha(c, 2, SHA)) is None
