"""F1-06: lacunas restantes (lock timeout com retry, idempotência por HTTP, segredos em 4xx)."""

import json
import logging
from typing import Any

import pytest

from inovalytics.db.repository import LockTimeoutError
from inovalytics.imports.coco_service import import_coco
from test_api_coco_import import JSON_H, JSON_SENTINEL, URL, Env, _no_leak, post
from test_api_coco_import import doc as api_doc
from test_coco_service import FakeRepo, coco, run


def test_service_lock_timeout_rolls_back_writes_nothing_and_retry_works() -> None:
    repo = FakeRepo()
    real_lock = repo.lock_project_for_import

    async def timeout(project_id: int) -> None:
        await real_lock(project_id)
        raise LockTimeoutError("tempo esgotado ao obter o bloqueio")

    repo.lock_project_for_import = timeout  # type: ignore[method-assign]
    parsed = coco()
    with pytest.raises(LockTimeoutError):
        run(import_coco(repo, 1, parsed))
    assert repo.writes() == [] and repo.classes == [] and repo.annotations == []
    assert repo.calls[-1] == "ROLLBACK" and "COMMIT" not in repo.calls
    repo.lock_project_for_import = real_lock  # type: ignore[method-assign]
    rep = run(import_coco(repo, 1, parsed))
    assert (rep.classes_created, rep.annotations_imported) == (1, 1)


def test_http_lock_timeout_then_retry_is_201_and_second_run_is_noop(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    e = Env(monkeypatch)
    real_lock = e.repo.lock_project_for_import
    boom = {"on": True}

    async def flaky(project_id: int) -> None:
        if boom["on"]:
            raise LockTimeoutError(f"{JSON_SENTINEL} pg_advisory_xact_lock")
        await real_lock(project_id)

    e.repo.lock_project_for_import = flaky  # type: ignore[method-assign]
    with caplog.at_level(logging.DEBUG), e.client() as c:
        r1 = post(c)
        boom["on"] = False
        r2 = post(c)
        writes_after_first = len(e.repo.writes())
        r3 = post(c)
    assert (r1.status_code, r2.status_code, r3.status_code) == (503, 201, 200)
    assert r1.json()["code"] == "importacao_em_andamento"
    assert r3.json()["status"] == "sem_alteracoes"
    assert len(e.repo.writes()) == writes_after_first  # idempotência: terceira chamada não escreve
    assert len(e.repo.annotations) == 1 and len(e.repo.classes) == 1
    _no_leak(r1.text, caplog)


def test_http_dry_run_never_takes_the_lock_or_opens_a_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    e = Env(monkeypatch)
    with e.client() as c:
        r = c.post(URL + "?dry_run=true", content=json.dumps(api_doc()), headers=JSON_H)
    assert r.status_code == 200 and r.json()["status"] == "simulado"
    assert "lock" not in e.repo.calls and "BEGIN" not in e.repo.calls
    assert e.repo.writes() == []


def test_http_error_responses_do_not_leak_secrets_or_body(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    e = Env(monkeypatch, import_max_json_bytes=200)
    big = json.dumps(api_doc(info={"note": JSON_SENTINEL * 50})).encode()
    assert len(big) > 200
    with caplog.at_level(logging.DEBUG), e.client() as c:
        too_big = post(c, big)
        wrong_type = c.post(URL, content=big[:100], headers={"Content-Type": "application/zip"})
        bad_ds = c.post("/datasets/abc/imports/coco", content=b"{}", headers=JSON_H)
        e.repo.dataset_exists = False
        missing = c.post(URL, content=b"{}", headers=JSON_H)
    codes = [r.status_code for r in (too_big, wrong_type, bad_ds, missing)]
    assert codes == [413, 415, 422, 404]
    _no_leak("".join(r.text for r in (too_big, wrong_type, bad_ds, missing)), caplog)


def test_http_truncated_error_report_keeps_real_total(monkeypatch: pytest.MonkeyPatch) -> None:
    e = Env(monkeypatch, import_max_errors=3)
    anns: list[Any] = [
        {"id": i, "image_id": 1, "category_id": 99, "bbox": [0, 0, 1, 1]} for i in range(1, 11)
    ]
    with e.client() as c:
        r = post(c, json.dumps(api_doc(annotations=anns)).encode())
    j = r.json()
    assert r.status_code == 422 and len(j["erros"]) == 3
    assert j["erros_total"] == 10 and j["erros_truncados"] is True
    assert e.repo.writes() == [] and e.repo.annotations == []
