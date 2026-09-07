"""백필이 남았는데 수집이 멈춰 있을 때 되살릴지 판단하는 감시자.

2026-09-07 초기 백필이 45분간 통째로 멈췄다. 마감이 지난 회차가 유지 모드로 빠지며
이어받기 사슬이 끊겼고, 사람이 마감을 늘렸지만 되돌릴 방아쇠가 없었다.
여기서는 되살리는 조건과, 헛돌지 않게 막는 조건을 함께 고정한다.
"""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

from test_pipeline import _seed

from app.ops import watchdog
from app.storage import models as m

NOW = datetime(2026, 9, 7, 2, 0, tzinfo=UTC)
LATER = NOW + timedelta(hours=7)  # 오늘 18:00 KST 를 흉내낸 마감
BACKFILL_LEFT = {"total": 370, "complete": 340, "remaining": 30, "blocked": 0}


def _decide(**kwargs):
    base = {
        "inventory": BACKFILL_LEFT,
        "deadline": LATER,
        "runs": watchdog.WorkflowRuns(),
        "now": NOW,
    }
    return watchdog.decide(**{**base, **kwargs})


def test_revives_when_backfill_remains_and_nothing_is_running():
    decision = _decide()
    assert decision.dispatch
    assert "30곳" in decision.reason


def test_extended_deadline_revives_even_after_a_maintenance_round():
    """마감을 넘겨 유지 모드로 떨어졌더라도, 마감이 늘어나면 다시 되살아난다.

    실행 중인 잡은 시작할 때 읽은 마감을 붙잡고 있어 늘어난 것을 모른다.
    감시자는 매번 저장소 변수를 새로 읽으므로 이 판단이 유일한 복구 경로다.
    """
    passed = NOW - timedelta(minutes=1)
    assert not _decide(deadline=passed).dispatch
    assert _decide(deadline=LATER).dispatch


def test_finished_backfill_stops():
    done = {"total": 370, "complete": 370, "remaining": 0, "blocked": 0}
    decision = _decide(inventory=done)
    assert not decision.dispatch
    assert "끝났" in decision.reason


def test_missing_targets_is_not_completion():
    decision = _decide(inventory={"total": 0, "complete": 0, "remaining": 0, "blocked": 0})
    assert not decision.dispatch
    assert "대상이 없" in decision.reason


def test_no_deadline_stops():
    assert not _decide(deadline=None).dispatch


def test_deadline_reserve_stops_a_pointless_round():
    """마감까지 여유보다 적게 남았으면 띄워봐야 유지 수집 한 번으로 끝난다."""
    assert not _decide(deadline=NOW + timedelta(minutes=10)).dispatch
    assert _decide(deadline=NOW + timedelta(minutes=20)).dispatch


def test_running_collection_is_not_duplicated():
    decision = _decide(runs=watchdog.WorkflowRuns(active=1, last_created_at=NOW - timedelta(hours=3)))
    assert not decision.dispatch
    assert "이미" in decision.reason


def test_cooldown_blocks_immediate_repeat():
    just_started = watchdog.WorkflowRuns(recent=1, last_created_at=NOW - timedelta(minutes=2))
    assert not _decide(runs=just_started).dispatch
    old = watchdog.WorkflowRuns(recent=1, last_created_at=NOW - timedelta(minutes=45))
    assert _decide(runs=old).dispatch


def test_hourly_cap_stops_spinning():
    spinning = watchdog.WorkflowRuns(recent=4, last_created_at=NOW - timedelta(minutes=12))
    decision = _decide(runs=spinning)
    assert not decision.dispatch
    assert "상한" in decision.reason


def test_repeated_failures_stop_and_ask_for_a_person():
    broken = watchdog.WorkflowRuns(
        consecutive_failures=3, last_created_at=NOW - timedelta(minutes=30)
    )
    decision = _decide(runs=broken)
    assert not decision.dispatch
    assert "사람" in decision.reason


def test_summarize_runs_reads_github_listing():
    rows = [
        {"databaseId": 3, "createdAt": "2026-09-07T01:57:09Z", "status": "in_progress", "conclusion": None},
        {"databaseId": 2, "createdAt": "2026-09-07T01:00:29Z", "status": "completed", "conclusion": "failure"},
        {"databaseId": 1, "createdAt": "2026-09-06T22:52:53Z", "status": "completed", "conclusion": "success"},
    ]
    summary = watchdog.summarize_runs(rows, now=NOW)
    assert summary.active == 1
    assert summary.recent == 2  # 최근 한 시간 안에 만들어진 둘
    assert summary.consecutive_failures == 1  # 진행 중인 것은 세지 않고 성공에서 끊긴다
    assert summary.last_created_at == datetime(2026, 9, 7, 1, 57, 9, tzinfo=UTC)


def test_cancelled_run_does_not_count_as_failure():
    rows = [
        {"createdAt": "2026-09-07T01:00:00Z", "status": "completed", "conclusion": "cancelled"},
        {"createdAt": "2026-09-07T00:00:00Z", "status": "completed", "conclusion": "failure"},
    ]
    assert watchdog.summarize_runs(rows, now=NOW).consecutive_failures == 0


def test_unreadable_run_list_holds_instead_of_dispatching():
    """목록을 못 읽으면 냉각·상한 장치가 풀린다. 그때는 띄우지 않는 쪽으로 기운다."""
    summary = watchdog.summarize_runs(watchdog.load_runs("없는파일.json"), now=NOW)
    assert summary.active == 1
    assert not _decide(runs=summary).dispatch


def test_report_reads_backfill_and_idle_time_from_the_database(session_factory, settings):
    with session_factory() as session:
        source = _seed(session)
        session.add(
            m.Run(
                id="run-old",
                kind="collect",
                started_at=NOW - timedelta(minutes=46),
                finished_at=NOW - timedelta(minutes=45),
                result="success",
            )
        )
        session.commit()
        health = session.get(m.SourceHealth, source.id)
        assert health is not None

    cfg = replace(settings, initial_window_start=None)
    report = watchdog.build_report(
        cfg=cfg, deadline=LATER, runs=watchdog.WorkflowRuns(), now=NOW
    )
    assert report["remaining"] == 1
    assert report["idle_minutes"] == 45.0
    assert report["stalled"]
    assert report["dispatch"]
    assert "수집을 띄웁니다" in watchdog.render(report)
