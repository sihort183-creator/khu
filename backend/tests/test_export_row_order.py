"""공개 파일 안 배열의 차례가 조회 계획에 흔들리지 않는지 검사한다.

왜 있나. 2026-09-13 그림자 대조가 25,655개 중 5개 파일에서 어긋났다
(docs/증분_대조_불일치_조사_2026-09-13.md). 자료가 다른 것이 아니라 **배열의 차례**만
달랐다. 공지 하나가 들고 있는 줄을 읽는 조회에 ORDER BY 가 없어서다.

    전체 회차  공지 전부를 읽으니 조건이 없다 -> 차례 훑기(Seq Scan) -> 물리적 차례
    증분 회차  바뀐 공지만 고른다(IN 목록)    -> 색인 훑기(Index Scan) -> 색인 차례

운영에서 실제로 갈라진 공지(ntc-3071cf54451e)는 주제가 대표=취업·인턴, 나머지가
졸업·학사였다. 넣은 차례는 (career, graduation, academic) 인데 색인은 코드 사전순
(academic, career, graduation) 이라, 전체는 [졸업, 학사] 를 증분은 [학사, 졸업] 을
내보냈다. 상세의 sources[] 는 전체 회차끼리도 갈라졌다. 작업 메모리가 2MB 라 해시
결합이 여러 판으로 쪼개지면서 회차마다 차례가 달라졌기 때문이다.

여기서 지키려는 것: **어떤 차례로 읽어 오든 나가는 파일은 같아야 한다.**
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.contracts import models as api
from app.contracts.vocab import CATEGORY_LABELS, MEDIUM_LABELS, coded
from app.export.static import _build_notice, _notice_detail
from app.storage import models as m

NOW = datetime(2026, 6, 1, 3, 0, tzinfo=UTC)


def _source_ref(source_id: str, name: str) -> api.SourceRef:
    return api.SourceRef(id=source_id, name=name, medium=api.Coded(**coded(MEDIUM_LABELS, "web")))


def _notice() -> m.Notice:
    return m.Notice(
        id="ntc-rich",
        primary_source_item_id="itm-0",
        status="visible",
        first_visible_at=NOW,
        updated_at=NOW,
        title="줄이 여럿인 공지",
        excerpt="발췌",
        published_date=NOW.date(),
        published_precision="date",
        audience_note=None,
        deadline_date=None,
        deadline_at=None,
        deadline_precision=None,
        deadline_evidence=None,
    )


def _item(item_id: str, source_id: str) -> m.SourceItem:
    return m.SourceItem(
        id=item_id,
        source_id=source_id,
        external_id=item_id,
        canonical_url=f"https://www.khu.ac.kr/{item_id}",
        current_revision_id=f"rev-{item_id}",
        original_status="available",
        is_pinned=False,
    )


# 운영에서 갈라진 공지의 주제 구성 그대로. 넣은 차례와 사전 차례가 다르다.
CATEGORY_ROWS = [
    m.NoticeCategory(id="cat-a", notice_id="ntc-rich", category_code="career", is_primary=True),
    m.NoticeCategory(id="cat-b", notice_id="ntc-rich", category_code="graduation", is_primary=False),
    m.NoticeCategory(id="cat-c", notice_id="ntc-rich", category_code="academic", is_primary=False),
]

AUDIENCE_ROWS = [
    (
        m.NoticeAudience(
            id="aud-a", notice_id="ntc-rich", audience_type="campus", campus_id="campus-seoul"
        ),
        "서울캠퍼스",
        None,
    ),
    (
        m.NoticeAudience(
            id="aud-b", notice_id="ntc-rich", audience_type="organization", organization_id="org-hq"
        ),
        None,
        "대학본부",
    ),
]


def _build(categories, audience_rows) -> api.Notice:
    return _build_notice(
        _notice(),
        _item("itm-0", "src-hq"),
        "본문",
        "<p>본문</p>",
        source_ref=_source_ref("src-hq", "대학본부 공지"),
        categories=list(categories),
        audience_rows=list(audience_rows),
        source_count=3,
        last_checked=NOW,
        now=NOW,
    )


# ------------------------------------------------------------------ 목록 항목


def test_secondary_categories_follow_the_vocabulary_order():
    """나머지 주제는 사전(CATEGORY_LABELS) 차례로 나간다. 화면 탭 차례와 같다."""
    notice = _build(CATEGORY_ROWS, AUDIENCE_ROWS)
    assert notice.primary_category.code == "career"
    assert [c.code for c in notice.secondary_categories] == ["academic", "graduation"]


def test_secondary_categories_ignore_the_order_rows_arrived_in():
    """줄이 어떤 차례로 들어오든 결과가 같아야 한다(차례 훑기 대 색인 훑기)."""
    expected = [c.code for c in _build(CATEGORY_ROWS, AUDIENCE_ROWS).secondary_categories]
    for shuffled in (
        list(reversed(CATEGORY_ROWS)),
        [CATEGORY_ROWS[2], CATEGORY_ROWS[0], CATEGORY_ROWS[1]],
        [CATEGORY_ROWS[1], CATEGORY_ROWS[2], CATEGORY_ROWS[0]],
    ):
        notice = _build(shuffled, AUDIENCE_ROWS)
        assert [c.code for c in notice.secondary_categories] == expected
        assert notice.primary_category.code == "career"


def test_unknown_category_codes_go_last_instead_of_crashing():
    rows = [
        *CATEGORY_ROWS,
        m.NoticeCategory(id="cat-z", notice_id="ntc-rich", category_code="zzz", is_primary=False),
    ]
    notice = _build(list(reversed(rows)), AUDIENCE_ROWS)
    assert [c.code for c in notice.secondary_categories] == ["academic", "graduation", "zzz"]


def test_audiences_put_the_narrower_target_first_whatever_the_row_order():
    """대상은 조직 -> 캠퍼스 -> 전체 -> 미정 차례다."""
    for rows in (AUDIENCE_ROWS, list(reversed(AUDIENCE_ROWS))):
        notice = _build(CATEGORY_ROWS, rows)
        assert [a.type for a in notice.audiences] == ["organization", "campus"]
        assert [a.name for a in notice.audiences] == ["대학본부", "서울캠퍼스"]


def test_every_category_code_in_the_vocabulary_has_a_place_in_the_order():
    """사전에 주제를 더하고 차례표를 잊으면 전부 맨 뒤로 몰린다. 그것을 막는다."""
    from app.export.static import CATEGORY_ORDER

    assert set(CATEGORY_ORDER) == set(CATEGORY_LABELS)


# ------------------------------------------------------------------ 상세


def _detail(links, attachments, mentions) -> api.NoticeDetail:
    base = _build(CATEGORY_ROWS, AUDIENCE_ROWS)
    return _notice_detail(
        None,
        base,
        "rev-itm-0",
        "본문",
        "<p>본문</p>",
        {
            "src-hq": _source_ref("src-hq", "대학본부 공지"),
            "src-1": _source_ref("src-1", "나 출처"),
            "src-2": _source_ref("src-2", "가 출처"),
        },
        attachments_by_revision={"rev-itm-0": list(attachments)},
        links_by_notice={"ntc-rich": list(links)},
        mentions_by_revision={"rev-itm-0": list(mentions)},
    )


LINK_ROWS = [
    (
        m.NoticeSource(id="lnk-0", notice_id="ntc-rich", source_item_id="itm-0", is_primary=True),
        _item("itm-0", "src-hq"),
        NOW.date(),
    ),
    (
        m.NoticeSource(id="lnk-1", notice_id="ntc-rich", source_item_id="itm-1", is_primary=False),
        _item("itm-1", "src-1"),
        NOW.date(),
    ),
    (
        m.NoticeSource(id="lnk-2", notice_id="ntc-rich", source_item_id="itm-2", is_primary=False),
        _item("itm-2", "src-2"),
        NOW.date(),
    ),
]

ATTACHMENT_ROWS = [
    m.Attachment(id="att-a", revision_id="rev-itm-0", filename="나중.pdf", status="listed"),
    m.Attachment(id="att-b", revision_id="rev-itm-0", filename="먼저.hwp", status="listed"),
]

MENTION_ROWS = [
    m.NoticeContactMention(
        id="men-a", revision_id="rev-itm-0", raw_text="02-961-1111",
        channel_kind="phone", value="02-961-1111",
    ),
    m.NoticeContactMention(
        id="men-b", revision_id="rev-itm-0", raw_text="02-961-0000",
        channel_kind="phone", value="02-961-0000",
    ),
]


def test_sources_put_the_primary_first_then_sort_by_source_name():
    detail = _detail(LINK_ROWS, ATTACHMENT_ROWS, MENTION_ROWS)
    assert [s.is_primary for s in detail.sources] == [True, False, False]
    assert [s.source.name for s in detail.sources] == ["대학본부 공지", "가 출처", "나 출처"]


@pytest.mark.parametrize("rotate", [0, 1, 2])
def test_sources_ignore_the_order_rows_arrived_in(rotate):
    """이것이 운영 상세 파일 3개가 회차마다 흔들린 자리다."""
    expected = [s.source_item_id for s in _detail(LINK_ROWS, [], []).sources]
    rotated = LINK_ROWS[rotate:] + LINK_ROWS[:rotate]
    assert [s.source_item_id for s in _detail(rotated, [], []).sources] == expected
    assert [s.source_item_id for s in _detail(list(reversed(rotated)), [], []).sources] == expected


def test_attachments_and_mentions_ignore_the_order_rows_arrived_in():
    forward = _detail(LINK_ROWS, ATTACHMENT_ROWS, MENTION_ROWS)
    backward = _detail(
        list(reversed(LINK_ROWS)), list(reversed(ATTACHMENT_ROWS)), list(reversed(MENTION_ROWS))
    )
    assert [a.filename for a in forward.attachments] == ["나중.pdf", "먼저.hwp"]
    assert [c.raw_text for c in forward.contact_mentions] == ["02-961-0000", "02-961-1111"]
    assert forward.model_dump() == backward.model_dump()


def test_the_whole_detail_is_byte_identical_whichever_order_rows_arrive_in():
    """마지막 관문. 내보내는 값 전체가 같아야 파일 해시가 같다."""
    import json

    def dump(detail: api.NoticeDetail) -> str:
        return json.dumps(detail.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)

    forward = dump(_detail(LINK_ROWS, ATTACHMENT_ROWS, MENTION_ROWS))
    backward = dump(
        _detail(
            list(reversed(LINK_ROWS)),
            list(reversed(ATTACHMENT_ROWS)),
            list(reversed(MENTION_ROWS)),
        )
    )
    assert forward == backward
