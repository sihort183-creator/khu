"""거대한 `IN` 목록을 끊는 것의 검사(docs/디스크IO_줄이기_2026-09-13.md 5.1절).

여기서 지키려는 것은 두 가지다.

1. **문장 하나가 짧아야 한다.** 2026-09-13 디스크 입출력 고갈의 진짜 원인은 우리
   조회가 아니라, Supabase 의 감시 수집기가 1분마다 `pg_stat_statements` 의 질의문
   본문을 통째로 재구성하는 것이었다. 그 비용은 보관된 본문 파일의 크기에 비례하고,
   그 파일을 102.6 MB 까지 키운 것이 원소 23,800개짜리 `IN` 목록이다.
2. **끊어 읽어도 결과가 한 번에 읽을 때와 같아야 한다.** 첨부·원문 연결·문의처·
   분류·대상·건수 모두.

여기서 재는 길이는 **PostgreSQL 이 `pg_stat_statements` 에 적어 두는 모양**의 길이다.
검사는 sqlite 로 도니 자리표가 `?` 하나뿐인데, 운영에서는 `$1, $2, …` 로 펴진다.
그래서 자리표만 갈아 끼워 길이를 센다(_pg_length).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event, select

from app.config import Settings
from app.export.static import LoadedNotice, _load_notice_detail_parts, _load_notices
from app.storage import db as db_mod
from app.storage import models as m
from app.storage.db import IN_CHUNK, IN_SHAPES, in_chunks

BASE_TIME = datetime(2026, 6, 1, 3, 0, tzinfo=UTC)

# 운영 전량 회차의 공지 수(2026-09-09 실측 23,831건에서 반올림한 값). 지시서와 문서가
# 쓰는 숫자를 그대로 쓴다.
NOTICES = 23_800

# 문장 하나의 상한. 운영에서 항목 하나가 약 190 KB 였다. 1,000개로 끊으면 6 KB 안팎이라
# 60 KB 는 넉넉한 상한이다. 이 값을 넘으면 어디선가 목록을 통째로 싣고 있다는 뜻이다.
MAX_STATEMENT_BYTES = 60_000


def _pg_length(sql: str) -> int:
    """sqlite 가 받은 문장(`?`)을 PostgreSQL 의 `$1, $2 …` 모양으로 바꾼 길이."""
    parts = sql.split("?")
    return len(sql) - (len(parts) - 1) + sum(len(f"${i}") for i in range(1, len(parts)))


class _Capture:
    """실제로 데이터베이스로 나간 문장을 모은다."""

    def __init__(self, engine):
        self.engine = engine
        self.statements: list[str] = []

    def __enter__(self):
        def _listen(conn, cursor, statement, parameters, context, executemany):
            self.statements.append(" ".join(statement.split()))

        self._listen = _listen
        event.listen(self.engine, "before_cursor_execute", _listen)
        return self

    def __exit__(self, *exc):
        event.remove(self.engine, "before_cursor_execute", self._listen)
        return False

    def matching(self, *needles: str) -> list[str]:
        return [sql for sql in self.statements if all(n in sql for n in needles)]


def _stub(index: int) -> LoadedNotice:
    """식별자만 있으면 되는 자리에 쓰는 가짜 항목.

    _load_notice_detail_parts 가 보는 것은 revision_id 와 notice.id 두 칸뿐이다.
    """

    class _Notice:
        id = f"ntc-{index:06d}"

    return LoadedNotice(notice=_Notice(), revision_id=f"rev-{index:06d}")


# ----------------------------------------------------------- 끊는 규칙 자체


def test_in_chunks_uses_a_fixed_ladder_of_lengths():
    """덩어리 길이는 언제나 IN_SHAPES 안의 값이다. 그래서 항목이 몇 개로 모인다."""
    for size in (1, 2, 3, 7, 500, 999, 1000, 1001, 23_800):
        chunks = in_chunks([f"id-{i}" for i in range(size)])
        assert all(len(chunk) in IN_SHAPES for chunk in chunks), size


def test_in_chunks_never_more_than_doubles_the_list():
    """채워 넣는 중복이 실제 길이를 넘지 않는다. 매개변수도 행 어림수도 두 배 안이다."""
    for size in range(1, 300):
        chunks = in_chunks([f"id-{i}" for i in range(size)])
        assert sum(len(chunk) for chunk in chunks) <= 2 * size, size


def test_in_chunks_keeps_the_same_values_and_drops_duplicates():
    """채워 넣어도 값의 집합은 그대로다. 같은 값이 두 덩어리에 걸치지도 않는다."""
    values = [f"id-{i}" for i in range(1050)] + ["id-0", "id-7"]
    chunks = in_chunks(values)
    assert set().union(*chunks) == set(values)
    assert [len(chunk) for chunk in chunks] == [IN_CHUNK, 64]  # 50개가 64로 채워진다
    seen: set[str] = set()
    for chunk in chunks:
        assert not (seen & set(chunk)), "같은 값이 두 덩어리에 걸쳤다"
        seen |= set(chunk)


def test_in_chunks_of_nothing_makes_no_query():
    assert in_chunks([]) == []


def test_the_ladder_reaches_the_chunk_size():
    """사다리의 마지막 칸이 곧 덩어리 크기여야 한다.

    IN_CHUNK 만 키우고 사다리를 그대로 두면 마지막 조각이 채워지지 않고 그대로 나간다.
    동작은 예전과 같아 조용히 넘어가므로 여기서 잡는다.
    """
    assert IN_SHAPES[-1] == IN_CHUNK
    assert list(IN_SHAPES) == sorted(IN_SHAPES)


# ------------------------------------------------- 문장 길이(전·후 실측)


def test_detail_part_statements_stay_short_for_a_full_run(session_factory, engine):
    """공지 23,800건의 상세 관계 조회 세 개가 모두 짧고, 모양이 하나씩뿐이다."""
    notices = [_stub(i) for i in range(NOTICES)]
    with session_factory() as session, _Capture(engine) as cap:
        attachments, links, mentions = _load_notice_detail_parts(session, notices)

    assert attachments == {} and links == {} and mentions == {}  # 빈 데이터베이스

    families = {
        "첨부": cap.matching("FROM attachments", "attachments.revision_id IN"),
        "원문 연결": cap.matching("FROM notice_sources", "notice_sources.notice_id IN"),
        "문의처": cap.matching("FROM notice_contact_mentions", "revision_id IN"),
    }
    for name, sqls in families.items():
        assert sqls, f"{name} 조회를 가로채지 못했다"
        # 23,800건이면 1,000개짜리 24덩어리다(마지막 800개는 1,000으로 채워진다).
        assert len(sqls) == 24, name
        assert len(set(sqls)) == 1, f"{name}: 문장 모양이 {len(set(sqls))}가지"
        assert max(_pg_length(sql) for sql in sqls) <= MAX_STATEMENT_BYTES, name


def test_incremental_filters_stay_short_for_a_full_run(session_factory, engine):
    """증분 필터 네 개도 모두 짧고, 모양이 하나씩뿐이다."""
    wanted = {f"ntc-{i:06d}" for i in range(NOTICES)}
    with session_factory() as session, _Capture(engine) as cap:
        built, dropped = _load_notices(
            session, BASE_TIME, source_refs={}, health={}, notice_ids=wanted
        )

    assert built == [] and dropped == []  # 빈 데이터베이스

    families = {
        "분류": cap.matching("FROM notice_categories", "notice_id IN"),
        "대상": cap.matching("FROM notice_audiences", "notice_id IN"),
        "건수": cap.matching("count(notice_sources.id)", "notice_id IN"),
        "공지 행": cap.matching("FROM notices", "JOIN source_items", "notices.id IN"),
    }
    for name, sqls in families.items():
        assert sqls, f"{name} 조회를 가로채지 못했다"
        assert len(sqls) == 24, name
        assert len(set(sqls)) == 1, f"{name}: 문장 모양이 {len(set(sqls))}가지"
        assert max(_pg_length(sql) for sql in sqls) <= MAX_STATEMENT_BYTES, name


def test_one_big_in_list_would_be_far_over_the_limit(session_factory, engine):
    """끊기 전의 모양은 상한을 크게 넘는다. 전·후를 같은 자로 재 둔다.

    여기서 잰 값을 docs/디스크IO_줄이기_2026-09-13.md 5.1절에 적었다.
    """
    ids = [f"rev-{i:06d}" for i in range(NOTICES)]
    with session_factory() as session, _Capture(engine) as cap:
        session.execute(select(m.Attachment).where(m.Attachment.revision_id.in_(ids))).all()
    before = max(_pg_length(sql) for sql in cap.matching("FROM attachments"))

    with session_factory() as session, _Capture(engine) as cap:
        _load_notice_detail_parts(session, [_stub(i) for i in range(NOTICES)])
    after = max(_pg_length(sql) for sql in cap.matching("FROM attachments"))

    assert before > 150_000, before
    assert after <= MAX_STATEMENT_BYTES
    # 전량 회차 기준으로 스무 배 넘게 짧아져야 의미가 있다.
    assert before / after > 20


# ---------------------------------------------------- 끊어도 결과가 같은가


def _seed(session, count: int) -> None:
    """공지마다 첨부 2개·문의처 1개·분류 1개·대상 1개·원문 연결 2개를 만든다."""
    session.add(m.University(id="univ-khu", code="khu", name="경희대학교"))
    session.flush()
    session.add(m.Campus(id="cmp-seoul", university_id="univ-khu", code="seoul", name="서울"))
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
    session.flush()

    for index in range(count):
        item_id, revision_id, notice_id = f"itm-{index}", f"rev-{index}", f"ntc-{index}"
        extra_id = f"itm-x-{index}"
        published = (BASE_TIME + timedelta(days=index)).date()
        for ident, external in ((item_id, index), (extra_id, 10_000 + index)):
            session.add(
                m.SourceItem(
                    id=ident, source_id="src-hq", external_id=str(external),
                    canonical_url=f"https://www.khu.ac.kr/view.do?id={external}",
                    original_status="available",
                )
            )
        session.flush()
        session.add(
            m.SourceItemRevision(
                id=revision_id, source_item_id=item_id, content_hash=f"hash{index}",
                title=f"공지 {index}", body_text=f"본문 {index}", body_html=f"<p>본문 {index}</p>",
                published_date=published, published_precision="date", extractor_version="test",
            )
        )
        session.flush()
        session.get(m.SourceItem, item_id).current_revision_id = revision_id
        session.add(
            m.Notice(
                id=notice_id, primary_source_item_id=item_id, status="visible",
                first_visible_at=BASE_TIME, updated_at=BASE_TIME, title=f"공지 {index}",
                excerpt=f"발췌 {index}", published_date=published, published_precision="date",
            )
        )
        session.flush()
        for suffix, source_item, primary in (("a", item_id, True), ("b", extra_id, False)):
            session.add(
                m.NoticeSource(
                    id=f"lnk-{index}-{suffix}", notice_id=notice_id, source_item_id=source_item,
                    is_active=True, is_primary=primary,
                )
            )
        for suffix in ("a", "b"):
            session.add(
                m.Attachment(
                    id=f"att-{index}-{suffix}", revision_id=revision_id,
                    filename=f"{suffix}-{index}.pdf", kind="pdf", status="listed",
                )
            )
        session.add(
            m.NoticeContactMention(
                id=f"mnt-{index}", revision_id=revision_id, raw_text=f"문의 {index}",
                channel_kind="phone", value=f"02-961-{index:04d}",
            )
        )
        session.add(
            m.NoticeCategory(
                id=f"cat-{index}", notice_id=notice_id, category_code="other", is_primary=True
            )
        )
        session.add(
            m.NoticeAudience(
                id=f"aud-{index}", notice_id=notice_id, audience_type="campus",
                campus_id="cmp-seoul",
            )
        )
        session.flush()
    session.commit()


@pytest.fixture
def cfg(settings: Settings) -> Settings:
    return replace(settings, export_max_changed_ratio=0.95)


def _detail_parts(session_factory, notices):
    with session_factory() as session:
        attachments, links, mentions = _load_notice_detail_parts(session, notices)
    return (
        {rev: sorted(a.id for a in rows) for rev, rows in attachments.items()},
        {nid: sorted(link.id for link, _, _ in rows) for nid, rows in links.items()},
        {rev: sorted(row.id for row in rows) for rev, rows in mentions.items()},
    )


def test_chunked_detail_parts_match_a_single_read(session_factory, monkeypatch):
    """첨부·원문 연결·문의처를 끊어 읽은 결과가 한 번에 읽은 결과와 같다."""
    with session_factory() as session:
        _seed(session, 25)
    notices = []
    for index in range(25):
        stub = _stub(index)
        stub.notice.id = f"ntc-{index}"  # 원문 연결은 공지 식별자로 맞춘다
        notices.append(LoadedNotice(notice=stub.notice, revision_id=f"rev-{index}"))

    monkeypatch.setattr(db_mod, "IN_CHUNK", 100_000)
    at_once = _detail_parts(session_factory, notices)
    monkeypatch.setattr(db_mod, "IN_CHUNK", 3)
    chunked = _detail_parts(session_factory, notices)

    assert at_once == chunked
    assert len(at_once[0]) == 25 and len(at_once[1]) == 25 and len(at_once[2]) == 25
    assert all(len(rows) == 2 for rows in at_once[0].values())  # 첨부 2개
    assert all(len(rows) == 2 for rows in at_once[1].values())  # 원문 연결 2개


def _incremental(session_factory, wanted):
    from app.export.static import _source_refs

    with session_factory() as session:
        refs = _source_refs(session)
        built, dropped = _load_notices(
            session, BASE_TIME + timedelta(days=400), source_refs=refs,
            health={"src-hq": BASE_TIME}, notice_ids=wanted,
        )
    return [
        (
            loaded.notice.id,
            loaded.notice.primary_category.code,
            tuple(c.code for c in loaded.notice.secondary_categories),
            tuple((a.type, a.id) for a in loaded.notice.audiences),
            loaded.notice.source_count,
            loaded.revision_id,
            loaded.body_text,
        )
        for loaded in built
    ], sorted(dropped)


def test_chunked_incremental_filters_match_a_single_read(session_factory, monkeypatch):
    """분류·대상·건수·공지 행을 끊어 읽은 결과가 한 번에 읽은 결과와 같다."""
    with session_factory() as session:
        _seed(session, 25)
    wanted = {f"ntc-{i}" for i in range(25)}

    monkeypatch.setattr(db_mod, "IN_CHUNK", 100_000)
    at_once = _incremental(session_factory, wanted)
    monkeypatch.setattr(db_mod, "IN_CHUNK", 3)
    chunked = _incremental(session_factory, wanted)

    assert at_once == chunked
    rows = at_once[0]
    assert len(rows) == 25
    # 값이 실제로 붙어 있는지도 본다. 비어 있는 것끼리 같으면 검사가 되지 않는다.
    assert all(row[1] == "other" for row in rows)
    assert all(row[3] == (("campus", "cmp-seoul"),) for row in rows)
    assert all(row[4] == 2 for row in rows)  # 활성 원문 연결 2개
    assert all(row[6] for row in rows)  # 본문도 덩어리로 읽어 붙는다


def test_chunked_export_makes_the_same_files(session_factory, cfg, store, monkeypatch):
    """덩어리 크기를 바꿔도 만들어진 공개 파일이 바이트까지 같다."""
    import json

    from app.export.static import BASE, export_static

    with session_factory() as session:
        _seed(session, 25)

    def _entries(revision: str) -> dict[str, str]:
        raw = store.get_bytes(cfg.r2.bucket_public, f"{BASE}/r/{revision}/manifest.json")
        # 내용 객체 이름이 곧 내용 해시다. 같으면 파일이 바이트까지 같다.
        return json.loads(raw)["entries"]

    monkeypatch.setattr(db_mod, "IN_CHUNK", 100_000)
    first = export_static(cfg, store=store, revision="r-1", mode="full")
    monkeypatch.setattr(db_mod, "IN_CHUNK", 3)
    second = export_static(cfg, store=store, revision="r-2", mode="full")

    assert first.notices == second.notices == 25
    assert _entries("r-1") == _entries("r-2")
