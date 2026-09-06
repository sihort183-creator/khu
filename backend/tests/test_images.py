"""본문 그림을 공개 가능한 중계 경로로 바꾸는 규칙."""

import base64

from app.domain.images import (
    PROXY_PREFIX,
    extract_images,
    is_allowed_image_host,
    poster_image,
    proxy_path,
)

BASE = "https://com.khu.ac.kr/com/user/bbs/BMSR00040/view.do?boardId=1"


def decoded(path: str) -> str:
    token = path.removeprefix(PROXY_PREFIX)
    return base64.urlsafe_b64decode(token + "=" * ((4 - len(token) % 4) % 4)).decode()


def test_http_image_becomes_a_relay_path():
    """원문 그림은 대부분 http 다. 그대로 두면 https 화면에서 브라우저가 막는다."""
    html = '<p><img src="http://com.khu.ac.kr/upload/poster.jpg"></p>'
    images = extract_images(html, base_url=BASE)
    assert len(images) == 1
    assert images[0].startswith(PROXY_PREFIX)
    assert decoded(images[0]) == "http://com.khu.ac.kr/upload/poster.jpg"


def test_relative_source_is_resolved_against_the_original_page():
    html = '<img src="/upload/cross/images/001/poster.png">'
    assert decoded(extract_images(html, base_url=BASE)[0]) == (
        "https://com.khu.ac.kr/upload/cross/images/001/poster.png"
    )


def test_outside_hosts_and_embedded_data_are_not_relayed():
    """중계는 학교 주소로만 한다. 아무 주소나 받으면 남의 서버를 대신 때리는 통로가 된다."""
    html = (
        '<img src="https://evil.example.com/a.png">'
        '<img src="data:image/png;base64,AAAA">'
        '<img src="https://khu.ac.kr.evil.example.com/b.png">'
    )
    assert extract_images(html, base_url=BASE) == []
    assert is_allowed_image_host("https://khu.ac.kr/a.png")
    assert is_allowed_image_host("http://com.khu.ac.kr/a.png")
    assert not is_allowed_image_host("https://khu.ac.kr.evil.example.com/a.png")
    assert not is_allowed_image_host("file:///etc/passwd")


def test_order_is_kept_and_duplicates_are_dropped():
    html = (
        '<img src="http://a.khu.ac.kr/1.jpg">'
        '<img src="http://a.khu.ac.kr/2.jpg">'
        '<img src="http://a.khu.ac.kr/1.jpg">'
    )
    images = extract_images(html, base_url=BASE)
    assert [decoded(i) for i in images] == [
        "http://a.khu.ac.kr/1.jpg",
        "http://a.khu.ac.kr/2.jpg",
    ]


def test_poster_is_given_only_when_the_body_has_no_words():
    """글자가 있는 공지는 발췌로 내용을 알 수 있다. 목록 파일을 무겁게 하지 않는다."""
    images = [proxy_path("http://a.khu.ac.kr/1.jpg")]
    assert poster_image(None, images) == images[0]
    assert poster_image("   ", images) == images[0]
    assert poster_image("짧은 안내", images) == images[0]
    assert poster_image("가" * 200, images) is None
    assert poster_image(None, []) is None


def test_broken_or_empty_html_is_not_an_error():
    assert extract_images(None, base_url=BASE) == []
    assert extract_images("", base_url=BASE) == []
    assert extract_images("<img src=", base_url=BASE) == []
