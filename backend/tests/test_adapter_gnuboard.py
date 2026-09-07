"""그누보드 어댑터 검사(21.1절 원문 구조 항목).

2026-09-06 에 받아 둔 실제 원문 표본으로 확인한다.
경희대 안에서 확인한 스킨은 표(law·swedu·khugpp), div-표(tourism), 옛 스킨(khugpp 상세)이다.
빈 게시판·회원 전용 글·구조 변경을 서로 구분해야 한다.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from app.domain.dates import KST
from app.ingestion import get_adapter
from app.ingestion.base import ListedItem, ParseError

LAW = {"base_url": "https://law.khu.ac.kr", "bo_table": "07_01_01"}
LAW_MEMBERS = {"base_url": "https://law.khu.ac.kr", "bo_table": "07_01_02_01"}
SWEDU = {"base_url": "https://swedu.khu.ac.kr", "bo_table": "07_01"}
TOURISM = {"base_url": "https://tourism.khu.ac.kr", "bo_table": "s4_1"}
KHUGPP = {"base_url": "https://khugpp.khu.ac.kr", "bo_table": "notice"}
MEDIA_EMPTY = {"base_url": "https://media.khu.ac.kr", "bo_table": "notice_01"}
KHUSM = {"base_url": "https://khusm.khu.ac.kr", "bo_table": "s6_1"}


@pytest.fixture
def adapter():
    return get_adapter("gnuboard")


def test_page_of_only_notices_pins_nothing(adapter, gnuboard_fixture):
    """줄마다 '공지' 를 단 게시판은 아무 줄도 고정으로 보지 않는다.

    2026-09-07 의과대학 게시판(khusm s6_1)은 4,392개 글 전부에 그누보드 공지 표시를
    달고 있었다. 고정 글은 수집 기간 밖이어도 상세를 받고 날짜 경계 판정에서도 빠지므로,
    그대로 믿으면 게시판 하나가 통째로 기간 제한을 벗어나 회차마다 시간 상한에 걸린다.
    실제로 그 게시판은 백필이 다섯 쪽에서 멈춘 채 열 시간을 헛돌았다.
    """
    page = adapter.parse_list(gnuboard_fixture("list_all_notice_khusm.html"), KHUSM, 1)

    assert len(page.items) == 15
    # 원문은 열다섯 줄 모두 tr.bo_notice · 번호 칸 '공지' 다.
    assert not any(item.is_pinned for item in page.items)
    assert page.has_next is True


def test_time_only_stamp_is_read_as_today(adapter, gnuboard_fixture):
    """등록일 칸에 시각만 있으면 오늘 올라온 글이다.

    그누보드는 그날 올라온 글의 등록일을 '10:24' 처럼 시각만 적는다. 날짜를 비워 두면
    그날의 새 공지가 발행일 모르는 글이 되어 공개 파일에서 빠지고, 목록 날짜 순서
    검증도 '날짜 모름' 으로 깨져 조기 종료 가정을 잃는다.
    """
    page = adapter.parse_list(gnuboard_fixture("list_all_notice_khusm.html"), KHUSM, 1)

    today = datetime.now(KST).date().isoformat()
    assert page.items[0].published_raw == f"{today} 10:24"
    # 날짜가 적힌 줄은 그대로 읽는다.
    assert page.items[1].published_raw == "2026-09-04"


def test_adapter_is_registered(adapter):
    assert adapter.name == "gnuboard"


def test_list_url_and_detail_url(adapter):
    assert adapter.list_url(LAW, 2) == (
        "https://law.khu.ac.kr/bbs/board.php?bo_table=07_01_01&page=2"
    )
    assert adapter.detail_url(LAW, "1320") == (
        "https://law.khu.ac.kr/bbs/board.php?bo_table=07_01_01&wr_id=1320"
    )


def test_allowed_hosts_limits_requests(adapter):
    assert adapter.allowed_hosts(LAW) == {"law.khu.ac.kr"}


# ---------------------------------------------------------------------- 목록
def test_parses_table_skin_list(adapter, gnuboard_fixture):
    page = adapter.parse_list(gnuboard_fixture("list_table_law.html"), LAW, 1)

    assert len(page.items) == 15
    first = page.items[0]
    assert first.external_id == "1314"
    assert first.title == "[공사] 제1법학관 등기구 교체에 따른 공사 안내"
    assert first.published_raw == "2026-08-26"
    assert first.author == "종합행정실"
    assert first.is_pinned is True
    assert first.url.endswith("bo_table=07_01_01&wr_id=1314")
    # 목록 아래에 다음 쪽이 있다.
    assert page.has_next is True


def test_parses_div_table_skin_list(adapter, gnuboard_fixture):
    """tourism 은 표 대신 div 로 짠 목록을 쓴다. 칸 이름도 col_ 로 다르다."""
    page = adapter.parse_list(gnuboard_fixture("list_divtable_tourism.html"), TOURISM, 1)

    assert len(page.items) == 15
    first = page.items[0]
    assert first.external_id == "385"
    assert first.published_raw == "2026-08-20"
    assert first.author == "전체관리자"


def test_parses_legacy_skin_list(adapter, gnuboard_fixture):
    """khugpp 는 tb_ 접두사를 쓰는 옛 표 스킨이다."""
    page = adapter.parse_list(gnuboard_fixture("list_legacy_khugpp.html"), KHUGPP, 1)

    assert len(page.items) == 2
    assert page.items[0].external_id == "17"
    assert page.items[0].published_raw == "2026-08-13"
    assert page.items[0].author == "국제시설운영팀"
    assert page.has_next is False


def test_empty_board_is_not_a_parse_error(adapter, gnuboard_fixture):
    """글이 없는 게시판은 실패가 아니라 0건이다(4절 8항)."""
    page = adapter.parse_list(gnuboard_fixture("list_empty_media.html"), MEDIA_EMPTY, 1)

    assert page.items == ()
    assert page.has_next is False


def test_other_boards_promo_rows_are_ignored(adapter, gnuboard_fixture):
    """빈 게시판 화면에도 다른 게시판을 끌어온 홍보 영역이 있다.

    게시판을 확인하지 않으면 남의 글을 이 출처의 공지로 주워 담는다.
    """
    html = gnuboard_fixture("list_empty_media.html")
    assert "bo_table=univJubo" in html  # 표본에 남의 글이 실제로 들어 있다.

    assert adapter.parse_list(html, MEDIA_EMPTY, 1).items == ()


def test_broken_structure_raises_parse_error(adapter):
    with pytest.raises(ParseError):
        adapter.parse_list("<html><body><p>안내</p></body></html>", LAW, 1)


# ---------------------------------------------------------------------- 상세
def test_parses_gnuboard5_detail(adapter, gnuboard_fixture):
    item = ListedItem(external_id="1320", url=adapter.detail_url(LAW, "1320"), title="목록 제목")
    detail = adapter.parse_detail(gnuboard_fixture("detail_gnuboard5_law.html"), LAW, item)

    assert detail.title == "Peace BAR Festival 2026 지구시민부스 안내"
    assert detail.author == "종합행정실"
    assert detail.published_raw is not None and detail.published_raw.startswith("2026-09-04")
    assert "Peace BAR Festival" in detail.body_text
    assert detail.extraction_notes["layout"] == "gnuboard5"


def test_detail_keeps_list_published_date(adapter, gnuboard_fixture):
    """목록에서 이미 안 날짜가 있으면 상세의 두 자리 연도 표기로 덮어쓰지 않는다."""
    item = ListedItem(
        external_id="2589",
        url=adapter.detail_url(SWEDU, "2589"),
        title="목록 제목",
        published_raw="2026-08-24",
    )
    detail = adapter.parse_detail(gnuboard_fixture("detail_gnuboard5_swedu.html"), SWEDU, item)

    assert detail.published_raw == "2026-08-24"
    assert detail.author == "SW중심대학"
    assert len(detail.body_text) > 100


def test_parses_legacy_detail_with_attachments(adapter, gnuboard_fixture):
    item = ListedItem(external_id="17", url=adapter.detail_url(KHUGPP, "17"), title="목록 제목")
    detail = adapter.parse_detail(gnuboard_fixture("detail_legacy_khugpp.html"), KHUGPP, item)

    assert detail.extraction_notes["layout"] == "default_view"
    # 클래스 이름이 t/top/cont 로 짧아 제목에 등록일이 섞이기 쉽다.
    assert "등록일" not in detail.title
    assert detail.title.startswith("[서울캠퍼스] 2026-2학기")
    assert detail.published_raw is not None and detail.published_raw.startswith("2026-08-13")
    assert len(detail.attachments) == 3
    assert detail.attachments[0].kind == "pdf"
    assert detail.attachments[0].filename.endswith(".pdf")
    assert detail.attachments[2].kind == "hwp"
    # 이 사이트의 내려받기 주소는 형제 도메인으로 나가고 만료되는 nonce 를 달고 있다.
    # 주소를 쓰려면 출처 설정에 allowed_hosts 로 gpp.khu.ac.kr 을 함께 적어야 한다.
    assert all(a.url and a.url.startswith("https://gpp.khu.ac.kr/") for a in detail.attachments)
    assert "nonce=" in detail.attachments[0].url


def test_members_only_post_is_reported_as_denied(adapter, gnuboard_fixture):
    """회원 전용 글은 구조 변경이 아니라 권한 문제로 구분한다."""
    item = ListedItem(external_id="3085", url="https://law.khu.ac.kr/x", title="목록 제목")

    with pytest.raises(ParseError, match="권한|로그인"):
        adapter.parse_detail(
            gnuboard_fixture("detail_members_only_law.html"), LAW_MEMBERS, item
        )
