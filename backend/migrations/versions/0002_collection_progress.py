"""초기 범위 이어받기와 상세 재시도 상태를 추가한다."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.storage.db import SCHEMA_STATE_KEY

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())

    def add(table: str, column: sa.Column) -> None:
        # 0001은 현재 모델 메타데이터로 create_all()을 수행할 수 있어,
        # 새 코드로 만든 fresh DB에는 이 열이 이미 있을 수 있다.
        if column.name not in {item["name"] for item in inspector.get_columns(table)}:
            op.add_column(table, column)

    health = "source_health"
    items = "source_items"
    runs = "source_runs"
    add(health, sa.Column("backfill_boundary_reached", sa.Boolean(), nullable=False, server_default=sa.false()))
    add(items, sa.Column("detail_listing", sa.JSON(), nullable=True))
    add(health, sa.Column("initial_window_start", sa.Date(), nullable=True))
    add(health, sa.Column("range_policy_version", sa.String(length=32), nullable=False, server_default="2026-03-01-v1"))
    add(health, sa.Column("backfill_status", sa.String(length=32), nullable=False, server_default="not_started"))
    add(health, sa.Column("backfill_cursor_page", sa.Integer(), nullable=False, server_default="1"))
    add(health, sa.Column("backfill_cursor_external_id", sa.String(length=200), nullable=True))
    add(health, sa.Column("backfill_oldest_date", sa.Date(), nullable=True))
    add(health, sa.Column("backfill_last_progress_at", sa.DateTime(timezone=True), nullable=True))
    add(health, sa.Column("backfill_last_stop_reason", sa.String(length=64), nullable=True))
    add(health, sa.Column("last_scan_complete", sa.Boolean(), nullable=False, server_default=sa.false()))
    add(health, sa.Column("last_scan_stop_reason", sa.String(length=64), nullable=True))

    add(items, sa.Column("last_detail_checked_at", sa.DateTime(timezone=True), nullable=True))
    add(items, sa.Column("detail_attempts", sa.Integer(), nullable=False, server_default="0"))
    add(items, sa.Column("next_detail_attempt_after", sa.DateTime(timezone=True), nullable=True))
    add(items, sa.Column("last_detail_error", sa.Text(), nullable=True))

    add(runs, sa.Column("detail_failures", sa.Integer(), nullable=False, server_default="0"))
    add(runs, sa.Column("scan_stop_reason", sa.String(length=64), nullable=True))
    add(runs, sa.Column("backfill_complete", sa.Boolean(), nullable=False, server_default=sa.false()))
    add(runs, sa.Column("missing_check_performed", sa.Boolean(), nullable=False, server_default=sa.false()))

    state = sa.table("schema_state", sa.column("key", sa.String), sa.column("value", sa.String))
    op.execute(
        state.update()
        .where(state.c.key == op.inline_literal(SCHEMA_STATE_KEY))
        .values(value="0002")
    )


def downgrade() -> None:
    for table, column in (
        ("source_items", "detail_listing"),
        ("source_health", "backfill_boundary_reached"),
        ("source_runs", "missing_check_performed"),
        ("source_runs", "backfill_complete"),
        ("source_runs", "scan_stop_reason"),
        ("source_runs", "detail_failures"),
        ("source_items", "last_detail_error"),
        ("source_items", "next_detail_attempt_after"),
        ("source_items", "detail_attempts"),
        ("source_items", "last_detail_checked_at"),
        ("source_health", "last_scan_stop_reason"),
        ("source_health", "last_scan_complete"),
        ("source_health", "backfill_last_stop_reason"),
        ("source_health", "backfill_last_progress_at"),
        ("source_health", "backfill_oldest_date"),
        ("source_health", "backfill_cursor_external_id"),
        ("source_health", "backfill_cursor_page"),
        ("source_health", "backfill_status"),
        ("source_health", "range_policy_version"),
        ("source_health", "initial_window_start"),
    ):
        op.drop_column(table, column)
    state = sa.table("schema_state", sa.column("key", sa.String), sa.column("value", sa.String))
    op.execute(state.update().where(state.c.key == SCHEMA_STATE_KEY).values(value="0001"))
