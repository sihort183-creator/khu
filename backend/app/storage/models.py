"""데이터베이스 스키마. 계획 6절의 표를 그대로 옮긴 단일 정본이다.

Alembic 초기 리비전이 이 MetaData 로 테이블을 만든다. 이후 변경은 새 리비전으로 쌓는다.

원칙(4절 불변 조건):
- 같은 출처의 같은 원본 식별자는 언제나 원본 게시물 1개다(source_items 유일키).
- 수정은 새 이력이다(source_item_revisions). 원문 내용과 운영 보정은 서로 다른 테이블이다.
- 하나의 원본은 동시에 하나의 유효한 공지 묶음에만 속한다(notice_sources 부분 유일 인덱스).
- 원문 주소와 확인 시각 없이 공개되는 공지·연락처는 없다(NOT NULL).
"""

from __future__ import annotations

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, mapped_column

# 제약 이름을 고정해야 Alembic 이 나중에 안전하게 변경할 수 있다.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_N_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s",
    "pk": "pk_%(table_name)s",
}

metadata = MetaData(naming_convention=NAMING_CONVENTION)


class Base(DeclarativeBase):
    metadata = metadata


def _pk():
    return mapped_column(String(64), primary_key=True)


def _now():
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


# --------------------------------------------------------------- 6.1 조직·출처


class University(Base):
    __tablename__ = "universities"
    id = _pk()
    code = mapped_column(String(32), nullable=False, unique=True)
    name = mapped_column(String(200), nullable=False)
    created_at = _now()


class Campus(Base):
    __tablename__ = "campuses"
    id = _pk()
    university_id = mapped_column(String(64), ForeignKey("universities.id"), nullable=False)
    code = mapped_column(String(32), nullable=False)
    name = mapped_column(String(200), nullable=False)
    created_at = _now()
    __table_args__ = (UniqueConstraint("university_id", "code"),)


class Organization(Base):
    __tablename__ = "organizations"
    id = _pk()
    university_id = mapped_column(String(64), ForeignKey("universities.id"), nullable=False)
    parent_id = mapped_column(String(64), ForeignKey("organizations.id"), nullable=True)
    org_type = mapped_column(String(32), nullable=False)
    name = mapped_column(String(300), nullable=False)
    short_name = mapped_column(String(120), nullable=True)
    aliases = mapped_column(JSON, nullable=False, default=list)
    homepage_url = mapped_column(Text, nullable=True)
    is_active = mapped_column(Boolean, nullable=False, default=True)
    # 같은 조직이 두 번 등록돼 하나로 합친 쪽. 행은 지우지 않고(공지·출처가 달려 있다)
    # 화면 트리에서만 감춘다. 공지 합산에는 그대로 쓰인다.
    is_alias = mapped_column(Boolean, nullable=False, default=False)
    # 등록부 열쇠. 조직 식별자는 이름 경로의 해시라 상위가 바뀌면 값이 달라진다.
    # 이 열이 등록부 열쇠와 행을 묶어 두므로 경로가 바뀌어도 같은 행을 계속 찾는다.
    registry_key = mapped_column(String(64), nullable=True)
    created_at = _now()
    __table_args__ = (
        CheckConstraint("id <> parent_id", name="no_self_parent"),
        Index("ix_organizations_parent", "parent_id"),
        Index("ix_organizations_type", "org_type"),
        Index("uq_organizations_registry_key", "registry_key", unique=True),
    )


class OrganizationCampus(Base):
    """공통 기관이 여러 캠퍼스에 걸치는 경우(6.1절)."""

    __tablename__ = "organization_campuses"
    organization_id = mapped_column(String(64), ForeignKey("organizations.id"), primary_key=True)
    campus_id = mapped_column(String(64), ForeignKey("campuses.id"), primary_key=True)


class OrganizationClosure(Base):
    """조상·자손 파생 관계. 조직 계층에서 재생성한다(내 공지 상속 조회용)."""

    __tablename__ = "organization_closure"
    ancestor_id = mapped_column(String(64), ForeignKey("organizations.id"), primary_key=True)
    descendant_id = mapped_column(String(64), ForeignKey("organizations.id"), primary_key=True)
    depth = mapped_column(Integer, nullable=False)


