"""조직 별칭 표시와 등록부 열쇠를 추가한다.

같은 조직이 두 번 등록된 경우(전화번호 명부에서 온 조직과 게시판에서 온 조직)
행을 지우지 않고 한쪽을 별칭으로 표시해 화면 트리에서만 감춘다.

registry_key 는 등록부 열쇠와 조직 행을 묶는다. 조직 식별자는 이름 경로의 해시라
상위를 바꾸면 값이 달라지는데, 이 열이 있으면 경로가 바뀌어도 같은 행을 찾는다.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.storage.db import SCHEMA_STATE_KEY

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    existing = {item["name"] for item in inspector.get_columns("organizations")}
    if "is_alias" not in existing:
        op.add_column(
            "organizations",
            sa.Column("is_alias", sa.Boolean(), nullable=False, server_default=sa.false()),
        )
    if "registry_key" not in existing:
        op.add_column("organizations", sa.Column("registry_key", sa.String(length=64), nullable=True))
    # 유일 제약이 아니라 유일 색인으로 만든다. SQLite 는 제약을 떼어 내지 못해
    # 되돌리기(downgrade)가 막힌다.
    names = {item["name"] for item in inspector.get_indexes("organizations")}
    if "uq_organizations_registry_key" not in names:
        op.create_index(
            "uq_organizations_registry_key", "organizations", ["registry_key"], unique=True
        )

    state = sa.table("schema_state", sa.column("key", sa.String), sa.column("value", sa.String))
    op.execute(
        state.update()
        .where(state.c.key == op.inline_literal(SCHEMA_STATE_KEY))
        .values(value="0003")
    )


def downgrade() -> None:
    op.drop_index("uq_organizations_registry_key", table_name="organizations")
    op.drop_column("organizations", "registry_key")
    op.drop_column("organizations", "is_alias")
    state = sa.table("schema_state", sa.column("key", sa.String), sa.column("value", sa.String))
    op.execute(state.update().where(state.c.key == SCHEMA_STATE_KEY).values(value="0002"))
