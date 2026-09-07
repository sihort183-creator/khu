"""수집 경계·중단·재개·실패 복구가 실제 저장 결과에 미치는 영향."""

import asyncio
from dataclasses import replace
from datetime import date, timedelta

import pytest
from sqlalchemy import select
from test_pipeline import CONFIG, _seed

from app.domain.dates import utcnow
from app.ingestion.base import FetchedDetail, ListedItem, ListPage, ParseError, RestrictedError
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


def test_page_entirely_older_than_window_stops_without_order_proof(session_factory, settings, store, monkeypatch):
    """날짜순 판정이 없어도 쪽 전체가 범위 밖이면 그 자리에서 멈춘다(안전망)."""
    settings = replace(settings, initial_window_start=date(2026, 3, 1), list_page_limit=5)
    with session_factory() as db:
        source = _seed(db)
        board = Board({1: [item(1, "2026-02-28")], 2: [item(2, "2026-03-01")]})
        result = run(db, source, settings, store, board, monkeypatch)
        assert board.read_pages == [1]
        assert board.read_details == []
        assert result.scan_stop_reason == "date_boundary"
        assert result.backfill_complete


def test_unknown_date_drops_order_proof_and_page_rule_still_ends_range(
    session_factory, settings, store, monkeypatch
):
    """날짜 미상은 정렬 검증을 폐기하지만, 범위 밖 쪽 규칙이 그대로 범위를 끝낸다."""
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
        # 1쪽은 날짜를 몰라 멈추지 않고, 2쪽이 전부 범위 밖이라 거기서 끝낸다.
        assert board.read_pages == [1, 2]
        active = db.scalar(select(m.SourceConfigVersion).where(m.SourceConfigVersion.is_active.is_(True)))
        assert active.version == 2
        assert active.config["date_ordered"] is False


def test_order_flag_without_evidence_still_ends_at_first_old_page(session_factory, settings, store, monkeypatch):
    """검증 증거가 없어도 쪽 전체가 범위 밖이면 더 내려가지 않는다."""
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
        assert board.read_pages == [1]
        assert repo.item_for_listing(db, source.id, "2") is None


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


def test_old_front_pages_end_range_instead_of_walking_to_board_end(session_factory, settings, store, monkeypatch):
    """앞쪽이 전부 범위 밖이면 뒤쪽에 범위 안 글이 있어도 게시판 끝까지 가지 않는다.

    사용자 결정(2026-09-07): "3월까지만 읽고 2월 나오면 멈춘다." 뒤쪽에 섞인 범위 안
    글을 놓칠 수는 있으나, 2011~2018년까지 끝없이 내려가는 회차를 막는다.
    """
    settings = replace(settings, initial_window_start=date(2026, 3, 1), list_page_limit=2)
    with session_factory() as db:
        source = _seed(db)
        board = Board({i: [item(i, "2026-02-01")] for i in range(1, 9)})
        board.pages[8] = [item(8, "2026-03-02")]
        result = run(db, source, settings, store, board, monkeypatch)
        assert result.backfill_complete
        assert board.read_pages == [1]
        assert db.get(m.SourceHealth, source.id).backfill_cursor_page == 1


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


def test_mixed_page_does_not_end_the_range(session_factory, settings, store, monkeypatch):
    """쪽에 범위 안 글이 하나라도 있으면 멈추지 않는다. 판단 단위는 글이 아니라 쪽이다."""
    settings = replace(
        settings, initial_window_start=date(2026, 3, 1), list_page_limit=10,
        optimistic_date_boundary=True,
    )
    with session_factory() as db:
        source = _seed(db)
        board = Board({
            1: [item(1, "2026-03-20")],
            2: [item(2, "2026-02-25"), item(3, "2026-03-05")],
            3: [item(4, "2026-03-10")],
        })
        run(db, source, settings, store, board, monkeypatch)
        assert board.read_pages == [1, 2, 3]
        assert repo.item_for_listing(db, source.id, "4") is not None


def test_optimistic_boundary_stops_at_the_first_old_page(session_factory, settings, store, monkeypatch):
    """범위 밖 쪽을 만나면 그 자리에서 멈추고 뒤쪽을 더 읽지 않는다."""
    settings = replace(
        settings, initial_window_start=date(2026, 3, 1), list_page_limit=10,
        optimistic_date_boundary=True,
    )
    with session_factory() as db:
        source = _seed(db)
        board = Board({
            1: [item(1, "2026-03-20")],
            2: [item(2, "2026-02-25")],
            3: [item(3, "2026-02-10")],
            4: [item(4, "2026-02-01")],
        })
        result = run(db, source, settings, store, board, monkeypatch)
        assert board.read_pages == [1, 2]
        assert db.get(m.SourceHealth, source.id).last_scan_stop_reason == "date_boundary"
        assert result.backfill_complete