class Source(Base):
    __tablename__ = "sources"
    id = _pk()
    organization_id = mapped_column(String(64), ForeignKey("organizations.id"), nullable=False)
    name = mapped_column(String(300), nullable=False)
    medium = mapped_column(String(32), nullable=False, default="web")
    content_kind = mapped_column(String(32), nullable=False, default="notice")
    adapter = mapped_column(String(64), nullable=False)
    list_url = mapped_column(Text, nullable=False)
    official_evidence_url = mapped_column(Text, nullable=True)
    status = mapped_column(String(32), nullable=False, default="pending")
    status_message = mapped_column(Text, nullable=True)
    is_public = mapped_column(Boolean, nullable=False, default=True)
    created_at = _now()
    __table_args__ = (
        Index("ix_sources_org", "organization_id"),
        Index("ix_sources_status", "status"),
        UniqueConstraint("adapter", "list_url", name="uq_sources_adapter_list_url"),
    )


class SourceConfigVersion(Base):
    """출처 설정 이력. 활성 설정은 1개이고 이전 버전은 바꾸지 않는다."""

    __tablename__ = "source_config_versions"
    id = _pk()
    source_id = mapped_column(String(64), ForeignKey("sources.id"), nullable=False)
    version = mapped_column(Integer, nullable=False)
    config = mapped_column(JSON, nullable=False)
    interval_minutes = mapped_column(Integer, nullable=False, default=60)
    is_active = mapped_column(Boolean, nullable=False, default=True)
    applied_at = _now()
    __table_args__ = (
        UniqueConstraint("source_id", "version"),
        Index("ix_source_config_active", "source_id", "is_active"),
    )


class SourceAudience(Base):
    """출처의 기본 대상 범위. 출처의 소속 조직과는 별개다(4절 4항)."""

    __tablename__ = "source_audiences"
    id = _pk()
    source_id = mapped_column(String(64), ForeignKey("sources.id"), nullable=False)
    audience_type = mapped_column(String(32), nullable=False)
    campus_id = mapped_column(String(64), ForeignKey("campuses.id"), nullable=True)
    organization_id = mapped_column(String(64), ForeignKey("organizations.id"), nullable=True)
    __table_args__ = (
        UniqueConstraint("source_id", "audience_type", "campus_id", "organization_id"),
        CheckConstraint(
            "audience_type in ('university','campus','organization','undetermined')",
            name="audience_type_known",
        ),
    )


class SourceHealth(Base):
    """출처 운영 상태. 시도 시각과 성공 시각을 분리한다(4절 8항)."""

    __tablename__ = "source_health"
    source_id = mapped_column(String(64), ForeignKey("sources.id"), primary_key=True)
    last_attempt_at = mapped_column(DateTime(timezone=True), nullable=True)
    last_list_success_at = mapped_column(DateTime(timezone=True), nullable=True)
    last_detail_complete_at = mapped_column(DateTime(timezone=True), nullable=True)
    next_attempt_after = mapped_column(DateTime(timezone=True), nullable=True)
    consecutive_failures = mapped_column(Integer, nullable=False, default=0)
    last_error_kind = mapped_column(String(64), nullable=True)
    last_error_message = mapped_column(Text, nullable=True)
    backfill_complete = mapped_column(Boolean, nullable=False, default=False)
    backfill_boundary_reached = mapped_column(Boolean, nullable=False, default=False)
    # 초기 범위 수집은 실행 성공 여부와 별도로 진행 상태를 보존한다.
    # 2026-03-01은 기본 정책이며, 정책이 바뀌면 range_policy_version으로 재평가한다.
    initial_window_start = mapped_column(Date, nullable=True)
    range_policy_version = mapped_column(String(32), nullable=False, default="2026-03-01-v1")
    backfill_status = mapped_column(String(32), nullable=False, default="not_started")
    backfill_cursor_page = mapped_column(Integer, nullable=False, default=1)
    backfill_cursor_external_id = mapped_column(String(200), nullable=True)
    backfill_oldest_date = mapped_column(Date, nullable=True)
    backfill_last_progress_at = mapped_column(DateTime(timezone=True), nullable=True)
    backfill_last_stop_reason = mapped_column(String(64), nullable=True)
    last_scan_complete = mapped_column(Boolean, nullable=False, default=False)
    last_scan_stop_reason = mapped_column(String(64), nullable=True)
    blocked_abroad = mapped_column(Boolean, nullable=False, default=False)
    __table_args__ = (Index("ix_source_health_next", "next_attempt_after"),)


