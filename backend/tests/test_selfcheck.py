"""자체 점검 7개 항목이 실제 저장 상태에서 무엇을 이상으로 부르는지."""

import asyncio
import json
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

from test_collection_progress import item
from test_pipeline import _seed

from app.domain.dates import KST, utcnow
from app.ingestion.base import ListPage
from app.ingestion.http import FetchError
from app.ops import selfcheck
from app.storage import models as m


def _extra_source(session, sid, *, name, list_url, status="active"):
    session.add(
        m.Source(
            id=sid,
            organization_id="org-hq",
            name=name,
            adapter="khu_board",
            list_url=list_url,
            status=status,
        )
    )
    session.flush()
    session.add(
        m.SourceConfigVersion(
            id=f"cfg-{sid}", source_id=sid, version=1, config={}, interval_minutes=60, is_active=True
        )
    )
    session.add(m.SourceHealth(source_id=sid))
    session.flush()
    return session.get(m.Source, sid)


def _source_item(session, iid, source_id, *, detail_error=None):
    session.add(
        m.SourceItem(
            id=iid,
            source_id=source_id,
            external_id=iid,
            canonical_url=f"https://www.khu.ac.kr/{iid}",
            last_detail_error=detail_error,
        )
    )
    session.flush()
    return iid


def _notice(session, nid, published, *, source_item_id):
    session.add(
        m.Notice(
            id=nid,
            primary_source_item_id=source_item_id,
            status="visible",
            title=f"공지 {nid}",
            published_date=published,
        )
    )


# ------------------------------------------------------------------ 1 조용한 0건


def test_zero_items_with_recent_original_is_an_anomaly_and_old_board_is_not(monkeypatch):
    """상태 코드는 같아도 최신 글 날짜가 정상과 이상을 가른다(4절 1번)."""

    class Board:
        def __init__(self, published):
            self.published = published

        async def list_page(self, fetcher, config, page_index):
            return ListPage(tuple(item(n, self.published) for n in range(3)), 1, False)

    def verdict(published):
        monkeypatch.setattr(selfcheck, "get_adapter", lambda name: Board(published))
        return asyncio.run(
            selfcheck.probe_source(
                None,
                {"source_id": "src-a", "name": "게시판", "adapter": "khu_board", "config": {}},
                window_start=date(2026, 3, 1),
            )
        )

    inside = verdict("2026-09-02")
    assert inside["verdict"] == selfcheck.ALERT
    assert inside["parsed_items"] == 3
    assert inside["latest_date"] == "2026-09-02"

    outside = verdict("2025-11-12")
    assert outside["verdict"] == selfcheck.OUT_OF_WINDOW
    assert outside["latest_date"] == "2025-11-12"


def test_probe_records_fetch_failure_instead_of_calling_it_an_out_of_window_board(monkeypatch):
    class Broken:
        async def list_page(self, fetcher, config, page_index):
            raise FetchError("timeout", "응답 없음")

    monkeypatch.setattr(selfcheck, "get_adapter", lambda name: Broken())
    row = asyncio.run(
        selfcheck.probe_source(
            None,
            {"source_id": "src-a", "name": "게시판", "adapter": "khu_board", "config": {}},
            window_start=date(2026, 3, 1),
        )
    )
    assert row["verdict"] == selfcheck.PROBE_FAILED
    assert "[timeout]" in row["error"]


def test_candidates_only_include_active_sources_that_listed_but_stored_nothing(session_factory):
    with session_factory() as db:
        _seed(db)
        db.get(m.SourceHealth, "src-hq").last_list_success_at = utcnow()
        quiet = _extra_source(db, "src-quiet", name="조용한 게시판", list_url="https://a.khu.ac.kr/1")
        db.get(m.SourceHealth, quiet.id).last_list_success_at = utcnow()
        _extra_source(db, "src-untried", name="시도 없음", list_url="https://a.khu.ac.kr/2")
        paused = _extra_source(
            db, "src-paused", name="중지", list_url="https://a.khu.ac.kr/3", status="pending"
        )
        db.get(m.SourceHealth, paused.id).last_list_success_at = utcnow()
        _source_item(db, "item-1", "src-hq")
        db.commit()

        ids = [c["source_id"] for c in selfcheck.silent_zero_candidates(db)]
    # 원문이 있는 src-hq, 목록 성공 기록이 없는 src-untried, 활성이 아닌 src-paused 는 후보가 아니다.
    assert ids == ["src-quiet"]


def test_rotation_resumes_after_the_previous_round_and_wraps_around():
    candidates = [{"source_id": f"src-{n}"} for n in "abcde"]
    assert [c["source_id"] for c in selfcheck.rotate(candidates, None, 2)] == ["src-a", "src-b"]
    assert [c["source_id"] for c in selfcheck.rotate(candidates, "src-b", 2)] == ["src-c", "src-d"]
    # 끝까지 돌면 처음으로 돌아온다. 같은 곳만 반복해서 보지 않는다.
    assert [c["source_id"] for c in selfcheck.rotate(candidates, "src-e", 2)] == ["src-a", "src-b"]


