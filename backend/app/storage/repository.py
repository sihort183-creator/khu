"""저장 규칙.

여기가 4절 불변 조건을 실제로 지키는 곳이다.
- 원본 게시물 1개(출처+원본 식별자 유일키), 수정은 새 이력
- 같은 내용 재요청은 이력을 만들지 않되, A→B→A 는 새 사건으로 남긴다
- 원본별 활성 공지 연결은 최대 1개
- 한 번의 누락으로 삭제하지 않는다
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.domain import ids
from app.domain.audiences import AudienceDecision, AudienceTarget
from app.domain.categories import CategoryDecision
from app.domain.dates import DeadlineGuess, ParsedDate, utcnow
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


def load_due_sources(session: Session, *, now: datetime | None = None, limit: int | None = None) -> list[DueSource]:
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

    due: list[DueSource] = []
    for source, config_row, health in rows:
        if health is None:
            health = m.SourceHealth(source_id=source.id)
            session.add(health)
            session.flush()

        if health.next_attempt_after and health.next_attempt_after > now:
            continue
        interval = int(config_row.interval_minutes or 60)
        if health.last_attempt_at is not None:
            elapsed = now - _aware(health.last_attempt_at)
            if elapsed < timedelta(minutes=interval) and health.consecutive_failures == 0:
                continue

        due.append(
            DueSource(
                source=source,
                config=dict(config_row.config or {}),
                interval_minutes=interval,
                health=health,
                audience_defaults=_source_audiences(session, source.id),
            )
        )
        if limit is not None and len(due) >= limit:
            break
    return due


def _source_audiences(session: Session, source_id: str) -> tuple[AudienceTarget, ...]:
    rows = session.execute(
        select(m.SourceAudience, m.Campus.name, m.Organization.name)
        .outerjoin(m.Campus, m.Campus.id == m.SourceAudience.campus_id)
        .outerjoin(m.Organization, m.Organization.id == m.SourceAudience.organization_id)
        .where(m.SourceAudience.source_id == source_id)
    ).all()
    out: list[AudienceTarget] = []
    for row, campus_name, org_name in rows:
        if row.audience_type == "university":
            out.append(AudienceTarget("university", None, "대학 전체"))
        elif row.audience_type == "campus" and row.campus_id:
            out.append(AudienceTarget("campus", row.campus_id, campus_name or "캠퍼스"))
        elif row.audience_type == "organization" and row.organization_id:
            out.append(AudienceTarget("organization", row.organization_id, org_name or "조직"))
        else:
            out.append(AudienceTarget("undetermined", None, "대상 미확정"))
    return tuple(out)


def campus_lookup(session: Session) -> dict[str, tuple[str, str]]:
    rows = session.execute(select(m.Campus.id, m.Campus.code, m.Campus.name)).all()
    return {code: (cid, name) for cid, code, name in rows}


# ------------------------------------------------------------------ 실행 기록


def start_run(session: Session, *, kind: str = "collect", code_commit: str | None = None) -> m.Run:
    """실행 시작. 30분 넘게 안 끝난 이전 실행은 비정상 종료로 표시한다(7.4절)."""
    now = utcnow()
    stale = session.execute(
        select(m.Run).where(m.Run.kind == kind, m.Run.finished_at.is_(None))
    ).scalars().all()
    for old in stale:
        if now - _aware(old.started_at) > timedelta(minutes=30):
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
    is_new_item = item is None
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

    digest = make_content_hash(detail.title, detail.body_text)

    # 현재 채택된 이력과 내용이 같으면 새 이력을 만들지 않는다.
    current = session.get(m.SourceItemRevision, item.current_revision_id) if item.current_revision_id else None
    if current is not None and current.content_hash == digest:
        current.observed_at = now
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


def mark_items_missing(session: Session, source_id: str, seen_ids: set[str], *, now: datetime | None = None) -> int:
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
    for item in rows:
        if item.id in seen_ids:
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
    if others is None:
        notice.status = "removed"
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
    session.add(
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


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)
