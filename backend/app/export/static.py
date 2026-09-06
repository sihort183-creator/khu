"""정적 조회 파일 생성(12·15.1절).

매 실행이 새 개정을 /v1/r/<revision>/ 아래에 먼저 올리고, 마지막에 /v1/latest.json 의
포인터만 바꾼다. 개정 경로의 파일은 불변이므로 Worker 와 브라우저가 길게 캐시한다.

만드는 파일:
  v1/latest.json                       개정 포인터 (짧은 캐시)
  v1/status.json                       공개 상태 (18.2절)
  v1/r/<rev>/catalog.json              분류 사전
  v1/r/<rev>/organizations.json        조직 목록
  v1/r/<rev>/sources.json              출처 목록·수집 상태
  v1/r/<rev>/notices/page/<n>.json     공지 목록 페이지
  v1/r/<rev>/notices/<id>.json         공지 상세
  v1/r/<rev>/notices/index.json        기기 검색·맞춤 필터용 경량 색인
  v1/r/<rev>/contacts/page/<n>.json    연락처 목록
  v1/r/<rev>/contacts/<id>.json        연락처 상세
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import CONTRACT_VERSION, Settings
from app.config import settings as default_settings
from app.contracts import models as api
from app.contracts.vocab import (
    AUDIENCE_TYPE_LABELS,
    CATEGORY_LABELS,
    CONTACT_CHANNEL_LABELS,
    FRESHNESS_LABELS,
    MEDIUM_LABELS,
    ORG_TYPE_LABELS,
    ORIGINAL_STATUS_LABELS,
    SOURCE_STATUS_LABELS,
    VERIFICATION_LABELS,
    code_list,
    coded,
)
from app.domain.dates import as_utc, freshness_code, utcnow
from app.storage import models as m
from app.storage.db import session_scope
from app.storage.objects import ObjectStore, build_store

log = logging.getLogger("khu.export")

PAGE_SIZE = 50
INDEX_LIMIT = 8000
BASE = "v1"


@dataclass
class ExportResult:
    revision: str
    files_written: int = 0
    bytes_written: int = 0
    notices: int = 0
    contacts: int = 0
    pages: int = 0
    skipped_reason: str | None = None
    keys: list[str] = field(default_factory=list)


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return as_utc(value).isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError(f"직렬화할 수 없는 값: {type(value)!r}")


def _dump(model: Any) -> bytes:
    payload = model.model_dump(mode="json") if hasattr(model, "model_dump") else model
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=_json_default).encode(
        "utf-8"
    )


class Writer:
    def __init__(self, store: ObjectStore, bucket: str, result: ExportResult, *, record_keys: bool = False):
        self.store = store
        self.bucket = bucket
        self.result = result
        self.record_keys = record_keys
        self._lock = threading.Lock()

    def put(self, key: str, model: Any) -> None:
        data = _dump(model)
        self._upload(key, data)

    def _upload(self, key: str, data: bytes) -> None:
        self.store.put_bytes(self.bucket, key, data, content_type="application/json; charset=utf-8", compress=True)
        with self._lock:
            self.result.files_written += 1
            self.result.bytes_written += len(data)
            if self.record_keys:
                self.result.keys.append(key)

    def put_many(self, items: Iterable[tuple[str, Any]], *, workers: int = 8) -> None:
        """여러 파일을 한꺼번에 올린다.

        공지가 천 건을 넘으면 상세 파일도 그만큼이라, 하나씩 올리면 내보내기만으로
        십수 분이 걸려 실행이 시간 제한에 걸린다. 서로 의존하지 않는 파일들이라
        동시에 올려도 된다. 개정 포인터(latest.json)는 이 뒤에 따로 올린다.
        """
        pairs = [(key, _dump(model)) for key, model in items]
        if not pairs:
            return
        if len(pairs) < workers:
            for key, data in pairs:
                self._upload(key, data)
            return
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for _ in pool.map(lambda pair: self._upload(*pair), pairs):
                pass


def _meta(revision: str, now: datetime) -> api.Meta:
    return api.Meta(request_id=f"static-{revision}", generated_at=now)


def _page(revision: str, now: datetime, *, next_cursor: str | None, has_next: bool) -> api.Page:
    return api.Page(
        next_cursor=next_cursor, has_next=has_next, snapshot_at=now, dataset_revision=revision
    )


# ------------------------------------------------------------------ 조회 도우미


def _org_ref(org: m.Organization, path: list[str]) -> api.OrganizationRef:
    return api.OrganizationRef(
        id=org.id,
        name=org.name,
        path=path,
        type=api.Coded(**coded(ORG_TYPE_LABELS, org.org_type)),
    )


def _org_paths(session: Session) -> dict[str, list[str]]:
    orgs = {o.id: o for o in session.execute(select(m.Organization)).scalars()}
    university = session.execute(select(m.University.name)).scalar() or "경희대학교"
    paths: dict[str, list[str]] = {}
    for org_id, org in orgs.items():
        chain: list[str] = []
        current: m.Organization | None = org
        guard = 0
        while current is not None and guard < 12:
            chain.append(current.name)
            current = orgs.get(current.parent_id) if current.parent_id else None
            guard += 1
        chain.append(university)
        paths[org_id] = list(reversed(chain))
    return paths


def _source_refs(session: Session) -> dict[str, api.SourceRef]:
    rows = session.execute(select(m.Source)).scalars().all()
    return {
        s.id: api.SourceRef(
            id=s.id, name=s.name, medium=api.Coded(**coded(MEDIUM_LABELS, s.medium))
        )
        for s in rows
    }


# ------------------------------------------------------------------ 공지


def _build_notice(
    notice: m.Notice,
    item: m.SourceItem,
    revision: m.SourceItemRevision,
    *,
    source_ref: api.SourceRef,
    categories: list[m.NoticeCategory],
    audience_rows: list[tuple[m.NoticeAudience, str | None, str | None]],
    source_count: int,
    last_checked: datetime | None,
    now: datetime,
) -> api.Notice:
    primary = next((c for c in categories if c.is_primary), None)
    secondary = [c for c in categories if not c.is_primary]

    audiences: list[api.Audience] = []
    for row, campus_name, org_name in audience_rows:
        if row.audience_type == "campus":
            audiences.append(api.Audience(type="campus", id=row.campus_id, name=campus_name or "캠퍼스"))
        elif row.audience_type == "organization":
            audiences.append(
                api.Audience(type="organization", id=row.organization_id, name=org_name or "조직")
            )
        elif row.audience_type == "university":
            audiences.append(api.Audience(type="university", id=None, name=AUDIENCE_TYPE_LABELS["university"]))
        else:
            audiences.append(
                api.Audience(type="undetermined", id=None, name=AUDIENCE_TYPE_LABELS["undetermined"])
            )

    deadline = None
    if notice.deadline_date is not None and notice.deadline_precision in ("date", "datetime"):
        deadline = api.Deadline(
            date=notice.deadline_date,
            at=notice.deadline_at,
            precision=notice.deadline_precision,
            evidence_text=notice.deadline_evidence,
        )

    return api.Notice(
        id=notice.id,
        title=notice.title,
        excerpt=notice.excerpt,
        primary_category=api.Coded(**coded(CATEGORY_LABELS, primary.category_code if primary else "other")),
        secondary_categories=[api.Coded(**coded(CATEGORY_LABELS, c.category_code)) for c in secondary],
        audiences=audiences,
        audience_note=notice.audience_note,
        primary_source=source_ref,
        source_count=source_count,
        published_date=notice.published_date,
        published_at=notice.published_at,
        published_precision=notice.published_precision or "unknown",
        first_visible_at=notice.first_visible_at,
        updated_at=notice.updated_at,
        deadline=deadline,
        original_url=item.canonical_url,
        original_status=api.Coded(**coded(ORIGINAL_STATUS_LABELS, item.original_status)),
        freshness=api.Freshness(
            **coded(FRESHNESS_LABELS, freshness_code(last_checked, now=now)),
            last_checked_at=last_checked,
        ),
        is_pinned=bool(item.is_pinned),
    )


def _load_notices(session: Session, now: datetime) -> list[tuple[api.Notice, m.SourceItemRevision, m.SourceItem, list]]:
    source_refs = _source_refs(session)
    health = {
        row.source_id: row.last_list_success_at
        for row in session.execute(select(m.SourceHealth)).scalars()
    }

    counts = dict(
        session.execute(
            select(m.NoticeSource.notice_id, func.count(m.NoticeSource.id))
            .where(m.NoticeSource.is_active.is_(True))
            .group_by(m.NoticeSource.notice_id)
        ).all()
    )

    categories: dict[str, list[m.NoticeCategory]] = {}
    for row in session.execute(select(m.NoticeCategory)).scalars():
        categories.setdefault(row.notice_id, []).append(row)

    audiences: dict[str, list[tuple[m.NoticeAudience, str | None, str | None]]] = {}
    for row, campus_name, org_name in session.execute(
        select(m.NoticeAudience, m.Campus.name, m.Organization.name)
        .outerjoin(m.Campus, m.Campus.id == m.NoticeAudience.campus_id)
        .outerjoin(m.Organization, m.Organization.id == m.NoticeAudience.organization_id)
    ).all():
        audiences.setdefault(row.notice_id, []).append((row, campus_name, org_name))

    rows = session.execute(
        select(m.Notice, m.SourceItem, m.SourceItemRevision, m.Source)
        .join(m.SourceItem, m.SourceItem.id == m.Notice.primary_source_item_id)
        .join(m.SourceItemRevision, m.SourceItemRevision.id == m.SourceItem.current_revision_id)
        .join(m.Source, m.Source.id == m.SourceItem.source_id)
        .where(m.Notice.status == "visible", m.Source.is_public.is_(True))
        .order_by(m.Notice.first_visible_at.desc(), m.Notice.id.desc())
    ).all()

    built = []
    for notice, item, revision, source in rows:
        built.append(
            (
                _build_notice(
                    notice,
                    item,
                    revision,
                    source_ref=source_refs[source.id],
                    categories=categories.get(notice.id, []),
                    audience_rows=audiences.get(notice.id, []),
                    source_count=counts.get(notice.id, 1),
                    last_checked=health.get(source.id),
                    now=now,
                ),
                revision,
                item,
                audiences.get(notice.id, []),
            )
        )
    return built


def _notice_detail(
    session: Session,
    base: api.Notice,
    revision: m.SourceItemRevision,
    source_refs: dict[str, api.SourceRef],
) -> api.NoticeDetail:
    attachments = [
        api.Attachment(
            id=a.id,
            filename=a.filename,
            kind=a.kind,
            size_bytes=a.size_bytes,
            url=a.url,
            status=api.Coded(code=a.status, label="목록 확인됨" if a.status == "listed" else a.status),
        )
        for a in session.execute(
            select(m.Attachment).where(m.Attachment.revision_id == revision.id)
        ).scalars()
    ]

    linked = session.execute(
        select(m.NoticeSource, m.SourceItem, m.SourceItemRevision)
        .join(m.SourceItem, m.SourceItem.id == m.NoticeSource.source_item_id)
        .outerjoin(m.SourceItemRevision, m.SourceItemRevision.id == m.SourceItem.current_revision_id)
        .where(m.NoticeSource.notice_id == base.id, m.NoticeSource.is_active.is_(True))
    ).all()

    sources = [
        api.NoticeSourceRef(
            source_item_id=item.id,
            source=source_refs[item.source_id],
            url=item.canonical_url,
            published_date=rev.published_date if rev else None,
            original_status=api.Coded(**coded(ORIGINAL_STATUS_LABELS, item.original_status)),
            is_primary=bool(link.is_primary),
        )
        for link, item, rev in linked
    ]

    mentions = [
        api.ContactMention(
            raw_text=row.raw_text,
            channels=(
                [
                    api.ContactChannel(
                        id=row.id,
                        kind=api.Coded(**coded(CONTACT_CHANNEL_LABELS, row.channel_kind or "phone")),
                        display_value=row.value or row.raw_text,
                        value=row.value,
                    )
                ]
                if row.value
                else []
            ),
            contact_id=row.contact_id,
        )
        for row in session.execute(
            select(m.NoticeContactMention).where(m.NoticeContactMention.revision_id == revision.id)
        ).scalars()
    ]

    return api.NoticeDetail(
        **base.model_dump(),
        body_text=revision.body_text,
        body_html=revision.body_html,
        sources=sources or None or [],
        attachments=attachments,
        contact_mentions=mentions,
        related_notices=[],
        resolved_from_id=None,
    )


# ------------------------------------------------------------------ 연락처


def _load_contacts(session: Session, org_paths: dict[str, list[str]]) -> list[api.Contact]:
    channels: dict[str, list[m.ContactChannel]] = {}
    for row in session.execute(select(m.ContactChannel).order_by(m.ContactChannel.priority)).scalars():
        channels.setdefault(row.contact_id, []).append(row)

    campuses: dict[str, list[api.Campus]] = {}
    for contact_id, campus_id, name in session.execute(
        select(m.ContactCampus.contact_id, m.Campus.id, m.Campus.name).join(
            m.Campus, m.Campus.id == m.ContactCampus.campus_id
        )
    ).all():
        campuses.setdefault(contact_id, []).append(api.Campus(id=campus_id, name=name))

    evidence: dict[str, list[api.Evidence]] = {}
    for ev, obs in session.execute(
        select(m.ContactFieldEvidence, m.ContactObservation).join(
            m.ContactObservation, m.ContactObservation.id == m.ContactFieldEvidence.observation_id
        )
    ).all():
        evidence.setdefault(ev.contact_id, []).append(
            api.Evidence(
                field=ev.field, url=obs.url, source_name=obs.source_name, observed_at=obs.observed_at
            )
        )

    out: list[api.Contact] = []
    rows = session.execute(
        select(m.ContactEntry, m.Organization)
        .join(m.Organization, m.Organization.id == m.ContactEntry.organization_id)
        .where(m.ContactEntry.status.in_(("verified", "stale", "conflict")))
        .order_by(m.Organization.name, m.ContactEntry.service_name)
    ).all()

    for entry, org in rows:
        out.append(
            api.Contact(
                id=entry.id,
                organization=_org_ref(org, org_paths.get(org.id, [org.name])),
                campuses=campuses.get(entry.id, []),
                service_name=entry.service_name,
                channels=[
                    api.ContactChannel(
                        id=c.id,
                        kind=api.Coded(**coded(CONTACT_CHANNEL_LABELS, c.channel_kind)),
                        display_value=c.display_value,
                        value=c.value,
                        action_url=_action_url(c),
                        extension=c.extension,
                        values=list(c.expanded_values) if c.expanded_values else None,
                    )
                    for c in channels.get(entry.id, [])
                ],
                location=entry.location,
                office_hours=entry.office_hours,
                official_url=entry.official_url,
                verification=api.Verification(
                    **coded(VERIFICATION_LABELS, entry.status),
                    verified_at=entry.verified_at,
                    message="공식 원문의 안내와 일치함을 확인한 시각입니다."
                    if entry.status == "verified"
                    else None,
                ),
                evidence=evidence.get(entry.id, []),
                note=entry.note,
            )
        )
    return out


def _action_url(channel: m.ContactChannel) -> str | None:
    """팩스에는 전화 연결 주소를 만들지 않는다(10절)."""
    if not channel.value:
        return None
    if channel.channel_kind == "phone":
        return f"tel:{channel.value}"
    if channel.channel_kind == "email":
        return f"mailto:{channel.value}"
    if channel.channel_kind == "website":
        return channel.value
    return None


# ------------------------------------------------------------------ 실행


def export_static(
    cfg: Settings | None = None,
    *,
    run_id: str | None = None,
    store: ObjectStore | None = None,
    revision: str | None = None,
) -> ExportResult:
    cfg = cfg or default_settings
    store = store or build_store(cfg)
    now = utcnow()
    rev = revision or f"r{int(now.timestamp())}-{(run_id or 'manual').split('-')[-1][:8]}"
    result = ExportResult(revision=rev)
    writer = Writer(store, cfg.r2.bucket_public, result)
    prefix = f"{BASE}/r/{rev}"

    with session_scope(cfg) as session:
        org_paths = _org_paths(session)
        source_refs = _source_refs(session)

        # 분류 사전
        campuses = [
            api.Campus(id=c.id, name=c.name)
            for c in session.execute(select(m.Campus).order_by(m.Campus.name)).scalars()
        ]
        writer.put(
            f"{prefix}/catalog.json",
            api.ItemResponse[api.Catalog](
                data=api.Catalog(
                    campuses=campuses,
                    organization_types=[api.Coded(**c) for c in code_list(ORG_TYPE_LABELS)],
                    categories=[api.Coded(**c) for c in code_list(CATEGORY_LABELS)],
                    media=[api.Coded(**c) for c in code_list(MEDIUM_LABELS)],
                    source_statuses=[api.Coded(**c) for c in code_list(SOURCE_STATUS_LABELS)],
                    features=api.CatalogFeatures(accounts=False, instagram=False, deadlines=True),
                    contract_version=CONTRACT_VERSION,
                ),
                meta=_meta(rev, now),
            ),
        )

        # 조직
        child_counts = dict(
            session.execute(
                select(m.Organization.parent_id, func.count(m.Organization.id))
                .where(m.Organization.parent_id.isnot(None))
                .group_by(m.Organization.parent_id)
            ).all()
        )
        source_counts = dict(
            session.execute(
                select(m.Source.organization_id, func.count(m.Source.id))
                .where(m.Source.is_public.is_(True))
                .group_by(m.Source.organization_id)
            ).all()
        )
        org_campuses: dict[str, list[api.Campus]] = {}
        for org_id, campus_id, name in session.execute(
            select(m.OrganizationCampus.organization_id, m.Campus.id, m.Campus.name).join(
                m.Campus, m.Campus.id == m.OrganizationCampus.campus_id
            )
        ).all():
            org_campuses.setdefault(org_id, []).append(api.Campus(id=campus_id, name=name))

        organizations = [
            api.Organization(
                id=o.id,
                name=o.name,
                short_name=o.short_name,
                type=api.Coded(**coded(ORG_TYPE_LABELS, o.org_type)),
                parent_id=o.parent_id,
                campuses=org_campuses.get(o.id, []),
                path=org_paths.get(o.id, [o.name]),
                has_children=bool(child_counts.get(o.id)),
                source_count=int(source_counts.get(o.id, 0)),
            )
            for o in session.execute(
                select(m.Organization).where(m.Organization.is_active.is_(True)).order_by(m.Organization.name)
            ).scalars()
        ]
        writer.put(
            f"{prefix}/organizations.json",
            api.ListResponse[api.Organization](
                data=organizations,
                page=_page(rev, now, next_cursor=None, has_next=False),
                meta=_meta(rev, now),
            ),
        )

        # 출처
        health_rows = {h.source_id: h for h in session.execute(select(m.SourceHealth)).scalars()}
        notice_counts = dict(
            session.execute(
                select(m.SourceItem.source_id, func.count(m.SourceItem.id))
                .where(m.SourceItem.original_status == "available")
                .group_by(m.SourceItem.source_id)
            ).all()
        )
        sources_out: list[api.Source] = []
        for source, org in session.execute(
            select(m.Source, m.Organization)
            .join(m.Organization, m.Organization.id == m.Source.organization_id)
            .where(m.Source.is_public.is_(True))
            .order_by(m.Source.name)
        ).all():
            health = health_rows.get(source.id)
            sources_out.append(
                api.Source(
                    id=source.id,
                    name=source.name,
                    organization=_org_ref(org, org_paths.get(org.id, [org.name])),
                    medium=api.Coded(**coded(MEDIUM_LABELS, source.medium)),
                    content_kind=api.Coded(
                        code=source.content_kind,
                        label="공지" if source.content_kind == "notice" else "연락처",
                    ),
                    url=source.list_url,
                    status=api.Coded(**coded(SOURCE_STATUS_LABELS, source.status)),
                    status_message=source.status_message,
                    last_success_at=health.last_list_success_at if health else None,
                    history_from=None,
                    notice_count=int(notice_counts.get(source.id, 0)),
                )
            )
        writer.put(
            f"{prefix}/sources.json",
            api.ListResponse[api.Source](
                data=sources_out,
                page=_page(rev, now, next_cursor=None, has_next=False),
                meta=_meta(rev, now),
            ),
        )

        # 공지 목록·상세·색인
        notices = _load_notices(session, now)
        result.notices = len(notices)
        pages = max(1, (len(notices) + PAGE_SIZE - 1) // PAGE_SIZE)
        result.pages = pages

        for page_no in range(1, pages + 1):
            chunk = notices[(page_no - 1) * PAGE_SIZE : page_no * PAGE_SIZE]
            has_next = page_no < pages
            writer.put(
                f"{prefix}/notices/page/{page_no}.json",
                api.NoticePageFile(
                    data=[n for n, _, _, _ in chunk],
                    page=_page(
                        rev,
                        now,
                        next_cursor=str(page_no + 1) if has_next else None,
                        has_next=has_next,
                    ),
                    meta=_meta(rev, now),
                    next=f"notices/page/{page_no + 1}.json" if has_next else None,
                ),
            )

        # 공지 상세는 서로 의존하지 않는다. 한꺼번에 올린다.
        writer.put_many(
            (
                f"{prefix}/notices/{notice.id}.json",
                api.ItemResponse[api.NoticeDetail](
                    data=_notice_detail(session, notice, revision_row, source_refs),
                    meta=_meta(rev, now),
                ),
            )
            for notice, revision_row, _item, _aud in notices
        )

        entries = [
            api.IndexEntry(
                id=n.id,
                t=n.title,
                c=n.primary_category.code,
                o=None,
                s=n.primary_source.id,
                d=n.published_date,
                v=n.first_visible_at,
                a=[
                    "university"
                    if a.type == "university"
                    else ("undetermined" if a.type == "undetermined" else f"{'campus' if a.type == 'campus' else 'org'}:{a.id}")
                    for a in n.audiences
                ],
            )
            for n, _, _, _ in notices[:INDEX_LIMIT]
        ]
        writer.put(
            f"{prefix}/notices/index.json",
            api.NoticeIndexFile(revision=rev, generated_at=now, count=len(entries), entries=entries),
        )

        # 연락처
        contacts = _load_contacts(session, org_paths)
        result.contacts = len(contacts)
        contact_pages = max(1, (len(contacts) + PAGE_SIZE - 1) // PAGE_SIZE)
        for page_no in range(1, contact_pages + 1):
            chunk = contacts[(page_no - 1) * PAGE_SIZE : page_no * PAGE_SIZE]
            has_next = page_no < contact_pages
            writer.put(
                f"{prefix}/contacts/page/{page_no}.json",
                api.ListResponse[api.Contact](
                    data=chunk,
                    page=_page(
                        rev, now, next_cursor=str(page_no + 1) if has_next else None, has_next=has_next
                    ),
                    meta=_meta(rev, now),
                ),
            )
        # 연락처 상세도 서로 의존하지 않는다.
        writer.put_many(
            (
                f"{prefix}/contacts/{contact.id}.json",
                api.ItemResponse[api.Contact](data=contact, meta=_meta(rev, now)),
            )
            for contact in contacts
        )

        # 공개 상태
        last_run = session.execute(
            select(m.Run).where(m.Run.kind == "collect").order_by(m.Run.started_at.desc()).limit(1)
        ).scalar_one_or_none()
        source_lines = [
            api.SourceStatusLine(
                id=s.id,
                name=s.name,
                status=api.Coded(**coded(SOURCE_STATUS_LABELS, s.status)),
                last_success_at=(health_rows.get(s.id).last_list_success_at if health_rows.get(s.id) else None),
                consecutive_failures=(health_rows.get(s.id).consecutive_failures if health_rows.get(s.id) else 0),
            )
            for s in session.execute(
                select(m.Source).where(m.Source.is_public.is_(True)).order_by(m.Source.name)
            ).scalars()
        ]
        status = api.RunStatus(
            generated_at=now,
            revision=rev,
            last_run_started_at=last_run.started_at if last_run else None,
            last_run_finished_at=last_run.finished_at if last_run else None,
            last_run_result=last_run.result if last_run else None,
            code_commit=last_run.code_commit if last_run else None,
            contract_version=CONTRACT_VERSION,
            sources_total=len(source_lines),
            sources_active=sum(1 for s in source_lines if s.status.code == "active"),
            sources_failing=sum(1 for s in source_lines if s.consecutive_failures > 0),
            notices_total=result.notices,
            contacts_total=result.contacts,
            sources=source_lines,
        )
        writer.put(f"{BASE}/status.json", status)

    # 마지막에 포인터를 바꾼다. 이 순서 때문에 두 개정이 섞이지 않는다.
    writer.put(
        f"{BASE}/latest.json",
        api.LatestPointer(
            revision=rev,
            generated_at=now,
            base_path=f"{BASE}/r/{rev}",
            contract_version=CONTRACT_VERSION,
            notice_pages=result.pages,
            notices_total=result.notices,
            contacts_total=result.contacts,
        ),
    )
    log.info(
        "정적 파일 %d개 생성(%.1fKB), 개정 %s", result.files_written, result.bytes_written / 1024, rev
    )
    return result


def prune_old_revisions(cfg: Settings | None = None, *, keep: int = 3, store: ObjectStore | None = None) -> int:
    """오래된 개정을 정리한다. 최신 몇 개는 이어보기 때문에 남긴다(12절)."""
    cfg = cfg or default_settings
    store = store or build_store(cfg)
    keys = store.list_keys(cfg.r2.bucket_public, f"{BASE}/r/")
    revisions = sorted({k.split("/")[2] for k in keys if k.count("/") >= 3})
    doomed = revisions[:-keep] if len(revisions) > keep else []
    removed = 0
    for revision in doomed:
        for key in [k for k in keys if k.startswith(f"{BASE}/r/{revision}/")]:
            store.delete(cfg.r2.bucket_public, key)
            removed += 1
    return removed