def test_optimistic_boundary_is_not_assumed_again_after_inversion(session_factory, settings, store, monkeypatch):
    """정렬이 깨진 게시판은 설정에 기록해 다음 회차부터 가정을 쓰지 않는다."""
    settings = replace(
        settings, initial_window_start=date(2026, 3, 1), list_page_limit=10,
        optimistic_date_boundary=True,
    )
    with session_factory() as db:
        source = _seed(db)
        # 범위 안에서 역전이 드러나므로 날짜 경계 종료 전에 가정이 폐기된다.
        board = Board({
            1: [item(1, "2026-03-20"), item(2, "2026-04-01")],
            2: [item(3, "2026-03-10")],
        })
        run(db, source, settings, store, board, monkeypatch)
        active = db.scalar(select(m.SourceConfigVersion).where(m.SourceConfigVersion.is_active.is_(True)))
        assert active.config["date_ordered"] is False
        assert active.config["date_order_invalidated"]["reason"] == "date_inversion"


def test_verified_order_still_stops_at_the_first_old_page(session_factory, settings, store, monkeypatch):
    """검증을 통과한 출처는 예전대로 한 쪽만 보고 멈춘다. 낙관 모드가 이를 늦추지 않는다."""
    settings = replace(
        settings, initial_window_start=date(2026, 3, 1), list_page_limit=10,
        optimistic_date_boundary=True,
    )
    with session_factory() as db:
        source = _seed(db)
        board = Board({
            1: [item(1, "2026-03-20")],
            2: [item(2, "2026-02-25")],
            3: [item(3, "2026-02-10")],
        })
        run(db, source, settings, store, board, monkeypatch, ordered=True)
        assert board.read_pages == [1, 2]


def test_login_walled_item_is_kept_from_the_listing(session_factory, settings, store, monkeypatch):
    """로그인해야 볼 수 있는 글도 목록 정보만으로 남긴다.

    본문이 없다고 빼 버리면 미래인재센터 채용 게시판처럼 게시판 하나가 통째로 없는 것처럼
    보인다. 2026-09-07 운영에서 1,579건이 이렇게 사라지고 있었다.
    """
    settings = replace(settings, initial_window_start=date(2026, 3, 1), list_page_limit=5)
    with session_factory() as db:
        source = _seed(db)
        board = Board({1: [item(1, "2026-09-01"), item(2, "2026-09-02")]})

        async def detail(fetcher, config, listed):
            if listed.external_id == "1":
                raise RestrictedError("상세를 열 권한이 없거나 로그인이 필요한 게시글입니다.")
            return await Board.detail(board, fetcher, config, listed)

        board.detail = detail
        run(db, source, settings, store, board, monkeypatch)

        locked = repo.item_for_listing(db, source.id, "1")
        assert locked is not None
        # 공지가 만들어져야 목록에 나온다. 예전에는 여기서 사라졌다.
        assert locked.current_revision_id is not None
        assert locked.original_status == "restricted"
        notice = db.scalar(select(m.Notice).where(m.Notice.primary_source_item_id == locked.id))
        assert notice is not None and notice.status == "visible"
        assert notice.title == "공지 1"
        # 본문은 없다. 있는 척하지 않는다.
        revision = db.get(m.SourceItemRevision, locked.current_revision_id)
        assert not revision.body_text
        assert revision.raw_object_key is None