# --------------------------------------------------------------- 6.2 원본·공지


class SourceItem(Base):
    """원본 게시물. 같은 출처의 같은 원본 식별자는 언제나 1행이다(4절 1항)."""

    __tablename__ = "source_items"
    id = _pk()
    source_id = mapped_column(String(64), ForeignKey("sources.id"), nullable=False)
    external_id = mapped_column(String(200), nullable=False)
    canonical_url = mapped_column(Text, nullable=False)
    first_seen_at = _now()
    last_seen_at = mapped_column(DateTime(timezone=True), nullable=True)
    current_revision_id = mapped_column(String(64), nullable=True)
    original_status = mapped_column(String(32), nullable=False, default="available")
    missing_streak = mapped_column(Integer, nullable=False, default=0)
    is_pinned = mapped_column(Boolean, nullable=False, default=False)
    last_detail_checked_at = mapped_column(DateTime(timezone=True), nullable=True)
    detail_attempts = mapped_column(Integer, nullable=False, default=0)
    next_detail_attempt_after = mapped_column(DateTime(timezone=True), nullable=True)
    last_detail_error = mapped_column(Text, nullable=True)
    detail_listing = mapped_column(JSON, nullable=True)
    __table_args__ = (
        UniqueConstraint("source_id", "external_id", name="uq_source_items_source_external"),
        Index("ix_source_items_source_seen", "source_id", "last_seen_at"),
    )


class SourceItemAlias(Base):
    __tablename__ = "source_item_aliases"
    id = _pk()
    source_item_id = mapped_column(String(64), ForeignKey("source_items.id"), nullable=False)
    url = mapped_column(Text, nullable=False)
    __table_args__ = (UniqueConstraint("url", name="uq_source_item_aliases_url"),)


class SourceItemRevision(Base):
    """원본 변경 이력. 같은 내용의 재요청은 새 이력을 만들지 않는다(6.2절)."""

    __tablename__ = "source_item_revisions"
    id = _pk()
    source_item_id = mapped_column(String(64), ForeignKey("source_items.id"), nullable=False)
    content_hash = mapped_column(String(64), nullable=False)
    title = mapped_column(Text, nullable=False)
    body_text = mapped_column(Text, nullable=True)
    body_html = mapped_column(Text, nullable=True)
    raw_object_key = mapped_column(Text, nullable=True)
    author = mapped_column(String(200), nullable=True)
    board_category = mapped_column(String(200), nullable=True)
    published_raw = mapped_column(String(200), nullable=True)
    published_date = mapped_column(Date, nullable=True)
    published_at = mapped_column(DateTime(timezone=True), nullable=True)
    published_precision = mapped_column(String(16), nullable=False, default="unknown")
    updated_raw = mapped_column(String(200), nullable=True)
    extractor_version = mapped_column(String(32), nullable=False)
    observed_at = _now()
    __table_args__ = (
        Index("ix_revisions_item_observed", "source_item_id", "observed_at"),
        CheckConstraint(
            "published_precision in ('date','datetime','unknown')", name="published_precision_known"
        ),
    )


