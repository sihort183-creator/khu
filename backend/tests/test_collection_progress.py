"""수집 경계·중단·재개·실패 복구가 실제 저장 결과에 미치는 영향."""

import asyncio
from dataclasses import replace
from datetime import date, timedelta

import pytest
from sqlalchemy import select
from test_pipeline import CONFIG, _seed

from app.domain.dates import utcnow
from app.ingestion.base import FetchedDetail, ListedItem, ListPage, ParseError
from app.run import collect
from app.storage import models as m
from app.storage import repository as repo


def item(number, published="2026-08-01", pinned=False):
    return ListedItem(str(number), f"https://www.khu.ac.kr/{number}", f"공지 {number}", published, is_pinned=pinned)


class Board:
    def __init__(self, pages):
        self.pages = pages
        self.failed = set()
        self.read_pages = []
        self.read_details = []

    async def list_page(self, fetcher, config, page_index):
        self.read_pages.append(page_index)
        return ListPage(tuple(self.pages.get(page_index, [])), page_index, page_index < max(self.pages))

    async def detail(self, fetcher, config, listed):
        self.read_details.append(listed.external_id)
        if listed.external_id in self.failed:
            raise ParseError("일시 상세 실패")
        return FetchedDetail(listed.external_id, listed.url, listed.title, "본문", "<p>본문</p>", "<p>원문</p>", published_raw=listed.published_raw)


def run(db, source, settings, store, board, monkeypatch, *, ordered=False, budget=None):
    monkeypatch.setattr(collect, "get_adapter", lambda name: board)
    config = {**CONFIG, "date_ordered": ordered}
    if ordered:
        config["date_order_evidence"] = {
            "method": "full_listing_monotonic", "valid_until": (utcnow() + timedelta(hours=1)).isoformat(),
        }
    due = repo.DueSource(source, config, 60, db.get(m.SourceHealth, source.id), ())
    outcome = asyncio.run(collect.collect_source(
        db, None, store, due, cfg=settings,
        budget=budget or collect.TimeBudget(60), campus_map={},
    ))
    db.commit()
    return outcome


def test_unverified_order_does_not_stop_at_old_item(session_factory, settings, store, monkeypatch):
    settings = replace(settings, initial_window_start=date(2026, 3, 1), list_page_limit=5)
    with session_factory() as db:
        source = _seed(db)
        board = Board({1: [item(1, "2026-02-28")], 2: [item(2, "2026-03-01")]})
        result = run(db, source, settings, store, board, monkeypatch)
        assert board.read_pages == [1, 2]
        assert board.read_details == ["2"]
        assert result.backfill_complete


def test_broken_order_falls_back_and_reads_later_in_scope_item(session_factory, settings, store, monkeypatch):
    settings = replace(settings, initial_window_start=date(2026, 3, 1), list_page_limit=10)
    with session_factory() as db:
        source = _seed(db)
        config = db.scalar(select(m.SourceConfigVersion))
        config.config = {**config.config, "date_ordered": True}
        board = Board({
            1: [item(1, None)],
            2: [item(2, "2025-01-01")],
            3: [item(3, "2026-03-01")],
        })
        result = run(db, source, settings, store, board, monkeypatch, ordered=True)
        assert result.backfill_complete
        assert board.read_pages == [1, 2, 3]
        assert repo.item_for_listing(db, source.id, "3") is not None
        active = db.scalar(select(m.SourceConfigVersion).where(m.SourceConfigVersion.is_active.is_(True)))
        assert active.version == 2
        assert active.config["date_ordered"] is False


def test_order_flag_without_evidence_cannot_skip_later_date(session_factory, settings, store, monkeypatch):
    settings = replace(settings, initial_window_start=date(2026, 3, 1), list_page_limit=5)
    board = Board({1: [item(1, "2025-01-01")], 2: [item(2, "2026-03-01")]})
    monkeypatch.setattr(collect, "get_adapter", lambda name: board)
    with session_factory() as db:
        source = _seed(db)
        due = repo.DueSource(source, {**CONFIG, "date_ordered": True}, 60, db.get(m.SourceHealth, source.id), ())
        result = asyncio.run(collect.collect_source(
            db, None, store, due, cfg=settings, budget=collect.TimeBudget(60), campus_map={},
        ))
        assert result.backfill_complete
        assert board.read_pages == [1, 2]
        assert repo.item_for_listing(db, source.id, "2") is not None


