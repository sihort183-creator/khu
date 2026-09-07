"""화면에 그대로 붙는 본문 HTML 을 안전하게 정리하는 규칙."""

from app.domain.body_html import sanitize_body_html

BASE = "https://com.khu.ac.kr/com/user/bbs/BMSR00040/view.do?boardId=1"


def clean(html: str) -> str:
    return sanitize_body_html(html, base_url=BASE) or ""


def test_scripts_and_event_handlers_are_removed():
    """화면이 이 HTML 을 문서에 그대로 넣는다. 남의 코드가 도는 길을 남기지 않는다."""
    html = (
        '<p onclick="steal()">안내</p>'
        '<script>steal()</script>'
        '<img src="x" onerror="steal()">'
        '<iframe src="https://evil.example.com"></iframe>'
    )
    result = clean(html)
    assert "onclick" not in result
    assert "onerror" not in result
    assert "steal" not in result
    assert "<script" not in result and "<iframe" not in result
    assert "안내" in result


def test_dangerous_link_schemes_are_dropped_but_text_stays():
    result = clean('<a href="javascript:steal()">누르기</a>')
    assert "javascript" not in result
    assert "누르기" in result


def test_safe_links_open_outside_without_leaking_the_page():
    result = clean('<a href="/notice/1">공지</a>')
    assert 'href="https://com.khu.ac.kr/notice/1"' in result
    assert 'rel="noopener noreferrer nofollow"' in result
    assert 'target="_blank"' in result


def test_school_images_become_relay_paths_and_others_become_text():
    result = clean(
        '<img src="http://com.khu.ac.kr/upload/poster.jpg" alt="포스터">'
        '<img src="https://evil.example.com/a.png" alt="밖의 그림">'
    )
    assert 'src="/v1/img/' in result
    assert "evil.example.com" not in result
    assert 'loading="lazy"' in result
    # 보여줄 수 없는 그림도 무엇이었는지는 남긴다.
    assert "밖의 그림" in result


def test_structure_that_notices_actually_use_survives():
    result = clean(
        "<h3>모집 안내</h3><ul><li>기간: 9월</li></ul>"
        "<table><tr><th scope='col'>구분</th><td colspan='2'>내용</td></tr></table>"
    )
    for kept in ("<h3>", "<ul>", "<li>", "<table>", 'scope="col"', 'colspan="2"'):
        assert kept in result


def test_unknown_tags_keep_their_words():
    result = clean("<marquee>지나가는 글</marquee>")
    assert "marquee" not in result
    assert "지나가는 글" in result


def test_text_around_a_removed_tag_is_not_lost():
    result = clean("<p>앞<script>x</script>뒤</p>")
    assert "앞" in result and "뒤" in result


def test_empty_input_gives_nothing_to_render():
    assert sanitize_body_html(None, base_url=BASE) is None
    assert sanitize_body_html("   ", base_url=BASE) is None
    assert sanitize_body_html("<script>x</script>", base_url=BASE) is None


def test_relay_base_is_used_inside_the_body():
    result = sanitize_body_html(
        '<img src="http://com.khu.ac.kr/a.png">', base_url=BASE,
        proxy_base="https://api.example.com",
    )
    assert 'src="https://api.example.com/v1/img/' in result


# 2026-09-07 연구처&산학협력단 공지 538796 번 글의 본문에 실제로 들어 있던 값이다.
# 원문 사이트의 이메일 가리기가 카카오 오픈채팅 주소 뒷부분을 통째로 치환했다.
# urllib 은 대괄호를 IPv6 주소로 읽어 ValueError("Invalid IPv6 URL") 를 던진다.
MANGLED = "https://open.kakao.[EMAIL]"


def test_url_that_urllib_cannot_read_drops_the_link_instead_of_raising():
    """읽을 수 없는 주소 하나가 본문 정리를 세우지 못하게 한다.

    이 값 하나 때문에 수집 회차가 'unexpected' 로 죽어 게시판 한 곳이 네 회차 연속
    실패하고 delayed 로 내려갔다. 내보내기도 같은 경로를 지난다.
    """
    result = clean(f'<p><a href="{MANGLED}">오픈채팅</a> 참여</p>')
    assert "open.kakao" not in result
    # 링크는 사라져도 글자는 남는다.
    assert "오픈채팅" in result and "참여" in result


def test_mangled_image_source_is_dropped_instead_of_raising():
    result = clean(f'<p><img src="{MANGLED}" alt="포스터"></p>')
    assert "open.kakao" not in result
    assert "포스터" in result
