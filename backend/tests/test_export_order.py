"""공개 파일의 목록 순서와 본문 읽는 길의 검사(docs/디스크IO_줄이기_2026-09-13.md).

여기서 지키려는 것은 두 가지다.

1. **목록 순서는 파이썬이 정한다.** 데이터베이스가 어떤 순서로 행을 돌려주든 결과가
   같아야 한다. 그래야 쓰이지도 않는 정렬을 데이터베이스에 시키지 않을 수 있다.
2. **본문 두 칸은 큰 조인을 지나지 않는다.** 개정 식별자로 따로 읽어 붙인다.
   운영의 work_mem 은 2,184kB 인데 본문 합계는 약 70MB 라, 조인이 본문을 끌고
   다니면 중간 결과가 메모리에 들어가지 못한다.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, event

from app.config import Settings
from app.export.static import BASE, export_static
from app.storage import models as m

BASE_TIME = datetime(2026, 6, 1, 3, 0, tzinfo=UTC)


def _seed_frame(session) -> None:
    session.add(m.University(id="univ-khu", code="khu", name="경희대학교"))
    session.flush()
    session.add(
        m.Organization(id="org-hq", university_id="univ-khu", org_type="office", name="대학본부")
    )
    session.flush()
    session.add(
        m.Source(
            id="src-hq", organization_id="org-hq", name="대학본부 공지", adapter="khu_board",
            list_url="https://www.khu.ac.kr/list.do", status="active",
        )
    )
    session.add(m.SourceHealth(source_id="src-hq", last_list_success_at=BASE_TIME))
    session.commit()


def _seed_notices(session, order: list[int]) -> None:
    """``order`` 에 적힌 차례대로 넣는다. 번호가 클수록 최신 글이다.

    넣는 차례가 곧 sqlite 의 물리적 순서(rowid)다. 이 차례를 뒤집어 두 번 내보내
    결과가 같은지 본다.
    """
    for index in order:
        item_id, revision_id, notice_id = f"itm-{index}", f"rev-{index}", f"ntc-{index}"
        published = (BASE_TIME + timedelta(days=index)).date()
        session.add(
            m.SourceItem(
                id=item_id, source_id="src-hq", external_id=str(index),
                canonical_url=f"https://www.khu.ac.kr/view.do?id={index}",
                current_revision_id=revision_id, original_status="available",
            )
        )
        session.flush()
        session.add(
            m.SourceItemRevision(
                id=revision_id, source_item_id=item_id, content_hash=f"hash{index}",
                title=f"공지 {index}", body_text=f"본문 {index} " * 30,
                body_html=f"<p>본문 {index}</p>", published_date=published,
                published_precision="date", extractor_version="test",
            )
        )
        session.flush()
        session.add(
            m.Notice(
                id=notice_id, primary_source_item_id=item_id, status="visible",
                first_visible_at=BASE_TIME, updated_at=BASE_TIME, title=f"공지 {index}",
                excerpt=f"발췌 {index}", published_date=published, published_precision="date",
            )
        )
        session.add(
            m.NoticeSource(
                id=f"lnk-{index}", notice_id=notice_id, source_item_id=item_id,
                is_active=True, is_primary=True,
            )
        )
        session.add(
            m.NoticeCategory(
                id=f"cat-{index}", notice_id=notice_id, category_code="other", is_primary=True
            )
        )
        session.flush()
    session.commit()


def _clear_notices(session) -> None:
    """같은 자료를 다른 물리적 순서로 다시 넣기 위해 공지 쪽을 비운다."""
    for table in (m.NoticeCategory, m.NoticeSource, m.Notice, m.SourceItemRevision, m.SourceItem):
        session.execute(delete(table))
    session.commit()


@pytest.fixture
def cfg(settings: Settings) -> Settings:
    return replace(settings, export_max_changed_ratio=0.95)


def _entries(store, cfg: Settings, revision: str) -> dict[str, str]:
    """개정 명세. 값은 내용 해시로 만든 객체 이름이라, 같으면 파일이 바이트까지 같다."""
    raw = store.get_bytes(cfg.r2.bucket_public, f"{BASE}/r/{revision}/manifest.json")
    return json.loads(raw)["entries"]


def _page_data(store, cfg: Settings, revision: str) -> list[dict]:
    key = _entries(store, cfg, revision)["notices/page/1.json"]
    return json.loads(store.get_bytes(cfg.r2.bucket_public, key))["data"]


def _page_ids(store, cfg: Settings, revision: str) -> list[str]:
    return [entry["id"] for entry in _page_data(store, cfg, revision)]


def test_result_is_the_same_whatever_order_the_database_returns(session_factory, cfg, store):
    """같은 자료를 반대 차례로 넣어도 만들어진 목록이 같다.

    SQL 정렬을 지워도 되는 근거다. 공개 순서는 _entry_order 가 정하고, 그 열쇠의
    마지막 칸이 공지 id 라 같은 값이 나올 수 없다.
    """
    forward = list(range(1, 9))
    with session_factory() as session:
        _seed_frame(session)
        _seed_notices(session, forward)
    first = export_static(cfg, store=store, revision="r-1", mode="full")

    with session_factory() as session:
        _clear_notices(session)
        _seed_notices(session, list(reversed(forward)))
    second = export_static(cfg, store=store, revision="r-2", mode="full")

    assert first.notices == second.notices == 8
    # 내용 객체 이름이 같다 = 만들어진 파일이 바이트까지 같다.
    assert (
        _entries(store, cfg, "r-1")["notices/page/1.json"]
        == _entries(store, cfg, "r-2")["notices/page/1.json"]
    )
    assert _page_data(store, cfg, "r-1") == _page_data(store, cfg, "r-2")
    # 최신 글이 위다. 번호가 클수록 최신이므로 내림차순이어야 한다.
    assert _page_ids(store, cfg, "r-1") == [f"ntc-{i}" for i in range(8, 0, -1)]


def test_big_join_does_not_carry_bodies_and_does_not_sort(session_factory, cfg, store, engine):
    """공지 행 조회에 본문 두 칸도, ORDER BY 도 실리지 않는다.

    둘 다 다시 들어오면 이 검사가 깨진다. 본문은 개정 식별자로 따로 읽는다.
    """
    with session_factory() as session:
        _seed_frame(session)
        _seed_notices(session, list(range(1, 9)))

    statements: list[str] = []

    def _capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(" ".join(statement.split()))

    event.listen(engine, "before_cursor_execute", _capture)
    try:
        export_static(cfg, store=store, revision="r-1", mode="full")
    finally:
        event.remove(engine, "before_cursor_execute", _capture)

    joined = [
        sql for sql in statements
        if "FROM notices" in sql and "JOIN source_items" in sql and "JOIN sources" in sql
    ]
    assert joined, "공지 행 조회를 가로채지 못했다"
    for sql in joined:
        assert "body_text" not in sql, sql
        assert "body_html" not in sql, sql
        assert "ORDER BY" not in sql, sql

    # 본문은 개정 식별자만 걸고 따로 읽는다. 조인도 정렬도 없다.
    body_reads = [
        sql for sql in statements
        if "body_text" in sql and "source_item_revisions.id IN" in sql
    ]
    assert body_reads, "본문 덩어리 조회를 가로채지 못했다"
    for sql in body_reads:
        assert "JOIN" not in sql, sql
        assert "ORDER BY" not in sql, sql


def test_bodies_are_read_in_chunks(session_factory, cfg, store, engine, monkeypatch):
    """본문은 한 번에 다 읽지 않고 덩어리로 나눠 읽는다."""
    from app.storage import db as db_mod

    monkeypatch.setattr(db_mod, "IN_CHUNK", 3)
    with session_factory() as session:
        _seed_frame(session)
        _seed_notices(session, list(range(1, 9)))

    statements: list[str] = []

    def _capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(" ".join(statement.split()))

    event.listen(engine, "before_cursor_execute", _capture)
    try:
        result = export_static(cfg, store=store, revision="r-1", mode="full")
    finally:
        event.remove(engine, "before_cursor_execute", _capture)

    body_reads = [sql for sql in statements if "body_text" in sql and "IN" in sql]
    # 8건을 3개씩 나누면 조회는 세 번이다(3+3+2). 본문은 빠짐없이 붙는다.
    assert len(body_reads) == 3
    assert result.notices == 8
    assert all(entry["excerpt"] for entry in _page_data(store, cfg, "r-1"))
