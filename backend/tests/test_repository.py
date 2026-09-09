"""저장 불변 조건 검사(4절·21.1절 수집·누락 항목)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event, func, select

from app.domain import ids
from app.domain.audiences import AudienceDecision, AudienceTarget
from app.domain.categories import classify
from app.domain.dates import NO_DEADLINE, parse_published
from app.ingestion.base import FetchedAttachment, FetchedDetail, ListedItem
from app.storage import models as m
from app.storage import repository as repo


@pytest.fixture
def db(session_factory):
    with session_factory() as session:
        yield session
        session.commit()


@pytest.fixture
def source(db):
    # 외래키 순서대로 단계별로 넣는다. sqlite 검사에서도 제약을 실제로 적용한다.
    db.add(m.University(id="univ-khu", code="khu", name="경희대학교"))
    db.flush()
    db.add(m.Campus(id="campus-seoul", university_id="univ-khu", code="seoul", name="서울캠퍼스"))
    db.add(m.Organization(id="org-1", university_id="univ-khu", org_type="department", name="시험학과"))
    db.flush()
    src = m.Source(
        id="src-1",
        organization_id="org-1",
        name="시험 공지",
        adapter="khu_board",
        list_url="https://example.khu.ac.kr/list.do?menuNo=1",
        status="active",
    )
    db.add(src)
    db.flush()
    db.add(m.SourceHealth(source_id="src-1"))
    db.flush()
    return src


def _listed(external_id="100", title="공지 제목", pinned=False) -> ListedItem:
    return ListedItem(
        external_id=external_id,
        url=f"https://example.khu.ac.kr/view.do?boardId={external_id}",
        title=title,
        published_raw="2026-03-02",
        is_pinned=pinned,
    )


def _detail(listed: ListedItem, body="본문 내용입니다.", title=None, attachments=()) -> FetchedDetail:
    return FetchedDetail(
        external_id=listed.external_id,
        url=listed.url,
        title=title or listed.title,
        body_text=body,
        body_html=f"<p>{body}</p>",
        raw_html=f"<html>{body}</html>",
        published_raw=listed.published_raw,
        attachments=attachments,
        extraction_notes={"extractor": "test/1"},
    )


def _write(db, source, listed, detail):
    return repo.upsert_item_and_revision(
        db,
        source=source,
        listed=listed,
        detail=detail,
        published=parse_published(detail.published_raw),
        raw_object_key=None,
        extractor_version="test/1",
    )


def test_same_item_collected_three_times_yields_one_item_and_one_revision(db, source):
    """4절 1항: 같은 글을 세 번 수집해도 원본 1개, 이력 중복 없음."""
    listed = _listed()
    for _ in range(3):
        _write(db, source, listed, _detail(listed))
    db.flush()

    assert db.execute(select(func.count(m.SourceItem.id))).scalar() == 1
    assert db.execute(select(func.count(m.SourceItemRevision.id))).scalar() == 1


def test_initial_defers_ordinary_recheck_but_not_changed_or_failed_item(db, source):
    listed = _listed()
    written = _write(db, source, listed, _detail(listed))
    item = db.get(m.SourceItem, written.item_id)
    item.last_detail_checked_at = datetime.now(UTC) - timedelta(days=30)
    assert repo.detail_is_due(db, source_id=source.id, listed=listed)
    assert not repo.detail_is_due(db, source_id=source.id, listed=listed, defer_ordinary_rechecks=True)
    assert repo.detail_is_due(
        db, source_id=source.id, listed=_listed(title="수정된 공지"), defer_ordinary_rechecks=True,
    )
    item.last_detail_error = "timeout"
    assert repo.detail_is_due(db, source_id=source.id, listed=listed, defer_ordinary_rechecks=True)


def test_content_change_creates_new_revision(db, source):
    listed = _listed()
    first = _write(db, source, listed, _detail(listed, body="처음 내용"))
    second = _write(db, source, listed, _detail(listed, body="바뀐 내용"))
    db.flush()

    assert first.revision_id != second.revision_id
    assert db.execute(select(func.count(m.SourceItemRevision.id))).scalar() == 2
    item = db.get(m.SourceItem, first.item_id)
    assert item.current_revision_id == second.revision_id


def test_reverted_content_is_recorded_as_a_new_event(db, source):
    """6.2절: A → B → A 면 마지막 A 도 새 변경 사건이다."""
    listed = _listed()
    _write(db, source, listed, _detail(listed, body="A"))
    _write(db, source, listed, _detail(listed, body="B"))
    _write(db, source, listed, _detail(listed, body="A"))
    db.flush()

    assert db.execute(select(func.count(m.SourceItemRevision.id))).scalar() == 3


def test_attachments_are_stored_per_revision(db, source):
    listed = _listed()
    detail = _detail(listed, attachments=(FetchedAttachment(filename="신청서.hwp", url=None, kind="hwp"),))
    written = _write(db, source, listed, detail)
    db.flush()

    rows = db.execute(
        select(m.Attachment).where(m.Attachment.revision_id == written.revision_id)
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].filename == "신청서.hwp"


def test_single_absence_does_not_remove_an_item(db, source):
    """4절 9항: 한 번 안 보인다고 삭제하지 않는다."""
    listed = _listed()
    _write(db, source, listed, _detail(listed))
    db.flush()

    for expected_streak in (1, 2):
        repo.mark_items_missing(db, source.id, seen_ids=set())
        db.flush()
        item = db.execute(select(m.SourceItem)).scalar_one()
        assert item.missing_streak == expected_streak
        assert item.original_status == "available"

    repo.mark_items_missing(db, source.id, seen_ids=set())
    db.flush()
    assert db.execute(select(m.SourceItem)).scalar_one().original_status == "removed"


def test_missing_comparison_uses_external_id_without_hiding_seen_item(db, source):
    first = _write(db, source, _listed("first"), _detail(_listed("first")))
    second = _write(db, source, _listed("second"), _detail(_listed("second")))
    db.flush()

    repo.mark_items_missing(db, source.id, seen_ids={"first"})
    db.flush()

    assert db.get(m.SourceItem, first.item_id).missing_streak == 0
    assert db.get(m.SourceItem, second.item_id).missing_streak == 1


def test_reappearing_item_resets_missing_streak(db, source):
    listed = _listed()
    _write(db, source, listed, _detail(listed))
    repo.mark_items_missing(db, source.id, seen_ids=set())
    db.flush()

    _write(db, source, listed, _detail(listed))
    db.flush()
    item = db.execute(select(m.SourceItem)).scalar_one()
    assert item.missing_streak == 0
    assert item.original_status == "available"


def test_reappearing_item_restores_automatically_removed_notice(db, source):
    listed = _listed()
    written = _write(db, source, listed, _detail(listed))
    notice, _ = _notice_for(db, written)
    db.flush()

    for _ in range(3):
        repo.mark_items_missing(db, source.id, seen_ids=set())
    db.flush()
    assert notice.status == "removed"

    _write(db, source, listed, _detail(listed))
    db.flush()
    assert notice.status == "visible"


def test_listing_repairs_available_item_removed_notice_but_not_manual_hide(db, source):
    listed = _listed()
    written = _write(db, source, listed, _detail(listed))
    notice, _ = _notice_for(db, written)
    db.flush()
    notice.status = "removed"
    repo.touch_item_from_listing(db, source_id=source.id, listed=listed)
    assert notice.status == "visible"
    notice.status = "hidden"
    repo.touch_item_from_listing(db, source_id=source.id, listed=listed)
    assert notice.status == "hidden"


def test_failed_stub_respects_retry_time(db, source):
    listed = _listed()
    repo.ensure_item_stub(db, source=source, listed=listed)
    repo.mark_detail_failure(db, source_id=source.id, external_id=listed.external_id, message="실패")
    assert not repo.detail_is_due(db, source_id=source.id, listed=listed)
    assert repo.pending_detail_listings(db, source.id) == []


def test_old_pinned_detail_is_rechecked_daily(db, source):
    listed = _listed(pinned=True)
    written = _write(db, source, listed, _detail(listed))
    stored = db.get(m.SourceItem, written.item_id)
    stored.last_detail_checked_at = datetime.now(UTC) - timedelta(days=2)
    assert repo.detail_is_due(db, source_id=source.id, listed=listed)


def _notice_for(db, written, title="공지 제목", body="본문"):
    revision = db.get(m.SourceItemRevision, written.revision_id)
    return repo.upsert_notice_for_item(
        db,
        item_id=written.item_id,
        revision=revision,
        category=classify(title, body),
        audience=AudienceDecision(targets=(AudienceTarget("undetermined", None, "대상 미확정"),), note=None),
        deadline=NO_DEADLINE,
    )


def test_notice_keeps_first_visible_time_across_recollection(db, source):
    """8.2절: 재수집·수정으로 최초 노출 시각이 바뀌지 않는다."""
    listed = _listed()
    written = _write(db, source, listed, _detail(listed, body="처음"))
    notice, created = _notice_for(db, written)
    db.flush()
    original = notice.first_visible_at
    assert created is True

    written2 = _write(db, source, listed, _detail(listed, body="수정됨"))
    notice2, created2 = _notice_for(db, written2)
    db.flush()

    assert created2 is False
    assert notice2.id == notice.id
    assert notice2.first_visible_at == original


def test_only_one_active_link_per_item(db, source):
    listed = _listed()
    written = _write(db, source, listed, _detail(listed))
    _notice_for(db, written)
    _notice_for(db, written)
    db.flush()

    active = db.execute(
        select(func.count(m.NoticeSource.id)).where(
            m.NoticeSource.source_item_id == written.item_id, m.NoticeSource.is_active.is_(True)
        )
    ).scalar()
    assert active == 1


def test_notice_has_exactly_one_primary_category(db, source):
    listed = _listed(title="[장학] 국가장학금 신청 안내")
    written = _write(db, source, listed, _detail(listed, title=listed.title, body="장학금 신청"))
    notice, _ = _notice_for(db, written, title=listed.title, body="장학금 신청")
    db.flush()

    primaries = db.execute(
        select(func.count(m.NoticeCategory.id)).where(
            m.NoticeCategory.notice_id == notice.id, m.NoticeCategory.is_primary.is_(True)
        )
    ).scalar()
    assert primaries == 1


def test_merge_moves_bookmarks_and_leaves_redirect(db, source):
    """9.3절: 병합해도 저장한 공지가 끊기지 않는다."""
    first = _write(db, source, _listed("1"), _detail(_listed("1")))
    second = _write(db, source, _listed("2"), _detail(_listed("2")))
    keep, _ = _notice_for(db, first)
    absorb, _ = _notice_for(db, second)
    db.add(m.Bookmark(user_id="user-1", notice_id=absorb.id))
    db.flush()

    repo.merge_notices(db, keep_notice_id=keep.id, absorb_notice_id=absorb.id, reason="본문 일치")
    db.flush()

    assert db.get(m.Notice, absorb.id).status == "hidden"
    assert db.get(m.NoticeRedirect, absorb.id).to_notice_id == keep.id
    bookmark = db.execute(select(m.Bookmark)).scalar_one()
    assert bookmark.notice_id == keep.id


def test_removing_one_source_keeps_notice_with_other_live_sources(db, source):
    """9.3절: 한 출처가 사라져도 다른 살아 있는 원본이 있으면 공지를 지우지 않는다."""
    first = _write(db, source, _listed("1"), _detail(_listed("1")))
    second = _write(db, source, _listed("2"), _detail(_listed("2")))
    keep, _ = _notice_for(db, first)
    absorb, _ = _notice_for(db, second)
    repo.merge_notices(db, keep_notice_id=keep.id, absorb_notice_id=absorb.id, reason="본문 일치")
    db.flush()

    for _ in range(3):
        repo.mark_items_missing(db, source.id, seen_ids={second.item_id})
    db.flush()

    assert db.get(m.SourceItem, first.item_id).original_status == "removed"
    assert db.get(m.Notice, keep.id).status == "visible"


def test_failure_backs_off_and_blocked_source_is_stopped(db, source):
    health = db.get(m.SourceHealth, source.id)
    now = datetime.now(UTC)

    repo.mark_source_failure(db, health, kind="server_error", message="500", now=now)
    assert health.consecutive_failures == 1
    assert health.next_attempt_after == now + timedelta(hours=1)

    repo.mark_source_failure(db, health, kind="server_error", message="500", now=now)
    assert health.next_attempt_after == now + timedelta(hours=2)

    repo.mark_source_failure(db, health, kind="access_denied", message="403", now=now)
    db.flush()
    assert db.get(m.Source, source.id).status == "blocked"


def test_success_clears_failure_state(db, source):
    health = db.get(m.SourceHealth, source.id)
    repo.mark_source_failure(db, health, kind="timeout", message="느림")
    repo.mark_source_success(db, health, detail_complete=True)

    assert health.consecutive_failures == 0
    assert health.next_attempt_after is None
    assert health.last_list_success_at is not None


def test_attempt_and_success_times_are_separate(db, source):
    """4절 8항: 시도했다는 것과 성공했다는 것은 다르다."""
    health = db.get(m.SourceHealth, source.id)
    repo.mark_source_failure(db, health, kind="timeout", message="느림")

    assert health.last_attempt_at is not None
    assert health.last_list_success_at is None


def test_abandoned_run_is_marked_on_next_start(db):
    old = m.Run(
        id=ids.run_id(),
        kind="collect",
        started_at=datetime.now(UTC) - timedelta(hours=2),
    )
    db.add(old)
    db.flush()

    repo.start_run(db)
    db.flush()
    assert db.get(m.Run, old.id).result == "abandoned"


def test_recent_run_is_not_marked_abandoned(db):
    recent = m.Run(id=ids.run_id(), kind="collect", started_at=datetime.now(UTC))
    db.add(recent)
    db.flush()

    repo.start_run(db)
    db.flush()
    assert db.get(m.Run, recent.id).result is None


def test_due_sources_respect_interval_and_backoff(db, source):
    db.add(
        m.SourceConfigVersion(
            id="cfg-1", source_id=source.id, version=1, config={}, interval_minutes=60, is_active=True
        )
    )
    db.flush()
    now = datetime.now(UTC)

    assert len(repo.load_due_sources(db, now=now)) == 1

    health = db.get(m.SourceHealth, source.id)
    health.last_attempt_at = now
    db.flush()
    assert repo.load_due_sources(db, now=now) == []
    assert len(repo.load_due_sources(db, now=now + timedelta(minutes=61))) == 1

    health.next_attempt_after = now + timedelta(hours=6)
    db.flush()
    assert repo.load_due_sources(db, now=now + timedelta(minutes=61)) == []


def test_pending_sources_are_not_collected_automatically(db, source):
    """5.2절: 검증하지 않은 출처를 수집 중으로 만들지 않는다."""
    db.add(
        m.SourceConfigVersion(
            id="cfg-p", source_id=source.id, version=1, config={}, interval_minutes=60, is_active=True
        )
    )
    source.status = "pending"
    db.flush()

    assert repo.load_due_sources(db) == []

    source.status = "active"
    db.flush()
    assert len(repo.load_due_sources(db)) == 1


def test_same_dedupe_pair_can_be_recorded_twice(db, source):
    """같은 두 글을 다음 실행에서 다시 비교해도 멈추지 않아야 한다.

    판정 열쇠가 (좌항목, 우항목, 좌개정, 우개정) 에서 만들어지므로 실행마다 같다.
    예전에는 두 번째 실행이 중복 열쇠로 죽어 수집 전체가 멈췄다.
    """
    left = _listed(external_id="201", title="첫 번째 공지")
    right = _listed(external_id="202", title="두 번째 공지")
    left_written = _write(db, source, left, _detail(left))
    right_written = _write(db, source, right, _detail(right))
    db.flush()

    def record(score: float) -> None:
        repo.record_dedupe_decision(
            db,
            left_item=left_written.item_id,
            right_item=right_written.item_id,
            left_revision=left_written.revision_id,
            right_revision=right_written.revision_id,
            decision="distinct",
            score=score,
            signals={"title": score},
            rule_version="test/1",
        )

    record(0.60)
    db.flush()
    record(0.75)  # 다음 실행이 같은 쌍을 다시 판정한다.
    db.flush()

    rows = db.execute(select(m.DedupeDecision)).scalars().all()
    assert len(rows) == 1
    # 다시 판정한 값으로 갱신된다.
    assert float(rows[0].score) == pytest.approx(0.75)


def test_preload_listing_items_does_not_read_bodies(db, source, engine, session_factory):
    """미리 읽기는 본문 두 칸을 select 하지 않아야 한다.

    수집 경로가 여기서 올린 기존 이력에서 보는 것은 제목·발행일·내용 지문·게시판
    분류뿐이다. 본문은 이력 한 건 평균 2.3KB 라 회차당 12.8MB 중 11.4MB 를 차지해
    Supabase egress 예산을 수집 혼자 넘겼다(2026-09-09 측정). defer 로 뺀다.
    """
    listed = _listed(external_id="300", title="본문 있는 공지")
    body = "아주 긴 본문입니다. " * 50
    written = _write(db, source, listed, _detail(listed, body=body))
    db.commit()

    statements: list[str] = []

    def _capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", _capture)
    try:
        with session_factory() as fresh:
            items, revisions = repo.preload_listing_items(fresh, source.id, [listed])
            assert len(items) == 1 and len(revisions) == 1

            revision_sql = [sql for sql in statements if "source_item_revisions" in sql]
            assert revision_sql, "이력 조회 문장을 가로채지 못했다"
            for sql in revision_sql:
                assert "body_html" not in sql, sql
                assert "body_text" not in sql, sql
            # 수집이 실제로 쓰는 칸은 그대로 들어 있어야 한다.
            assert "content_hash" in revision_sql[0]
            assert "published_raw" in revision_sql[0]
            assert "board_category" in revision_sql[0]

            revision = revisions[0]
            assert revision.id == written.revision_id
            # 본문은 아직 세션에 올라오지 않았다.
            assert "body_text" not in revision.__dict__
            assert "body_html" not in revision.__dict__
            # 제목은 추가 조회 없이 읽힌다.
            before = len(statements)
            assert revision.title == listed.title
            assert len(statements) == before
            # 본문을 만지면 그때 한 번 더 읽어 오고 값은 그대로다.
            assert revision.body_text == body
            assert len(statements) > before
    finally:
        event.remove(engine, "before_cursor_execute", _capture)