def test_pinned_outside_window_and_date_boundary(session_factory, settings, store, monkeypatch):
    settings = replace(settings, initial_window_start=date(2026, 3, 1), list_page_limit=5)
    with session_factory() as db:
        source = _seed(db)
        board = Board({1: [item(1, "2026-02-01", True), item(2, "2026-03-01")], 2: [item(3, "2026-02-28")], 3: [item(4, "2026-01-01")]})
        result = run(db, source, settings, store, board, monkeypatch, ordered=True)
        assert board.read_details == ["1", "2"]
        assert result.scan_stop_reason == "date_boundary"
        assert not result.missing_check_performed


def test_detail_failure_blocks_completion_and_retries_without_listing(session_factory, settings, store, monkeypatch):
    with session_factory() as db:
        source = _seed(db)
        board = Board({1: [item(1), item(2)]})
        board.failed.add("2")
        result = run(db, source, settings, store, board, monkeypatch)
        assert not result.backfill_complete
        assert db.get(m.SourceHealth, source.id).backfill_status == "detail_pending"
        failed = repo.item_for_listing(db, source.id, "2")
        failed.next_detail_attempt_after = utcnow() - timedelta(seconds=1)
        board.failed.clear()
        board.pages = {1: [item(1)]}
        result = run(db, source, settings, store, board, monkeypatch)
        assert result.backfill_complete
        assert failed.last_detail_error is None
        assert len(db.scalars(select(m.Notice)).all()) == 2


def test_resume_checks_latest_and_shifted_anchor(session_factory, settings, store, monkeypatch):
    settings = replace(settings, list_page_limit=3)
    with session_factory() as db:
        source = _seed(db)
        board = Board({i: [item(i)] for i in range(1, 8)})
        run(db, source, settings, store, board, monkeypatch)
        assert db.get(m.SourceHealth, source.id).backfill_cursor_external_id == "3"
        board.pages = {1: [item(100)], **{i + 1: [item(i)] for i in range(1, 8)}}
        board.read_pages.clear()
        run(db, source, settings, store, board, monkeypatch)
        assert board.read_pages[0] == 1
        assert repo.item_for_listing(db, source.id, "100") is not None
        assert db.get(m.SourceHealth, source.id).backfill_cursor_external_id == "5"
        # 겹침 페이지는 반복되지만 원본이 중복 저장되지는 않는다.
        assert len(db.scalars(select(m.SourceItem)).all()) == 6


def test_missing_anchor_resets_without_claiming_complete(session_factory, settings, store, monkeypatch):
    with session_factory() as db:
        source = _seed(db)
        health = db.get(m.SourceHealth, source.id)
        health.backfill_cursor_page, health.backfill_cursor_external_id = 5, "gone"
        board = Board({i: [item(i)] for i in range(1, 7)})
        # 최신 구간을 먼저 저장해 기존 구간임을 확인할 수 있게 한다.
        settings = replace(settings, list_page_limit=2)
        for listed in board.pages[1]:
            repo.upsert_item_and_revision(db, source=source, listed=listed,
                detail=asyncio.run(board.detail(None, {}, listed)),
                published=collect.date_rules.parse_published(listed.published_raw), raw_object_key=None, extractor_version="test")
        result = run(db, source, settings, store, board, monkeypatch)
        assert result.scan_stop_reason == "anchor_missing"
        assert health.backfill_cursor_page == 1
        assert not health.backfill_complete


def test_pinned_known_items_do_not_hide_new_normal_pages(session_factory, settings, store, monkeypatch):
    with session_factory() as db:
        source = _seed(db)
        board = Board({1: [item(1, pinned=True), item(2, pinned=True), item(3)]})
        run(db, source, settings, store, board, monkeypatch)
        board.pages = {1: [item(1, pinned=True), item(2, pinned=True), item(4)], 2: [item(5), item(3)]}
        board.read_pages.clear()
        run(db, source, settings, store, board, monkeypatch)
        assert board.read_pages == [1, 2]
        assert repo.item_for_listing(db, source.id, "5") is not None


def test_zero_budget_keeps_resume_position(session_factory, settings, store, monkeypatch):
    with session_factory() as db:
        source = _seed(db)
        health = db.get(m.SourceHealth, source.id)
        health.backfill_cursor_page, health.backfill_cursor_external_id = 4, "4"
        result = run(db, source, settings, store, Board({1: [item(1)]}), monkeypatch, budget=collect.TimeBudget(0))
        assert result.partial
        assert health.backfill_cursor_page == 4
        assert health.backfill_cursor_external_id == "4"


