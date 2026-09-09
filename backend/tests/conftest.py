"""검사 공통 준비.

운영 비밀값 없이, 네트워크 없이 재현한다(15.3절).
데이터베이스는 매번 새 sqlite 파일, 객체 저장은 임시 폴더다.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.config import R2Settings, Settings  # noqa: E402
from app.storage import db as db_module  # noqa: E402
from app.storage.db import (  # noqa: E402
    EXPECTED_SCHEMA_VERSION,
    build_engine,
    create_all,
    write_schema_version,
)
from app.storage.objects import LocalObjectStore  # noqa: E402

FIXTURES = BACKEND_ROOT / "tests" / "fixtures"


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES


@pytest.fixture
def board_fixture():
    def _load(name: str) -> str:
        return (FIXTURES / "khu_board" / name).read_text(encoding="utf-8")

    return _load


@pytest.fixture
def gnuboard_fixture():
    def _load(name: str) -> str:
        return (FIXTURES / "gnuboard" / name).read_text(encoding="utf-8")

    return _load


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        env="test",
        database_url=f"sqlite:///{(tmp_path / 'test.db').as_posix()}",
        r2=R2Settings(
            bucket_public="public", bucket_evidence="evidence", bucket_backup="backup"
        ),
        run_budget_seconds=60,
        http_concurrency=2,
        http_delay_seconds=0.0,
        list_page_limit=2,
        initial_window_start=None,
    )


@pytest.fixture
def engine(settings: Settings):
    db_module.reset_engine()
    eng = build_engine(settings)
    create_all(eng)
    from sqlalchemy.orm import Session

    with Session(eng) as session:
        write_schema_version(session, EXPECTED_SCHEMA_VERSION)
        session.commit()
    yield eng
    eng.dispose()
    db_module.reset_engine()


@pytest.fixture
def session_factory(engine, settings: Settings):
    """app.storage.db 의 전역 엔진을 검사용으로 바꾼다."""
    from sqlalchemy.orm import sessionmaker

    db_module._engine = engine
    db_module._session_factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    yield db_module._session_factory
    db_module.reset_engine()


@pytest.fixture
def store(tmp_path: Path) -> LocalObjectStore:
    return LocalObjectStore(tmp_path / "objects")


@pytest.fixture(autouse=True)
def export_metrics_path(tmp_path, monkeypatch):
    """공개 회차 기록을 검사마다 따로 둔다.

    기본값은 실행 폴더의 .localstore 라서, 한 검사가 남긴 기록을 다음 검사가
    읽어 결과가 순서에 따라 달라진다.
    """
    from app.export import static as static_module

    monkeypatch.setattr(static_module, "METRICS_PATH", tmp_path / "export-metrics.json")
