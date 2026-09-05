"""응답 모델. KHU_API_CONTRACT_DRAFT.md 를 기계 검증 가능한 형태로 옮긴 것이다.

정적 JSON 생성기와 가상 자료 검사기가 모두 이 모델을 통과해야 한다(12절).
규칙: 알 수 없는 값은 None(=JSON null), 없는 목록은 빈 배열. 빈 문자열·0·오늘 날짜로 대체하지 않는다.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class Strict(BaseModel):
    """계약에 없는 필드가 새는 것을 막는다."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Coded(Strict):
    code: str
    label: str


class Meta(Strict):
    request_id: str
    generated_at: datetime


class Page(Strict):
    next_cursor: str | None = None
    has_next: bool = False
    snapshot_at: datetime
    dataset_revision: str


class ListResponse(Strict, Generic[T]):
    data: list[T]
    page: Page
    meta: Meta


class ItemResponse(Strict, Generic[T]):
    data: T
    meta: Meta


class ErrorBody(Strict):
    code: str
    message: str
    retryable: bool
    details: list[dict[str, str]] | None = None
    request_id: str


class ErrorResponse(Strict):
    error: ErrorBody


# ---------------------------------------------------------------- 조직·출처


class Campus(Strict):
    id: str
    name: str


class OrganizationRef(Strict):
    id: str
    name: str
    path: list[str] = Field(default_factory=list)
    type: Coded | None = None


class Organization(Strict):
    id: str
    name: str
    short_name: str | None = None
    type: Coded
    parent_id: str | None = None
    campuses: list[Campus] = Field(default_factory=list)
    path: list[str] = Field(default_factory=list)
    has_children: bool = False
    source_count: int = 0


class SourceRef(Strict):
    id: str
    name: str
    medium: Coded


class Source(Strict):
    id: str
    name: str
    organization: OrganizationRef
    medium: Coded
    content_kind: Coded
    url: str
    status: Coded
    status_message: str | None = None
    last_success_at: datetime | None = None
    history_from: date | None = None
    notice_count: int = 0


# ---------------------------------------------------------------- 공지


class Audience(Strict):
    type: Literal["university", "campus", "organization", "undetermined"]
    id: str | None = None
    name: str


class Freshness(Strict):
    code: str
    label: str
    last_checked_at: datetime | None = None


class Deadline(Strict):
    date: date
    at: datetime | None = None
    precision: Literal["date", "datetime"]
    evidence_text: str | None = None


class Notice(Strict):
    id: str
    kind: Literal["notice"] = "notice"
    title: str
    excerpt: str | None = None
    primary_category: Coded
    secondary_categories: list[Coded] = Field(default_factory=list)
    audiences: list[Audience] = Field(default_factory=list)
    audience_note: str | None = None
    primary_source: SourceRef
    source_count: int = 1
    published_date: date | None = None
    published_at: datetime | None = None
    published_precision: Literal["date", "datetime", "unknown"]
    first_visible_at: datetime
    updated_at: datetime
    deadline: Deadline | None = None
    original_url: str
    original_status: Coded
    freshness: Freshness
    is_pinned: bool = False


class NoticeSourceRef(Strict):
    """상세의 sources[] 항목. 어떤 원본이 이 공지에 묶여 있는지 보여준다."""

    source_item_id: str
    source: SourceRef
    url: str
    published_date: date | None = None
    original_status: Coded
    is_primary: bool = False


class Attachment(Strict):
    id: str
    filename: str
    kind: str | None = None
    size_bytes: int | None = None
    url: str | None = None
    status: Coded


class ContactChannel(Strict):
    id: str
    kind: Coded
    display_value: str
    value: str | None = None
    action_url: str | None = None
    extension: str | None = None
    values: list[str] | None = None


class ContactMention(Strict):
    raw_text: str
    channels: list[ContactChannel] = Field(default_factory=list)
    contact_id: str | None = None


class RelatedNotice(Strict):
    id: str
    title: str
    relation: Coded


class NoticeDetail(Notice):
    body_text: str | None = None
    body_html: str | None = None
    sources: list[NoticeSourceRef] = Field(default_factory=list)
    attachments: list[Attachment] = Field(default_factory=list)
    contact_mentions: list[ContactMention] = Field(default_factory=list)
    related_notices: list[RelatedNotice] = Field(default_factory=list)
    resolved_from_id: str | None = None


# ---------------------------------------------------------------- 연락처


class Evidence(Strict):
    field: str
    url: str
    source_name: str
    observed_at: datetime | None = None


class Verification(Strict):
    code: str
    label: str
    verified_at: datetime | None = None
    message: str | None = None


class Contact(Strict):
    id: str
    kind: Literal["contact"] = "contact"
    organization: OrganizationRef
    campuses: list[Campus] = Field(default_factory=list)
    service_name: str
    channels: list[ContactChannel] = Field(default_factory=list)
    location: str | None = None
    office_hours: str | None = None
    official_url: str | None = None
    verification: Verification
    evidence: list[Evidence] = Field(default_factory=list)
    note: str | None = None
    source: str | None = None


# ---------------------------------------------------------------- 분류 사전·상태


class CatalogFeatures(Strict):
    accounts: bool = False
    instagram: bool = False
    deadlines: bool = False


class Catalog(Strict):
    campuses: list[Campus] = Field(default_factory=list)
    organization_types: list[Coded] = Field(default_factory=list)
    categories: list[Coded] = Field(default_factory=list)
    media: list[Coded] = Field(default_factory=list)
    source_statuses: list[Coded] = Field(default_factory=list)
    features: CatalogFeatures = Field(default_factory=CatalogFeatures)
    contract_version: str = "v1"


class SourceStatusLine(Strict):
    id: str
    name: str
    status: Coded
    last_success_at: datetime | None = None
    consecutive_failures: int = 0


class RunStatus(Strict):
    """/v1/status.json. 18.2절의 공개 상태."""

    generated_at: datetime
    revision: str
    last_run_started_at: datetime | None = None
    last_run_finished_at: datetime | None = None
    last_run_result: str | None = None
    code_commit: str | None = None
    contract_version: str = "v1"
    sources_total: int = 0
    sources_active: int = 0
    sources_failing: int = 0
    notices_total: int = 0
    contacts_total: int = 0
    sources: list[SourceStatusLine] = Field(default_factory=list)


class LatestPointer(Strict):
    """/v1/latest.json. 프론트는 이 파일을 먼저 읽고 해당 개정 경로만 따라간다."""

    revision: str
    generated_at: datetime
    base_path: str
    contract_version: str = "v1"
    notice_pages: int = 0
    notices_total: int = 0
    contacts_total: int = 0


class NoticePageFile(Strict):
    """정적 목록 페이지 파일(12절)."""

    data: list[Notice]
    page: Page
    meta: Meta
    next: str | None = None


class IndexEntry(Strict):
    """기기 검색·맞춤 필터용 경량 색인 항목. 키를 줄여 파일 크기를 낮춘다."""

    id: str
    t: str
    c: str
    o: str | None = None
    s: str
    d: date | None = None
    v: datetime
    a: list[str] = Field(default_factory=list)


class NoticeIndexFile(Strict):
    revision: str
    generated_at: datetime
    count: int
    entries: list[IndexEntry] = Field(default_factory=list)