# --------------------------------------------------------------- 2~7 과 전체 보고


def test_full_report_fills_every_check_and_reports_the_worst_status(session_factory, settings):
    cfg = replace(settings, initial_window_start=date(2026, 3, 1))
    now = datetime(2026, 9, 7, 3, 0, tzinfo=KST).astimezone(UTC)
    with session_factory() as db:
        source = _seed(db)
        health = db.get(m.SourceHealth, source.id)
        health.last_list_success_at = now
        # 2 범위 미달: 3월에 아직 못 닿았다.
        health.backfill_oldest_date = date(2026, 5, 1)
        health.backfill_last_stop_reason = "time_budget"
        # 5 접근 실패: 3회 이상 고착.
        health.consecutive_failures = 4
        health.last_error_kind = "parse_error"
        health.last_error_message = "목록 구조를 읽지 못했습니다"

        # 3 날짜 오염: 미래 1건, 1990년 이전 1건, 정상 1건.
        good = _source_item(db, "item-ok", source.id)
        _notice(db, "ntc-future", date(2099, 12, 31), source_item_id=good)
        _notice(db, "ntc-ancient", date(1970, 1, 1), source_item_id=good)
        _notice(db, "ntc-normal", date(2026, 4, 1), source_item_id=good)

        # 4 상세 실패: 성격이 다른 두 가지.
        _source_item(db, "item-403", source.id, detail_error="상세를 열 권한이 없거나 로그인이 필요한 게시글입니다.")
        _source_item(db, "item-net", source.id, detail_error="[connection] 연결 실패: https://me.khu.ac.kr/x")

        # 6 좀비 실행, 7 공개 지연.
        db.add(m.Run(id="run-zombie", kind="collect", started_at=now - timedelta(hours=9)))
        db.add(
            m.Run(
                id="run-old-export",
                kind="collect",
                started_at=now - timedelta(hours=20),
                finished_at=now - timedelta(hours=19),
                result="success",
                revision="r-old",
            )
        )
        db.add(
            m.Run(
                id="run-recent",
                kind="collect",
                started_at=now - timedelta(minutes=20),
                finished_at=now - timedelta(minutes=5),
                result="partial",
            )
        )
        db.commit()

    report = selfcheck.run_check(cfg=cfg, fetch=False, now=now)
    checks = report["checks"]
    assert list(checks) == [
        "silent_zero",
        "window_shortfall",
        "date_pollution",
        "detail_failures",
        "access_failures",
        "zombie_runs",
        "publish_delay",
    ]
    assert report["status"] == selfcheck.ALERT

    assert checks["silent_zero"]["candidates"] == 0  # 원문이 저장되어 있으므로 후보가 아니다
    assert checks["window_shortfall"]["count"] == 1
    assert checks["window_shortfall"]["sources"][0]["oldest_date"] == "2026-05-01"
    assert checks["date_pollution"]["future"] == 1
    assert checks["date_pollution"]["ancient"] == 1
    assert checks["detail_failures"]["count"] == 2
    assert {k["kind"] for k in checks["detail_failures"]["kinds"]} == {"권한/로그인 필요", "connection"}
    assert checks["access_failures"]["stuck"] == 1
    assert checks["zombie_runs"]["count"] == 1
    assert checks["zombie_runs"]["runs"][0]["run_id"] == "run-zombie"
    assert checks["publish_delay"]["status"] == selfcheck.ALERT
    assert checks["publish_delay"]["last_revision"] == "r-old"

    text = selfcheck.render(report)
    assert "자체 점검 — 이상" in text
    assert "고착된 접근 실패 1곳" in text
    for check in checks.values():
        assert check["name"] in text
    # 기계 판독용 결과는 그대로 직렬화된다.
    assert json.loads(json.dumps(report, ensure_ascii=False))["status"] == selfcheck.ALERT


def test_window_shortfall_carries_the_previous_round_so_a_trend_is_visible(session_factory, settings):
    cfg = replace(settings, initial_window_start=date(2026, 3, 1))
    now = utcnow()
    with session_factory() as db:
        source = _seed(db)
        db.get(m.SourceHealth, source.id).backfill_oldest_date = date(2026, 5, 1)
        db.add(
            m.Run(
                id="run-export",
                kind="collect",
                started_at=now - timedelta(minutes=30),
                finished_at=now - timedelta(minutes=20),
                result="success",
                revision="r-1",
            )
        )
        db.commit()

    previous = {"checks": {"window_shortfall": {"count": 4}}}
    report = selfcheck.run_check(cfg=cfg, fetch=False, previous=previous, now=now)
    shortfall = report["checks"]["window_shortfall"]
    assert shortfall["previous_count"] == 4
    assert shortfall["delta"] == -3
    assert "변화 -3" in shortfall["summary"]
    # 공개 개정이 마지막 수집 종료와 같은 회차면 지연이 아니다.
    assert report["checks"]["publish_delay"]["status"] == selfcheck.OK
    assert report["status"] != selfcheck.ALERT
