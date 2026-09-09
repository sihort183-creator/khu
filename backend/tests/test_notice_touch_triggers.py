"""마이그레이션 0004(공지 수정 시각 트리거)의 검사.

PostgreSQL 없이 도는 검사라 실제 트리거 동작은 확인하지 못한다. 대신 두 가지를 본다.

1. 만드는 문장과 되돌리는 문장이 짝이 맞는가 — 트리거 이름, 대상 표, 부르는 함수,
   전이 표(``khu_new``/``khu_old``) 선언이 함수 몸통과 어긋나지 않는가.
2. 검사용 SQLite 에서 upgrade/downgrade 가 깨지지 않고 스키마 표시가 오르내리는가.

실제 PostgreSQL 에서의 확인 방법은 docs/공개파일_증분화_2026-09-09.md 에 적었다.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from app.storage.models import metadata

BACKEND_ROOT = Path(__file__).resolve().parents[1]
MIGRATION = BACKEND_ROOT / "migrations" / "versions" / "0004_notice_touch_triggers.py"


def _module():
    spec = importlib.util.spec_from_file_location("khu_migration_0004", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_trigger_targets_a_real_table():
    module = _module()
    tables = set(metadata.tables)
    assert set(module.TRIGGERS) == set(module.TRIGGER_TABLES)
    for name, table in module.TRIGGER_TABLES.items():
        assert table in tables, f"{name} 이 없는 표 {table} 을 가리킵니다"
        assert f" on {table}" in module.TRIGGERS[name]


def test_every_trigger_calls_a_function_this_migration_defines():
    module = _module()
    for name, statement in module.TRIGGERS.items():
        called = re.search(r"execute function (\w+)\(\)", statement)
        assert called, f"{name} 이 부르는 함수를 찾지 못했습니다"
        assert called.group(1) in module.FUNCTIONS


def test_transition_tables_are_declared_wherever_the_function_body_uses_them():
    """문단위 트리거가 쓰는 전이 표는 REFERENCING 절에 반드시 선언되어야 한다.

    선언을 빠뜨리면 트리거를 만들 때가 아니라 **처음 발화할 때** 터진다. 운영에서
    수집이 통째로 멎는 종류의 실수라 여기서 미리 막는다.
    """
    module = _module()
    for name, statement in module.TRIGGERS.items():
        called = re.search(r"execute function (\w+)\(\)", statement).group(1)
        body = module.FUNCTIONS[called]
        for transition in ("khu_new", "khu_old"):
            if transition in body:
                assert f"table as {transition}" in statement, (
                    f"{name} 이 {transition} 을 선언하지 않은 채 {called} 을 부릅니다"
                )
        if "for each row" in statement:
            assert "khu_new" not in body and "khu_old" not in body


def test_row_level_triggers_only_fire_on_columns_that_reach_the_public_files():
    """원본 표는 회차마다 last_seen_at 이 바뀐다. 그것까지 세면 증분이 무의미해진다."""
    module = _module()
    statement = module.TRIGGERS["trg_source_items_touch_upd"]
    assert "for each row when (" in statement
    for column in module._ITEM_COLUMNS:
        assert f"old.{column} is distinct from new.{column}" in statement
    assert "last_seen_at" not in statement
    assert "last_detail_checked_at" not in statement


def test_function_bodies_are_balanced_dollar_quoted_plpgsql():
    module = _module()
    for name, body in module.FUNCTIONS.items():
        assert body.count("$khu$") == 2, f"{name} 의 달러 인용이 짝이 맞지 않습니다"
        assert "language plpgsql" in body
        assert body.strip().endswith("$khu$;")
        assert f"function {name}()" in body


def test_upgrade_and_downgrade_move_the_schema_marker_on_sqlite(tmp_path):
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "migrations"))
    url = f"sqlite:///{(tmp_path / 'triggers.db').as_posix()}"
    config.set_main_option("sqlalchemy.url", url)

    command.upgrade(config, "head")
    engine = create_engine(url)
    with engine.connect() as connection:
        assert connection.scalar(
            text("SELECT value FROM schema_state WHERE key='schema_version'")
        ) == "0004"
    command.downgrade(config, "0003")
    with engine.connect() as connection:
        assert connection.scalar(
            text("SELECT value FROM schema_state WHERE key='schema_version'")
        ) == "0003"
    command.upgrade(config, "0004")
    with engine.connect() as connection:
        assert connection.scalar(
            text("SELECT value FROM schema_state WHERE key='schema_version'")
        ) == "0004"
    engine.dispose()