def test_one_page_budget_eventually_reaches_end(session_factory, settings, store, monkeypatch):
    settings = replace(settings, list_page_limit=1)
    with session_factory() as db:
        source = _seed(db)
        board = Board({i: [item(i)] for i in range(1, 6)})
        for _ in range(6):
            result = run(db, source, settings, store, board, monkeypatch)
            if result.backfill_complete:
                break
        assert result.backfill_complete
        assert {i.external_id for i in db.scalars(select(m.SourceItem))} == {str(i) for i in range(1, 6)}


def test_old_unordered_pages_do_not_reset_history_forever(session_factory, settings, store, monkeypatch):
    settings = replace(settings, initial_window_start=date(2026, 3, 1), list_page_limit=2)
    with session_factory() as db:
        source = _seed(db)
        # 최신 앞쪽이 모두 기간 밖이어도 날짜순 미검증 게시판 뒤쪽에 새 글이 있다.
        board = Board({i: [item(i, "2026-02-01")] for i in range(1, 9)})
        board.pages[8] = [item(8, "2026-03-02")]
        cursors = []
        for _ in range(8):
            result = run(db, source, settings, store, board, monkeypatch)
            cursors.append(db.get(m.SourceHealth, source.id).backfill_cursor_page)
            if result.backfill_complete:
                break
        assert result.backfill_complete
        assert max(cursors) == 8
        assert board.read_details == ["8"]
        assert repo.item_for_listing(db, source.id, "8") is not None


def test_large_new_burst_revalidates_anchor_and_fills_shifted_gap(session_factory, settings, store, monkeypatch):
    settings = replace(settings, list_page_limit=2)
    with session_factory() as db:
        source = _seed(db)
        board = Board({i: [item(i)] for i in range(1, 8)})
        run(db, source, settings, store, board, monkeypatch)
        # 진행 기준점이 겹침 탐색 한도를 넘도록 신규 페이지가 대량 삽입된다.
        board.pages = {
            **{i: [item(100 + i)] for i in range(1, 7)},
            **{i + 6: [item(i)] for i in range(1, 8)},
        }
        first = run(db, source, settings, store, board, monkeypatch)
        assert not first.backfill_complete
        assert first.scan_stop_reason == "anchor_missing"
        for _ in range(15):
            result = run(db, source, settings, store, board, monkeypatch)
            if result.backfill_complete:
                break
        expected = {str(i) for i in range(1, 8)} | {str(100 + i) for i in range(1, 7)}
        assert result.backfill_complete
        assert {i.external_id for i in db.scalars(select(m.SourceItem))} == expected


def test_changed_window_reopens_completed_history(session_factory, settings, store, monkeypatch):
    settings = replace(settings, initial_window_start=date(2026, 3, 1), list_page_limit=5)
    with session_factory() as db:
        source = _seed(db)
        board = Board({1: [item(1, "2026-03-01")], 2: [item(2, "2026-02-01")]})
        run(db, source, settings, store, board, monkeypatch, ordered=True)
        assert repo.item_for_listing(db, source.id, "2") is None
        result = run(db, source, replace(settings, initial_window_start=date(2026, 2, 1)), store, board, monkeypatch, ordered=True)
        assert result.backfill_complete
        assert repo.item_for_listing(db, source.id, "2") is not None


def test_unexpected_failure_preserves_committed_resume_position(session_factory, settings, store, monkeypatch):
    class InterruptedBoard(Board):
        async def list_page(self, fetcher, config, page_index):
            if page_index == 2:
                raise RuntimeError("예기치 않은 실행 중단")
            return await super().list_page(fetcher, config, page_index)

    board = InterruptedBoard({1: [item(1)], 2: [item(2)]})
    monkeypatch.setattr(collect, "get_adapter", lambda name: board)
    with session_factory() as db:
        source = _seed(db)
        source_id = source.id
        due = repo.DueSource(source, CONFIG, 60, db.get(m.SourceHealth, source_id), ())
        with pytest.raises(RuntimeError, match="예기치 않은"):
            asyncio.run(collect.collect_source(
                db, None, store, due, cfg=settings, budget=collect.TimeBudget(60),
                campus_map={}, persist_progress=True,
            ))
        db.rollback()
    with session_factory() as db:
        health = db.get(m.SourceHealth, source_id)
        assert health.backfill_cursor_external_id == "1"
        assert not health.backfill_complete
        assert len(db.scalars(select(m.Notice)).all()) == 1
        complete_board = Board({1: [item(1)], 2: [item(2)]})
        result = run(db, db.get(m.Source, source_id), settings, store, complete_board, monkeypatch)
        assert result.backfill_complete
        assert len(db.scalars(select(m.Notice)).all()) == 2
