"""그누보드 게시판 어댑터.

경희대 사이트 가운데 공통 CMS 를 쓰지 않는 곳 상당수가 그누보드5 를 쓴다.
2026-09-06 실제 원문으로 확인한 곳: law(법학전문대학원), swedu(SW중심대학사업단),
media(미디어센터), tourism(관광대학원), khugpp(시설 대관).
스킨은 사이트마다 다르지만 뼈대 표식(td_subject, bo_v_title, bo_v_con)은 같다.

목록: GET {base}/bbs/board.php?bo_table={bo_table}&page={n}
      tr 안의 td_num2(번호 또는 '공지')·td_subject(제목)·td_name(작성자)
      ·td_datetime(등록일). 공지 행은 tr.bo_notice 다.
상세: GET {base}/bbs/board.php?bo_table={bo_table}&wr_id={wr_id}
      #bo_v_title(제목)·#bo_v_info(작성자·작성일)·#bo_v_con(본문)·#bo_v_file(첨부)

상세 주소가 그대로 목록 링크에 들어 있어 별도 POST 가 필요 없다.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qs, urlsplit, urlunsplit

from lxml import html as lxml_html

from app.ingestion.base import (
    Adapter,
    FetchedAttachment,
    FetchedDetail,
    ListedItem,
    ListPage,
    ParseError,
    register,
)
from app.ingestion.http import Fetcher
from app.ingestion.sanitize import clean_body_html, html_to_text

EXTRACTOR_VERSION = "gnuboard/1"

_WR_ID = re.compile(r"[?&]wr_id=(\d+)")
_DATE_TEXT = re.compile(r"\d{4}[-./]\d{1,2}[-./]\d{1,2}")
# 상세 화면은 두 자리 연도로 적는다: "작성일 26-09-04 09:04"
_SHORT_DATE = re.compile(r"(?<!\d)(\d{2})-(\d{2})-(\d{2})(?!\d)")
_INVISIBLE = re.compile("[﻿​-‍⁠]")

# 로그인·권한 안내로 되돌아가는 화면.
_DENIED_HINT = re.compile(r"로그인\s*(이|후)|권한이\s*없|비밀글")
# 글이 하나도 없는 게시판이 돌려주는 안내. 구조 변경과 구분하는 근거다.
_EMPTY_HINT = re.compile(r"게시물이\s*없|게시글이\s*없|등록된\s*게시물")

# 한 줄을 담는 그릇이 스킨마다 다르다. 2026-09-06 기준 세 가지를 확인했다.
#   표      : tr            (law, swedu, khugpp)
#   div-표  : div.div_tb_tr (tourism)
#   카드    : li.item       (media 홍보 영역)
_ROW_XPATH = (
    "//tr[.//a[{link}]]"
    " | //div[contains(@class,'div_tb_tr')][.//a[{link}]]"
    " | //li[contains(@class,'item')][.//a[{link}]]"
)

# 칸 이름도 스킨마다 다르다: td_subject / tb_subject / col_subject 처럼
# 접두사만 갈리므로 뒷말로 찾는다.
_CELL_ALIASES = {
    "subject": ("subject", "title"),
    "date": ("datetime", "date"),
    "name": ("name", "writer"),
    "writer": ("writer", "name"),
    "num": ("num", "no"),
    "category": ("category", "cate"),
}


def _link_condition(bo_table: str) -> str:
    """설정한 게시판의 글만 고른다.

    한 목록 화면에 다른 게시판을 끌어다 보여주는 홍보 영역이 있어서
    (media 의 캐러셀), 게시판을 확인하지 않으면 남의 글을 주워 담는다.
    """
    safe = bo_table.replace("'", "")
    return f"contains(@href,'bo_table={safe}&') and contains(@href,'wr_id=')"


def _has_class(name: str) -> str:
    """클래스 이름을 낱말 단위로 맞춘다. contains 는 top 안의 t 까지 잡는다."""
    return f"contains(concat(' ', normalize-space(@class), ' '), ' {name} ')"


def _cell(row, role: str):
    """행 안에서 뜻이 같은 칸을 찾는다. 스킨별 접두사를 따지지 않는다."""
    for alias in _CELL_ALIASES.get(role, (role,)):
        found = row.xpath(f".//*[contains(@class,'_{alias}')]")
        if found:
            return found[0]
    return None


def _clean(value: str | None) -> str:
    return re.sub(r"\s+", " ", _INVISIBLE.sub("", value or "")).strip()


def _text(node) -> str:
    return _clean(node.text_content()) if node is not None else ""


def _first(root, xpath: str):
    found = root.xpath(xpath)
    return found[0] if found else None


def _expand_year(value: str) -> str:
    """두 자리 연도 표기를 네 자리로 편다. 공통 날짜 해석기가 네 자리를 요구한다."""
    return _SHORT_DATE.sub(lambda m: f"20{m.group(1)}-{m.group(2)}-{m.group(3)}", value, count=1)


class GnuboardAdapter:
    """설정 키: base_url, bo_table, (선택) allowed_hosts."""

    name = "gnuboard"

    # ------------------------------------------------------------------ 설정
    @staticmethod
    def _require(config: dict[str, Any], key: str) -> str:
        value = str(config.get(key) or "").strip()
        if not value:
            raise ParseError(f"출처 설정에 {key} 가 없습니다.")
        return value

    def _base(self, config: dict[str, Any]) -> str:
        return self._require(config, "base_url").rstrip("/")

    def allowed_hosts(self, config: dict[str, Any]) -> set[str]:
        hosts = {(urlsplit(self._base(config)).hostname or "").lower()}
        for extra in config.get("allowed_hosts") or []:
            hosts.add(str(extra).lower())
        return {h for h in hosts if h}

    def list_url(self, config: dict[str, Any], page_index: int = 1) -> str:
        return (
            f"{self._base(config)}/bbs/board.php"
            f"?bo_table={self._require(config, 'bo_table')}&page={page_index}"
        )

    def detail_url(self, config: dict[str, Any], external_id: str) -> str:
        return (
            f"{self._base(config)}/bbs/board.php"
            f"?bo_table={self._require(config, 'bo_table')}&wr_id={external_id}"
        )

    # ------------------------------------------------------------------ 목록
    async def list_page(self, fetcher: Fetcher, config: dict[str, Any], page_index: int) -> ListPage:
        resp = await fetcher.get(
            self.list_url(config, page_index), allowed_hosts=self.allowed_hosts(config)
        )
        return self.parse_list(resp.text, config, page_index)

    def parse_list(self, text: str, config: dict[str, Any], page_index: int) -> ListPage:
        doc = lxml_html.fromstring(text)
        bo_table = self._require(config, "bo_table")
        rows = doc.xpath(_ROW_XPATH.format(link=_link_condition(bo_table)))

        if not rows:
            # 진짜 빈 게시판과 구조 변경을 구분한다(4절 8항).
            if _EMPTY_HINT.search(text):
                return ListPage(items=(), page_index=page_index, has_next=False)
            raise ParseError("그누보드 목록 구조를 찾지 못했습니다. 원문 구조 변경 가능성.")

        items: list[ListedItem] = []
        seen: set[str] = set()
        for row in rows:
            item = self._parse_row(row, config, bo_table)
            if item is not None and item.external_id not in seen:
                seen.add(item.external_id)
                items.append(item)

        if not items:
            raise ParseError(f"목록 행 {len(rows)}개를 읽었으나 항목을 하나도 추출하지 못했습니다.")

        return ListPage(
            items=tuple(items),
            page_index=page_index,
            has_next=self._has_next(doc, page_index),
            total_text=_text(_first(doc, "//*[contains(@class,'bo_fx')]//*[contains(text(),'Total')]"))
            or None,
        )

    def _parse_row(self, row, config: dict[str, Any], bo_table: str) -> ListedItem | None:
        link = _first(row, f".//a[{_link_condition(bo_table)}]")
        if link is None:
            return None
        match = _WR_ID.search((link.get("href") or "").replace("&amp;", "&"))
        if not match:
            return None
        external_id = match.group(1)

        subject = _cell(row, "subject")
        title = _text(subject) if subject is not None else _text(link)
        # 스킨에 따라 댓글 수가 제목 뒤에 붙는다: "제목 (3)"
        title = re.sub(r"\s*\(\d+\)\s*$", "", title).strip()
        if not title:
            return None

        # 날짜 칸이 있으면 그 칸만 읽는다. 칸이 없는 스킨에서만 행 전체를 훑는다.
        stamp = _cell(row, "date")
        stamp_text = _text(stamp) if stamp is not None else _text(row)
        found = _DATE_TEXT.search(stamp_text) or _SHORT_DATE.search(stamp_text)
        published_raw = _expand_year(found.group(0)) if found else None

        author_cell = _cell(row, "name")
        if author_cell is None:
            author_cell = _cell(row, "writer")
        author = _text(author_cell) if author_cell is not None else None

        return ListedItem(
            external_id=external_id,
            url=self.detail_url(config, external_id),
            title=title,
            published_raw=published_raw,
            board_category=_text(_cell(row, "category")) or None,
            author=author or None,
            is_pinned=self._is_pinned(row),
        )

    @staticmethod
    def _is_pinned(row) -> bool:
        if "bo_notice" in (row.get("class") or ""):
            return True
        # 번호 칸에 숫자 대신 '공지' 가 들어간 행이 상단 고정이다.
        number = _cell(row, "num")
        value = _text(number)
        return bool(value and not value.isdigit())

    @staticmethod
    def _has_next(doc, page_index: int) -> bool:
        numbers: list[int] = []
        for anchor in doc.xpath("//*[contains(@class,'pg_wrap') or contains(@class,'pg')]//a"):
            href = (anchor.get("href") or "").replace("&amp;", "&")
            page = parse_qs(urlsplit(href).query).get("page")
            if page and page[0].isdigit():
                numbers.append(int(page[0]))
        return bool(numbers) and max(numbers) > page_index

    # ------------------------------------------------------------------ 상세
    async def detail(self, fetcher: Fetcher, config: dict[str, Any], item: ListedItem) -> FetchedDetail:
        resp = await fetcher.get(item.url, allowed_hosts=self.allowed_hosts(config))
        return self.parse_detail(resp.text, config, item)

    def parse_detail(self, text: str, config: dict[str, Any], item: ListedItem) -> FetchedDetail:
        doc = lxml_html.fromstring(text)
        base_url = self._base(config)

        # 스킨이 둘이다. 도메인이 아니라 문서 구조로 고른다(5.2절).
        contents = _first(doc, "//*[@id='bo_v_con']")
        legacy = _first(doc, "//div[contains(@class,'default_view')]") if contents is None else None
        if contents is None and legacy is None:
            if _DENIED_HINT.search(text):
                raise ParseError("상세를 열 권한이 없거나 로그인이 필요한 게시글입니다.")
            raise ParseError("본문 영역을 찾지 못했습니다. 원문 구조 변경 가능성.")

        if contents is not None:
            layout = "gnuboard5"
            title = _text(_first(doc, "//*[@id='bo_v_title']")) or item.title
            info = _text(_first(doc, "//*[@id='bo_v_info']"))
            file_area = _first(doc, "//*[@id='bo_v_file']")
        else:
            layout = "default_view"
            # 이 스킨은 클래스 이름이 한 글자다(t, top, cont). contains 로 찾으면
            # 't' 가 'top' 까지 잡아 제목에 등록일이 섞인다. 토큰을 정확히 맞춘다.
            contents = _first(legacy, f".//div[{_has_class('cont')}]")
            if contents is None:
                raise ParseError("본문 영역(div.cont)을 찾지 못했습니다. 원문 구조 변경 가능성.")
            title = _text(_first(legacy, f".//div[{_has_class('t')}]")) or item.title
            info = _text(_first(legacy, f".//div[{_has_class('top')}]"))
            file_area = legacy

        published_raw = item.published_raw
        author = item.author
        if info:
            written = re.search(r"(?:작성일|등록일)\s*[:：]?\s*([\d.\-/]+(?:\s+[\d:]+)?)", info)
            if written and not published_raw:
                published_raw = _expand_year(written.group(1).strip())
            writer = re.search(r"작성자\s*[:：]?\s*(.+?)\s*(?:댓글|조회|작성일|등록일|$)", info)
            if writer and not author:
                author = _clean(writer.group(1))

        # 분류 표식은 두 스킨 모두 같은 클래스를 쓴다.
        category = item.board_category or _text(_first(doc, "//*[contains(@class,'bo_v_cate')]")) or None

        body_html = clean_body_html(contents, base_url=base_url)
        body_text = html_to_text(contents)
        attachments = self._attachments(file_area, base_url)

        return FetchedDetail(
            external_id=item.external_id,
            url=item.url,
            title=title,
            body_text=body_text,
            body_html=body_html,
            raw_html=text,
            published_raw=published_raw or None,
            board_category=category,
            author=author or None,
            attachments=attachments,
            extraction_notes={
                "extractor": EXTRACTOR_VERSION,
                "layout": layout,
                "body_chars": len(body_text),
                "attachment_count": len(attachments),
            },
        )

    def _attachments(self, area, base_url: str) -> tuple[FetchedAttachment, ...]:
        if area is None:
            return ()
        found: list[FetchedAttachment] = []
        seen: set[str] = set()
        # 그누보드5 는 첨부 목록을 따로 두고, 옛 스킨은 a.view_file_download 로 흩어 놓는다.
        anchors = area.xpath(".//a[contains(@class,'view_file_download')]") or area.xpath(".//a")
        for anchor in anchors:
            # 그누보드는 파일 이름과 내려받기 수를 한 덩어리로 적는다.
            raw = _text(anchor)
            filename = _clean(re.split(r"\s*\(\s*\d[\d.,]*\s*[KMG]?B", raw)[0])
            filename = re.sub(r"\s*\d+회\s*다운로드.*$", "", filename).strip()
            if not filename or filename in seen:
                continue
            seen.add(filename)

            href = (anchor.get("href") or "").strip()
            url = _absolute(base_url, href) if href and not href.lower().startswith("javascript:") else None

            size = None
            size_match = re.search(r"\(([\d.,]+)\s*(KB|MB|Bytes?)\)", raw, re.IGNORECASE)
            if size_match:
                try:
                    number = float(size_match.group(1).replace(",", ""))
                    unit = size_match.group(2).lower()
                    size = int(number * {"kb": 1024, "mb": 1024 * 1024}.get(unit, 1))
                except ValueError:
                    size = None

            kind = filename.rsplit(".", 1)[-1].lower() if "." in filename else None
            found.append(FetchedAttachment(filename=filename, url=url, kind=kind, size_bytes=size))
        return tuple(found)


def _absolute(base_url: str, href: str) -> str:
    if href.startswith(("http://", "https://")):
        return href
    parts = urlsplit(base_url)
    if href.startswith("//"):
        return f"{parts.scheme}:{href}"
    if href.startswith("/"):
        return urlunsplit((parts.scheme, parts.netloc, href, "", ""))
    return f"{base_url}/{href.lstrip('./')}"


adapter: Adapter = register(GnuboardAdapter())