class Attachment(Base):
    __tablename__ = "attachments"
    id = _pk()
    revision_id = mapped_column(String(64), ForeignKey("source_item_revisions.id"), nullable=False)
    filename = mapped_column(Text, nullable=False)
    url = mapped_column(Text, nullable=True)
    kind = mapped_column(String(64), nullable=True)
    size_bytes = mapped_column(BigInteger, nullable=True)
    content_hash = mapped_column(String(64), nullable=True)
    status = mapped_column(String(32), nullable=False, default="listed")
    __table_args__ = (Index("ix_attachments_revision", "revision_id"),)


class Notice(Base):
    """사용자에게 보이는 공지 묶음."""

    __tablename__ = "notices"
    id = _pk()
    primary_source_item_id = mapped_column(String(64), ForeignKey("source_items.id"), nullable=False)
    status = mapped_column(String(32), nullable=False, default="visible")
    first_visible_at = _now()
    updated_at = _now()
    title = mapped_column(Text, nullable=False)
    excerpt = mapped_column(Text, nullable=True)
    published_date = mapped_column(Date, nullable=True)
    published_at = mapped_column(DateTime(timezone=True), nullable=True)
    published_precision = mapped_column(String(16), nullable=False, default="unknown")
    deadline_date = mapped_column(Date, nullable=True)
    deadline_at = mapped_column(DateTime(timezone=True), nullable=True)
    deadline_precision = mapped_column(String(16), nullable=True)
    deadline_evidence = mapped_column(Text, nullable=True)
    audience_note = mapped_column(Text, nullable=True)
    search_text = mapped_column(Text, nullable=True)
    derived_version = mapped_column(String(32), nullable=True)
    __table_args__ = (
        Index("ix_notices_visible", "status", "first_visible_at", "id"),
        Index("ix_notices_published", "published_date"),
        CheckConstraint("status in ('visible','hidden','removed')", name="notice_status_known"),
    )


class NoticeSource(Base):
    """공지 묶음과 원본의 연결. 원본별 활성 연결은 최대 1개다(4절 6항)."""

    __tablename__ = "notice_sources"
    id = _pk()
    notice_id = mapped_column(String(64), ForeignKey("notices.id"), nullable=False)
    source_item_id = mapped_column(String(64), ForeignKey("source_items.id"), nullable=False)
    is_active = mapped_column(Boolean, nullable=False, default=True)
    is_primary = mapped_column(Boolean, nullable=False, default=False)
    linked_at = _now()
    unlinked_at = mapped_column(DateTime(timezone=True), nullable=True)
    reason = mapped_column(Text, nullable=True)
    __table_args__ = (
        Index(
            "uq_notice_sources_active_item",
            "source_item_id",
            unique=True,
            postgresql_where=(is_active.is_(True)),
            sqlite_where=(is_active.is_(True)),
        ),
        Index("ix_notice_sources_notice", "notice_id"),
    )


class NoticeAudience(Base):
    __tablename__ = "notice_audiences"
    id = _pk()
    notice_id = mapped_column(String(64), ForeignKey("notices.id"), nullable=False)
    audience_type = mapped_column(String(32), nullable=False)
    campus_id = mapped_column(String(64), ForeignKey("campuses.id"), nullable=True)
    organization_id = mapped_column(String(64), ForeignKey("organizations.id"), nullable=True)
    evidence_text = mapped_column(Text, nullable=True)
    rule_version = mapped_column(String(32), nullable=True)
    __table_args__ = (
        UniqueConstraint("notice_id", "audience_type", "campus_id", "organization_id"),
        Index("ix_notice_audiences_notice", "notice_id"),
    )


class NoticeCategory(Base):
    __tablename__ = "notice_categories"
    id = _pk()
    notice_id = mapped_column(String(64), ForeignKey("notices.id"), nullable=False)
    category_code = mapped_column(String(32), nullable=False)
    is_primary = mapped_column(Boolean, nullable=False, default=False)
    rule_name = mapped_column(String(64), nullable=True)
    rule_version = mapped_column(String(32), nullable=True)
    evidence_text = mapped_column(Text, nullable=True)
    __table_args__ = (
        UniqueConstraint("notice_id", "category_code"),
        Index(
            "uq_notice_categories_primary",
            "notice_id",
            unique=True,
            postgresql_where=(is_primary.is_(True)),
            sqlite_where=(is_primary.is_(True)),
        ),
    )


