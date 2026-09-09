"""정적 조회 파일 생성(12·15.1절).

내용이 같은 JSON은 /v1/objects/<hash>.json 에 한 번만 올린다. 개정별 manifest가
공개 경로와 내용 객체, 생성시각·신선도를 묶고 Worker가 기존 응답을 합성한다.
명세 업로드 후 /v1/latest.json을 전환하고 공개 상태를 갱신한다.

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
  v1/state/<rev>.json                  다음 회차가 "무엇이 바뀌었나"를 알기 위한 기록

만드는 방식은 세 가지다(KHU_EXPORT_MODE). docs/공개파일_증분화_2026-09-09.md 참고.
  full        보이는 공지를 본문까지 전부 다시 읽어 전부 다시 만든다.
  incremental 바뀐 공지만 본문까지 읽고, 나머지 상세 파일은 이전 개정 명세를 물려받는다.
  shadow      full 로 만들어 공개하되 incremental 도 함께 돌려 결과를 바이트 단위로 견준다.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Load, Session

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
from app.domain.body_html import sanitize_body_html
from app.domain.dates import (
    KST,
    as_utc,
    display_published,
    freshness_code,
    is_future_published,
    utcnow,
)
from app.domain.images import extract_images, poster_image
from app.export.state import (
    EXPORT_FORMAT_VERSION,
    STATE_PREFIX,
    ExportPlan,
    ExportState,
    NoticeState,
    iso_stamp,
    latest_revision,
    load_manifest,
    load_state,
    plan_export,
    save_state,
    shared_fingerprint,
    state_key,
)
from app.storage import models as m
from app.storage.db import measure_reads, session_scope
from app.storage.objects import ObjectStore, PutResult, build_store

log = logging.getLogger("khu.export")

PAGE_SIZE = 50
INDEX_SHARD_SIZE = 2000
BASE = "v1"


@dataclass
class ExportResult:
    revision: str
    files_written: int = 0
    files_reused: int = 0
    bytes_written: int = 0
    notices: int = 0
    contacts: int = 0
    pages: int = 0
    skipped_reason: str | None = None
    keys: list[str] = field(default_factory=list)
    # 아래는 증분화(2026-09-09)에서 붙었다. asdict 로 그대로 기록에 실린다.
    requested_mode: str = "full"
    mode: str = "full"
    mode_reason: str = ""
    notices_changed: int = 0
    notices_reused: int = 0
    details_carried: int = 0
    state_bytes: int = 0
    db_read_bytes: int = 0
    db_read_rows: int = 0
    db_read_statements: int = 0
    shadow: dict[str, Any] | None = None


@dataclass
class LoadedNotice:
    """목록 항목 하나와, 상세를 만들 때 필요한 본문.

    본문은 이번 회차에 실제로 읽은 공지에만 들어 있다. 이전 판을 물려받은 공지는
    상세 파일을 다시 만들지 않으므로 None 이다.
    """

    notice: api.Notice
    revision_id: str
    body_text: str | None = None
    body_html: str | None = None
    carried: bool = False


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
    def __init__(
        self,
        store: ObjectStore,
        bucket: str,
        result: ExportResult,
        *,
        record_keys: bool = False,
        existing: set[str] | None = None,
    ):
        self.store = store
        self.bucket = bucket
        self.result = result
        self.record_keys = record_keys
        self._lock = threading.Lock()
        self.entries: dict[str, str] = {}
        self.freshness: dict[str, Any] = {}
        self._existing: set[str] | None = existing

    def existing_objects(self) -> set[str]:
        """이미 올라가 있는 내용 객체 이름. 한 회차에 한 번만 목록을 받는다."""
        if self._existing is None:
            self._existing = set(self.store.list_keys(self.bucket, f"{BASE}/objects/"))
        return self._existing

    def carry(self, key: str, object_key: str) -> None:
        """이전 개정의 파일을 그대로 물려받는다.

        내용 객체는 이름이 곧 내용 해시라, 내용이 같으면 새로 올릴 것이 없다.
        새 개정 명세가 같은 객체를 가리키게만 하면 된다.
        """
        self.entries[key.split("/", 3)[3]] = object_key
        with self._lock:
            self.result.files_reused += 1
            self.result.details_carried += 1

    def record_freshness(self, source_id: str, value: Any) -> None:
        """개정 명세의 신선도 표를 채운다.

        Worker 는 상세·목록을 합성할 때 이 표에서 신선도를 꺼낸다. 값이 없으면
        조회가 통째로 실패하므로, 물려받은 파일의 출처도 반드시 여기 들어가야 한다.
        """
        self.freshness.setdefault(source_id, value)

    def _prepare(self, key: str, model: Any) -> tuple[str, bytes] | None:
        data = json.loads(_dump(model))
        if not key.startswith(f"{BASE}/r/"):
            return key, _dump(data)
        self.existing_objects()
        if "meta" in data:
            data["meta"] = None
        if "page" in data:
            data["page"]["snapshot_at"] = None
            data["page"]["dataset_revision"] = None
        for name in ("revision", "generated_at"):
            if name in data:
                data[name] = None

        def strip_freshness(value):
            if isinstance(value, dict):
                if "freshness" in value and "primary_source" in value:
                    self.freshness[value["primary_source"]["id"]] = value["freshness"]
                    value["freshness"] = None
                for child in value.values():
                    strip_freshness(child)
            elif isinstance(value, list):
                for child in value:
                    strip_freshness(child)

        strip_freshness(data)
        payload = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        object_key = f"{BASE}/objects/{hashlib.sha256(payload).hexdigest()}.json"
        self.entries[key.split("/", 3)[3]] = object_key
        if object_key in self._existing:
            self.result.files_reused += 1
            return None
        self._existing.add(object_key)
        return object_key, payload

    def publish_manifest(self, prefix: str, now: datetime) -> None:
        self._upload(f"{prefix}/manifest.json", _dump({
            "version": 1, "revision": self.result.revision, "generated_at": now,
            "entries": self.entries, "freshness": self.freshness,
        }))

    def put(self, key: str, model: Any) -> None:
        prepared = self._prepare(key, model)
        if prepared is not None:
            self._upload(*prepared)

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
        pairs = [prepared for key, model in items if (prepared := self._prepare(key, model)) is not None]
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
    body_text: str | None,
    body_html: str | None,
    *,
    source_ref: api.SourceRef,
    categories: list[m.NoticeCategory],
    audience_rows: list[tuple[m.NoticeAudience, str | None, str | None]],
    source_count: int,
    last_checked: datetime | None,
    now: datetime,
    proxy_base: str = "",
) -> api.Notice:
    primary = next((c for c in categories if c.is_primary), None)
    secondary = [c for c in categories if not c.is_primary]
    # 원문 날짜가 미래면 보이는 날짜만 "처음 본 시각"으로 바꾼다. 저장은 그대로 둔다.
    shown = _shown_published(notice, now)

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
        published_date=shown.date,
        published_at=shown.at,
        published_precision=shown.precision,
        published_adjusted=shown.adjusted,
        original_published_date=notice.published_date if shown.adjusted else None,
        original_published_at=as_utc(notice.published_at) if shown.adjusted else None,
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
        # 포스터 한 장뿐인 공지는 그림을 못 보여주면 제목만 남는다. 목록에도 대표 그림을 준다.
        poster_image=poster_image(body_text, extract_images(
            body_html, base_url=item.canonical_url, proxy_base=proxy_base,
        )),
    )


def window_floor(window_start: date | None) -> datetime | None:
    """공개 하한을 UTC 시각으로 바꾼다.

    경계는 표시 기준인 Asia/Seoul 날짜다. 3월 1일 오전 한국시간 글이 UTC 로는
    2월 28일이므로 UTC 자정으로 자르면 하루치를 잃는다. 목록과 총계가 같은
    기준을 쓰도록 이 함수 하나만 본다.
    """
    if window_start is None:
        return None
    return datetime(window_start.year, window_start.month, window_start.day, tzinfo=KST).astimezone(UTC)


# 시각을 모르는 공지는 그날의 맨 아래로 간다. 어느 시각이었는지 모르는 글을 목록
# 꼭대기에 올리면, 방금 올라온 것이 확실한 글을 아래로 밀어낸다. 목록의 맨 위는 가장
# 최신이라고 확신하는 글의 자리다. 이 값이 그 "맨 아래"를 뜻한다.
UNKNOWN_TIME = datetime.min.replace(tzinfo=UTC)


def newest_first(
    *,
    published_date: date | None,
    published_at: datetime | None,
    first_visible_at: datetime | None,
    notice_id: str,
) -> tuple:
    """화면의 "최신순" 키. 큰 것이 위로 온다(내림차순 정렬).

    우리가 처음 본 시각으로 세우면 백필이 과거 페이지를 긁는 동안 오래된 공지가
    맨 위로 올라와 뒤죽박죽으로 보인다. 그래서 발행일이 먼저다. 날짜가 같으면
    발행 시각으로, 시각을 모르면 그날의 맨 아래에 두고 처음 본 시각으로 가른다.
    데이터베이스마다 시간대 함수가 달라 파이썬에서 정렬한다.

    같은 키를 색인(IndexEntry)의 d·p·v·id 로도 내보낸다. 화면이 색인을 걸러낸 뒤
    다시 세워도 목록 페이지와 같은 순서가 나오게 하려면 두 곳이 같은 키를 써야 한다.
    """
    stamp = as_utc(published_at)
    day = published_date or (stamp.astimezone(KST).date() if stamp else None)
    return (
        day or date.min,
        stamp or UNKNOWN_TIME,
        as_utc(first_visible_at) or UNKNOWN_TIME,
        notice_id,
    )


def _shown_published(notice: m.Notice, now: datetime):
    """이 공지를 화면에 어떤 발행일로 내보낼지 정한다."""
    return display_published(
        published_date=notice.published_date,
        published_at=notice.published_at,
        published_precision=notice.published_precision,
        first_visible_at=notice.first_visible_at,
        now=now,
    )


def _entry_order(entry: api.Notice) -> tuple:
    """목록·색인 정렬 키.

    내보낸 값(= 보이는 날짜) 그대로 세운다. 이전 판에서 물려받은 항목과 이번에 새로
    만든 항목을 한 줄로 섞어 세워야 하므로, 정렬은 데이터베이스 행이 아니라 계약
    모형을 본다. 두 항목의 값은 같은 규칙으로 만들어졌다.
    """
    return newest_first(
        published_date=entry.published_date,
        published_at=entry.published_at,
        first_visible_at=entry.first_visible_at,
        notice_id=entry.id,
    )


def _light_scan(session: Session) -> list[tuple[str, datetime | None, str | None]]:
    """공개 대상 공지의 식별자·수정 시각·현재 개정 id 만 훑는다.

    제목도 본문도 읽지 않는다. 2026-09-09 운영 실측으로 공지 23,831건에 약 1.5 MB다
    (같은 공지의 본문 body_html+body_text 는 49.5 MB). 무엇이 바뀌었는지는 이 결과와
    이전 판 기록을 견주어 정한다. 추측으로 "이쯤 바뀌었겠지" 하고 고르지 않는다.
    """
    rows = session.execute(
        select(m.Notice.id, m.Notice.updated_at, m.SourceItem.current_revision_id)
        .join(m.SourceItem, m.SourceItem.id == m.Notice.primary_source_item_id)
        .join(m.Source, m.Source.id == m.SourceItem.source_id)
        .where(m.Notice.status == "visible", m.Source.is_public.is_(True))
    ).all()
    return [(row[0], row[1], row[2]) for row in rows]


def _load_notices(
    session: Session,
    now: datetime,
    *,
    source_refs: dict[str, api.SourceRef],
    health: dict[str, datetime | None],
    window_start: date | None = None,
    proxy_base: str = "",
    notice_ids: set[str] | None = None,
) -> tuple[list[LoadedNotice], list[str]]:
    """공개 대상 공지를 읽어 목록 항목을 만든다.

    ``notice_ids`` 를 주면 그 공지만 읽는다(증분). 주지 않으면 전부 읽는다.
    돌려주는 값은 (공개할 항목, 공개 범위 밖이라 뺀 공지 id) 두 가지다. 범위 밖도
    돌려주는 이유는, 다음 회차가 그 공지를 "처음 보는 공지"로 오해해 본문까지 다시
    읽지 않도록 기록에 남겨야 하기 때문이다.

    수집 범위 시작일보다 오래된 글은 공개하지 않는다. 저장은 그대로 두고 공개만 막는다.
    경계는 표시 기준인 Asia/Seoul 날짜로 판정한다.

    열을 골라 읽는다. notices.search_text 는 내보내기에서 쓰지 않는데 회차마다
    16.5 MB 였고(2026-09-09 실측), source_items.detail_listing 도 2.0 MB 였다.
    """
    if notice_ids is not None and not notice_ids:
        return [], []

    categories_query = select(m.NoticeCategory)
    audiences_query = (
        select(m.NoticeAudience, m.Campus.name, m.Organization.name)
        .outerjoin(m.Campus, m.Campus.id == m.NoticeAudience.campus_id)
        .outerjoin(m.Organization, m.Organization.id == m.NoticeAudience.organization_id)
    )
    counts_query = (
        select(m.NoticeSource.notice_id, func.count(m.NoticeSource.id))
        .where(m.NoticeSource.is_active.is_(True))
        .group_by(m.NoticeSource.notice_id)
    )
    rows_query = (
        select(m.Notice, m.SourceItem, m.SourceItemRevision, m.Source)
        .join(m.SourceItem, m.SourceItem.id == m.Notice.primary_source_item_id)
        .join(m.SourceItemRevision, m.SourceItemRevision.id == m.SourceItem.current_revision_id)
        .join(m.Source, m.Source.id == m.SourceItem.source_id)
        .where(m.Notice.status == "visible", m.Source.is_public.is_(True))
        .options(
            Load(m.Notice).load_only(
                m.Notice.title, m.Notice.excerpt, m.Notice.published_date, m.Notice.published_at,
                m.Notice.published_precision, m.Notice.first_visible_at, m.Notice.updated_at,
                m.Notice.deadline_date, m.Notice.deadline_at, m.Notice.deadline_precision,
                m.Notice.deadline_evidence, m.Notice.audience_note,
            ),
            Load(m.SourceItem).load_only(
                m.SourceItem.source_id, m.SourceItem.canonical_url,
                m.SourceItem.original_status, m.SourceItem.is_pinned,
            ),
            Load(m.SourceItemRevision).load_only(
                m.SourceItemRevision.body_text, m.SourceItemRevision.body_html,
            ),
            Load(m.Source).load_only(m.Source.name, m.Source.medium),
        )
        .order_by(m.Notice.first_visible_at.desc(), m.Notice.id.desc())
    )
    if notice_ids is not None:
        wanted = list(notice_ids)
        categories_query = categories_query.where(m.NoticeCategory.notice_id.in_(wanted))
        audiences_query = audiences_query.where(m.NoticeAudience.notice_id.in_(wanted))
        counts_query = counts_query.where(m.NoticeSource.notice_id.in_(wanted))
        rows_query = rows_query.where(m.Notice.id.in_(wanted))

    counts = dict(session.execute(counts_query).all())

    categories: dict[str, list[m.NoticeCategory]] = {}
    for row in session.execute(categories_query).scalars():
        categories.setdefault(row.notice_id, []).append(row)

    audiences: dict[str, list[tuple[m.NoticeAudience, str | None, str | None]]] = {}
    for row, campus_name, org_name in session.execute(audiences_query).all():
        audiences.setdefault(row.notice_id, []).append((row, campus_name, org_name))

    rows = session.execute(rows_query).all()

    floor = window_floor(window_start)

    def within_window(notice: m.Notice) -> bool:
        """공개 범위 안인지 판정한다."""
        if is_future_published(
            published_date=notice.published_date, published_at=notice.published_at, now=now
        ):
            # 사용자 결정(2026-09-07): 미래 날짜 글도 공개한다. 보이는 날짜는 우리가 처음
            # 본 시각이 되고(_shown_published), 그 시각은 언제나 범위 안이다.
            return True
        # sqlite 는 시간대 없이 돌려주므로 먼저 UTC 로 정규화한다.
        published = as_utc(notice.published_at)
        if published is None:
            # 원문에 시각이 없으면 파서는 시각을 지어내지 않고 날짜만 남긴다.
            # 날짜를 아는 글까지 불명으로 묶으면 멀쩡한 공지가 가려지므로 날짜로 판정한다.
            stamp = notice.published_date
            if stamp is None:
                # 정말로 발행일을 모르면 범위 안이라고 볼 근거가 없다. 사용자 결정(2026-09-07)에
                # 따라 공개하지 않는다. 원문은 남으므로 날짜를 알아내면 다시 공개된다.
                return window_start is None
            return window_start is None or stamp >= window_start
        return floor is None or published >= floor

    built: list[LoadedNotice] = []
    dropped: list[str] = []
    for notice, item, revision, source in rows:
        if not within_window(notice):
            dropped.append(notice.id)
            continue
        built.append(
            LoadedNotice(
                notice=_build_notice(
                    notice,
                    item,
                    revision.body_text,
                    revision.body_html,
                    source_ref=source_refs[source.id],
                    categories=categories.get(notice.id, []),
                    audience_rows=audiences.get(notice.id, []),
                    source_count=counts.get(notice.id, 1),
                    last_checked=health.get(source.id),
                    now=now,
                    proxy_base=proxy_base,
                ),
                revision_id=revision.id,
                body_text=revision.body_text,
                body_html=revision.body_html,
            )
        )
    built.sort(key=lambda loaded: _entry_order(loaded.notice), reverse=True)
    return built, dropped


def _notice_detail(
    session: Session,
    base: api.Notice,
    revision_id: str,
    body_text: str | None,
    body_html: str | None,
    source_refs: dict[str, api.SourceRef],
    *,
    proxy_base: str = "",
    attachments_by_revision: dict[str, list[m.Attachment]] | None = None,
    links_by_notice: dict[str, list[tuple[m.NoticeSource, m.SourceItem, date | None]]] | None = None,
    mentions_by_revision: dict[str, list[m.NoticeContactMention]] | None = None,
) -> api.NoticeDetail:
    attachment_rows = (
        attachments_by_revision.get(revision_id, [])
        if attachments_by_revision is not None
        else session.execute(
            select(m.Attachment).where(m.Attachment.revision_id == revision_id)
        ).scalars().all()
    )
    attachments = [
        api.Attachment(
            id=a.id,
            filename=a.filename,
            kind=a.kind,
            size_bytes=a.size_bytes,
            url=a.url,
            status=api.Coded(code=a.status, label="목록 확인됨" if a.status == "listed" else a.status),
        )
        for a in attachment_rows
    ]

    if links_by_notice is None:
        linked = session.execute(
            _linked_sources_query().where(
                m.NoticeSource.notice_id == base.id, m.NoticeSource.is_active.is_(True)
            )
        ).all()
    else:
        linked = links_by_notice.get(base.id, [])

    sources = [
        api.NoticeSourceRef(
            source_item_id=item.id,
            source=source_refs[item.source_id],
            url=item.canonical_url,
            published_date=published,
            original_status=api.Coded(**coded(ORIGINAL_STATUS_LABELS, item.original_status)),
            is_primary=bool(link.is_primary),
        )
        for link, item, published in linked
    ]

    mention_rows = (
        mentions_by_revision.get(revision_id, [])
        if mentions_by_revision is not None
        else session.execute(
            select(m.NoticeContactMention).where(m.NoticeContactMention.revision_id == revision_id)
        ).scalars().all()
    )
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
        for row in mention_rows
    ]

    return api.NoticeDetail(
        **base.model_dump(),
        body_text=body_text,
        # 화면이 이 HTML 을 문서에 그대로 넣는다. 남의 글이므로 허용 목록으로 다시 짓고,
        # 그림 주소는 중계 경로로 바꾼다.
        body_html=sanitize_body_html(body_html, base_url=base.original_url, proxy_base=proxy_base),
        images=extract_images(body_html, base_url=base.original_url, proxy_base=proxy_base),
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


def _linked_sources_query():
    """상세의 sources[] 를 만드는 조회.

    개정 행에서 필요한 값은 발행일 하나뿐이다. 예전에는 개정 전체를 가져와
    이 조회만으로 본문을 한 번 더 읽었다(2026-09-09 실측 기준 활성 연결 25,152건,
    본문 수십 MB). 열을 하나만 고르면 그 비용이 통째로 사라진다.
    """
    return (
        select(m.NoticeSource, m.SourceItem, m.SourceItemRevision.published_date)
        .join(m.SourceItem, m.SourceItem.id == m.NoticeSource.source_item_id)
        .outerjoin(m.SourceItemRevision, m.SourceItemRevision.id == m.SourceItem.current_revision_id)
        .options(
            Load(m.SourceItem).load_only(
                m.SourceItem.source_id, m.SourceItem.canonical_url, m.SourceItem.original_status,
            ),
            Load(m.NoticeSource).load_only(m.NoticeSource.notice_id, m.NoticeSource.is_primary),
        )
    )


def _load_notice_detail_parts(
    session: Session,
    notices: list[LoadedNotice],
) -> tuple[
    dict[str, list[m.Attachment]],
    dict[str, list[tuple[m.NoticeSource, m.SourceItem, date | None]]],
    dict[str, list[m.NoticeContactMention]],
]:
    """공지 상세에 필요한 관계를 묶어서 읽는다.

    공지마다 첨부·원문 연결·문의처를 다시 조회하면 대량 내보내기에서
    왕복이 공지 수에 비례한다. 개정 스냅샷의 식별자를 먼저 모은 뒤 세 번의
    조회로 전체 상세 자료를 준비한다. 증분 회차에서는 상세를 다시 만드는 공지만
    여기 들어오므로 이 세 조회도 그만큼만 읽는다.
    """
    revision_ids = [loaded.revision_id for loaded in notices]
    notice_ids = [loaded.notice.id for loaded in notices]

    attachments: dict[str, list[m.Attachment]] = {}
    if revision_ids:
        for row in session.execute(
            select(m.Attachment).where(m.Attachment.revision_id.in_(revision_ids))
        ).scalars():
            attachments.setdefault(row.revision_id, []).append(row)

    links: dict[str, list[tuple[m.NoticeSource, m.SourceItem, date | None]]] = {}
    if notice_ids:
        rows = session.execute(
            _linked_sources_query().where(
                m.NoticeSource.notice_id.in_(notice_ids), m.NoticeSource.is_active.is_(True)
            )
        ).all()
        for link, item, published in rows:
            links.setdefault(link.notice_id, []).append((link, item, published))

    mentions: dict[str, list[m.NoticeContactMention]] = {}
    if revision_ids:
        for row in session.execute(
            select(m.NoticeContactMention).where(m.NoticeContactMention.revision_id.in_(revision_ids))
        ).scalars():
            mentions.setdefault(row.revision_id, []).append(row)
    return attachments, links, mentions


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


MODES = ("full", "shadow", "incremental")


class _ReadOnlyStore:
    """그림자 회차용 저장소. 읽기는 그대로 하고 쓰기만 삼킨다.

    그림자 회차는 "증분으로 만들었으면 무엇이 나왔을까"를 계산하기만 한다.
    공개되는 것은 전체 방식의 결과이므로 여기서는 한 바이트도 올리지 않는다.
    """

    def __init__(self, inner: ObjectStore) -> None:
        self.inner = inner

    def put_bytes(self, bucket: str, key: str, data: bytes, *, content_type: str, compress: bool = False):
        return PutResult(bucket=bucket, key=key, size=len(data), compressed=False)

    def get_bytes(self, bucket: str, key: str) -> bytes:
        return self.inner.get_bytes(bucket, key)

    def delete(self, bucket: str, key: str) -> None:  # pragma: no cover - 부르지 않는다
        raise RuntimeError("그림자 회차는 지우지 않습니다")

    def list_keys(self, bucket: str, prefix: str) -> list[str]:
        return self.inner.list_keys(bucket, prefix)


def _freshness_of(source_id: str, health: dict[str, datetime | None], now: datetime) -> api.Freshness:
    last = health.get(source_id)
    return api.Freshness(
        **coded(FRESHNESS_LABELS, freshness_code(last, now=now)), last_checked_at=last
    )


def _fingerprint_parts(
    cfg: Settings,
    *,
    source_refs: dict[str, api.SourceRef],
    organizations: dict[str, str],
    campuses: list[api.Campus],
) -> dict[str, Any]:
    """이전 판을 물려받아도 되는지 가르는 "공유 정보".

    공지 상세·목록 파일 안에는 그 공지 것이 아닌 값도 들어간다. 출처 이름, 조직·캠퍼스
    이름, 공개 하한, 그림 중계 주소가 그렇다. 이것들이 바뀌면 공지가 하나도 바뀌지
    않아도 파일 내용이 달라지므로, 그 회차는 전체 방식으로 돈다.

    코드 판(GITHUB_SHA)도 넣는다. 내보내기 코드를 고쳐 놓고 지문을 올리는 것을 잊으면
    낡은 파일이 조용히 살아남기 때문이다. 배포한 다음 회차 한 번만 전체로 돌면 된다.
    """
    return {
        "format": EXPORT_FORMAT_VERSION,
        "contract": CONTRACT_VERSION,
        "window": cfg.initial_window_start.isoformat() if cfg.initial_window_start else None,
        "proxy": cfg.public_api_base,
        "code": os.environ.get("GITHUB_SHA", "").strip(),
        "sources": sorted((ref.id, ref.name, ref.medium.code) for ref in source_refs.values()),
        "organizations": sorted(organizations.items()),
        "campuses": sorted((c.id, c.name) for c in campuses),
    }


def _compare_entries(full: dict[str, str], shadow: dict[str, str], *, limit: int = 10) -> dict[str, Any]:
    """전체 방식과 증분 방식이 만든 파일을 견준다.

    개정 명세의 값은 곧 내용 해시(``v1/objects/<sha256>.json``)이므로, 두 명세를
    맞춰 보는 것이 파일을 바이트 단위로 맞춰 보는 것과 같다. 내려받을 필요가 없다.
    """
    only_full = sorted(set(full) - set(shadow))
    only_shadow = sorted(set(shadow) - set(full))
    both = sorted(set(full) & set(shadow))
    differing = [path for path in both if full[path] != shadow[path]]
    return {
        "compared": len(both),
        "mismatched": len(differing),
        "missing_in_incremental": len(only_full),
        "extra_in_incremental": len(only_shadow),
        "examples": differing[:limit],
        "missing_examples": only_full[:limit],
        "extra_examples": only_shadow[:limit],
        "matched": not differing and not only_full and not only_shadow,
    }


def _write_revision(
    session: Session,
    cfg: Settings,
    result: ExportResult,
    *,
    now: datetime,
    rev: str,
    plan: ExportPlan,
    state: ExportState | None,
    manifest_entries: dict[str, str],
    live: dict[str, tuple[datetime | None, str | None]],
    store: ObjectStore,
    existing: set[str] | None,
    org_paths: dict[str, list[str]],
    source_refs: dict[str, api.SourceRef],
    campuses: list[api.Campus],
    health_rows: dict[str, m.SourceHealth],
    fingerprint: str,
    record_keys: bool = False,
) -> tuple[Writer, ExportState]:
    """한 개정을 실제로 만든다. 방식(plan.mode)에 따라 읽는 양만 달라진다.

    공유 정보(분류 사전·조직·출처·연락처·공개 상태)는 어느 방식이든 매 회차 전부
    새로 읽고 다시 만든다. 작고, 화면 전체에 영향을 주며, 틀리면 티가 나기 때문이다.
    증분이 아끼는 것은 오직 "본문이 든 공지 상세"와 그 목록 항목이다.
    """
    bucket = cfg.r2.bucket_public
    writer = Writer(store, bucket, result, record_keys=record_keys, existing=existing)
    prefix = f"{BASE}/r/{rev}"
    health = {source_id: row.last_list_success_at for source_id, row in health_rows.items()}

    # 분류 사전
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
                initial_window_start=cfg.initial_window_start,
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
    # 폐쇄·대기·운영 중지를 뺀 수. 화면은 이 값으로 "아직 볼 것이 없는 조직"을 가린다.
    # (정경대학은 출처 4개가 전부 폐쇄인데 source_count 로는 4로 세어져 살아남았다.)
    active_source_counts = dict(
        session.execute(
            select(m.Source.organization_id, func.count(m.Source.id))
            .where(
                m.Source.is_public.is_(True),
                m.Source.status.in_(("active", "delayed", "blocked")),
            )
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
            active_source_count=int(active_source_counts.get(o.id, 0)),
            is_alias=bool(getattr(o, "is_alias", False)),
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
        health_row = health_rows.get(source.id)
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
                last_success_at=health_row.last_list_success_at if health_row else None,
                history_from=(health_row.backfill_oldest_date if health_row else None),
                notice_count=int(notice_counts.get(source.id, 0)),
                initial_window_start=(
                    health_row.initial_window_start if health_row else cfg.initial_window_start
                ),
                backfill_status=(health_row.backfill_status if health_row else "not_started"),
                backfill_complete=bool(health_row.backfill_complete) if health_row else False,
                backfill_oldest_date=(health_row.backfill_oldest_date if health_row else None),
                last_scan_stop_reason=(health_row.last_scan_stop_reason if health_row else None),
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
    fresh_ids = plan.changed if plan.mode == "incremental" else None
    loaded, dropped = _load_notices(
        session,
        now,
        source_refs=source_refs,
        health=health,
        window_start=cfg.initial_window_start,
        proxy_base=cfg.public_api_base,
        notice_ids=fresh_ids,
    )
    carried: list[LoadedNotice] = []
    if plan.mode == "incremental" and state is not None:
        for notice_id in plan.reused:
            previous = state.notices.get(notice_id)
            if previous is None or previous.entry is None:  # pragma: no cover - 계획이 걸러 낸다
                continue
            entry = api.Notice.model_validate(previous.entry)
            # 신선도는 파일 안이 아니라 개정 명세에 실린다. 물려받은 항목이라도
            # 이번 회차의 값으로 갈아 끼워야 화면이 낡은 "마지막 확인"을 보지 않는다.
            entry.freshness = _freshness_of(entry.primary_source.id, health, now)
            carried.append(
                LoadedNotice(notice=entry, revision_id=previous.revision_id or "", carried=True)
            )

    notices = loaded + carried
    notices.sort(key=lambda item: _entry_order(item.notice), reverse=True)
    result.notices = len(notices)
    result.notices_changed = len(loaded)
    result.notices_reused = len(carried)
    pages = max(1, (len(notices) + PAGE_SIZE - 1) // PAGE_SIZE)
    result.pages = pages

    for page_no in range(1, pages + 1):
        chunk = notices[(page_no - 1) * PAGE_SIZE : page_no * PAGE_SIZE]
        has_next = page_no < pages
        writer.put(
            f"{prefix}/notices/page/{page_no}.json",
            api.NoticePageFile(
                data=[item.notice for item in chunk],
                page=_page(
                    rev, now,
                    next_cursor=str(page_no + 1) if has_next else None,
                    has_next=has_next,
                ),
                meta=_meta(rev, now),
                next=f"notices/page/{page_no + 1}.json" if has_next else None,
            ),
        )

    # 물려받는 상세 파일. 내용 객체 이름만 새 명세에 옮겨 적는다.
    for item in carried:
        path = f"notices/{item.notice.id}.json"
        writer.carry(f"{prefix}/{path}", manifest_entries[path])
        writer.record_freshness(
            item.notice.primary_source.id, json.loads(_dump(item.notice.freshness))
        )

    # 이번에 다시 만드는 상세는 서로 의존하지 않는다. 한꺼번에 올린다.
    detail_attachments, detail_links, detail_mentions = _load_notice_detail_parts(session, loaded)
    writer.put_many(
        (
            f"{prefix}/notices/{item.notice.id}.json",
            api.ItemResponse[api.NoticeDetail](
                data=_notice_detail(
                    session,
                    item.notice,
                    item.revision_id,
                    item.body_text,
                    item.body_html,
                    source_refs,
                    proxy_base=cfg.public_api_base,
                    attachments_by_revision=detail_attachments,
                    links_by_notice=detail_links,
                    mentions_by_revision=detail_mentions,
                ),
                meta=_meta(rev, now),
            ),
        )
        for item in loaded
    )

    entries = [
        api.IndexEntry(
            id=n.id,
            t=n.title,
            c=n.primary_category.code,
            o=None,
            s=n.primary_source.id,
            d=n.published_date,
            # 목록 페이지와 똑같은 "최신순" 키를 색인에도 담는다. p 가 없으면
            # 화면은 같은 날 안의 시각 순서를 복원할 수 없다.
            p=n.published_at,
            v=n.first_visible_at,
            a=[
                "university"
                if a.type == "university"
                else ("undetermined" if a.type == "undetermined" else f"{'campus' if a.type == 'campus' else 'org'}:{a.id}")
                for a in n.audiences
            ],
        )
        for n in (item.notice for item in notices)
    ]
    shards = []
    if len(entries) > INDEX_SHARD_SIZE:
        for offset in range(0, len(entries), INDEX_SHARD_SIZE):
            chunk = entries[offset:offset + INDEX_SHARD_SIZE]
            path = f"notices/index/{offset // INDEX_SHARD_SIZE + 1}.json"
            writer.put(f"{prefix}/{path}", api.NoticeIndexFile(
                revision=rev, generated_at=now, count=len(chunk), entries=chunk,
            ))
            shards.append({"path": path, "count": len(chunk)})
    writer.put(
        f"{prefix}/notices/index.json",
        api.NoticeIndexFile(revision=rev, generated_at=now, count=len(entries), entries=[] if shards else entries, shards=shards),
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
            backfill_status=(health_rows.get(s.id).backfill_status if health_rows.get(s.id) else "not_started"),
            backfill_complete=bool(health_rows.get(s.id).backfill_complete) if health_rows.get(s.id) else False,
            last_scan_stop_reason=(health_rows.get(s.id).last_scan_stop_reason if health_rows.get(s.id) else None),
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
        initial_window_start=cfg.initial_window_start,
        sources_backfill_complete=sum(1 for s in source_lines if s.backfill_complete),
        sources_backfill_incomplete=sum(1 for s in source_lines if not s.backfill_complete),
        sources=source_lines,
    )

    writer.publish_manifest(prefix, now)
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
            initial_window_start=cfg.initial_window_start,
        ),
    )
    writer.put(f"{BASE}/status.json", status)

    # 다음 회차가 볼 이전 판 기록.
    last_full = iso_stamp(now) if plan.mode == "full" else (state.last_full_at if state else None)
    kept_excluded: dict[str, NoticeState] = {}
    for notice_id in plan.excluded_kept:
        previous = state.excluded.get(notice_id) if state else None
        if previous is not None:
            kept_excluded[notice_id] = previous
    for notice_id in dropped:
        stamp, revision_id = live.get(notice_id, (None, None))
        kept_excluded[notice_id] = NoticeState(iso_stamp(stamp), revision_id)

    new_state = ExportState(
        revision=rev,
        generated_at=iso_stamp(now) or "",
        fingerprint=fingerprint,
        mode=plan.mode,
        last_full_at=last_full,
        notices={
            item.notice.id: NoticeState(
                updated_at=iso_stamp(live.get(item.notice.id, (None, None))[0]),
                revision_id=live.get(item.notice.id, (None, None))[1],
                entry=json.loads(_dump(item.notice)),
            )
            for item in notices
        },
        excluded=kept_excluded,
    )
    result.state_bytes = save_state(store, bucket, new_state)
    return writer, new_state


STATE_KEEP = 3


def prune_export_state(store: ObjectStore, bucket: str, *, keep: int = STATE_KEEP) -> int:
    """이전 판 기록을 최근 몇 개만 남긴다.

    회차마다 4 MB 남짓을 올리므로 그냥 두면 R2 무료 용량을 야금야금 먹는다.
    실제로 읽는 것은 latest.json 이 가리키는 개정의 것 하나뿐이지만, 회차가 중간에
    실패해 포인터가 뒤에 남는 경우가 있어 몇 개는 남긴다. 개정 파일 정리
    (prune_old_revisions)와 별개로 돈다. 실패해도 공개를 실패시키지 않는다.
    """
    try:
        keys = [
            key for key in store.list_keys(bucket, f"{STATE_PREFIX}/")
            if key != METRICS_KEY and key.endswith(".json")
        ]
        if len(keys) <= keep:
            return 0
        # 개정 이름은 r<타임스탬프>-<실행 id> 라 이름순이 곧 시간순이다.
        removed = 0
        for key in sorted(keys)[:-keep]:
            store.delete(bucket, key)
            removed += 1
        return removed
    except Exception:  # noqa: BLE001 - 정리 실패가 공개를 막지 않는다
        log.debug("이전 판 기록을 정리하지 못했습니다", exc_info=True)
        return 0


def export_static(
    cfg: Settings | None = None,
    *,
    run_id: str | None = None,
    store: ObjectStore | None = None,
    revision: str | None = None,
    mode: str | None = None,
) -> ExportResult:
    """공개 파일을 만든다.

    ``mode`` 를 주지 않으면 설정(KHU_EXPORT_MODE)을 따른다. 증분을 요청해도
    이전 판이 없거나 바뀐 공지가 너무 많으면 그 회차는 전체로 돌고, 그 이유가
    결과(mode_reason)와 회차 기록에 남는다.
    """
    cfg = cfg or default_settings
    store = store or build_store(cfg)
    requested = (mode or cfg.export_mode or "full").strip().lower()
    if requested not in MODES:
        log.warning("알 수 없는 공개 방식 %r 입니다. full 로 돕니다.", requested)
        requested = "full"
    bucket = cfg.r2.bucket_public
    now = utcnow()
    rev = revision or f"r{int(now.timestamp())}-{(run_id or 'manual').split('-')[-1][:8]}"
    result = ExportResult(revision=rev, requested_mode=requested)

    with measure_reads() as measured, session_scope(cfg) as session:
        org_paths = _org_paths(session)
        source_refs = _source_refs(session)
        campuses = [
            api.Campus(id=c.id, name=c.name)
            for c in session.execute(select(m.Campus).order_by(m.Campus.name)).scalars()
        ]
        organization_names = dict(
            session.execute(select(m.Organization.id, m.Organization.name)).all()
        )
        health_rows = {h.source_id: h for h in session.execute(select(m.SourceHealth)).scalars()}
        fingerprint = shared_fingerprint(
            _fingerprint_parts(
                cfg, source_refs=source_refs, organizations=organization_names, campuses=campuses
            )
        )

        # 가벼운 전체 훑기. 어느 방식이든 여기까지는 똑같이 한다.
        scan = _light_scan(session)
        live = {notice_id: (stamp, revision_id) for notice_id, stamp, revision_id in scan}

        previous_revision = latest_revision(store, bucket)
        state = load_state(store, bucket, previous_revision) if previous_revision else None
        manifest = load_manifest(store, bucket, previous_revision) if previous_revision else None
        manifest_entries = manifest.get("entries") if isinstance(manifest, dict) else None
        # 물려받을 객체가 실제로 남아 있는지도 본다. 개정 정리가 지웠거나 올리다 만
        # 파일을 가리키면 조회가 통째로 깨진다.
        existing = set(store.list_keys(bucket, f"{BASE}/objects/"))

        def make_plan(requested_mode: str, *, skip_daily_full: bool = False) -> ExportPlan:
            return plan_export(
                requested_mode=requested_mode,
                skip_daily_full=skip_daily_full,
                live=scan,
                state=state,
                manifest_entries=manifest_entries,
                existing_objects=existing,
                fingerprint=fingerprint,
                now=now,
                kst=KST,
                max_changed_ratio=cfg.export_max_changed_ratio,
                full_every_hours=cfg.export_full_every_hours,
                full_hour_kst=cfg.export_full_hour_kst,
            )

        plan = make_plan("full" if requested == "shadow" else requested)
        result.mode = plan.mode
        result.mode_reason = plan.reason

        shared = dict(
            now=now, live=live, org_paths=org_paths, source_refs=source_refs,
            campuses=campuses, health_rows=health_rows, fingerprint=fingerprint,
        )
        writer, _ = _write_revision(
            session, cfg, result, rev=rev, plan=plan, state=state,
            manifest_entries=manifest_entries or {}, store=store, existing=existing, **shared,
        )

        # 그림자 대조. 공개는 위에서 끝났고, 아래는 "증분이었다면 무엇이 나왔을까"만 센다.
        # 하루 한 번 전체 재생성으로 되돌아온 회차에서도 돌려, 그 회차가 곧 "증분·전체
        # 일치" 점검이 되게 한다. 이때 드는 추가 데이터베이스 읽기는 가벼운 훑기뿐이다.
        want_shadow = requested == "shadow" or (requested == "incremental" and plan.mode == "full")
        if want_shadow:
            shadow_plan = make_plan("incremental", skip_daily_full=True)
            if shadow_plan.mode != "incremental":
                result.shadow = {
                    "ran": False, "reason": shadow_plan.reason, "matched": None,
                    **shadow_plan.summary(),
                }
            else:
                mirror = ExportResult(revision=f"{rev}-shadow", requested_mode="incremental")
                mirror.mode = "incremental"
                shadow_writer, _ = _write_revision(
                    session, cfg, mirror, rev=f"{rev}-shadow", plan=shadow_plan, state=state,
                    manifest_entries=manifest_entries or {}, store=_ReadOnlyStore(store),
                    existing=set(existing), **shared,
                )
                result.shadow = {
                    "ran": True, "reason": shadow_plan.reason,
                    **shadow_plan.summary(),
                    **_compare_entries(writer.entries, shadow_writer.entries),
                }

    stats = measured[0]
    result.db_read_bytes = stats.bytes
    result.db_read_rows = stats.rows
    result.db_read_statements = stats.statements
    log.info(
        "정적 파일 %d개 생성(%.1fKB), 물려받은 상세 %d개, 개정 %s, 방식 %s(%s), "
        "데이터베이스 읽기 %.1fMB",
        result.files_written, result.bytes_written / 1024, result.details_carried, rev,
        result.mode, result.mode_reason, result.db_read_bytes / 1024 / 1024,
    )
    write_export_metrics(cfg, result, store=store)
    prune_export_state(store, bucket)
    return result


METRICS_PATH = Path(".localstore/export-metrics.json")
METRICS_KEY = f"{STATE_PREFIX}/export-metrics.json"


def export_metrics(result: ExportResult) -> dict[str, Any]:
    """자체 점검과 실행 요약이 함께 보는 회차 기록."""
    return {
        "generated_at": iso_stamp(utcnow()),
        "revision": result.revision,
        "requested_mode": result.requested_mode,
        "mode": result.mode,
        "mode_reason": result.mode_reason,
        "notices": result.notices,
        "notices_changed": result.notices_changed,
        "notices_reused": result.notices_reused,
        "details_carried": result.details_carried,
        "files_written": result.files_written,
        "files_reused": result.files_reused,
        "bytes_written": result.bytes_written,
        "state_bytes": result.state_bytes,
        "db_read_bytes": result.db_read_bytes,
        "db_read_rows": result.db_read_rows,
        "db_read_statements": result.db_read_statements,
        "shadow": result.shadow,
    }


def write_export_metrics(
    cfg: Settings, result: ExportResult, *, store: ObjectStore | None = None
) -> None:
    """회차 기록을 남긴다. 실패해도 공개를 실패시키지 않는다."""
    payload = json.dumps(export_metrics(result), ensure_ascii=False, indent=2).encode("utf-8")
    try:
        METRICS_PATH.parent.mkdir(parents=True, exist_ok=True)
        METRICS_PATH.write_bytes(payload)
    except OSError:  # pragma: no cover - 쓰기 권한이 없는 곳에서도 공개는 계속한다
        log.debug("공개 회차 기록을 로컬에 쓰지 못했습니다", exc_info=True)
    try:
        (store or build_store(cfg)).put_bytes(
            cfg.r2.bucket_public, METRICS_KEY, payload,
            content_type="application/json; charset=utf-8", compress=True,
        )
    except Exception:  # noqa: BLE001 - 기록 실패가 공개를 막지 않는다
        log.debug("공개 회차 기록을 올리지 못했습니다", exc_info=True)


def read_export_metrics(
    cfg: Settings | None = None, *, store: ObjectStore | None = None
) -> dict[str, Any] | None:
    """자체 점검이 읽는다. 같은 작업에서 만든 로컬 파일을 먼저 본다."""
    cfg = cfg or default_settings
    try:
        return json.loads(METRICS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    try:
        return json.loads((store or build_store(cfg)).get_bytes(cfg.r2.bucket_public, METRICS_KEY))
    except Exception:  # noqa: BLE001
        return None


def refresh_public_status(
    cfg: Settings | None = None,
    *,
    revision: str,
    store: ObjectStore | None = None,
) -> None:
    """수집 실행을 마친 뒤 공개 상태 파일만 다시 쓴다.

    전체 개정 파일은 이미 포인터 전환 전에 생성되므로, 실행 종료 시각을
    반영하기 위해 상태 파일만 같은 개정으로 갱신한다. 이전에는 내보내기가
    실행 종료보다 먼저 만들어져 ``last_run_result``가 한 회차 늦었다.
    """
    cfg = cfg or default_settings
    store = store or build_store(cfg)
    now = utcnow()
    with session_scope(cfg) as session:
        health_rows = {h.source_id: h for h in session.execute(select(m.SourceHealth)).scalars()}
        last_run = session.execute(
            select(m.Run).where(m.Run.kind == "collect").order_by(m.Run.started_at.desc()).limit(1)
        ).scalar_one_or_none()
        sources = list(
            session.execute(
                select(m.Source).where(m.Source.is_public.is_(True)).order_by(m.Source.name)
            ).scalars()
        )
        lines = [
            api.SourceStatusLine(
                id=source.id,
                name=source.name,
                status=api.Coded(**coded(SOURCE_STATUS_LABELS, source.status)),
                last_success_at=(health_rows[source.id].last_list_success_at if source.id in health_rows else None),
                consecutive_failures=(health_rows[source.id].consecutive_failures if source.id in health_rows else 0),
                backfill_status=(health_rows[source.id].backfill_status if source.id in health_rows else "not_started"),
                backfill_complete=bool(health_rows[source.id].backfill_complete) if source.id in health_rows else False,
                last_scan_stop_reason=(health_rows[source.id].last_scan_stop_reason if source.id in health_rows else None),
            )
            for source in sources
        ]
        # 공개 총계는 실제로 내보낸 공지와 같은 기준이어야 한다(_load_notices 와 동일).
        floor = window_floor(cfg.initial_window_start)
        notices_query = (
            select(func.count(m.Notice.id))
            .join(m.SourceItem, m.SourceItem.id == m.Notice.primary_source_item_id)
            .join(m.Source, m.Source.id == m.SourceItem.source_id)
            .where(m.Notice.status == "visible", m.Source.is_public.is_(True))
        )
        window_start = cfg.initial_window_start
        # 시각을 아는 글은 published_at 으로, 날짜만 아는 글은 published_date 로 판정한다.
        # 정말로 발행일을 모르는 글만 총계에서 뺀다(_load_notices 의 within_window 와 같은 규칙).
        # 위쪽 경계는 두지 않는다. 원문 날짜가 미래인 글도 공개하기 때문이다(사용자 결정
        # 2026-09-07). 그런 글의 보이는 날짜는 처음 본 시각이라 언제나 범위 안이다.
        by_date = and_(
            m.Notice.published_at.is_(None),
            m.Notice.published_date.is_not(None),
        )
        if floor is None:
            notices_query = notices_query.where(
                or_(m.Notice.published_at.is_not(None), by_date)
            )
        else:
            notices_query = notices_query.where(
                or_(
                    m.Notice.published_at >= floor,
                    and_(by_date, m.Notice.published_date >= window_start),
                )
            )
        notices_total = int(session.execute(notices_query).scalar() or 0)
        contacts_total = int(
            session.execute(
                select(func.count(m.ContactEntry.id)).where(
                    m.ContactEntry.status.in_(("verified", "stale", "conflict"))
                )
            ).scalar()
            or 0
        )
        status = api.RunStatus(
            generated_at=now,
            revision=revision,
            last_run_started_at=last_run.started_at if last_run else None,
            last_run_finished_at=last_run.finished_at if last_run else None,
            last_run_result=last_run.result if last_run else None,
            code_commit=last_run.code_commit if last_run else None,
            contract_version=CONTRACT_VERSION,
            sources_total=len(lines),
            sources_active=sum(1 for line in lines if line.status.code == "active"),
            sources_failing=sum(1 for line in lines if line.consecutive_failures > 0),
            notices_total=notices_total,
            contacts_total=contacts_total,
            initial_window_start=cfg.initial_window_start,
            sources_backfill_complete=sum(1 for line in lines if line.backfill_complete),
            sources_backfill_incomplete=sum(1 for line in lines if not line.backfill_complete),
            sources=lines,
        )
        Writer(store, cfg.r2.bucket_public, ExportResult(revision=revision)).put(f"{BASE}/status.json", status)


def prune_old_revisions(cfg: Settings | None = None, *, keep: int = 3, store: ObjectStore | None = None) -> int:
    """오래된 개정을 정리한다. 최신 몇 개는 이어보기 때문에 남긴다(12절)."""
    cfg = cfg or default_settings
    if keep < 1:
        raise ValueError("최소 한 개정은 보존해야 합니다")
    store = store or build_store(cfg)
    keys = store.list_keys(cfg.r2.bucket_public, f"{BASE}/r/")
    revisions = sorted({k.split("/")[2] for k in keys if k.count("/") >= 3})
    doomed = revisions[:-keep] if len(revisions) > keep else []
    latest = json.loads(store.get_bytes(cfg.r2.bucket_public, f"{BASE}/latest.json"))
    doomed = [revision for revision in doomed if revision != latest["revision"]]
    removed = 0
    for revision in doomed:
        for key in [k for k in keys if k.startswith(f"{BASE}/r/{revision}/")]:
            store.delete(cfg.r2.bucket_public, key)
            removed += 1
        # 개정과 함께 만든 이전 판 기록도 지운다. 개정이 사라지면 그 기록으로
        # 물려받을 것도 없다.
        store.delete(cfg.r2.bucket_public, state_key(revision))
    # 내용 객체는 다른 실행이 새 개정에서 재사용 중일 수 있다. 전역 쓰기 잠금 없는
    # 정리에서는 삭제하지 않는다. 개정 명세가 사라지면 공개 조회 경로도 사라진다.
    return removed
