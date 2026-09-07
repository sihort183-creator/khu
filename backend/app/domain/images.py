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
import re
from urllib.parse import unquote, urljoin, urlsplit

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


def proxy_path(url: str, *, base: str = "") -> str:
    """원문 그림 주소를 조회 서버 중계 주소로 바꾼다.

    base 를 주면 절대 주소가 된다. 본문 HTML 안의 그림은 화면이 그대로 문서에 넣으므로
    상대 경로면 화면 쪽 주소로 붙어 깨진다. 그래서 내보낼 때 조회 서버 주소를 붙인다.
    """
    token = base64.urlsafe_b64encode(url.encode("utf-8")).decode("ascii").rstrip("=")
    return f"{base.rstrip('/')}{PROXY_PREFIX}{token}"


def extract_images(
    body_html: str | None, *, base_url: str | None = None, proxy_base: str = "",
) -> list[str]:
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
        found.append(proxy_path(absolute, base=proxy_base))
        if len(found) >= MAX_IMAGES:
            break
    return found


# 학교 통합 편집기가 그림을 올리는 경로. 마지막 앞 칸의 숫자가 올림 폴더 번호다.
_CROSS_PATH = re.compile(r"^/upload/cross/images/(\d+)/(.+)$")
# 편집기가 이름을 새로 지어 붙인 파일. 같은 그림이라도 올릴 때마다 값이 달라진다.
_CMS_FILENAME = re.compile(r"^\d{17}_[0-9a-z]{8}$")
# 같은 파일을 다시 올리면 편집기가 뒤에 _1 _2 를 붙인다. 그것만 떼어 낸다.
_COPY_SUFFIX = re.compile(r"(?:_\d{1,2})+$")
# 이름이 아니라 자리표시자다. 서로 다른 공지가 같은 값을 쓴다(실측: unnamed 65건, image001 48건).
_GENERIC_FILENAMES = frozenset({"unnamed", "image", "image001", "image002", "img", "poster", "본문"})


def poster_keys(body_html: str | None, *, base_url: str | None = None) -> frozenset[str]:
    """포스터 그림에서 묶음 열쇠를 뽑는다. 병합 근거가 아니라 보조 신호다.

    두 가지를 따로 낸다. 세기가 다르므로 섞어 쓰면 안 된다.

    - ``bucket:<호스트>/<폴더번호>`` — 올림 폴더 번호. **같은 포스터라는 뜻이 아니다.**
      실측으로 공지 7,671건이 폴더 462개에 들어가고 한 폴더에 서로 무관한 공지가
      20~60건씩 섞인다. 번호는 날짜순으로 커진다(000992=1월, 001064=4월, 001152=7월,
      001198=8월 말). 즉 이것은 "며칠 안에 같은 편집기로 올렸다"는 뜻일 뿐이다.
      제목이 완전히 같은 다른 출처 짝 6,214개 중 폴더가 같은 것은 2,100개(34%)뿐이다.
    - ``file:<호스트>/<폴더번호>/<이름>`` — 올린 파일 이름에서 복사본 꼬리(_1, _2)와
      확장자를 뗀 값. 학과가 같은 첨부를 그대로 다시 올리면 이 값이 같다
      (실측: 붙임4_학위지도교수_신청_안내문(학사공지) 와 ...(학사공지)_1 이 같은 공지).
      편집기가 새로 지은 이름과 unnamed·image001 같은 자리표시자는 뺀다.

    두 열쇠 모두 단독으로는 같은 공지라는 증거가 못 된다(9.1절).
    """
    keys: set[str] = set()
    for path in extract_images(body_html, base_url=base_url, proxy_base=""):
        # extract_images 는 중계 경로를 주므로 원래 주소로 되돌린다.
        token = path.removeprefix(PROXY_PREFIX)
        try:
            url = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            continue
        parts = urlsplit(url)
        matched = _CROSS_PATH.match(unquote(parts.path))
        if not matched:
            continue
        host, folder, filename = parts.hostname or "", matched.group(1), matched.group(2)
        keys.add(f"bucket:{host}/{folder}")
        stem = _COPY_SUFFIX.sub("", filename.rsplit(".", 1)[0]).strip().lower()
        if stem and len(stem) >= 6 and stem not in _GENERIC_FILENAMES and not _CMS_FILENAME.match(stem):
            keys.add(f"file:{host}/{folder}/{stem}")
    return frozenset(keys)


def poster_image(body_text: str | None, images: list[str]) -> str | None:
    """목록에 걸 대표 그림. 본문 글자가 사실상 없는 공지에만 준다.

    글자가 있는 공지는 발췌로 내용을 알 수 있으므로 목록 파일을 무겁게 하지 않는다.
    """
    if not images:
        return None
    if body_text and len(body_text.strip()) >= 80:
        return None
    return images[0]
