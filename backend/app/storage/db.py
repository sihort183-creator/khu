"""데이터베이스 연결.

실행 서버는 IPv4만 주므로 Supabase 세션 방식 중계기(포트 5432)를 쓴다(15.1절).
연결 예산은 실행당 5개 이내다. 긴 웹 요청 동안 연결을 잡고 있지 않는다.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator

from sqlalchemy import Engine, create_engine, event, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import ConfigError, Settings
from app.config import settings as default_settings
from app.storage.models import SchemaState, metadata

# 코드가 기대하는 스키마 표시. 마이그레이션이 이 값을 올리고,
# 수집 실행은 시작할 때 일치를 확인한 뒤에만 진행한다(17.1절 3항).
EXPECTED_SCHEMA_VERSION = "0002"
SCHEMA_STATE_KEY = "schema_version"

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def build_engine(cfg: Settings | None = None, *, url: str | None = None) -> Engine:
    cfg = cfg or default_settings
    dsn = url or cfg.require_database()
    engine = create_engine(
        dsn,
        # 출처를 동시에 처리하면 그만큼 연결도 함께 쓴다. 여유를 두 개 더 둔다.
        pool_size=max(3, cfg.source_concurrency + 2),
        max_overflow=2,
        pool_pre_ping=True,
        pool_recycle=280,
        future=True,
        connect_args={"connect_timeout": 15} if dsn.startswith("postgresql") else {},
    )
    if dsn.startswith("sqlite"):
        # 검사용 sqlite 에서도 외래키 제약을 실제로 적용한다.
        @event.listens_for(engine, "connect")
        def _fk_on(dbapi_conn, _record):  # pragma: no cover - 드라이버 콜백
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

    return engine


def get_engine(cfg: Settings | None = None) -> Engine:
    global _engine, _session_factory
    if _engine is None:
        _engine = build_engine(cfg)
        _session_factory = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    return _engine


def get_session_factory(cfg: Settings | None = None) -> sessionmaker[Session]:
    get_engine(cfg)
    assert _session_factory is not None
    return _session_factory


@contextlib.contextmanager
def session_scope(cfg: Settings | None = None) -> Iterator[Session]:
    """한 트랜잭션. 예외가 나면 되돌린다."""
    factory = get_session_factory(cfg)
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def create_all(engine: Engine) -> None:
    """검사·로컬 전용. 운영 스키마는 Alembic 이 만든다."""
    metadata.create_all(engine)


def read_schema_version(session: Session) -> str | None:
    row = session.execute(
        select(SchemaState.value).where(SchemaState.key == SCHEMA_STATE_KEY)
    ).scalar_one_or_none()
    return row


def write_schema_version(session: Session, value: str = EXPECTED_SCHEMA_VERSION) -> None:
    current = session.get(SchemaState, SCHEMA_STATE_KEY)
    if current is None:
        session.add(SchemaState(key=SCHEMA_STATE_KEY, value=value))
    else:
        current.value = value


class SchemaMismatch(RuntimeError):
    """코드가 기대하는 스키마와 데이터베이스가 다르다. 즉시 멈춘다."""


def assert_schema_ready(session: Session) -> None:
    """수집 실행 시작 시 호출. 다르면 조용히 진행하지 않고 멈춘다(17.1절 3항)."""
    found = read_schema_version(session)
    if found is None:
        raise SchemaMismatch(
            "schema_state 가 비어 있습니다. 마이그레이션을 먼저 실행하세요: "
            "alembic -c backend/alembic.ini upgrade head"
        )
    if found != EXPECTED_SCHEMA_VERSION:
        raise SchemaMismatch(
            f"스키마 버전 불일치: 데이터베이스 {found}, 코드 기대 {EXPECTED_SCHEMA_VERSION}. "
            "마이그레이션을 실행한 뒤 다시 시도하세요."
        )


def ping(engine: Engine) -> bool:
    with engine.connect() as conn:
        conn.execute(text("select 1"))
    return True


def reset_engine() -> None:
    """검사에서 설정을 바꿔 다시 연결할 때 사용."""
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None


__all__ = [
    "ConfigError",
    "EXPECTED_SCHEMA_VERSION",
    "SchemaMismatch",
    "assert_schema_ready",
    "build_engine",
    "create_all",
    "get_engine",
    "get_session_factory",
    "ping",
    "read_schema_version",
    "reset_engine",
    "session_scope",
    "write_schema_version",
]