class DedupeDecision(Base):
    __tablename__ = "dedupe_decisions"
    id = _pk()
    left_item_id = mapped_column(String(64), ForeignKey("source_items.id"), nullable=False)
    right_item_id = mapped_column(String(64), ForeignKey("source_items.id"), nullable=False)
    left_revision_id = mapped_column(String(64), nullable=False)
    right_revision_id = mapped_column(String(64), nullable=False)
    decision = mapped_column(String(32), nullable=False)
    score = mapped_column(Numeric(5, 4), nullable=True)
    signals = mapped_column(JSON, nullable=False, default=dict)
    rule_version = mapped_column(String(32), nullable=False)
    decided_by = mapped_column(String(64), nullable=False, default="auto")
    decided_at = _now()
    __table_args__ = (
        Index("ix_dedupe_pair", "left_item_id", "right_item_id"),
        CheckConstraint(
            "decision in ('merge','distinct','review')", name="dedupe_decision_known"
        ),
    )


class NoticeRedirect(Base):
    """병합 전 공지 주소를 현재 공지로 연결한다. 저장한 공지가 끊기지 않게 한다."""

    __tablename__ = "notice_redirects"
    from_notice_id = mapped_column(String(64), primary_key=True)
    to_notice_id = mapped_column(String(64), ForeignKey("notices.id"), nullable=False)
    created_at = _now()
    __table_args__ = (CheckConstraint("from_notice_id <> to_notice_id", name="no_self_redirect"),)


class ContentOverride(Base):
    """운영 보정. 원문 이력을 고치지 않고 표시층에서만 우선한다(6.2절)."""

    __tablename__ = "content_overrides"
    id = _pk()
    target_kind = mapped_column(String(32), nullable=False)
    target_id = mapped_column(String(64), nullable=False)
    field = mapped_column(String(64), nullable=False)
    value = mapped_column(JSON, nullable=True)
    reason = mapped_column(Text, nullable=False)
    author = mapped_column(String(120), nullable=False)
    effective_from = _now()
    effective_to = mapped_column(DateTime(timezone=True), nullable=True)
    __table_args__ = (
        Index("ix_overrides_target", "target_kind", "target_id", "field"),
        CheckConstraint("target_kind in ('notice','contact','source')", name="override_target_known"),
    )


# --------------------------------------------------------------- 6.3 연락처


class ContactEntry(Base):
    """업무 단위 연락처. 번호가 바뀌어도 이 식별자는 유지된다."""

    __tablename__ = "contact_entries"
    id = _pk()
    organization_id = mapped_column(String(64), ForeignKey("organizations.id"), nullable=False)
    service_name = mapped_column(String(300), nullable=False)
    location = mapped_column(Text, nullable=True)
    office_hours = mapped_column(Text, nullable=True)
    official_url = mapped_column(Text, nullable=True)
    status = mapped_column(String(32), nullable=False, default="verified")
    verified_at = mapped_column(DateTime(timezone=True), nullable=True)
    note = mapped_column(Text, nullable=True)
    search_text = mapped_column(Text, nullable=True)
    __table_args__ = (
        Index("ix_contacts_org", "organization_id"),
        CheckConstraint(
            "status in ('verified','stale','conflict','hidden','retired')", name="contact_status_known"
        ),
    )


class ContactCampus(Base):
    __tablename__ = "contact_campuses"
    contact_id = mapped_column(String(64), ForeignKey("contact_entries.id"), primary_key=True)
    campus_id = mapped_column(String(64), ForeignKey("campuses.id"), primary_key=True)


class ContactChannel(Base):
    __tablename__ = "contact_channels"
    id = _pk()
    contact_id = mapped_column(String(64), ForeignKey("contact_entries.id"), nullable=False)
    channel_kind = mapped_column(String(32), nullable=False)
    display_value = mapped_column(String(300), nullable=False)
    value = mapped_column(String(300), nullable=True)
    extension = mapped_column(String(64), nullable=True)
    expanded_values = mapped_column(JSON, nullable=True)
    priority = mapped_column(Integer, nullable=False, default=0)
    __table_args__ = (
        Index("ix_contact_channels_contact", "contact_id"),
        CheckConstraint(
            "channel_kind in ('phone','email','fax','website')", name="channel_kind_known"
        ),
    )


