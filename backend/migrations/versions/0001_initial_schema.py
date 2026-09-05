"""초기 스키마.

계획 6절의 표 전체를 만든다. 테이블 정의의 정본은 app/storage/models.py 이고
이 리비전은 그 MetaData 로 생성한다. 이후 변경은 새 리비전에 손으로 적는다.

Revision ID: 0001
Revises:
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.storage.db import EXPECTED_SCHEMA_VERSION, SCHEMA_STATE_KEY
from app.storage.models import metadata

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

# 검색·중복 후보에 쓰는 확장. 유료 인공지능 호출 없이 시작한다(1절).
EXTENSIONS = ("pg_trgm",)

# 공개 조회용 뷰. 내부 테이블은 REST 에 노출하지 않고 이 뷰만 노출 스키마에 둔다(13.2절).
PUBLIC_NOTICE_VIEW = """
create or replace view public_notice_search as
select
    n.id,
    n.title,
    n.excerpt,
    n.search_text,
    n.published_date,
    n.first_visible_at,
    n.updated_at,
    si.canonical_url as original_url,
    s.id as source_id,
    s.name as source_name,
    o.id as organization_id,
    o.name as organization_name,
    (select nc.category_code from notice_categories nc
      where nc.notice_id = n.id and nc.is_primary limit 1) as primary_category
from notices n
join source_items si on si.id = n.primary_source_item_id
join sources s on s.id = si.source_id
join organizations o on o.id = s.organization_id
where n.status = 'visible' and s.is_public
"""

PUBLIC_CONTACT_VIEW = """
create or replace view public_contact_search as
select
    c.id,
    c.service_name,
    c.location,
    c.office_hours,
    c.official_url,
    c.status,
    c.verified_at,
    c.search_text,
    o.id as organization_id,
    o.name as organization_name
from contact_entries c
join organizations o on o.id = c.organization_id
where c.status in ('verified', 'stale', 'conflict')
"""


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    bind = op.get_bind()

    if _is_postgres():
        for ext in EXTENSIONS:
            op.execute(f'create extension if not exists "{ext}"')

    metadata.create_all(bind, checkfirst=True)

    if _is_postgres():
        # 한글 부분 일치 검색용 인덱스(8.3절).
        op.execute(
            "create index if not exists ix_notices_search_trgm "
            "on notices using gin (search_text gin_trgm_ops)"
        )
        op.execute(
            "create index if not exists ix_contacts_search_trgm "
            "on contact_entries using gin (search_text gin_trgm_ops)"
        )
        op.execute(PUBLIC_NOTICE_VIEW)
        op.execute(PUBLIC_CONTACT_VIEW)

    # 코드가 기대하는 스키마 표시를 남긴다.
    schema_state = sa.table(
        "schema_state",
        sa.column("key", sa.String),
        sa.column("value", sa.String),
    )
    op.execute(
        schema_state.delete().where(schema_state.c.key == op.inline_literal(SCHEMA_STATE_KEY))
    )
    op.bulk_insert(schema_state, [{"key": SCHEMA_STATE_KEY, "value": EXPECTED_SCHEMA_VERSION}])


def downgrade() -> None:
    bind = op.get_bind()
    if _is_postgres():
        op.execute("drop view if exists public_contact_search")
        op.execute("drop view if exists public_notice_search")
        op.execute("drop index if exists ix_contacts_search_trgm")
        op.execute("drop index if exists ix_notices_search_trgm")
    metadata.drop_all(bind, checkfirst=True)
