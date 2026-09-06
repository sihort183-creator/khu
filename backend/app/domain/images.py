"""본문에 박힌 이미지를 공개 가능한 주소로 바꾼다.

공지의 절반 가까이가 본문 글자 없이 포스터 그림 한 장으로 되어 있다. 그림을
못 보여주면 그 공지는 제목만 남는다. 그런데 원문 그림 주소는 대부분 http 라서
https 인 화면에서 그대로 쓰면 브라우저가 막는다(mixed content).

그래서 조회 서버(Worker)가 중계할 경로를 만들어 내보낸다. 그림 파일 자체는
저장하지 않는다. 저장하면 용량 비용이 생기고, 원문이 살아 있는 한 중계로 충분하다.

경로는 조회 서버 기준 상대 경로다. 화면은 이미 조회 서버 주소를 알고 있으므로
그 앞에 붙여 쓴다. 어떤 주소를 중계할지는 이쪽과 Worker 가 같은 규칙으로 검사한다.
"""

from __future__ import annotations

import base64
from urllib.parse import urljoin, urlsplit

from lxml import html as lxml_html

# 중계는 학교 주소로만 한다. 아무 주소나 받으면 남의 서버를 대신 때리는 통로가 된다.
ALLOWED_SUFFIX = ".khu.ac.kr"
ALLOWED_HOST = "khu.ac.kr"
PROXY_PREFIX = "/v1/img/"
# 한 공지에서 내보내는 그림 수. 목록형 게시글이 그림을 수십 장 달기도 한다.
MAX_IMAGES = 12


def is_allowed_image_host(url: str) -> bool:
    """중계해도 되는 주소인지 본다. Worker 의 검사와 같은 규칙이어야 한다."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        return False
    host = parts.hostname or ""
    return host == ALLOWED_HOST or host.endswith(ALLOWED_SUFFIX)


def proxy_path(url: str) -> str:
    """원문 그림 주소를 조회 서버 중계 경로로 바꾼다."""
    token = base64.urlsafe_b64encode(url.encode("utf-8")).decode("ascii").rstrip("=")
    return f"{PROXY_PREFIX}{token}"


def extract_images(body_html: str | None, *, base_url: str | None = None) -> list[str]:
    """본문에서 그림 주소를 찾아 중계 경로로 돌려준다.

    상대 주소는 원문 주소를 기준으로 절대 주소로 만든 뒤 검사한다. 학교 밖 주소와
    data: 같은 내장 그림은 내보내지 않는다. 순서는 본문에 나온 순서를 지킨다.
    """
    if not body_html:
        return []
    try:
        root = lxml_html.fragment_fromstring(body_html, create_parent=True)
    except (ValueError, lxml_html.etree.ParserError):
        return []

    found: list[str] = []
    seen: set[str] = set()
    for element in root.iter("img"):
        src = (element.get("src") or "").strip()
        # 깨진 태그를 lxml 이 복구하면 "</body" 같은 값이 src 로 들어온다. 그대로 이어
        # 붙이면 학교 주소로 보이는 쓰레기 경로가 만들어지므로 주소가 아닌 값은 버린다.
        if not src or any(c in src for c in "<>\"'") or any(c.isspace() for c in src):
            continue
        absolute = urljoin(base_url, src) if base_url else src
        if not is_allowed_image_host(absolute) or absolute in seen:
            continue
        seen.add(absolute)
        found.append(proxy_path(absolute))
        if len(found) >= MAX_IMAGES:
            break
    return found


def poster_image(body_text: str | None, images: list[str]) -> str | None:
    """목록에 걸 대표 그림. 본문 글자가 사실상 없는 공지에만 준다.

    글자가 있는 공지는 발췌로 내용을 알 수 있으므로 목록 파일을 무겁게 하지 않는다.
    """
    if not images:
        return None
    if body_text and len(body_text.strip()) >= 80:
        return None
    return images[0]
