"""본문 그림을 공개 가능한 중계 경로로 바꾸는 규칙."""

import base64

from app.domain.images import (
    PROXY_PREFIX,
    extract_images,
    is_allowed_image_host,
    poster_image,
    poster_keys,
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


def test_relay_base_makes_the_address_absolute():
    """본문 HTML 안의 그림은 화면이 그대로 문서에 넣는다. 상대 경로면 화면 쪽 주소로 붙어 깨진다."""
    images = extract_images(
        '<img src="http://a.khu.ac.kr/1.jpg">', base_url=BASE,
        proxy_base="https://api.example.com/",
    )
    assert images[0].startswith("https://api.example.com/v1/img/")


# ------------------------------------------------------------- 포스터 묶음 열쇠

CROSS = "http://com.khu.ac.kr/upload/cross/images/001187/"


def test_poster_keys_separate_upload_bucket_from_file_name():
    """폴더 번호와 파일 이름은 세기가 다르므로 따로 낸다."""
    html = f'<img src="{CROSS}%EB%B6%99%EC%9E%844_%EC%95%88%EB%82%B4%EB%AC%B8.png">'
    keys = poster_keys(html, base_url=BASE)
    assert "bucket:com.khu.ac.kr/001187" in keys
    assert "file:com.khu.ac.kr/001187/붙임4_안내문" in keys


def test_reuploaded_copy_of_the_same_file_gets_the_same_key():
    """학과가 같은 첨부를 다시 올리면 편집기가 _1 을 붙인다. 그것은 같은 파일이다."""
    first = poster_keys(f'<img src="{CROSS}notice_poster.png">', base_url=BASE)
    second = poster_keys(f'<img src="{CROSS}notice_poster_1.png">', base_url=BASE)
    assert first & second


def test_editor_generated_and_placeholder_names_make_no_file_key():
    """편집기가 새로 지은 이름과 unnamed 는 서로 다른 공지가 같이 쓴다.

    실측으로 unnamed 가 65건, image001 이 48건이었다. 파일 이름 열쇠로 쓰면 안 된다.
    """
    for name in ("20260820091521570_7DEJ8I8D.png", "unnamed.jpg", "image001.jpg"):
        keys = poster_keys(f'<img src="{CROSS}{name}">', base_url=BASE)
        assert keys == {"bucket:com.khu.ac.kr/001187"}


def test_non_cross_images_and_outside_hosts_make_no_poster_key():
    assert poster_keys('<img src="https://www.khu.ac.kr/crosseditor/images/emoticon/a.gif">',
                       base_url=BASE) == frozenset()
    assert poster_keys('<img src="https://evil.example.com/upload/cross/images/001/x.png">',
                       base_url=BASE) == frozenset()
    assert poster_keys(None) == frozenset()


MANGLED = "https://open.kakao.[EMAIL]"


def test_unreadable_url_is_refused_instead_of_raising():
    """urllib 이 읽지 못하는 주소도 예외 없이 거절한다(연구처 공지 538796)."""
    assert is_allowed_image_host(MANGLED) is False
    assert extract_images(f'<p><img src="{MANGLED}"></p>', base_url=BASE) == []
    assert poster_keys(f'<p><img src="{MANGLED}"></p>', base_url=BASE) == set()
