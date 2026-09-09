"""데이터베이스 읽기량 계측(app/storage/db.py)의 검사.

2026-09-09 Supabase 가 무료 egress 한도를 넘겼다고 알려 온 뒤에 붙었다. 어느 단계가
얼마나 읽는지 모르면 줄일 수도 없다. 값은 근사치지만, **읽은 것과 읽지 않은 것을
가르는 데는 충분해야 한다**. 그것을 여기서 확인한다.
"""

from __future__ import annotations

from sqlalchemy import select

from app.storage import models as m
from app.storage.db import (
    disable_read_meter,
    enable_read_meter,
    measure_reads,
    read_stats,
    reset_read_meter,
)


def _seed(session) -> None:
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
    session.flush()
    session.add(
        m.SourceItem(
            id="itm-1", source_id="src-hq", external_id="1",
            canonical_url="https://www.khu.ac.kr/view.do?id=1", current_revision_id="rev-1",
        )
    )
    session.flush()
    session.add(
        m.SourceItemRevision(
            id="rev-1", source_item_id="itm-1", content_hash="h1", title="제목",
            body_text="가" * 1000, body_html="<p>" + "가" * 1000 + "</p>",
            published_precision="unknown", extractor_version="test",
        )
    )
    session.commit()


def test_meter_counts_only_the_columns_that_were_actually_selected(session_factory, engine):
    with session_factory() as session:
        _seed(session)

    enable_read_meter(engine)
    reset_read_meter()
    try:
        with session_factory() as session, measure_reads(engine) as light:
            session.execute(select(m.SourceItemRevision.id)).all()
        with session_factory() as session, measure_reads(engine) as heavy:
            session.execute(
                select(m.SourceItemRevision.id, m.SourceItemRevision.body_html)
            ).all()
    finally:
        disable_read_meter(engine)

    assert light[0].rows == 1
    assert heavy[0].rows == 1
    # 본문은 한글 1000자 + 태그다. UTF-8 로 3000바이트가 넘는다.
    assert heavy[0].bytes - light[0].bytes > 3000
    assert light[0].bytes < 100


def test_meter_accumulates_across_statements_and_resets(session_factory, engine):
    with session_factory() as session:
        _seed(session)

    enable_read_meter(engine)
    reset_read_meter()
    try:
        with session_factory() as session:
            session.execute(select(m.Source.id)).all()
            first = read_stats()
            session.execute(select(m.Source.id)).all()
            second = read_stats()
        assert second.statements > first.statements
        assert second.rows == first.rows * 2
        reset_read_meter()
        assert read_stats().rows == 0
    finally:
        disable_read_meter(engine)


def test_enabling_twice_does_not_count_twice(session_factory, engine):
    with session_factory() as session:
        _seed(session)

    enable_read_meter(engine)
    enable_read_meter(engine)
    reset_read_meter()
    try:
        with session_factory() as session, measure_reads(engine) as measured:
            session.execute(select(m.Source.id)).all()
    finally:
        disable_read_meter(engine)
    assert measured[0].rows == 1


def test_measuring_does_not_change_query_results(session_factory, engine):
    with session_factory() as session:
        _seed(session)

    with session_factory() as session:
        plain = session.execute(select(m.SourceItemRevision.body_text)).scalars().all()

    enable_read_meter(engine)
    try:
        with session_factory() as session:
            metered = session.execute(select(m.SourceItemRevision.body_text)).scalars().all()
            rows = session.execute(select(m.SourceItemRevision)).scalars().all()
    finally:
        disable_read_meter(engine)

    assert metered == plain
    assert [row.id for row in rows] == ["rev-1"]
