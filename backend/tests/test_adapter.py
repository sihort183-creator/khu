"""게시판 어댑터 검사(21.1절 원문 구조 항목).

실제 원문 표본으로 확인한다. 빈 목록과 분석 실패를 구분해야 한다.
"""

from __future__ import annotations

import pytest

from app.ingestion import get_adapter
from app.ingestion.base import ListedItem, ParseError

HQ = {"base_url": "https://www.khu.ac.kr", "prefix": "kor", "board_code": "BMSR00040", "menu_no": "200072"}
DEPT = {"base_url": "https://cs.khu.ac.kr", "prefix": "cs", "board_code": "BMSR00040", "menu_no": "12200006"}


@pytest.fixture
def adapter():
    return get_adapter("khu_board")


def test_parses_headquarters_list(adapter, board_fixture):
    page = adapter.parse_list(board_fixture("list_main_notice.html"), HQ, 1)

    assert len(page.items) == 6
    first = page.items[0]
    assert first.external_id == "322063"
    assert first.title == "2025학년도 결산 공고"
    assert first.published_raw == "2026-05-28"
    assert first.board_category == "공통"
    # 제목에서 분류 배지 문자열을 뺀다.
    assert "공통" not in first.title
    assert first.url.endswith("view.do?menuNo=200072&boardId=322063")


def test_parses_department_list(adapter, board_fixture):
    page = adapter.parse_list(board_fixture("list_dept_notice.html"), DEPT, 1)

    assert len(page.items) == 10
    assert page.items[0].external_id == "273194"
    assert page.items[0].published_raw == "2023-05-17"
    assert page.total_text and "10" in page.total_text


def test_parses_headquarters_detail(adapter, board_fixture):
    page = adapter.parse_list(board_fixture("list_main_notice.html"), HQ, 1)
    detail = adapter.parse_detail(board_fixture("detail_main_notice.html"), HQ, page.items[0])

    assert detail.title == "2025학년도 결산 공고"
    assert detail.author == "전체관리자"
    assert detail.published_raw == "2026-05-28"
    assert detail.extraction_notes["layout"] == "board02"
    assert "교비회계결산서" in detail.body_text
    # 원문 HTML 은 증거로 보관하고 사용자 본문은 따로 만든다.
    assert detail.raw_html != detail.body_html
    assert "<script" not in detail.body_html


def test_parses_department_detail(adapter, board_fixture):
    page = adapter.parse_list(board_fixture("list_dept_notice.html"), DEPT, 1)
    detail = adapter.parse_detail(board_fixture("detail_dept_notice.html"), DEPT, page.items[0])

    assert detail.extraction_notes["layout"] == "bbs-view"
    assert detail.author == "고객지원 통합관리자"
    assert detail.published_raw.startswith("2023-05-17")
    assert len(detail.body_text) > 50


def test_empty_board_is_not_a_parse_failure(adapter):
    """진짜 빈 게시판과 구조 변경을 구별한다(4절 8항)."""
    html = """
    <html><body><div class="bbs-list">
      <div class="bbs-total">전체 <strong>0</strong> 건</div>
      <div class="bbs_tbl-st1"><table><tbody></tbody></table></div>
    </div></body></html>
    """
    page = adapter.parse_list(html, DEPT, 1)
    assert page.items == ()
    assert page.has_next is False


def test_structure_change_raises_parse_error(adapter):
    html = "<html><body><h1>페이지를 찾을 수 없습니다</h1></body></html>"
    with pytest.raises(ParseError):
        adapter.parse_list(html, DEPT, 1)


def test_permission_denied_detail_raises_parse_error(adapter):
    """응답이 200이어도 권한 없음 페이지는 성공이 아니다."""
    html = """
    <html><head><script>
      var msg = "게시글에 대한 권한이 없습니다.";
      alert(msg);
    </script></head><body></body></html>
    """
    item = ListedItem(external_id="1", url="https://x/1", title="t")
    with pytest.raises(ParseError):
        adapter.parse_detail(html, DEPT, item)


def test_pinned_rows_detected(adapter):
    html = """
    <html><body><div class="bbs_tbl-st1"><table>
      <tbody id="noticeTbody">
        <tr><td>공지</td><td class="tal"><a href="javascript:view('900');">고정 공지</a></td><td>2026-01-02</td></tr>
      </tbody>
      <tbody>
        <tr><td>5</td><td class="tal"><a href="javascript:view('500');">일반 글</a></td><td>2026-01-01</td></tr>
      </tbody>
    </table></div></body></html>
    """
    page = adapter.parse_list(html, DEPT, 1)
    by_id = {i.external_id: i for i in page.items}
    assert by_id["900"].is_pinned is True
    assert by_id["500"].is_pinned is False


