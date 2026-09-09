"""데이터베이스 연결.

실행 서버는 IPv4만 주므로 Supabase 세션 방식 중계기(포트 5432)를 쓴다(15.1절).
연결 예산은 실행당 5개 이내다. 긴 웹 요청 동안 연결을 잡고 있지 않는다.
"""

from __future__ import annotations

import contextlib
import logging
import threading
from collections.abc import Iterator
from dataclasses import dataclass, replace

from sqlalchemy import Engine, create_engine, event, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import ConfigError, Settings
from app.config import settings as default_settings
from app.storage.models import SchemaState, metadata

log = logging.getLogger("khu.db")

# 코드가 기대하는 스키마 표시. 마이그레이션이 이 값을 올리고,
# 수집 실행은 시작할 때 일치를 확인한 뒤에만 진행한다(17.1절 3항).
EXPECTED_SCHEMA_VERSION = "0004"
SCHEMA_STATE_KEY = "schema_version"

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def build_engine(cfg: Settings | None = None, *, url: str | None = None) -> Engine:
    cfg = cfg or default_settings
    dsn = url or cfg.require_database()
    engine = create_engine(
        dsn,
        # 출처를 동시에 처리하면 그만큼 연결도 함께 쓴다. 여유를 두 개 더 둔다.
        # 연결 상한은 Postgres 의 max_connections(60)가 아니라 Supabase 풀러가 세션 모드에서
        # 사용자마다 거는 pool_size(15)다. 이 값을 넘기면 수집이 통째로 죽는다.
        # 2026-09-07 KHU_SOURCE_CONCURRENCY 를 24 로 올렸다가 6분 만에 실패했다.
        # 아래 계산상 KHU_SOURCE_CONCURRENCY 는 11 을 넘지 않아야 한다.
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


# --------------------------------------------------------------- 읽기량 계측

# 2026-09-09 Supabase 가 무료 한도(월 5 GB)를 넘겼다고 알려 왔다. 어디서 얼마나
# 읽는지 모르면 줄일 수도 없어서, 회차마다 "데이터베이스에서 받아 온 양"을 잰다.
#
# 재는 방법은 근사치다. SQLAlchemy 가 결과를 꺼낼 때 쓰는 커서를 얇은 대리자로
# 감싸고, 넘어온 값의 길이를 더한다(문자열은 UTF-8 바이트, 나머지는 8바이트로 셈).
# 실제 회선 위의 바이트에는 열 이름·형식 정보·프로토콜 덮개가 더 붙으므로 이 값은
# 하한에 가깝다. 절대값보다 "증분 전후 비교"에 쓰라고 만든 값이다.


@dataclass(frozen=True)
class ReadStats:
    """데이터베이스에서 읽어 온 양. 세 값 모두 누적이다."""

    statements: int = 0
    rows: int = 0
    bytes: int = 0

    def delta(self, before: ReadStats) -> ReadStats:
        return ReadStats(
            statements=self.statements - before.statements,
            rows=self.rows - before.rows,
            bytes=self.bytes - before.bytes,
        )

    def as_dict(self) -> dict[str, int]:
        return {"statements": self.statements, "rows": self.rows, "bytes": self.bytes}


_meter_lock = threading.Lock()
_meter_total = ReadStats()
_metered_engines: set[int] = set()


def _cell_bytes(value: object) -> int:
    if value is None:
        return 0
    if isinstance(value, str):
        return len(value.encode("utf-8", "ignore"))
    if isinstance(value, bytes | bytearray | memoryview):
        return len(value)
    # 숫자·시각·불리언은 회선에서 짧은 고정 폭이다. 8바이트로 어림한다.
    return 8


def _tally(rows) -> None:
    count = 0
    size = 0
    for row in rows:
        count += 1
        for value in row:
            size += _cell_bytes(value)
    if not count:
        return
    global _meter_total
    with _meter_lock:
        _meter_total = ReadStats(
            statements=_meter_total.statements, rows=_meter_total.rows + count,
            bytes=_meter_total.bytes + size,
        )


class _CountingCursor:
    """DBAPI 커서를 그대로 흉내 내면서 꺼낸 행만 세는 대리자."""

    __slots__ = ("_cursor",)

    def __init__(self, cursor) -> None:
        object.__setattr__(self, "_cursor", cursor)

    def __getattr__(self, name):  # pragma: no cover - 단순 위임
        return getattr(self._cursor, name)

    def __setattr__(self, name, value):  # pragma: no cover - 단순 위임
        setattr(self._cursor, name, value)

    def __iter__(self):
        for row in self._cursor:
            _tally((row,))
            yield row

    def fetchone(self):
        row = self._cursor.fetchone()
        if row is not None:
            _tally((row,))
        return row

    def fetchmany(self, *args, **kwargs):
        rows = self._cursor.fetchmany(*args, **kwargs)
        _tally(rows)
        return rows

    def fetchall(self):
        rows = self._cursor.fetchall()
        _tally(rows)
        return rows


def _on_cursor_execute(_conn, cursor, _statement, _parameters, context, _many) -> None:
    global _meter_total
    with _meter_lock:
        _meter_total = ReadStats(
            statements=_meter_total.statements + 1, rows=_meter_total.rows,
            bytes=_meter_total.bytes,
        )
    # 결과 대리자는 이 뒤에 context.cursor 로 만들어진다. 여기서 바꿔치기하면
    # 실제로 꺼내 가는 행이 대리자를 지나간다.
    if context is not None and getattr(context, "cursor", None) is cursor:
        try:
            context.cursor = _CountingCursor(cursor)
        except Exception:  # pragma: no cover - 드라이버가 막으면 계측만 포기한다
            log.debug("읽기량 계측을 붙이지 못했습니다", exc_info=True)


def enable_read_meter(engine: Engine | None = None) -> None:
    """읽기량 계측을 켠다. 여러 번 불러도 한 번만 붙는다.

    계측은 순수하게 관찰만 한다. 결과·트랜잭션·예외 처리에 손대지 않는다.
    """
    engine = engine or get_engine()
    if id(engine) in _metered_engines:
        return
    event.listen(engine, "after_cursor_execute", _on_cursor_execute)
    _metered_engines.add(id(engine))


def disable_read_meter(engine: Engine | None = None) -> None:
    """계측을 뗀다. 누적값은 지우지 않는다."""
    engine = engine or get_engine()
    if id(engine) not in _metered_engines:
        return
    with contextlib.suppress(Exception):
        event.remove(engine, "after_cursor_execute", _on_cursor_execute)
    _metered_engines.discard(id(engine))


def read_stats() -> ReadStats:
    """지금까지의 누적 읽기량."""
    with _meter_lock:
        return replace(_meter_total)


def reset_read_meter() -> None:
    global _meter_total
    with _meter_lock:
        _meter_total = ReadStats()


@contextlib.contextmanager
def measure_reads(engine: Engine | None = None) -> Iterator[list[ReadStats]]:
    """구간 읽기량을 잰다.

        with measure_reads() as measured:
            ...
        print(measured[0].bytes)

    한 칸짜리 목록을 주는 이유는 구간이 끝나야 값이 정해지기 때문이다.
    구간 안에서 읽으면 아직 0 이다. 계측이 꺼져 있으면 자동으로 켠다.
    """
    enable_read_meter(engine)
    before = read_stats()
    box: list[ReadStats] = [ReadStats()]
    try:
        yield box
    finally:
        box[0] = read_stats().delta(before)


__all__ = [
    "ConfigError",
    "EXPECTED_SCHEMA_VERSION",
    "ReadStats",
    "SchemaMismatch",
    "assert_schema_ready",
    "build_engine",
    "create_all",
    "disable_read_meter",
    "enable_read_meter",
    "get_engine",
    "get_session_factory",
    "measure_reads",
    "ping",
    "read_schema_version",
    "read_stats",
    "reset_engine",
    "reset_read_meter",
    "session_scope",
    "write_schema_version",
]