def test_board_read_to_the_end_finishes_even_if_details_never_open(
    session_factory, settings, store, monkeypatch
):
    """끝까지 읽은 게시판은 열리지 않는 상세 때문에 영원히 미완으로 남지 않는다.

    2026-09-07 미래인재센터 프로그램 신청은 게시판 끝까지 읽고도(end_of_board)
    완료로 넘어가지 못했다. 글 20개가 전부 로그인해야 열리는 글이라 상세 실패가
    비지 않았고, 완료 조건이 "경계 도달 + 실패 0" 이었기 때문이다. 몇 번을 더 돌아도
    실패가 사라지지 않는 게시판인데, 완료가 되지 않으면 초기 백필 감독이 남은 곳으로
    계속 세어 회차를 끝없이 이어받는다.

    재시도는 그대로 계속한다. 다만 `DETAIL_RETRY_LIMIT` 번을 넘게 실패한 글은
    더 해 볼 것이 없는 글로 보고 게시판 완료를 막지 않는다.
    """
    settings = replace(settings, initial_window_start=date(2026, 3, 1), list_page_limit=5)
    with session_factory() as db:
        source = _seed(db)
        board = Board({1: [item(1, "2026-08-01"), item(2, "2026-08-02")]})
        board.failed = {"1", "2"}
        health = db.get(m.SourceHealth, source.id)

        for attempt in range(1, repo.DETAIL_RETRY_LIMIT + 1):
            result = run(db, source, settings, store, board, monkeypatch)
            assert result.scan_stop_reason == "end_of_board"
            assert health.backfill_boundary_reached is True
            if attempt < repo.DETAIL_RETRY_LIMIT:
                assert health.backfill_complete is False, f"{attempt}회차"
                assert health.backfill_status == "detail_pending"
            # 다음 회차가 재시도하도록 물러서기 시간만 앞당긴다.
            for row in db.scalars(select(m.SourceItem)):
                row.next_detail_attempt_after = utcnow() - timedelta(seconds=1)
            db.commit()

        assert health.backfill_complete is True
        assert health.backfill_status == "complete"
        # 포기한 것이 아니다. 실패 기록은 남아 있고 다음 회차도 다시 열어 본다.
        rows = db.scalars(select(m.SourceItem)).all()
        assert all(row.last_detail_error for row in rows)
        assert len(board.read_details) == repo.DETAIL_RETRY_LIMIT * 2

        # 원문이 열리면 그다음 회차가 그대로 채운다.
        board.failed.clear()
        run(db, source, settings, store, board, monkeypatch)
        assert all(row.last_detail_error is None for row in db.scalars(select(m.SourceItem)))
        assert health.backfill_complete is True


class TimestampBoard(Board):
    """목록은 날짜만, 상세는 시각까지 주는 게시판.

    경희 공통 게시판(khu_board)이 실제로 이렇다. 목록 "2026-08-01",
    상세 "2026-08-01 12:54:28.0".
    """

    async def detail(self, fetcher, config, listed):
        detail = await super().detail(fetcher, config, listed)
        raw = f"{listed.published_raw} 12:54:28.0" if listed.published_raw else None
        return replace(detail, published_raw=raw)


def test_list_date_only_notation_does_not_refetch_known_details(
    session_factory, settings, store, monkeypatch
):
    """목록의 날짜 표기가 저장된 상세 표기와 달라도 같은 날짜면 다시 받지 않는다.

    2026-09-07 운영 실측: khu_board 출처의 이력 12,742/13,863 건이 상세에서 온
    "2026-07-14 12:54:28.0" 꼴을 들고 있고 목록은 "2026-07-14" 만 준다.
    글자 그대로 비교하던 예전 판정은 이것을 매번 "발행일이 바뀌었다" 로 읽어
    이미 가진 글의 상세를 회차마다 다시 받았다. 3시간 동안 기존 글 재요청 2,859건,
    그중 내용이 실제로 달라진 것은 16건이었다.
    """
    settings = replace(settings, list_page_limit=5)
    with session_factory() as db:
        source = _seed(db)
        board = TimestampBoard({1: [item(1), item(2)]})
        run(db, source, settings, store, board, monkeypatch)
        assert board.read_details == ["1", "2"]

        board.read_details.clear()
        run(db, source, settings, store, board, monkeypatch)
        assert board.read_details == []
        # 글은 그대로 남아 있다. 덜 받았다고 빠지지 않는다.
        assert {i.external_id for i in db.scalars(select(m.SourceItem))} == {"1", "2"}


def test_changed_publish_date_still_refetches(session_factory, settings, store, monkeypatch):
    """진짜로 날짜가 바뀐 글은 예전대로 다시 받는다."""
    settings = replace(settings, list_page_limit=5)
    with session_factory() as db:
        source = _seed(db)
        board = TimestampBoard({1: [item(1, "2026-08-01")]})
        run(db, source, settings, store, board, monkeypatch)
        board.read_details.clear()
        board.pages = {1: [item(1, "2026-08-02")]}
        run(db, source, settings, store, board, monkeypatch)
        assert board.read_details == ["1"]


def test_unreadable_list_date_still_refetches(session_factory, settings, store, monkeypatch):
    """날짜를 읽을 수 없는 표기는 예전대로 변화로 본다. 덜 받는 쪽으로 기울지 않는다."""
    settings = replace(settings, list_page_limit=5)
    with session_factory() as db:
        source = _seed(db)
        board = TimestampBoard({1: [item(1, "2026-08-01")]})
        run(db, source, settings, store, board, monkeypatch)
        board.read_details.clear()
        board.pages = {1: [item(1, "어제")]}
        run(db, source, settings, store, board, monkeypatch)
        assert board.read_details == ["1"]