def test_pagination_detected(adapter):
    html = """
    <html><body><div class="bbs_tbl-st1"><table><tbody>
      <tr><td>1</td><td class="tal"><a href="javascript:view('1');">글</a></td><td>2026-01-01</td></tr>
    </tbody></table></div>
    <div class="pager"><a class="active">1</a><a>2</a><a>3</a></div></body></html>
    """
    assert adapter.parse_list(html, DEPT, 1).has_next is True
    assert adapter.parse_list(html, DEPT, 3).has_next is False


def test_allowed_hosts_limits_requests(adapter):
    assert adapter.allowed_hosts(HQ) == {"www.khu.ac.kr"}
    with_extra = dict(HQ, allowed_hosts=["cdn.khu.ac.kr"])
    assert adapter.allowed_hosts(with_extra) == {"www.khu.ac.kr", "cdn.khu.ac.kr"}


THUMB = {
    "base_url": "https://ee.khu.ac.kr",
    "prefix": "ee25",
    "board_code": "BMSR00044",
    "menu_no": "21700021",
}
GALLERY = {
    "base_url": "https://kbiz.khu.ac.kr",
    "prefix": "biz_kor",
    "board_code": "BMSR00045",
    "menu_no": "14500124",
}


def test_parses_thumbnail_gallery_list(adapter, board_fixture):
    """형식 C: ul.bbs-thumb. 표가 아니라 카드라 칸 위치로 날짜를 찾을 수 없다."""
    page = adapter.parse_list(board_fixture("list_thumb_notice.html"), THUMB, 1)

    assert len(page.items) == 8
    first = page.items[0]
    assert first.external_id == "531880"
    assert first.title == "시뮬레이션으로 뉴로모픽 디바이스 신뢰성 개선"
    assert first.published_raw == "2026-03-25"
    # 갤러리에는 상단 고정이 없다.
    assert not any(item.is_pinned for item in page.items)
    assert first.url.endswith("view.do?menuNo=21700021&boardId=531880")
    assert page.has_next is True


SPEECH = {
    "base_url": "https://news.khu.ac.kr",
    "prefix": "kor",
    "board_code": "BMSR00057",
    "menu_no": "200386",
}


def test_parses_list05_gallery_list(adapter, board_fixture):
    """형식 C의 또 다른 표기: ul.list05 안의 li.clearfix.

    news.khu.ac.kr 의 총장 연설문 게시판이 이 표기를 쓴다. li 를 class="item" 으로만
    찾던 옛 조건으로는 한 줄도 못 읽어 '구조 변경' 으로 잘못 실패했고, 그 게시판 두 곳이
    네 회차 연속 parse_error 로 delayed 까지 내려갔다. 목록 li 인지는 클래스가 아니라
    안에 게시글 열기 호출 view(...) 가 있는지로 가른다.
    """
    page = adapter.parse_list(board_fixture("list_speech_news.html"), SPEECH, 1)

    assert len(page.items) == 3
    first = page.items[0]
    assert first.external_id == "322887"
    assert first.title == "자신과 세계를 숙고(熟考)하는 지혜로운 실천 지성인의 길"
    assert first.published_raw == "2026-08-19"
    assert first.url.endswith("view.do?menuNo=200386&boardId=322887")
    # 한 쪽뿐인 게시판이다. 쪽 번호 1 만 있으면 다음 쪽이 없다.
    assert page.has_next is False
    # 메뉴의 view.do 링크를 글로 잘못 담지 않는다.
    assert {i.external_id for i in page.items} == {"322887", "321387", "321227"}


def test_parses_card_gallery_list(adapter, board_fixture):
    """형식 C의 다른 표기: div.bbs-gallery. 제목 앞에 BOM 이 섞여 온다."""
    page = adapter.parse_list(board_fixture("list_gallery_notice.html"), GALLERY, 1)

    assert len(page.items) == 9
    first = page.items[0]
    assert first.external_id == "552070"
    assert first.published_raw == "2026-09-03"
    # 보이지 않는 문자가 제목 앞에 남으면 화면에서 깨져 보인다.
    assert first.title.startswith("[인터뷰]")
    assert "\ufeff" not in first.title
