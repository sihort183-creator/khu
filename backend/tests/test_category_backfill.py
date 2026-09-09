"""쌓인 공지의 주제(탭)를 저장된 값으로 다시 계산하는 작업 검사(8.2절).

2026-09-09 장학 탭 실측에서 넷 중 하나가 장학이 아니었고, 제목에 장학이 있는 글의
15%가 다른 탭에 있었다. 규칙을 고쳤지만(categories/2) 분류는 새 리비전이 생길 때
한 번만 계산되므로, 이미 쌓인 공지는 이 작업이 있어야 바뀐다. 여기서 고정하는 것은
넷이다. 옛 규칙이 남긴 잘못된 대표 주제가 새 규칙으로 옮겨진다는 것, --only 가 그 탭의
드나듦만 손댄다는 것, 바뀐 공지의 갱신 시각이 올라가 증분 내보내기가 알아챈다는 것,
그리고 묶음 번호로 그대로 되돌릴 수 있다는 것이다.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.domain.audiences import AudienceDecision, AudienceTarget
from app.domain.categories import CategoryDecision
from app.domain.dates import NO_DEADLINE, parse_published
from app.ingestion.base import FetchedDetail, ListedItem
from app.run.collect import revert_category_backfill, run_category_backfill
from app.storage import models as m
from app.storage import repository as repo

LATE_SCHOLARSHIP_BODY = "행정 업무를 돕는 조교를 모집합니다. " * 20 + "선발된 조교에게는 장학금을 지급합니다."


@pytest.fixture
def db(session_factory):
    with session_factory() as session:
        yield session
        session.commit()


@pytest.fixture
def source(db):
    db.add(m.University(id="univ-khu", code="khu", name="경희대학교"))
    db.flush()
    db.add(m.Organization(id="org-law", university_id="univ-khu", org_type="college", name="법과대학"))
    db.flush()
    src = m.Source(
        id="src-law",
        organization_id="org-law",
        name="법과대학 공지사항",
        adapter="khu_board",
        list_url="https://law.khu.ac.kr/list.do?menuNo=1",
        status="active",
    )
    db.add(src)
    db.flush()
    db.add(m.SourceHealth(source_id="src-law"))
    db.flush()
    return src


def _seed_notice(db, source, *, external_id: str, title: str, body: str, stored_primary: str) -> str:
    """대표 주제만 옛 규칙대로 저장한 공지를 하나 만든다."""
    listed = ListedItem(
        external_id=external_id,
        url=f"https://law.khu.ac.kr/view.do?id={external_id}",
        title=title,
        published_raw="2026-03-02",
    )
    detail = FetchedDetail(
        external_id=external_id,
        url=listed.url,
        title=title,
        body_text=body,
        body_html=f"<p>{body}</p>",
        raw_html=f"<html>{body}</html>",
        published_raw=listed.published_raw,
        extraction_notes={"extractor": "test/1"},
    )
    written = repo.upsert_item_and_revision(
        db,
        source=source,
        listed=listed,
        detail=detail,
        published=parse_published(detail.published_raw),
        raw_object_key=None,
        extractor_version="test/1",
    )
    revision = db.get(m.SourceItemRevision, written.revision_id)
    notice, _ = repo.upsert_notice_for_item(
        db,
        item_id=written.item_id,
        revision=revision,
        # 옛 규칙(categories/1)이 남긴 모양을 그대로 심는다. 여기가 다시 계산의 출발점이다.
        category=CategoryDecision(
            primary=stored_primary, rule_name="body_keyword", rule_version="categories/1",
            all_codes=(stored_primary,),
        ),
        audience=AudienceDecision(targets=(AudienceTarget("organization", "org-law", "법과대학"),), note=None),
        deadline=NO_DEADLINE,
    )
    notice.updated_at = datetime(2026, 3, 2, tzinfo=UTC)
    db.flush()
    return notice.id


def _primary(db, notice_id: str) -> str:
    return repo.notice_category_codes(db, [notice_id])[notice_id][0]


@pytest.fixture
def seeded(db, source):
    # 본문 뒤쪽의 "장학금" 때문에 장학 탭에 들어가 있던 조교 모집.
    ta = _seed_notice(
        db, source, external_id="1",
        title="[법학계열 종합행정실] 2026-2학기 조교 모집", body=LATE_SCHOLARSHIP_BODY,
        stored_primary="scholarship",
    )
    # "장학금·장학생" 만 보던 규칙이 기타로 보낸 교내 장학.
    bare = _seed_notice(
        db, source, external_id="2",
        title="2026-1학기 융합인재장학 신청 안내", body="신청 기간과 자격은 아래와 같습니다.",
        stored_primary="other",
    )
    # 졸업 규칙이 장학보다 앞서서 졸업 탭에 있던 논문발표장학.
    grad = _seed_notice(
        db, source, external_id="3",
        title="2026학년도 1학기 학술대회 논문발표장학 신청 안내", body="학술대회 발표자 지원.",
        stored_primary="graduation",
    )
    # 장학과 무관하게, 옛 규칙이 본문 키워드로 국제에 두었던 행사 공지. --only 밖이다.
    other_tab = _seed_notice(
        db, source, external_id="4",
        title="법과대학 학술 세미나 개최 안내", body="교환학생 경험을 나눕니다.",
        stored_primary="international",
    )
    # 이미 맞게 분류된 글은 건드리지 않는다.
    fine = _seed_notice(
        db, source, external_id="5",
        title="2026학년도 2학기 국가장학금 신청 안내", body="신청하세요.",
        stored_primary="scholarship",
    )
    return {"ta": ta, "bare": bare, "grad": grad, "other_tab": other_tab, "fine": fine}


def test_dry_run_counts_without_writing(db, seeded):
    stats = run_category_backfill(db, apply=False, sample_limit=10)
    assert stats["scanned"] == 5
    assert stats["changed"] == 4
    assert stats["unchanged"] == 1
    assert stats["transitions"]["scholarship -> career"] == 1
    assert stats["transitions"]["other -> scholarship"] == 1
    assert stats["transitions"]["graduation -> scholarship"] == 1
    assert stats["transitions"]["international -> event"] == 1
    assert {s["after"] for s in stats["samples"]} == {"career", "scholarship", "event"}
    # 저장하지 않았다.
    assert _primary(db, seeded["ta"]) == "scholarship"
    assert db.query(m.AuditLog).count() == 0


def test_only_scope_touches_just_that_tab(db, seeded):
    stats = run_category_backfill(
        db, apply=True, batch_id="b1", reason="장학 먼저", only_codes=frozenset({"scholarship"})
    )
    assert stats["changed"] == 3
    assert stats["skipped_outside_scope"] == 1
    assert _primary(db, seeded["ta"]) == "career"
    assert _primary(db, seeded["bare"]) == "scholarship"
    assert _primary(db, seeded["grad"]) == "scholarship"
    assert _primary(db, seeded["other_tab"]) == "international"
    assert _primary(db, seeded["fine"]) == "scholarship"


def test_apply_bumps_updated_at_and_rule_version(db, seeded):
    before = db.get(m.Notice, seeded["ta"]).updated_at
    run_category_backfill(db, apply=True, batch_id="b1", reason="검사")
    notice = db.get(m.Notice, seeded["ta"])
    assert notice.updated_at > before
    assert notice.derived_version.startswith("categories/2|")
    untouched = db.get(m.Notice, seeded["fine"])
    assert untouched.updated_at == before
    row = db.query(m.NoticeCategory).filter_by(notice_id=seeded["ta"], is_primary=True).one()
    assert row.category_code == "career"
    assert row.rule_version == "categories/2"
    assert row.rule_name == "title_keyword"
    logs = db.query(m.AuditLog).filter_by(action="notice.reclassify", run_id="b1").all()
    assert len(logs) == 4
    assert {log.before["primary"] for log in logs} == {"scholarship", "other", "graduation", "international"}


def test_revert_restores_previous_primary(db, seeded):
    run_category_backfill(db, apply=True, batch_id="b1", reason="검사")
    preview = revert_category_backfill(db, batch_id="b1", apply=False)
    assert preview["entries"] == 4 and preview["restored"] == 4
    assert _primary(db, seeded["ta"]) == "career"
    revert_category_backfill(db, batch_id="b1", apply=True)
    assert _primary(db, seeded["ta"]) == "scholarship"
    assert _primary(db, seeded["bare"]) == "other"
    assert _primary(db, seeded["grad"]) == "graduation"
    assert _primary(db, seeded["other_tab"]) == "international"
    assert revert_category_backfill(db, batch_id="없는묶음", apply=False)["entries"] == 0
