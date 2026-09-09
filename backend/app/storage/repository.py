"""저장 규칙.

여기가 4절 불변 조건을 실제로 지키는 곳이다.
- 원본 게시물 1개(출처+원본 식별자 유일키), 수정은 새 이력
- 같은 내용 재요청은 이력을 만들지 않되, A→B→A 는 새 사건으로 남긴다
- 원본별 활성 공지 연결은 최대 1개
- 한 번의 누락으로 삭제하지 않는다
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, defer

from app.domain import ids
from app.domain.audiences import AudienceDecision, AudienceTarget
from app.domain.categories import CategoryDecision
from app.domain.dates import DeadlineGuess, ParsedDate, parse_published, utcnow
from app.domain.dedupe import content_hash as make_content_hash
from app.ingestion.base import FetchedDetail, ListedItem
from app.ingestion.sanitize import make_excerpt
from app.storage import models as m

# 목록에서 사라졌다고 바로 지우지 않는다. 이 횟수만큼 연속으로 사라져야 상태를 바꾼다(4절 9항).
MISSING_STREAK_FOR_REMOVED = 3

# 연속 실패 시 다음 시도 간격(시간). 최대 24시간에서 멈춘다(7.4절).
BACKOFF_HOURS = (1, 2, 4, 8, 24)

SEARCH_TEXT_LIMIT = 4000


@dataclass
class ItemWriteResult:
    item_id: str
    revision_id: str
    is_new_item: bool
    is_new_revision: bool
    content_hash: str


# ------------------------------------------------------------------ 출처 조회


@dataclass(frozen=True)
class DueSource:
    source: m.Source
    config: dict
    interval_minutes: int
    health: m.SourceHealth
    audience_defaults: tuple[AudienceTarget, ...]


def load_due_sources(
    session: Session, *, now: datetime | None = None, limit: int | None = None,
    initial_mode: bool = False, initial_window_start: date | None = None,
) -> list[DueSource]:
    """실행 시각이 된 출처를 고른다. 대기열 대신 이 조회가 그 역할을 한다(7.4절)."""
    now = now or utcnow()
    rows = session.execute(
        select(m.Source, m.SourceConfigVersion, m.SourceHealth)
        .join(
            m.SourceConfigVersion,
            (m.SourceConfigVersion.source_id == m.Source.id)
            & (m.SourceConfigVersion.is_active.is_(True)),
        )
        .outerjoin(m.SourceHealth, m.SourceHealth.source_id == m.Source.id)
        # 검증하지 않은 pending 출처는 자동 수집하지 않는다(5.2절).
        # 운영자가 `ops sources promote` 로 올린 뒤에만 수집 대상이 된다.
        .where(m.Source.status.in_(("active", "delayed")))
        .order_by(m.Source.id)
    ).all()

    audience_map = _source_audiences_many(session, [source.id for source, _, _ in rows])
    due: list[DueSource] = []
    for source, config_row, health in rows:
        if health is None:
            health = m.SourceHealth(source_id=source.id)
            session.add(health)
            session.flush()

        if health.next_attempt_after and _aware(health.next_attempt_after) > now:
            continue
        interval = int(config_row.interval_minutes or 60)
        initial_pending = initial_mode and (
            not health.backfill_complete
            or (initial_window_start is not None and health.initial_window_start != initial_window_start)
        )
        if health.last_attempt_at is not None and not initial_pending:
            elapsed = now - _aware(health.last_attempt_at)
            if elapsed < timedelta(minutes=interval) and health.consecutive_failures == 0:
                continue

        due.append(
            DueSource(
                source=source,
                config=dict(config_row.config or {}),
                interval_minutes=interval,
                health=health,
                audience_defaults=audience_map.get(source.id, ()),
            )
        )
    # 초기 범위를 못 채운 출처를 먼저 재개하되, 최신 확인이 오래 밀린 출처도
    # 같은 실행에서 뒤로 밀리지 않게 한다.
    due.sort(
        key=lambda item: (
            _aware(item.health.last_attempt_at) if item.health.last_attempt_at else datetime.min.replace(tzinfo=UTC),
            bool(getattr(item.health, "backfill_complete", False)),
            item.source.id,
        )
    )
    return due[:limit] if limit is not None else due


def _source_audiences(session: Session, source_id: str) -> tuple[AudienceTarget, ...]:
    return _source_audiences_many(session, [source_id]).get(source_id, ())


def _source_audiences_many(session: Session, source_ids: list[str]) -> dict[str, tuple[AudienceTarget, ...]]:
    if not source_ids:
        return {}
    rows = session.execute(
        select(m.SourceAudience, m.Campus.name, m.Organization.name)
        .outerjoin(m.Campus, m.Campus.id == m.SourceAudience.campus_id)
        .outerjoin(m.Organization, m.Organization.id == m.SourceAudience.organization_id)
        .where(m.SourceAudience.source_id.in_(source_ids))
    ).all()
    grouped: dict[str, list[AudienceTarget]] = {}
    for row, campus_name, org_name in rows:
        out = grouped.setdefault(row.source_id, [])
        if row.audience_type == "university":
            out.append(AudienceTarget("university", None, "대학 전체"))
        elif row.audience_type == "campus" and row.campus_id:
            out.append(AudienceTarget("campus", row.campus_id, campus_name or "캠퍼스"))
        elif row.audience_type == "organization" and row.organization_id:
            out.append(AudienceTarget("organization", row.organization_id, org_name or "조직"))
        else:
            out.append(AudienceTarget("undetermined", None, "대상 미확정"))
    return {source_id: tuple(audiences) for source_id, audiences in grouped.items()}


def campus_lookup(session: Session) -> dict[str, tuple[str, str]]:
    rows = session.execute(select(m.Campus.id, m.Campus.code, m.Campus.name)).all()
    return {code: (cid, name) for cid, code, name in rows}


# ------------------------------------------------------------------ 실행 기록


def start_run(
    session: Session,
    *,
    kind: str = "collect",
    code_commit: str | None = None,
    stale_after_minutes: int = 30,
) -> m.Run:
    """실행 시작. 실제 실행 제한에 맞는 오래된 실행만 비정상 종료로 표시한다."""
    now = utcnow()
    stale = session.execute(
        select(m.Run).where(m.Run.kind == kind, m.Run.finished_at.is_(None))
    ).scalars().all()
    for old in stale:
        if now - _aware(old.started_at) > timedelta(minutes=max(1, stale_after_minutes)):
            old.finished_at = now
            old.result = "abandoned"
            old.note = "다음 실행 시작 시점에 종료되지 않은 상태로 발견됨"

    run = m.Run(id=ids.run_id(), kind=kind, started_at=now, code_commit=code_commit)
    session.add(run)
    session.flush()
    return run


def finish_run(
    session: Session,
    run: m.Run,
    *,
    result: str,
    revision: str | None = None,
    note: str | None = None,
) -> None:
    run.finished_at = utcnow()
    run.result = result
    if revision:
        run.revision = revision
    if note:
        run.note = note


def record_source_run(
    session: Session,
    run: m.Run,
    source_id: str,
    *,
    succeeded: bool,
    list_items: int = 0,
    new_items: int = 0,
    updated_items: int = 0,
    duration_ms: int | None = None,
    error_kind: str | None = None,
    error_message: str | None = None,
    detail_failures: int = 0,
    scan_stop_reason: str | None = None,
    backfill_complete: bool = False,
    missing_check_performed: bool = False,
) -> None:
    session.add(
        m.SourceRun(
            id=ids._digest(run.id, source_id, str(utcnow().timestamp())),
            run_id=run.id,
            source_id=source_id,
            succeeded=succeeded,
            list_items=list_items,
            new_items=new_items,
            updated_items=updated_items,
            duration_ms=duration_ms,
            error_kind=error_kind,
            error_message=(error_message or "")[:2000] or None,
            detail_failures=detail_failures,
            scan_stop_reason=scan_stop_reason,
            backfill_complete=backfill_complete,
            missing_check_performed=missing_check_performed,
        )
    )


def mark_source_success(
    session: Session, health: m.SourceHealth, *, detail_complete: bool, now: datetime | None = None
) -> None:
    now = now or utcnow()
    health.last_attempt_at = now
    health.last_list_success_at = now
    if detail_complete:
        health.last_detail_complete_at = now
    health.consecutive_failures = 0
    health.last_error_kind = None
    health.last_error_message = None
    health.next_attempt_after = None


def mark_source_failure(
    session: Session,
    health: m.SourceHealth,
    *,
    kind: str,
    message: str,
    now: datetime | None = None,
) -> None:
    """실패를 기록하고 다음 시도를 미룬다. 접근 차단은 출처를 멈춘다(7.4절)."""
    now = now or utcnow()
    health.last_attempt_at = now
    health.consecutive_failures = int(health.consecutive_failures or 0) + 1
    health.last_error_kind = kind
    health.last_error_message = message[:2000]
    index = min(health.consecutive_failures - 1, len(BACKOFF_HOURS) - 1)
    health.next_attempt_after = now + timedelta(hours=BACKOFF_HOURS[index])

    source = session.get(m.Source, health.source_id)
    if source is None:
        return
    if kind in ("access_denied", "rate_limited", "blocked_target"):
        source.status = "blocked"
        source.status_message = "원문 서버가 접근을 제한했습니다. 운영 검토 대상입니다."
        if kind == "blocked_target":
            health.blocked_abroad = True
    elif health.consecutive_failures >= 3:
        source.status = "delayed"
        source.status_message = "연속 실패로 갱신이 지연되고 있습니다."


# 상세 하나를 몇 번까지 "아직 해 볼 것이 남았다"고 볼지. 재시도 간격이 1·2·4·8·16
# ·16시간이므로 이 횟수를 채우려면 하루가 넘게 걸린다. 그때까지 같은 이유로 실패하는
# 글은 우리가 고칠 수 없는 글로 보고 게시판 완료를 막지 않는다. 재시도는 계속한다.
DETAIL_RETRY_LIMIT = 6


def record_scan_progress(
    session: Session,
    health: m.SourceHealth,
    *,
    initial_window_start: date,
    cursor_page: int,
    cursor_external_id: str | None,
    oldest_date: date | None,
    stop_reason: str,
    complete: bool,
    now: datetime | None = None,
) -> None:
    """목록 경계 도달과 미해결 상세를 분리해 완료를 계산한다.

    남은 상세 실패는 "아직 해 볼 것이 남은" 실패만 센다. `DETAIL_RETRY_LIMIT` 번을
    넘게 실패한 글은 우리가 더 해 볼 것이 없으므로 게시판 전체의 완료를 막지 않는다.

    2026-09-07 미래인재센터 프로그램 신청은 게시판 끝까지 읽고도(end_of_board)
    완료로 넘어가지 못했다. 글 20개가 전부 로그인해야 열리는 글이라 상세 실패가
    비지 않았기 때문이다. 이런 게시판은 몇 번을 더 돌아도 실패가 사라지지 않는데,
    완료가 되지 않으면 초기 백필 감독(app.run.initial)이 남은 곳으로 계속 세어
    회차를 끝없이 이어받는다. 재시도는 그대로 계속하므로 원문이 열리게 되면
    다음 회차가 그 글을 다시 채운다.
    """
    now = now or utcnow()
    previous_page = health.backfill_cursor_page or 1
    previous_anchor = health.backfill_cursor_external_id
    health.initial_window_start = initial_window_start
    health.range_policy_version = f"{initial_window_start.isoformat()}-v1"
    health.backfill_cursor_page = max(1, cursor_page)
    health.backfill_cursor_external_id = cursor_external_id
    if oldest_date is not None and (
        health.backfill_oldest_date is None or oldest_date < health.backfill_oldest_date
    ):
        health.backfill_oldest_date = oldest_date
    if previous_page != cursor_page or previous_anchor != cursor_external_id:
        health.backfill_last_progress_at = now
    health.backfill_last_stop_reason = stop_reason
    health.last_scan_complete = complete
    health.last_scan_stop_reason = stop_reason
    if stop_reason in {"end_of_board", "date_boundary"}:
        health.backfill_boundary_reached = True
    session.flush()
    pending = session.scalar(
        select(func.count(m.SourceItem.id)).where(
            m.SourceItem.source_id == health.source_id,
            m.SourceItem.last_detail_error.isnot(None),
            func.coalesce(m.SourceItem.detail_attempts, 0) < DETAIL_RETRY_LIMIT,
        )
    ) or 0
    health.backfill_complete = bool(health.backfill_boundary_reached and not pending)
    health.backfill_status = (
        "complete" if health.backfill_complete else
        "blocked" if stop_reason == "blocked" else
        "detail_pending" if health.backfill_boundary_reached else "in_progress"
    )


def pending_detail_listings(session: Session, source_id: str, *, limit: int = 20) -> list[ListedItem]:
    """목록 위치와 무관하게 실패 원문을 재시도한다. 추출 힌트도 보존한다."""
    now = utcnow()
    rows = session.scalars(
        select(m.SourceItem).where(
            m.SourceItem.source_id == source_id,
            m.SourceItem.last_detail_error.isnot(None),
            (m.SourceItem.next_detail_attempt_after.is_(None))
            | (m.SourceItem.next_detail_attempt_after <= now),
        ).order_by(m.SourceItem.next_detail_attempt_after, m.SourceItem.id).limit(limit)
    ).all()
    listings = []
    for item in rows:
        if item.detail_listing:
            listings.append(ListedItem(**item.detail_listing))
        else:
            current = session.get(m.SourceItemRevision, item.current_revision_id) if item.current_revision_id else None
            listings.append(ListedItem(
                external_id=item.external_id, url=item.canonical_url,
                title=current.title if current else "", is_pinned=bool(item.is_pinned),
                published_raw=current.published_raw if current else None,
            ))
    return listings


def item_for_listing(session: Session, source_id: str, external_id: str) -> m.SourceItem | None:
    """출처별 원문 번호로만 기존 항목을 찾는다."""
    return session.get(m.SourceItem, ids.source_item_id(source_id, external_id))


def preload_listing_items(session: Session, source_id: str, listings: list[ListedItem]) -> tuple[list, list]:
    """페이지 단위로 원본과 현재 이력을 읽어 세션 identity map에 유지한다."""
    keys = [ids.source_item_id(source_id, listed.external_id) for listed in listings]
    if not keys:
        return [], []
    items = list(session.scalars(select(m.SourceItem).where(m.SourceItem.id.in_(keys))))
    # 본문 두 칸은 빼고 읽는다. 수집 경로가 여기서 올린 기존 이력에서 보는 것은
    # title·published_*·content_hash·board_category·raw_object_key 뿐이고 본문은
    # 한 번도 읽지 않는다(2026-09-09 실측: 회차당 12.83MB 중 11.4MB가 본문).
    # defer 라서 나중에 누가 본문을 만지면 그때 한 번 더 읽어 온다. 결과는 같다.
    revisions = list(session.scalars(
        select(m.SourceItemRevision)
        .options(defer(m.SourceItemRevision.body_html), defer(m.SourceItemRevision.body_text))
        .where(
            m.SourceItemRevision.id.in_(
                [item.current_revision_id for item in items if item.current_revision_id]
            )
        )
    ))
    return items, revisions


def ensure_item_stub(session: Session, *, source: m.Source, listed: ListedItem) -> m.SourceItem:
    """상세를 열지 못한 목록 항목도 재시도할 수 있게 최소 원본을 만든다."""
    item = item_for_listing(session, source.id, listed.external_id)
    if item is not None:
        item.detail_listing = asdict(listed)
        item.last_seen_at = utcnow()
        item.is_pinned = bool(listed.is_pinned)
        return item
    item = m.SourceItem(
        id=ids.source_item_id(source.id, listed.external_id),
        source_id=source.id,
        external_id=listed.external_id,
        canonical_url=ids.canonical_url(listed.url),
        last_seen_at=utcnow(),
        original_status="available",
        is_pinned=bool(listed.is_pinned),
        detail_listing=asdict(listed),
    )
    session.add(item)
    session.flush()
    return item


def detail_is_due(
    session: Session,
    *,
    source_id: str,
    listed: ListedItem,
    recheck_days: int = 14,
    now: datetime | None = None,
    defer_ordinary_rechecks: bool = False,
) -> bool:
    """목록 변화·재확인 주기에 해당하는 글만 상세를 다시 요청한다."""
    now = now or utcnow()
    item = item_for_listing(session, source_id, listed.external_id)
    if item is None:
        return True
    if item.next_detail_attempt_after and _aware(item.next_detail_attempt_after) > now:
        return False
    if not item.current_revision_id or item.last_detail_error:
        return True
    current = session.get(m.SourceItemRevision, item.current_revision_id)
    if current is None:
        return True
    if current.title != listed.title or bool(item.is_pinned) != bool(listed.is_pinned):
        return True
    if listed.published_raw and current.published_raw and listed.published_raw.strip() != current.published_raw.strip():
        # 목록과 상세가 같은 날짜를 다른 표기로 준다. 경희 공통 게시판(khu_board)의
        # 목록은 "2026-07-14", 상세는 "2026-07-14 12:54:28.0" 을 준다. 저장된 값은
        # 상세에서 온 것이므로 글자 그대로 비교하면 영원히 "발행일이 바뀌었다" 가 되어
        # 이미 가진 글의 상세를 회차마다 다시 받는다. 2026-09-07 운영 실측으로
        # 3시간 동안 기존 글 상세 재요청 2,859건이 났고 그중 내용이 실제로 달라진 것은
        # 16건뿐이었다. 날짜가 같으면 표기 차이로 보고 다시 받지 않는다.
        # 날짜를 읽지 못하는 표기는 예전처럼 변화로 본다(놓치는 쪽보다 더 받는 쪽).
        listed_date = parse_published(listed.published_raw).date
        if listed_date is None or current.published_date is None or listed_date != current.published_date:
            return True
    if item.last_detail_checked_at is None:
        return True
    published = current.published_date
    recent_cutoff = now.date() - timedelta(days=14)
    active_notice = session.execute(
        select(m.Notice.id).join(m.NoticeSource, m.NoticeSource.notice_id == m.Notice.id)
        .where(m.NoticeSource.source_item_id == item.id, m.NoticeSource.is_active.is_(True),
               m.Notice.deadline_date >= now.date()).limit(1)
    ).first()
    # 초기 채우기는 빠진 상세를 우선한다. 실패·목록 변화·고정·진행 중
    # 공지는 위 규칙대로 확인하고, 일반 재확인은 완료 후 유지 수집이 맡는다.
    if defer_ordinary_rechecks and not listed.is_pinned and not active_notice:
        return False
    interval_days = 1 if listed.is_pinned or active_notice or (published is not None and published >= recent_cutoff) else max(1, recheck_days)
    return now - _aware(item.last_detail_checked_at) >= timedelta(days=interval_days)


def touch_item_from_listing(
    session: Session,
    *,
    source_id: str,
    listed: ListedItem,
    now: datetime | None = None,
) -> bool:
    """상세를 생략한 목록 항목도 발견 사실과 자동 삭제 복구를 기록한다."""
    now = now or utcnow()
    item = item_for_listing(session, source_id, listed.external_id)
    if item is None:
        return False
    item.last_seen_at = now
    item.original_status = "available"
    item.missing_streak = 0
    item.is_pinned = bool(listed.is_pinned)
    _restore_notice_for_item(session, item.id)
    return True


def mark_detail_failure(
    session: Session,
    *,
    source_id: str,
    external_id: str,
    message: str,
    now: datetime | None = None,
) -> None:
    """상세 실패를 별도 재시도 상태로 남기고 목록 진전은 막지 않는다."""
    now = now or utcnow()
    item = item_for_listing(session, source_id, external_id)
    if item is None:
        return
    item.detail_attempts = int(item.detail_attempts or 0) + 1
    backoff_hours = min(24, 2 ** min(item.detail_attempts - 1, 4))
    item.next_detail_attempt_after = now + timedelta(hours=backoff_hours)
    item.last_detail_error = message[:2000]


# ------------------------------------------------------------------ 원본 저장


def upsert_item_and_revision(
    session: Session,
    *,
    source: m.Source,
    listed: ListedItem,
    detail: FetchedDetail,
    published: ParsedDate,
    raw_object_key: str | None,
    extractor_version: str,
    now: datetime | None = None,
) -> ItemWriteResult:
    """원본 1개 + 필요할 때만 새 이력. 4절 1·3항을 지킨다."""
    now = now or utcnow()
    item_id = ids.source_item_id(source.id, listed.external_id)

    item = session.get(m.SourceItem, item_id)
    is_new_item = item is None or item.current_revision_id is None
    was_removed = bool(item is not None and item.original_status == "removed")
    if item is None:
        item = m.SourceItem(
            id=item_id,
            source_id=source.id,
            external_id=listed.external_id,
            canonical_url=ids.canonical_url(detail.url or listed.url),
            first_seen_at=now,
        )
        session.add(item)
        session.flush()

    item.last_seen_at = now
    item.original_status = "available"
    item.missing_streak = 0
    item.is_pinned = bool(listed.is_pinned)
    item.last_detail_checked_at = now
    item.detail_attempts = 0
    item.next_detail_attempt_after = None
    item.last_detail_error = None

    digest = make_content_hash(detail.title, detail.body_text, detail.attachments)

    # 현재 채택된 이력과 내용이 같으면 새 이력을 만들지 않는다.
    current = session.get(m.SourceItemRevision, item.current_revision_id) if item.current_revision_id else None
    if (current is not None and current.content_hash == digest
            and current.published_date == published.date and current.published_at == published.at
            and current.published_raw == published.raw and current.board_category == detail.board_category):
        current.observed_at = now
        if raw_object_key:
            current.raw_object_key = raw_object_key
        _restore_notice_for_item(session, item.id)
        return ItemWriteResult(item.id, current.id, is_new_item, False, digest)

    # A → B → A 로 돌아온 경우도 새 사건으로 기록한다(6.2절).
    revision = m.SourceItemRevision(
        id=ids.revision_id(item.id, f"{digest}:{now.isoformat()}"),
        source_item_id=item.id,
        content_hash=digest,
        title=detail.title,
        body_text=detail.body_text or None,
        body_html=detail.body_html or None,
        raw_object_key=raw_object_key,
        author=detail.author,
        board_category=detail.board_category,
        published_raw=published.raw,
        published_date=published.date,
        published_at=published.at,
        published_precision=published.precision,
        extractor_version=extractor_version,
        observed_at=now,
    )
    session.add(revision)
    session.flush()

    for index, att in enumerate(detail.attachments):
        session.add(
            m.Attachment(
                id=ids.attachment_id(revision.id, att.filename, index),
                revision_id=revision.id,
                filename=att.filename,
                url=att.url,
                kind=att.kind,
                size_bytes=att.size_bytes,
                status="listed",
            )
        )

    item.current_revision_id = revision.id
    _record_url_alias(session, item, detail.url or listed.url)
    if was_removed:
        _restore_notice_for_item(session, item.id)
    return ItemWriteResult(item.id, revision.id, is_new_item, True, digest)


def _record_url_alias(session: Session, item: m.SourceItem, url: str) -> None:
    canonical = ids.canonical_url(url)
    if canonical == item.canonical_url:
        return
    existing = session.execute(
        select(m.SourceItemAlias).where(m.SourceItemAlias.url == canonical)
    ).scalar_one_or_none()
    if existing is None:
        session.add(
            m.SourceItemAlias(
                id=ids._digest(item.id, canonical), source_item_id=item.id, url=canonical
            )
        )


def mark_items_missing(
    session: Session,
    source_id: str,
    seen_ids: set[str],
    *,
    now: datetime | None = None,
) -> int:
    """목록에서 보이지 않은 원본의 연속 미발견 횟수를 올린다.

    한 번의 누락으로 삭제하지 않는다(4절 9항). 최근에 본 항목만 대상으로 한다.
    """
    now = now or utcnow()
    cutoff = now - timedelta(days=120)
    rows = session.execute(
        select(m.SourceItem).where(
            m.SourceItem.source_id == source_id,
            m.SourceItem.original_status == "available",
            m.SourceItem.last_seen_at.isnot(None),
            m.SourceItem.last_seen_at >= cutoff,
        )
    ).scalars().all()

    changed = 0
    # 목록 어댑터가 반환하는 것은 출처 내부 원문 번호다. 내부 digest 식별자와
    # 비교하면 매번 모든 글이 미발견으로 계산되므로 외부 번호로 통일한다.
    # 기존 운영 명령이 내부 id를 넘기는 호환 호출도 잠시 허용해, 마이그레이션
    # 중 한 번의 실행으로 정상 글을 숨기지 않게 한다.
    normalized_seen = {str(value) for value in seen_ids}
    for item in rows:
        if str(item.external_id) in normalized_seen or str(item.id) in normalized_seen:
            continue
        item.missing_streak = int(item.missing_streak or 0) + 1
        if item.missing_streak >= MISSING_STREAK_FOR_REMOVED:
            item.original_status = "removed"
            _hide_notice_for_item(session, item.id)
            changed += 1
    return changed


def _hide_notice_for_item(session: Session, item_id: str) -> None:
    link = session.execute(
        select(m.NoticeSource).where(
            m.NoticeSource.source_item_id == item_id, m.NoticeSource.is_active.is_(True)
        )
    ).scalar_one_or_none()
    if link is None:
        return
    notice = session.get(m.Notice, link.notice_id)
    if notice is None:
        return
    # 다른 살아 있는 원본이 있으면 공지를 숨기지 않는다(9.3절).
    others = session.execute(
        select(m.NoticeSource.id)
        .join(m.SourceItem, m.SourceItem.id == m.NoticeSource.source_item_id)
        .where(
            m.NoticeSource.notice_id == notice.id,
            m.NoticeSource.is_active.is_(True),
            m.NoticeSource.source_item_id != item_id,
            m.SourceItem.original_status == "available",
        )
    ).first()
    if others is None and notice.status == "visible":
        notice.status = "removed"
        notice.updated_at = utcnow()


def _restore_notice_for_item(session: Session, item_id: str) -> None:
    """자동 누락으로 제거된 공지만 원문 재발견 시 복구한다.

    운영자가 숨긴 공지나 병합으로 숨긴 공지는 건드리지 않는다.
    """
    link = session.execute(
        select(m.NoticeSource).where(
            m.NoticeSource.source_item_id == item_id, m.NoticeSource.is_active.is_(True)
        )
    ).scalar_one_or_none()
    if link is None:
        return
    notice = session.get(m.Notice, link.notice_id)
    if notice is not None and notice.status == "removed":
        notice.status = "visible"
        notice.updated_at = utcnow()


# ------------------------------------------------------------------ 공지 묶음


def upsert_notice_for_item(
    session: Session,
    *,
    item_id: str,
    revision: m.SourceItemRevision,
    category: CategoryDecision,
    audience: AudienceDecision,
    deadline: DeadlineGuess,
    now: datetime | None = None,
) -> tuple[m.Notice, bool]:
    """원본 하나에 대응하는 공지 묶음을 만들거나 갱신한다.

    자동 병합은 별도 단계에서 판정한 뒤에만 일어난다. 여기서는 1원본 1묶음이 기본이다.
    """
    now = now or utcnow()
    link = session.execute(
        select(m.NoticeSource).where(
            m.NoticeSource.source_item_id == item_id, m.NoticeSource.is_active.is_(True)
        )
    ).scalar_one_or_none()

    created = False
    if link is None:
        notice = m.Notice(
            id=ids.notice_id(item_id),
            primary_source_item_id=item_id,
            status="visible",
            first_visible_at=now,
            updated_at=now,
            title=revision.title,
        )
        session.add(notice)
        session.flush()
        session.add(
            m.NoticeSource(
                id=ids._digest(notice.id, item_id),
                notice_id=notice.id,
                source_item_id=item_id,
                is_active=True,
                is_primary=True,
                linked_at=now,
            )
        )
        created = True
    else:
        notice = session.get(m.Notice, link.notice_id)
        if notice is None:  # pragma: no cover - 참조 무결성 방어
            raise RuntimeError(f"연결된 공지를 찾을 수 없습니다: {link.notice_id}")

    # 표시값 갱신. first_visible_at 은 재수집으로 바뀌지 않는다(8.2절).
    notice.title = revision.title
    notice.excerpt = make_excerpt(revision.body_text or "")
    notice.published_date = revision.published_date
    notice.published_at = revision.published_at
    notice.published_precision = revision.published_precision
    notice.deadline_date = deadline.date
    notice.deadline_at = deadline.at
    notice.deadline_precision = deadline.precision
    notice.deadline_evidence = deadline.evidence
    notice.audience_note = audience.note
    notice.search_text = _search_text(revision)
    notice.derived_version = f"{category.rule_version}|{audience.rule_version}"
    if not created:
        notice.updated_at = now
    if notice.status == "removed":
        notice.status = "visible"

    _replace_categories(session, notice.id, category)
    _replace_audiences(session, notice.id, audience)
    session.add(
        m.OutboxEvent(
            id=ids._digest("notice", notice.id, now.isoformat()),
            event_type="notice.changed",
            payload={"notice_id": notice.id, "created": created},
        )
    )
    return notice, created


def _search_text(revision: m.SourceItemRevision) -> str:
    joined = f"{revision.title}\n{revision.body_text or ''}"
    return joined[:SEARCH_TEXT_LIMIT]


def _replace_categories(session: Session, notice_id: str, category: CategoryDecision) -> None:
    session.query(m.NoticeCategory).filter(m.NoticeCategory.notice_id == notice_id).delete(
        synchronize_session=False
    )
    session.add(
        m.NoticeCategory(
            id=ids._digest(notice_id, category.primary, "primary"),
            notice_id=notice_id,
            category_code=category.primary,
            is_primary=True,
            rule_name=category.rule_name,
            rule_version=category.rule_version,
            evidence_text=category.evidence,
        )
    )
    for code in category.secondary:
        if code == category.primary:
            continue
        session.add(
            m.NoticeCategory(
                id=ids._digest(notice_id, code, "secondary"),
                notice_id=notice_id,
                category_code=code,
                is_primary=False,
                rule_name=category.rule_name,
                rule_version=category.rule_version,
            )
        )


def _replace_audiences(session: Session, notice_id: str, audience: AudienceDecision) -> None:
    session.query(m.NoticeAudience).filter(m.NoticeAudience.notice_id == notice_id).delete(
        synchronize_session=False
    )
    for target in audience.targets:
        session.add(
            m.NoticeAudience(
                id=ids._digest(notice_id, target.key()),
                notice_id=notice_id,
                audience_type=target.type,
                campus_id=target.id if target.type == "campus" else None,
                organization_id=target.id if target.type == "organization" else None,
                rule_version=audience.rule_version,
            )
        )


def merge_notices(
    session: Session,
    *,
    keep_notice_id: str,
    absorb_notice_id: str,
    reason: str,
    actor: str = "auto",
) -> None:
    """공지 병합. 이전 주소를 새 공지로 잇고 저장한 공지를 옮긴다(9.3절)."""
    if keep_notice_id == absorb_notice_id:
        return
    keep = session.get(m.Notice, keep_notice_id)
    absorb = session.get(m.Notice, absorb_notice_id)
    if keep is None or absorb is None:
        raise ValueError("병합 대상 공지를 찾을 수 없습니다.")

    now = utcnow()
    session.execute(
        update(m.NoticeSource)
        .where(m.NoticeSource.notice_id == absorb_notice_id, m.NoticeSource.is_active.is_(True))
        .values(notice_id=keep_notice_id, is_primary=False, reason=reason)
    )
    session.execute(
        update(m.Bookmark)
        .where(m.Bookmark.notice_id == absorb_notice_id)
        .values(notice_id=keep_notice_id)
    )
    absorb.status = "hidden"
    absorb.updated_at = now
    keep.updated_at = now
    session.merge(
        m.NoticeRedirect(from_notice_id=absorb_notice_id, to_notice_id=keep_notice_id, created_at=now)
    )
    session.add(
        m.AuditLog(
            id=ids._digest("merge", keep_notice_id, absorb_notice_id, now.isoformat()),
            actor=actor,
            action="notice.merge",
            target_kind="notice",
            target_id=keep_notice_id,
            after={"absorbed": absorb_notice_id},
            reason=reason,
        )
    )


def record_dedupe_decision(
    session: Session,
    *,
    left_item: str,
    right_item: str,
    left_revision: str,
    right_revision: str,
    decision: str,
    score: float,
    signals: dict,
    rule_version: str,
    decided_by: str = "auto",
) -> None:
    # 열쇠가 입력에서 결정되므로 같은 두 글을 다음 실행에서 다시 비교하면 열쇠가 같다.
    # add() 로 넣으면 두 번째 실행이 중복 열쇠로 멈춘다. 판정은 다시 해도 같은 사실이라
    # 이미 있으면 갱신한다. merge() 는 PostgreSQL 과 SQLite 에서 같게 동작한다.
    session.merge(
        m.DedupeDecision(
            id=ids._digest(left_item, right_item, left_revision, right_revision),
            left_item_id=left_item,
            right_item_id=right_item,
            left_revision_id=left_revision,
            right_revision_id=right_revision,
            decision=decision,
            score=round(score, 4),
            signals=signals,
            rule_version=rule_version,
            decided_by=decided_by,
        )
    )


def recent_items_for_dedupe(
    session: Session, *, since: date | None = None, limit: int = 400
) -> list[tuple[m.SourceItem, m.SourceItemRevision, m.Notice | None]]:
    """중복 후보 비교 대상. 최근 항목만 본다."""
    stmt = (
        select(m.SourceItem, m.SourceItemRevision, m.Notice)
        .join(m.SourceItemRevision, m.SourceItemRevision.id == m.SourceItem.current_revision_id)
        .outerjoin(
            m.NoticeSource,
            (m.NoticeSource.source_item_id == m.SourceItem.id) & m.NoticeSource.is_active.is_(True),
        )
        .outerjoin(m.Notice, m.Notice.id == m.NoticeSource.notice_id)
        .where(m.SourceItem.original_status == "available")
        .order_by(m.SourceItemRevision.observed_at.desc())
        .limit(limit)
    )
    if since is not None:
        stmt = stmt.where(m.SourceItemRevision.published_date >= since)
    return list(session.execute(stmt).all())


def all_source_audiences(session: Session) -> dict[str, tuple[AudienceTarget, ...]]:
    """모든 출처의 기본 대상. 대상 다시 계산이 출처마다 조회하지 않도록 한 번에 읽는다."""
    source_ids = list(session.execute(select(m.Source.id)).scalars())
    return _source_audiences_many(session, source_ids)


def replace_notice_audiences(session: Session, notice_id: str, audience: AudienceDecision) -> None:
    """공지 하나의 대상 행을 판정 결과로 바꾼다. 수집이 쓰는 것과 같은 경로다."""
    _replace_audiences(session, notice_id, audience)


def notice_audience_keys(session: Session, notice_ids: list[str]) -> dict[str, set[str]]:
    """지금 저장된 대상을 AudienceTarget.key() 와 같은 표기로 읽는다."""
    if not notice_ids:
        return {}
    rows = session.execute(
        select(
            m.NoticeAudience.notice_id,
            m.NoticeAudience.audience_type,
            m.NoticeAudience.campus_id,
            m.NoticeAudience.organization_id,
        ).where(m.NoticeAudience.notice_id.in_(notice_ids))
    ).all()
    out: dict[str, set[str]] = {}
    for notice_id, audience_type, campus_id, organization_id in rows:
        if audience_type == "university":
            key = "university"
        elif audience_type == "campus":
            key = f"campus:{campus_id}"
        elif audience_type == "organization":
            key = f"org:{organization_id}"
        else:
            key = "undetermined"
        out.setdefault(notice_id, set()).add(key)
    return out


def iter_notices_for_audience_recompute(session: Session, *, chunk: int = 1000):
    """공지와 그 대표 원본의 저장된 제목·본문·출처를 순서대로 흘려준다.

    대상 판정의 입력은 제목·본문·출처 기본 대상뿐이다(domain.audiences.decide).
    셋 다 이미 저장되어 있으므로 원문을 다시 받지 않고 다시 계산할 수 있다.

    대표 원본만 본다. 병합된 공지는 활성 연결이 여럿이지만 대표가 아닌 연결까지
    쓰면 어느 원본이 이겼는지가 순서에 달리게 된다. 대표는 하나뿐이라 결과가
    한결같다. 본문은 수만 건이면 수십 MB 라 keyset 으로 한 덩이씩만 읽는다.
    """
    after = ""
    while True:
        rows = session.execute(
            select(
                m.Notice.id,
                m.Notice.status,
                m.SourceItem.source_id,
                m.SourceItemRevision.title,
                m.SourceItemRevision.body_text,
            )
            .join(
                m.NoticeSource,
                (m.NoticeSource.notice_id == m.Notice.id)
                & m.NoticeSource.is_active.is_(True)
                & m.NoticeSource.is_primary.is_(True),
            )
            .join(m.SourceItem, m.SourceItem.id == m.NoticeSource.source_item_id)
            .join(m.SourceItemRevision, m.SourceItemRevision.id == m.SourceItem.current_revision_id)
            .where(m.Notice.id > after)
            .order_by(m.Notice.id)
            .limit(chunk)
        ).all()
        if not rows:
            return
        yield rows
        after = rows[-1][0]


def iter_item_bodies_for_dedupe(session: Session, *, chunk: int = 1000):
    """공개 중인 공지의 원본 식별자·본문·본문 HTML·원문 주소를 순서대로 흘려준다.

    전체 중복 판정이 본문 지문과 포스터 묶음으로 묶을 때만 쓴다. 본문을 한꺼번에
    들고 있으면 수십 MB 가 되므로 keyset 방식으로 한 덩이씩만 읽고 열쇠만 남긴다.
    포스터 열쇠는 상대 주소를 절대 주소로 만들어야 해서 원문 주소가 함께 필요하다.
    """
    after = ""
    while True:
        rows = session.execute(
            select(
                m.SourceItem.id,
                m.SourceItemRevision.body_text,
                m.SourceItemRevision.body_html,
                m.SourceItem.canonical_url,
            )
            .join(m.SourceItemRevision, m.SourceItemRevision.id == m.SourceItem.current_revision_id)
            .join(
                m.NoticeSource,
                (m.NoticeSource.source_item_id == m.SourceItem.id)
                & m.NoticeSource.is_active.is_(True),
            )
            .join(
                m.Notice,
                (m.Notice.id == m.NoticeSource.notice_id) & (m.Notice.status == "visible"),
            )
            .where(m.SourceItem.original_status == "available", m.SourceItem.id > after)
            .order_by(m.SourceItem.id)
            .limit(chunk)
        ).all()
        if not rows:
            return
        yield from rows
        after = rows[-1][0]


def items_for_dedupe(
    session: Session, item_ids: list[str]
) -> list[tuple[m.SourceItem, m.SourceItemRevision, m.Notice | None]]:
    """식별자로 지목한 원본만 비교 대상 모양으로 읽는다."""
    if not item_ids:
        return []
    stmt = (
        select(m.SourceItem, m.SourceItemRevision, m.Notice)
        .join(m.SourceItemRevision, m.SourceItemRevision.id == m.SourceItem.current_revision_id)
        .outerjoin(
            m.NoticeSource,
            (m.NoticeSource.source_item_id == m.SourceItem.id) & m.NoticeSource.is_active.is_(True),
        )
        .outerjoin(m.Notice, m.Notice.id == m.NoticeSource.notice_id)
        .where(m.SourceItem.id.in_(item_ids))
        .order_by(m.SourceItem.id)
    )
    return list(session.execute(stmt).all())


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)
