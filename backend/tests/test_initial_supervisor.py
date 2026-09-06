import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from test_pipeline import _seed

from app.run import initial
from app.storage import models as m


def test_deadline_requires_timezone():
    assert initial.parse_deadline("") is None
    assert initial.parse_deadline("2026-09-07T08:00:00+09:00") == datetime(2026, 9, 6, 23, tzinfo=UTC)
    with pytest.raises(ValueError):
        initial.parse_deadline("2026-09-07T08:00:00")


def test_after_deadline_runs_maintenance(settings, monkeypatch):
    calls = []

    async def collect(cfg, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(result="success")

    monkeypatch.setattr(initial, "run_collection", collect)
    result = asyncio.run(initial.supervise(settings, deadline=datetime.now(UTC) - timedelta(seconds=1)))
    assert calls == [{}]
    assert result["mode"] == "maintenance"
    assert not result["continue_initial"]


def test_initial_publishes_from_the_first_round_and_at_the_end(settings, monkeypatch):
    """첫 회차 직후부터 내보낸다.

    한 주기가 지나야 처음 공개하면 잡이 그 전에 끝나는 동안 화면이 몇 시간 낡은 것만
    보여준다. 2026-09-07 실제로 3시간 51분 동안 공개가 한 번도 나가지 않았다.
    """
    calls, publications = [], []
    inventories = iter([
        {"total": 2, "complete": 0, "remaining": 2, "blocked": 0},
        {"total": 2, "complete": 1, "remaining": 1, "blocked": 0},
        {"total": 2, "complete": 2, "remaining": 0, "blocked": 0},
        {"total": 2, "complete": 2, "remaining": 0, "blocked": 0},
    ])

    async def collect(cfg, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(run_id=f"run-{len(calls)}", result="partial", new_items=5, attempted=1)

    monkeypatch.setattr(initial, "run_collection", collect)
    monkeypatch.setattr(initial, "initial_inventory", lambda cfg: next(inventories))
    monkeypatch.setattr(initial, "publish", lambda cfg, run_id: publications.append(run_id) or "revision")
    result = asyncio.run(initial.supervise(settings, deadline=datetime.now(UTC) + timedelta(hours=2)))
    assert calls == [{"initial_mode": True, "publish": False, "dedupe": False}] * 2
    assert publications == ["run-1", "run-2"]
    assert result["new_items"] == 10
    assert result["result"] == "complete"
    assert not result["continue_initial"]


def test_cutoff_preserves_unfinished_state(settings, monkeypatch):
    monkeypatch.setattr(initial, "initial_inventory", lambda cfg: {"total": 1, "complete": 0, "remaining": 1, "blocked": 1})
    monkeypatch.setattr(initial, "publish", lambda cfg, run_id: "revision")
    result = asyncio.run(initial.supervise(settings, deadline=datetime.now(UTC) + timedelta(minutes=5)))
    assert result["result"] == "partial"
    assert result["remaining"] == 1
    assert result["rounds"] == 0
    assert not result["continue_initial"]


def test_runner_limit_requests_continuation_before_deadline(settings, monkeypatch):
    monkeypatch.setattr(initial, "initial_inventory", lambda cfg: {"total": 1, "complete": 0, "remaining": 1, "blocked": 0})
    monkeypatch.setattr(initial, "publish", lambda cfg, run_id: "revision")
    result = asyncio.run(initial.supervise(settings, deadline=datetime.now(UTC) + timedelta(hours=3), max_seconds=0))
    assert result["continue_initial"]
    assert result["remaining"] == 1


def test_no_targets_is_not_completion(settings, monkeypatch):
    monkeypatch.setattr(initial, "initial_inventory", lambda cfg: {"total": 0, "complete": 0, "remaining": 0, "blocked": 0})
    with pytest.raises(RuntimeError, match="대상이 없습니다"):
        asyncio.run(initial.supervise(settings, deadline=datetime.now(UTC) + timedelta(hours=3)))


def test_completed_initial_range_immediately_uses_maintenance(settings, monkeypatch):
    calls = []

    async def collect(cfg, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(result="success")

    monkeypatch.setattr(initial, "initial_inventory", lambda cfg: {"total": 1, "complete": 1, "remaining": 0, "blocked": 0})
    monkeypatch.setattr(initial, "run_collection", collect)
    result = asyncio.run(initial.supervise(settings, deadline=datetime.now(UTC) + timedelta(hours=3)))
    assert calls == [{}]
    assert result["mode"] == "maintenance"
    assert not result["continue_initial"]


def test_inventory_does_not_trust_old_window(session_factory, settings):
    with session_factory() as session:
        source = _seed(session)
        health = session.get(m.SourceHealth, source.id)
        health.backfill_complete = True
        health.initial_window_start = datetime(2026, 4, 1).date()
        session.commit()
    cfg = replace(settings, initial_window_start=datetime(2026, 3, 1).date())
    assert initial.initial_inventory(cfg)["remaining"] == 1


def test_cancelled_job_still_publishes_what_it_collected(settings, monkeypatch):
    """잡이 취소되거나 터져도 마지막 공개 뒤에 모은 것은 내보낸다.

    2026-09-07 회차를 스물다섯 번 돌고도 공개가 한 번만 나갔다. 취소된 잡이 아무것도
    내보내지 않았기 때문이다. 파일을 다 올린 뒤에야 포인터가 바뀌므로 도중에 죽어도
    개정이 섞이지 않는다.
    """
    publications = []
    rounds = 0

    async def collect(cfg, **kwargs):
        nonlocal rounds
        rounds += 1
        if rounds == 3:
            raise KeyboardInterrupt("잡 취소")
        return SimpleNamespace(run_id=f"run-{rounds}", result="partial", new_items=1, attempted=1)

    monkeypatch.setattr(initial, "run_collection", collect)
    monkeypatch.setattr(initial, "initial_inventory",
        lambda cfg: {"total": 2, "complete": 0, "remaining": 2, "blocked": 0})
    monkeypatch.setattr(initial, "publish", lambda cfg, run_id: publications.append(run_id) or "revision")

    with pytest.raises(KeyboardInterrupt):
        asyncio.run(initial.supervise(
            settings, deadline=datetime.now(UTC) + timedelta(hours=2), publish_seconds=10_000,
        ))
    # 첫 회차 직후 한 번, 그리고 취소 시점에 2회차 몫을 한 번 더 내보낸다.
    assert publications == ["run-1", "run-2"]