class ContactObservation(Base):
    __tablename__ = "contact_observations"
    id = _pk()
    contact_id = mapped_column(String(64), ForeignKey("contact_entries.id"), nullable=False)
    source_id = mapped_column(String(64), ForeignKey("sources.id"), nullable=True)
    source_name = mapped_column(String(300), nullable=False)
    url = mapped_column(Text, nullable=False)
    raw_object_key = mapped_column(Text, nullable=True)
    observed_at = mapped_column(DateTime(timezone=True), nullable=False)
    extractor_version = mapped_column(String(32), nullable=True)
    __table_args__ = (Index("ix_contact_observations_contact", "contact_id"),)


class ContactFieldEvidence(Base):
    __tablename__ = "contact_field_evidence"
    id = _pk()
    contact_id = mapped_column(String(64), ForeignKey("contact_entries.id"), nullable=False)
    observation_id = mapped_column(String(64), ForeignKey("contact_observations.id"), nullable=False)
    field = mapped_column(String(120), nullable=False)
    adopted = mapped_column(Boolean, nullable=False, default=True)
    __table_args__ = (Index("ix_contact_evidence_contact", "contact_id", "field"),)


class NoticeContactMention(Base):
    """공지 본문의 문의처. 상시 연락처로 자동 승격하지 않는다(10절)."""

    __tablename__ = "notice_contact_mentions"
    id = _pk()
    revision_id = mapped_column(String(64), ForeignKey("source_item_revisions.id"), nullable=False)
    raw_text = mapped_column(Text, nullable=False)
    channel_kind = mapped_column(String(32), nullable=True)
    value = mapped_column(String(300), nullable=True)
    contact_id = mapped_column(String(64), ForeignKey("contact_entries.id"), nullable=True)
    __table_args__ = (Index("ix_mentions_revision", "revision_id"),)


# --------------------------------------------------------------- 6.4 사용자·운영


class Profile(Base):
    __tablename__ = "profiles"
    user_id = mapped_column(String(64), primary_key=True)
    campus_id = mapped_column(String(64), ForeignKey("campuses.id"), nullable=True)
    organization_id = mapped_column(String(64), ForeignKey("organizations.id"), nullable=True)
    version = mapped_column(Integer, nullable=False, default=1)
    updated_at = _now()


class Subscription(Base):
    __tablename__ = "subscriptions"
    user_id = mapped_column(String(64), primary_key=True)
    source_id = mapped_column(String(64), ForeignKey("sources.id"), primary_key=True)
    created_at = _now()


class Bookmark(Base):
    __tablename__ = "bookmarks"
    user_id = mapped_column(String(64), primary_key=True)
    notice_id = mapped_column(String(64), ForeignKey("notices.id"), primary_key=True)
    source_item_id = mapped_column(String(64), nullable=True)
    created_at = _now()


class Run(Base):
    """실행 기록. 대기열을 두지 않는 대신 이 표로 겹침·비정상 종료를 판정한다(7.4절)."""

    __tablename__ = "runs"
    id = _pk()
    kind = mapped_column(String(32), nullable=False, default="collect")
    started_at = _now()
    finished_at = mapped_column(DateTime(timezone=True), nullable=True)
    result = mapped_column(String(32), nullable=True)
    code_commit = mapped_column(String(64), nullable=True)
    revision = mapped_column(String(64), nullable=True)
    sources_attempted = mapped_column(Integer, nullable=False, default=0)
    sources_succeeded = mapped_column(Integer, nullable=False, default=0)
    sources_skipped = mapped_column(Integer, nullable=False, default=0)
    items_new = mapped_column(Integer, nullable=False, default=0)
    items_updated = mapped_column(Integer, nullable=False, default=0)
    note = mapped_column(Text, nullable=True)
    __table_args__ = (
        Index("ix_runs_started", "started_at"),
        CheckConstraint(
            "result is null or result in ('success','partial','failed','abandoned')",
            name="run_result_known",
        ),
    )


