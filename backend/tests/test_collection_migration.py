"""이전 스키마에서 진행 상태를 추가해도 기존 행과 버전 표시를 보존한다."""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text


def test_upgrade_downgrade_and_reupgrade_preserve_existing_data(tmp_path):
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    url = f"sqlite:///{(tmp_path / 'migration.db').as_posix()}"
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO schema_state (key, value) VALUES ('preserve-test', 'kept')"))
    command.downgrade(config, "0001")
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT value FROM schema_state WHERE key='schema_version'")) == "0001"
        assert "detail_listing" not in {c["name"] for c in inspect(connection).get_columns("source_items")}
    command.upgrade(config, "0002")
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT value FROM schema_state WHERE key='schema_version'")) == "0002"
        assert connection.scalar(text("SELECT value FROM schema_state WHERE key='preserve-test'")) == "kept"
        assert "detail_listing" in {c["name"] for c in inspect(connection).get_columns("source_items")}
        assert "backfill_boundary_reached" in {c["name"] for c in inspect(connection).get_columns("source_health")}
    engine.dispose()
