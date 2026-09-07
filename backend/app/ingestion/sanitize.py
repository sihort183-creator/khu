"""본문 정리(8.1절·14절).

원문 HTML 은 비공개로 보관하고 사용자에게는 여기를 통과한 본문만 내보낸다.
스크립트·폼·삽입 프레임·이벤트 속성을 제거하고 링크 형식을 제한한다.
표·줄바꿈·목록·링크는 보존한다.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit

from lxml import etree
from lxml import html as lxml_html

from app.domain.urls import safe_split

# 허용 태그. 표와 목록은 공지에서 의미를 가지므로 남긴다.
ALLOWED_TAGS = {
    "p", "br", "span", "div", "strong", "b", "em", "i", "u", "s",
    "ul", "ol", "li", "a", "img",
    "table", "thead", "tbody", "tfoot", "tr", "th", "td", "caption", "colgroup", "col",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "blockquote", "pre", "code", "hr", "sup", "sub", "figure", "figcaption",
}

# 태그 자체와 그 안의 내용을 함께 버린다.
DROP_WITH_CONTENT = {"script", "style", "iframe", "frame", "frameset", "object", "embed", "form",
                     "input", "button", "select", "textarea", "noscript", "svg", "math", "template"}

ALLOWED_ATTRS = {
    "a": {"href", "title"},
    "img": {"src", "alt", "title"},
    "td": {"colspan", "rowspan"},
    "th": {"colspan", "rowspan", "scope"},
    "col": {"span"},
    "colgroup": {"span"},
}

SAFE_SCHEMES = {"http", "https", "mailto", "tel"}

BLOCK_TAGS = {
    "p", "div", "br", "li", "tr", "table", "h1", "h2", "h3", "h4", "h5", "h6",
    "blockquote", "pre", "ul", "ol", "hr", "figure", "caption",
}

_WS = re.compile(r"[ \t 　]+")
_BLANKS = re.compile(r"\n{3,}")


def _safe_url(value: str | None, *, base_url: str | None = None) -> str | None:
    if not value:
        return None
    url = value.strip()
    if not url:
        return None
    lowered = url.lower()
    if lowered.startswith(("javascript:", "data:", "vbscript:", "file:")):
        return None
    if url.startswith("//"):
        return f"https:{url}"
    if url.startswith(("http://", "https://", "mailto:", "tel:")):
        # 원문이 망가뜨린 주소(예: https://open.kakao.[EMAIL])는 읽을 수 없다.
        # 링크를 만들지 않고 넘어간다. 글 하나 때문에 회차가 죽으면 안 된다.
        parts = safe_split(url)
        if parts is None:
            return None
        return url if parts.scheme.lower() in SAFE_SCHEMES else None
    if base_url:
        parts = urlsplit(base_url)
        if url.startswith("/"):
            return urlunsplit((parts.scheme, parts.netloc, url, "", ""))
        return f"{base_url.rstrip('/')}/{url.lstrip('./')}"
    # 상대 주소인데 기준을 모르면 링크를 만들지 않는다.
    return None


def _strip_tag_keep_children(element) -> None:
    parent = element.getparent()
    if parent is None:
        return
    index = parent.index(element)
    text = element.text or ""
    if text:
        if index == 0:
            parent.text = (parent.text or "") + text
        else:
            prev = parent[index - 1]
            prev.tail = (prev.tail or "") + text
    for offset, child in enumerate(list(element)):
        parent.insert(index + offset, child)
    tail = element.tail or ""
    if tail:
        if len(parent) > index:
            node = parent[index + len(element) - 1] if len(element) else None
            if node is not None:
                node.tail = (node.tail or "") + tail
            else:
                parent.text = (parent.text or "") + tail
        else:
            parent.text = (parent.text or "") + tail
    parent.remove(element)


def sanitize_element(element, *, base_url: str | None = None):
    """복사본을 정리해서 돌려준다. 원본 트리는 건드리지 않는다."""
    root = etree.fromstring(etree.tostring(element), parser=lxml_html.HTMLParser())
    if root is None:  # pragma: no cover - 파서 방어
        return element

    for node in root.iter():
        if isinstance(node.tag, str) and node.tag.lower() in DROP_WITH_CONTENT:
            parent = node.getparent()
            if parent is not None:
                tail = node.tail or ""
                if tail:
                    if node.getprevious() is not None:
                        prev = node.getprevious()
                        prev.tail = (prev.tail or "") + tail
                    else:
                        parent.text = (parent.text or "") + tail
                parent.remove(node)

    for node in list(root.iter()):
        if not isinstance(node.tag, str):
            if node.getparent() is not None:  # 주석 제거
                node.getparent().remove(node)
            continue

        tag = node.tag.lower()
        if tag in ("html", "body"):
            continue

        if tag not in ALLOWED_TAGS:
            _strip_tag_keep_children(node)
            continue

        allowed = ALLOWED_ATTRS.get(tag, set())
        for attr in list(node.attrib):
            low = attr.lower()
            if low.startswith("on") or low not in allowed:
                del node.attrib[attr]

        if tag == "a":
            href = _safe_url(node.get("href"), base_url=base_url)
            if href is None:
                node.attrib.pop("href", None)
            else:
                node.set("href", href)
                node.set("rel", "noopener nofollow ugc")
                node.set("target", "_blank")
        elif tag == "img":
            src = _safe_url(node.get("src"), base_url=base_url)
            if src is None:
                _strip_tag_keep_children(node)
            else:
                node.set("src", src)
                node.set("loading", "lazy")

    return root


def clean_body_html(element, *, base_url: str | None = None) -> str:
    """사용자에게 내보낼 본문 HTML."""
    root = sanitize_element(element, base_url=base_url)
    body = root.find("body")
    target = body if body is not None else root
    chunks: list[str] = []
    if target.text and target.text.strip():
        chunks.append(target.text)
    for child in target:
        chunks.append(etree.tostring(child, encoding="unicode", method="html"))
    out = "".join(chunks).strip()
    out = _WS.sub(" ", out)
    return out


def html_to_text(element) -> str:
    """검색·요약용 일반 텍스트. 표와 줄바꿈 구조를 대략 보존한다."""
    root = sanitize_element(element)
    parts: list[str] = []

    def walk(node) -> None:
        if not isinstance(node.tag, str):
            return
        tag = node.tag.lower()
        if tag in DROP_WITH_CONTENT:
            return
        if tag == "br":
            parts.append("\n")
        if node.text:
            parts.append(node.text)
        for child in node:
            walk(child)
            if child.tail:
                parts.append(child.tail)
        if tag in BLOCK_TAGS:
            parts.append("\n")
        elif tag in ("td", "th"):
            parts.append("\t")

    walk(root)
    text = "".join(parts)
    text = text.replace(" ", " ").replace("　", " ")
    lines = [_WS.sub(" ", line).strip() for line in text.splitlines()]
    text = "\n".join(line for line in lines if line)
    return _BLANKS.sub("\n\n", text).strip()


def make_excerpt(body_text: str, limit: int = 180) -> str | None:
    """목록 카드용 본문 일부. 요약 생성문이 아니라 원문 앞부분이다."""
    if not body_text:
        return None
    flat = _WS.sub(" ", body_text.replace("\n", " ")).strip()
    if not flat:
        return None
    if len(flat) <= limit:
        return flat
    cut = flat[:limit]
    if " " in cut[limit // 2:]:
        cut = cut[: cut.rfind(" ")]
    return cut.rstrip() + "…"