def test_timestamped_board_backfills_without_skipping_any_item(
    session_factory, settings, store, monkeypatch
):
    """상세가 시각을 주는 게시판도 예산 안에서 끝까지 읽고 한 글도 빠뜨리지 않는다.

    2026-09-07 운영에서 이런 게시판 여섯 곳이 회차마다 시간 상한에 걸려 커서가
    다섯 쪽에 못 박혀 있었다. 이미 가진 글의 상세를 다시 받느라 예산을 다 쓴 탓이다.
    한 회차에 한 쪽만 넘길 수 있게 조여 두고 여러 회차를 돌려, 진행 위치가 앞으로만
    가고 어느 글도 건너뛰지 않는지 고정한다.
    """
    settings = replace(settings, list_page_limit=1, initial_window_start=None)
    with session_factory() as db:
        source = _seed(db)
        board = TimestampBoard({page: [item(page * 10 + n) for n in range(3)] for page in range(1, 7)})
        expected = {str(page * 10 + n) for page in range(1, 7) for n in range(3)}
        for _ in range(12):
            result = run(db, source, settings, store, board, monkeypatch)
            if result.backfill_complete:
                break
        assert result.backfill_complete
        assert {i.external_id for i in db.scalars(select(m.SourceItem))} == expected
        # 각 글의 상세는 딱 한 번씩만 받는다(고정 글·실패 글은 없다).
        assert sorted(board.read_details) == sorted(expected)


def test_invalidated_order_stops_at_old_page_and_cursor_does_not_advance(
    session_factory, settings, store, monkeypatch
):
    """(가) 날짜순 판정이 해제된 출처도 범위 밖 쪽에서 완료되고 커서가 더 나가지 않는다."""
    settings = replace(settings, initial_window_start=date(2026, 3, 1), list_page_limit=5)
    with session_factory() as db:
        source = _seed(db)
        config = db.scalar(select(m.SourceConfigVersion))
        config.config = {**config.config, "date_ordered": False,
                         "date_order_invalidated": {"reason": "date_inversion"}}
        board = Board({
            1: [item(1, "2026-08-01")],
            2: [item(2, "2013-05-01"), item(3, "2012-01-01")],
            3: [item(4, "2011-08-05")],
        })
        result = run(db, source, settings, store, board, monkeypatch)
        assert board.read_pages == [1, 2]
        assert result.scan_stop_reason == "date_boundary"
        assert result.backfill_complete
        health = db.get(m.SourceHealth, source.id)
        assert health.backfill_boundary_reached
        assert health.backfill_cursor_page == 2
        # 다음 회차는 최신 구간만 다시 본다. 게시판 끝(3쪽)으로 더 내려가지 않는다.
        board.read_pages.clear()
        run(db, source, settings, store, board, monkeypatch)
        assert board.read_pages == [1]


def test_single_old_pinned_notice_does_not_stop_the_page(session_factory, settings, store, monkeypatch):
    """(나) 고정 공지 하나만 오래되고 나머지가 범위 안이면 멈추지 않는다."""
    settings = replace(settings, initial_window_start=date(2026, 3, 1), list_page_limit=5)
    with session_factory() as db:
        source = _seed(db)
        board = Board({
            1: [item(1, "2011-01-01", True), item(2, "2026-08-01")],
            2: [item(3, "2026-07-01")],
        })
        result = run(db, source, settings, store, board, monkeypatch)
        assert board.read_pages == [1, 2]
        assert result.scan_stop_reason == "end_of_board"
        assert repo.item_for_listing(db, source.id, "3") is not None


def test_page_without_any_known_date_does_not_stop(session_factory, settings, store, monkeypatch):
    """(다) 날짜를 아는 글이 하나도 없는 쪽에서는 멈추지 않는다."""
    settings = replace(settings, initial_window_start=date(2026, 3, 1), list_page_limit=5)
    with session_factory() as db:
        source = _seed(db)
        board = Board({
            1: [item(1, None), item(2, None)],
            2: [item(3, "2026-07-01")],
        })
        result = run(db, source, settings, store, board, monkeypatch)
        assert board.read_pages == [1, 2]
        assert result.scan_stop_reason == "end_of_board"
        assert repo.item_for_listing(db, source.id, "3") is not None