class SourceRun(Base):
    __tablename__ = "source_runs"
    id = _pk()
    run_id = mapped_column(String(64), ForeignKey("runs.id"), nullable=False)
    source_id = mapped_column(String(64), ForeignKey("sources.id"), nullable=False)
    attempted_at = _now()
    succeeded = mapped_column(Boolean, nullable=False, default=False)
    error_kind = mapped_column(String(64), nullable=True)
    error_message = mapped_column(Text, nullable=True)
    list_items = mapped_column(Integer, nullable=False, default=0)
    new_items = mapped_column(Integer, nullable=False, default=0)
    updated_items = mapped_column(Integer, nullable=False, default=0)
    duration_ms = mapped_column(Integer, nullable=True)
    detail_failures = mapped_column(Integer, nullable=False, default=0)
    scan_stop_reason = mapped_column(String(64), nullable=True)
    backfill_complete = mapped_column(Boolean, nullable=False, default=False)
    missing_check_performed = mapped_column(Boolean, nullable=False, default=False)
    __table_args__ = (
        Index("ix_source_runs_run", "run_id"),
        Index("ix_source_runs_source", "source_id", "attempted_at"),
    )


class OutboxEvent(Base):
    """저장과 같은 트랜잭션에서 후속 처리를 예약한다(캐시 무효화·재생성)."""

    __tablename__ = "outbox_events"
    id = _pk()
    event_type = mapped_column(String(64), nullable=False)
    payload = mapped_column(JSON, nullable=False, default=dict)
    created_at = _now()
    processed_at = mapped_column(DateTime(timezone=True), nullable=True)
    __table_args__ = (Index("ix_outbox_unprocessed", "processed_at", "created_at"),)


class AdminMembership(Base):
    __tablename__ = "admin_memberships"
    user_id = mapped_column(String(64), primary_key=True)
    role = mapped_column(String(32), nullable=False)
    created_at = _now()
    __table_args__ = (
        CheckConstraint("role in ('viewer','source_admin','editor','owner')", name="admin_role_known"),
    )


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id = _pk()
    actor = mapped_column(String(120), nullable=False)
    action = mapped_column(String(120), nullable=False)
    target_kind = mapped_column(String(32), nullable=False)
    target_id = mapped_column(String(64), nullable=True)
    before = mapped_column(JSON, nullable=True)
    after = mapped_column(JSON, nullable=True)
    reason = mapped_column(Text, nullable=True)
    run_id = mapped_column(String(64), nullable=True)
    created_at = _now()
    __table_args__ = (Index("ix_audit_target", "target_kind", "target_id", "created_at"),)


class CorrectionReport(Base):
    """오류 신고. 불필요한 신고자 개인정보를 저장하지 않는다(6.4절)."""

    __tablename__ = "correction_reports"
    id = _pk()
    target_kind = mapped_column(String(32), nullable=False)
    target_id = mapped_column(String(64), nullable=False)
    message = mapped_column(Text, nullable=False)
    status = mapped_column(String(32), nullable=False, default="received")
    reporter_hash = mapped_column(String(64), nullable=True)
    created_at = _now()
    resolved_at = mapped_column(DateTime(timezone=True), nullable=True)
    __table_args__ = (
        Index("ix_reports_status", "status", "created_at"),
        CheckConstraint(
            "status in ('received','reviewing','resolved','rejected')", name="report_status_known"
        ),
    )


class SchemaState(Base):
    """수집 실행이 시작 시 확인하는 코드-스키마 호환 표시(17.1절 3항)."""

    __tablename__ = "schema_state"
    key = mapped_column(String(64), primary_key=True)
    value = mapped_column(String(200), nullable=False)
    updated_at = _now()


ALL_TABLES = tuple(metadata.tables)
