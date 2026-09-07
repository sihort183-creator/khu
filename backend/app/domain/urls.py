"""주소 문자열을 예외 없이 다루는 도구.

원문 본문에는 사람이 잘못 적었거나 원문 사이트가 치환하다 망가뜨린 주소가 섞인다.
2026-09-07 연구처&산학협력단 공지 538796 번 글의 본문에는 카카오 오픈채팅 주소가
`https://open.kakao.[EMAIL]` 로 바뀐 채 들어 있었다. 원문 사이트의 이메일 가리기가
주소 뒷부분을 통째로 치환한 흔적이다.

`urllib.parse` 는 이런 값을 IPv6 주소로 잘못 읽고 `ValueError("Invalid IPv6 URL")` 을
던진다. 우리 코드가 그것을 잡지 않아 본문 정리 중에 예외가 위로 새고, 수집 회차가
`unexpected` 로 통째로 죽었다. 그 게시판은 네 회차 연속 실패해 `delayed` 로 내려갔다.
글 하나에 박힌 주소 하나가 게시판 하나를 세우게 두지 않는다.

그래서 이 모듈은 "읽을 수 없는 주소"를 예외가 아니라 None 으로 돌려준다. 부르는 쪽은
링크를 만들지 않고 넘어가면 된다.
"""

from __future__ import annotations

from urllib.parse import SplitResult, urljoin, urlsplit

__all__ = ["safe_join", "safe_split"]


def safe_split(url: str) -> SplitResult | None:
    """주소를 조각낸다. 읽을 수 없는 주소면 None 을 준다."""
    try:
        return urlsplit(url)
    except ValueError:
        return None


def safe_join(base: str | None, url: str) -> str | None:
    """상대 주소를 기준 주소에 붙인다. 읽을 수 없으면 None 을 준다."""
    if not base:
        return url or None
    try:
        return urljoin(base, url)
    except ValueError:
        return None
