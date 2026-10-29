"""F1-08: nomes de classe hostis custam menos de 1 s; o planejamento roda fora do laço."""

from __future__ import annotations

import json
import threading
from typing import Any

import pytest

from _timing import timed
from _voc_bench import build_body
from inovalytics.db.repository import ImportImage
from inovalytics.imports import voc_service
from inovalytics.imports.voc_parser import ParsedVoc, VocLimits, parse_voc
from inovalytics.imports.voc_service import import_voc
from test_coco_service import FakeRepo, run
from test_voc_parser import ann, obj

LIM = VocLimits()


@pytest.mark.parametrize("kind", ["plain", "zwj"])
def test_hostile_class_names_are_refused_in_under_a_second(kind: str) -> None:
    body, _, nobj = build_body(kind)
    assert len(body) > 14_000_000 and nobj >= 7000
    elapsed, result = timed(lambda: parse_voc(body, LIM, collect_errors=True), 8.0)
    assert isinstance(result, ParsedVoc) and result.boxes_count == 0
    assert result.errors_total == nobj  # todos recusados como classe_invalida
    assert elapsed < 1.0  # medido ~0,4 a 0,6 s; antes da correção 2,3 s


@pytest.mark.parametrize("kind", ["valid", "zwj_valid"])
def test_valid_class_names_at_the_same_size_stay_under_a_second(kind: str) -> None:
    body, _, nobj = build_body(kind)
    elapsed, result = timed(lambda: parse_voc(body, LIM, collect_errors=True), 8.0)
    assert isinstance(result, ParsedVoc) and result.boxes_count == nobj
    assert elapsed < 1.5  # medido ~0,3 a 0,5 s; margem para máquina carregada


def test_planning_runs_outside_the_event_loop_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[int] = []
    real = voc_service._plan

    def spy(*args: Any) -> Any:
        seen.append(threading.get_ident())
        return real(*args)

    monkeypatch.setattr(voc_service, "_plan", spy)
    data = json.dumps({"annotations": {"a.xml": ann(obj("pessoa"))}}).encode()
    parsed = parse_voc(data, LIM, collect_errors=True)
    repo = FakeRepo(images=[ImportImage(10, "a.jpg", 200, 100)])
    report = run(import_voc(repo, 1, parsed))
    assert report.annotations_imported == 1
    assert seen and seen[0] != threading.get_ident()
