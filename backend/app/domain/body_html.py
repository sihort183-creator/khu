"""원문 본문 HTML 을 화면에 붙여도 되는 형태로 정리한다.

화면은 이 HTML 을 그대로 문서에 넣는다. 원문은 우리가 통제하지 않는 남의 글이므로
그대로 넣으면 남의 코드가 우리 화면에서 도는 길이 열린다. <script> 는 이렇게 넣으면
실행되지 않지만 onerror 같은 속성은 실행된다. 그래서 허용 목록으로 다시 짓는다.

그림 주소는 중계 경로로 바꾼다. 원문 주소는 대부분 http 라 https 화면에서 막힌다.
"""

from __future__ import annotations

from urllib.parse import urljoin, urlsplit

from lxml import html as lxml_html

from app.domain.images import is_allowed_image_host, proxy_path

# 공지 본문에 실제로 쓰이는 것만 남긴다. 모르는 태그는 글자만 남기고 껍데기를 벗긴다.
ALLOWED_TAGS = {
    "p", "br", "div", "span", "strong", "b", "em", "i", "u", "s", "mark", "small", "sub", "sup",
    "ul", "ol", "li", "dl", "dt", "dd", "blockquote", "pre", "code",
    "h1", "h2", "h3", "h4", "h5", "h6", "hr",
    "table", "thead", "tbody", "tfoot", "tr", "th", "td", "caption", "colgroup", "col",
    "a", "img", "figure", "figcaption",
}
# 통째로 버리는 태그. 글자도 남기지 않는다.
DROPPED_TAGS = {"script", "style", "iframe", "object", "embed", "form", "input", "button", "noscript", "svg"}
ALLOWED_ATTRS = {
    "a": {"href", "title"},
    "img": {"alt", "title", "width", "height"},
    "td": {"colspan", "rowspan"},
    "th": {"colspan", "rowspan", "scope"},
    "col": {"span"},
    "colgroup": {"span"},
}
SAFE_LINK_SCHEMES = ("http://", "https://", "mailto:", "tel:")


def _clean_link(href: str, base_url: str | None) -> str | None:
    value = href.strip()
    if not value:
        return None
    absolute = urljoin(base_url, value) if base_url else value
    if not absolute.lower().startswith(SAFE_LINK_SCHEMES):
        # javascript: 와 data: 는 누르는 순간 남의 코드가 도는 길이다.
        return None
    if urlsplit(absolute).scheme == "http":
        # 화면이 https 라 http 링크는 경고가 뜬다. 주소만 올려 시도한다.
        return "https://" + absolute[len("http://"):]
    return absolute


def sanitize_body_html(body_html: str | None, *, base_url: str | None = None) -> str | None:
    """허용한 태그와 속성만 남기고, 그림은 중계 경로로 바꾼다.

    남길 것이 없으면 None 을 준다. 화면은 그때 본문 글자나 안내를 대신 보여준다.
    """
    if not body_html or not body_html.strip():
        return None
    try:
        root = lxml_html.fragment_fromstring(body_html, create_parent=True)
    except (ValueError, lxml_html.etree.ParserError):
        return None

    for element in list(root.iter()):
        tag = element.tag
        if not isinstance(tag, str):
            # 주석과 처리 지시문은 남기지 않는다.
            element.getparent().remove(element)
            continue
        if tag in DROPPED_TAGS:
            parent = element.getparent()
            if parent is not None:
                # 꼬리 글자는 형제 자리에 남아야 문장이 끊기지 않는다.
                if element.tail:
                    previous = element.getprevious()
                    if previous is not None:
                        previous.tail = (previous.tail or "") + element.tail
                    else:
                        parent.text = (parent.text or "") + element.tail
                parent.remove(element)
            continue
        if tag == "a":
            href = _clean_link(element.get("href") or "", base_url)
            keep = dict(element.attrib)
            element.attrib.clear()
            if href:
                element.set("href", href)
                element.set("target", "_blank")
                element.set("rel", "noopener noreferrer nofollow")
            if keep.get("title"):
                element.set("title", keep["title"])
            continue
        if tag == "img":
            src = (element.get("src") or "").strip()
            absolute = urljoin(base_url, src) if base_url else src
            keep = {k: v for k, v in element.attrib.items() if k in ALLOWED_ATTRS["img"]}
            element.attrib.clear()
            if not src or any(c in src for c in "<>\"'") or not is_allowed_image_host(absolute):
                # 보여줄 수 없는 그림이다. 자리만 없애고 대체 글자는 남긴다.
                element.tag = "span"
                if keep.get("alt"):
                    element.text = keep["alt"]
                continue
            element.set("src", proxy_path(absolute))
            element.set("loading", "lazy")
            for name, value in keep.items():
                if name != "alt" or value:
                    element.set(name, value)
            continue
        allowed = ALLOWED_ATTRS.get(tag, set())
        for name in list(element.attrib):
            if name not in allowed:
                element.attrib.pop(name)
        if tag not in ALLOWED_TAGS:
            element.tag = "span"

    cleaned = "".join(
        part for part in (
            root.text or "",
            *(lxml_html.tostring(child, encoding="unicode") for child in root),
        )
    ).strip()
    return cleaned or None
