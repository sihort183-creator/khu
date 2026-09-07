"""쌓인 공지의 대상 범위를 저장된 값으로 다시 계산하는 작업 검사(8.2절).

2026-09-07 게시물이 캠퍼스를 언급하면 출처 조직을 통째로 버리던 규칙을 고쳤다.
고친 규칙은 새로 들어오는 글에만 걸리고, 이미 쌓인 공지는 상세 재확인이 돌아오는
최대 14일 뒤에야 반영된다. 이 작업이 그 기다림을 없앤다. 여기서 고정하는 것은
두 가지다. 학과 게시판 글이 캠퍼스를 언급해도 학과를 잃지 않는다는 것,
그리고 스스로 전교생 대상이라 밝힌 글은 다시 계산해도 그대로라는 것이다.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.domain.audiences import AudienceDecision, AudienceTarget
from app.domain.categories import classify
from app.domain.dates import NO_DEADLINE, parse_published
from app.ingestion.base import FetchedDetail, ListedItem
from app.run.collect import revert_audience_backfill, run_audience_backfill
from app.storage import models as m
from app.storage import repository as repo

CAMPUS_MENTION = "국제캠퍼스 국제교육원에서 진행합니다. 자세한 내용은 첨부를 보세요."
UNIVERSITY_WIDE = "전교생 대상입니다. 재학생 전원이 확인해야 합니다."


@pytest.fixture
def db(session_factory):
    with session_factory() as session:
        yield session
        session.commit()


@pytest.fixture
def source(db):
    db.add(m.University(id="univ-khu", code="khu", name="경희대학교"))
    db.flush()
    db.add(m.Campus(id="campus-global", university_id="univ-khu", code="global", name="국제캠퍼스"))
    db.add(
        m.Organization(
            id="org-swcon", university_id="univ-khu", org_type="department", name="소프트웨어융합학과"
        )
    )
    db.flush()
    src = m.Source(
        id="src-swcon",
        organization_id="org-swcon",
        name="소프트웨어융합학과 공지사항",
        adapter="khu_board",
        list_url="https://swcon.khu.ac.kr/list.do?menuNo=1",
        status="active",
    )
    db.add(src)
    db.flush()
    db.add(m.SourceHealth(source_id="src-swcon"))
    db.add(
        m.SourceAudience(
            id="sa-swcon", source_id="src-swcon", audience_type="organization", organization_id="org-swcon"
        )
    )
    db.flush()
    return src


def _seed_notice(db, source, *, external_id: str, title: str, body: str, targets: tuple) -> str:
    """대상만 옛 규칙대로 저장한 공지를 하나 만든다."""
    listed = ListedItem(
        external_id=external_id,
        url=f"https://swcon.khu.ac.kr/view.do?id={external_id}",
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
        category=classify(title, body, board_category=None),
        # 옛 규칙이 남긴 모양을 그대로 심는다. 여기가 다시 계산의 출발점이다.
        audience=AudienceDecision(targets=targets, note=None),
        deadline=NO_DEADLINE,
    )
    db.flush()
    return notice.id


def _keys(db, notice_id: str) -> set[str]:
    return repo.notice_audience_keys(db, [notice_id]).get(notice_id, set())


@pytest.fixture
def seeded(db, source):
    campus_only = _seed_notice(
        db,
        source,
        external_id="1",
        title="[홍보] [미래인재센터(국제)] 2026학년도 1학기 현장실습 안내",
        body=CAMPUS_MENTION,
        # 옛 규칙: 캠퍼스 언급을 만나면 출처 조직을 버렸다.
        targets=(AudienceTarget("campus", "campus-global", "국제캠퍼스"),),
    )
    university = _seed_notice(
        db,
        source,
        external_id="2",
        title="2026학년도 1학기 성적입력 및 공시(정정)기간 안내",
        body=UNIVERSITY_WIDE,
        targets=(AudienceTarget("university", None, "대학 전체"),),
    )
    plain = _seed_notice(
        db,
        source,
        external_id="3",
        title="학과 사무실 이전 안내",
        body="9월부터 사무실이 옮겨집니다.",
        targets=(AudienceTarget("organization", "org-swcon", "소프트웨어융합학과"),),
    )
    return {"campus_only": campus_only, "university": university, "plain": plain}


def test_dry_run_counts_changes_without_touching_stored_audiences(db, seeded):
    stats = run_audience_backfill(db, apply=False, sample_limit=5)

    assert stats["scanned"] == 3
    assert stats["changed"] == 1
    assert stats["unchanged"] == 2
    assert stats["transitions"] == {"campus -> campus+organization": 1}
    assert stats["applied"] is False
    # 모의 실행은 세기만 한다. 저장된 대상은 그대로다.
    assert _keys(db, seeded["campus_only"]) == {"campus:campus-global"}


def test_campus_mention_no_longer_erases_the_department(db, seeded):
    run_audience_backfill(db, apply=True, batch_id="b1", reason="검사")
    db.flush()

    # 학과를 되찾되 캠퍼스를 버리지 않는다. 다시 계산은 보태기만 한다.
    assert _keys(db, seeded["campus_only"]) == {"campus:campus-global", "org:org-swcon"}


def test_genuine_university_wide_notice_is_left_alone(db, seeded):
    run_audience_backfill(db, apply=True, batch_id="b1", reason="검사")
    db.flush()

    # 전교 공지를 학과 공지로 좁히면 고치기 전보다 나쁘다. 그대로 두는 것을 고정한다.
    assert _keys(db, seeded["university"]) == {"university"}
    assert _keys(db, seeded["plain"]) == {"org:org-swcon"}


def test_recompute_never_takes_a_target_away(db, seeded):
    before = {key: _keys(db, key) for key in seeded.values()}
    run_audience_backfill(db, apply=True, batch_id="b1", reason="검사")
    db.flush()

    for notice_id, old in before.items():
        assert old <= _keys(db, notice_id)


def test_applied_change_is_recorded_and_revertible(db, seeded):
    run_audience_backfill(db, apply=True, batch_id="b1", reason="검사")
    db.flush()

    logs = list(
        db.execute(
            select(m.AuditLog).where(
                m.AuditLog.action == "notice.reaudience", m.AuditLog.run_id == "b1"
            )
        ).scalars()
    )
    assert [log.target_id for log in logs] == [seeded["campus_only"]]
    assert logs[0].before == {"audiences": ["campus:campus-global"]}

    revert_audience_backfill(db, batch_id="b1", apply=True)
    db.flush()
    assert _keys(db, seeded["campus_only"]) == {"campus:campus-global"}


def test_revert_dry_run_reports_without_restoring(db, seeded):
    run_audience_backfill(db, apply=True, batch_id="b1", reason="검사")
    db.flush()

    stats = revert_audience_backfill(db, batch_id="b1", apply=False)

    assert stats == {"batch": "b1", "entries": 1, "restored": 1, "unusable": 0}
    assert _keys(db, seeded["campus_only"]) == {"campus:campus-global", "org:org-swcon"}


def test_second_pass_changes_nothing(db, seeded):
    run_audience_backfill(db, apply=True, batch_id="b1", reason="검사")
    db.flush()

    again = run_audience_backfill(db, apply=False)
    assert again["changed"] == 0
    assert again["unchanged"] == 3


def test_ops_command_applies_then_reverts(db, seeded, capsys):
    """운영 명령 경로로도 적용과 되돌리기가 끝까지 돈다."""
    from app.ops import cli as ops

    db.commit()
    assert ops.main(["notice", "reaudience", "--reason", "검사"]) == 0
    printed = capsys.readouterr().out
    batch = printed.rsplit("--revert ", 1)[1].strip().splitlines()[0]
    assert _keys(db, seeded["campus_only"]) == {"campus:campus-global", "org:org-swcon"}

    assert ops.main(["notice", "reaudience", "--revert", batch]) == 0
    db.expire_all()
    assert _keys(db, seeded["campus_only"]) == {"campus:campus-global"}
