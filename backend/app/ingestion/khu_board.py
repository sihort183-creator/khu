"""경희대 공통 게시판 어댑터.

2026-09-06 실제 원문으로 확인했다. 같은 게시판 엔진이지만 화면 표시 형식이 둘이다.
도메인별 조건문을 늘리지 않고 문서 구조로 형식을 판정한다(5.2절).

목록: GET  {base}/{prefix}/user/bbs/{board}/list.do?menuNo={menuNo}&pageIndex={n}
      형식 A(본부): table.board01 tbody tr, a[href="javascript:view('<id>','<cat>')"]
      형식 B(학과): div.bbs_tbl-st1 table tbody tr, a[href="javascript:view('<id>')"]
      형식 C(갤러리): ul.bbs-thumb 또는 div.bbs-gallery 안의 li.item
                      — strong.t(제목)·span.date(등록일), 표가 아니라 카드다.
      표 형식 둘은 tbody#noticeTbody 안의 행이 상단 고정 공지다. 갤러리에는 고정이 없다.

상세: POST {base}/{prefix}/user/bbs/{board}/view.do  (menuNo, boardId, catId, pageIndex)
      형식 A: div.board02 — p.txt06(제목)·span.txtBox01(분류)·span.date·.txtWriter
                            ·div.row.contents(본문)·div.row.addFile(첨부)
      형식 B: article.bbs-view — header .t(제목)·.info(등록일·작성자)·.bbs-view_c(본문)
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit

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

EXTRACTOR_VERSION = "khu_board/3"

_VIEW_CALL = re.compile(r"view\(\s*'([^']*)'(?:\s*,\s*'([^']*)')?\s*\)")
_DATE_TEXT = re.compile(r"\d{4}[-./]\d{1,2}[-./]\d{1,2}")
_COUNT_ONLY = re.compile(r"^[\d,]+$")

# 권한 없음·로그인 요구 페이지는 짧은 스크립트 문서로 돌아온다.
_DENIED_HINT = re.compile(r"권한이\s*없|로그인이\s*필요|비공개\s*게시물")


# BOM·제로폭 문자. 일부 게시글 제목 앞에 섞여 들어온다.
_INVISIBLE = re.compile("[\ufeff\u200b-\u200d\u2060]")


def _clean(value: str | None) -> str:
    return re.sub(r"\s+", " ", _INVISIBLE.sub("", value or "")).strip()


def _text(node) -> str:
    return _clean(node.text_content()) if node is not None else ""


def _first(root, xpath: str):
    found = root.xpath(xpath)
    return found[0] if found else None


class KhuBoardAdapter:
    """설정 키: base_url, prefix, board_code, menu_no, (선택) allowed_hosts."""

    name = "khu_board"

    # ------------------------------------------------------------------ 설정
    @staticmethod
    def _require(config: dict[str, Any], key: str) -> str:
        value = str(config.get(key) or "").strip()
        if not value:
            raise ParseError(f"출처 설정에 {key} 가 없습니다.")
        return value

    def _base(self, config: dict[str, Any]) -> str:
        return self._require(config, "base_url").rstrip("/")

    def _dir(self, config: dict[str, Any]) -> str:
        prefix = self._require(config, "prefix").strip("/")
        board = self._require(config, "board_code")
        return f"{self._base(config)}/{prefix}/user/bbs/{board}"

    def allowed_hosts(self, config: dict[str, Any]) -> set[str]:
        hosts = {(urlsplit(self._base(config)).hostname or "").lower()}
        for extra in config.get("allowed_hosts") or []:
            hosts.add(str(extra).lower())
        return {h for h in hosts if h}

    def list_url(self, config: dict[str, Any], page_index: int = 1) -> str:
        url = f"{self._dir(config)}/list.do?menuNo={self._require(config, 'menu_no')}&pageIndex={page_index}"
        count = config.get("user_display_count")
        if count in (10, 20, 30, 40, 50):
            url += f"&userDisplayCount={count}"
        return url

    def detail_url(self, config: dict[str, Any], external_id: str) -> str:
        """사용자에게 보여줄 안정 주소. 실제 요청은 POST 지만 주소 형식은 고정한다."""
        return f"{self._dir(config)}/view.do?menuNo={self._require(config, 'menu_no')}&boardId={external_id}"

    # ------------------------------------------------------------------ 목록
    async def list_page(self, fetcher: Fetcher, config: dict[str, Any], page_index: int) -> ListPage:
        resp = await fetcher.get(self.list_url(config, page_index), allowed_hosts=self.allowed_hosts(config))
        return self.parse_list(resp.text, config, page_index)

    def parse_list(self, text: str, config: dict[str, Any], page_index: int) -> ListPage:
        doc = lxml_html.fromstring(text)

        rows = doc.xpath("//tr[.//a[contains(@href,'view(')]]")
        # 표가 없으면 갤러리 형식을 본다. 도메인이 아니라 문서 구조로 고른다.
        cards = (
            doc.xpath("//li[contains(@class,'item')][.//a[contains(@href,'view(')]]")
            if not rows
            else []
        )
        if not rows and not cards:
            # 진짜 빈 목록과 구조 변경을 구분한다(4절 8항).
            container = doc.xpath(
                "//*[contains(@class,'bbs-list') or contains(@class,'bbs_tbl')"
                " or contains(@class,'board01') or contains(@class,'bbs-total')]"
            )
            if container:
                return ListPage(
                    items=(),
                    page_index=page_index,
                    has_next=False,
                    total_text=_text(_first(doc, "//*[contains(@class,'bbs-total')]")) or None,
                )
            raise ParseError("게시판 목록 구조를 찾지 못했습니다. 원문 구조 변경 가능성.")

        items: list[ListedItem] = []
        for node in rows or cards:
            item = self._parse_row(node, config) if rows else self._parse_card(node, config)
            if item is not None:
                items.append(item)

        if not items:
            raise ParseError(
                f"목록 행 {len(rows) or len(cards)}개를 읽었으나 항목을 하나도 추출하지 못했습니다."
            )

        return ListPage(
            items=tuple(items),
            page_index=page_index,
            has_next=self._has_next(doc, page_index),
            total_text=_text(_first(doc, "//*[contains(@class,'bbs-total')]")) or None,
        )

    def _parse_row(self, row, config: dict[str, Any]) -> ListedItem | None:
        link = _first(row, ".//a[contains(@href,'view(')]")
        if link is None:
            return None
        match = _VIEW_CALL.search(link.get("href") or "")
        if not match:
            return None
        external_id = (match.group(1) or "").strip()
        cat_id = (match.group(2) or "").strip()
        if not external_id:
            return None

        badge = _first(link, ".//span[contains(@class,'txtBox01')]")
        board_category = _text(badge) or None

        title = _text(link)
        if board_category and title.startswith(board_category):
            title = title[len(board_category) :].strip()
        if not title:
            return None

        cells = row.xpath("./td")
        published_raw = None
        for cell in cells:
            value = _text(cell)
            if _DATE_TEXT.fullmatch(value):
                published_raw = value

        author = None
        for cell in cells[1:-1]:
            value = _text(cell)
            if not value or _DATE_TEXT.fullmatch(value) or _COUNT_ONLY.match(value):
                continue
            if cell is not None and "tal" in (cell.get("class") or ""):
                continue
            if len(value) <= 40:
                author = value

        return ListedItem(
            external_id=external_id,
            url=self.detail_url(config, external_id),
            title=title,
            published_raw=published_raw,
            board_category=board_category,
            author=author,
            is_pinned=self._is_pinned(row, cells),
            detail_hint={"catId": cat_id},
        )

    def _parse_card(self, card, config: dict[str, Any]) -> ListedItem | None:
        """갤러리 형식의 카드 하나를 읽는다. 표 형식과 달리 칸이 없어 클래스로 찾는다."""
        link = _first(card, ".//a[contains(@href,'view(')]")
        if link is None:
            return None
        match = _VIEW_CALL.search(link.get("href") or "")
        if not match:
            return None
        external_id = (match.group(1) or "").strip()
        cat_id = (match.group(2) or "").strip()
        if not external_id:
            return None

        title = _text(_first(card, ".//*[contains(@class,'t')][self::strong or self::span]"))
        if not title:
            return None

        published_raw = None
        for node in card.xpath(".//*[contains(@class,'date')]"):
            found = _DATE_TEXT.search(_text(node))
            if found:
                published_raw = found.group(0)
                break

        return ListedItem(
            external_id=external_id,
            url=self.detail_url(config, external_id),
            title=title,
            published_raw=published_raw,
            board_category=None,
            author=None,
            is_pinned=False,
            detail_hint={"catId": cat_id},
        )

    @staticmethod
    def _is_pinned(row, cells) -> bool:
        parent = row.getparent()
        if parent is not None and (parent.get("id") or "") == "noticeTbody":
            return True
        if row.xpath(".//*[contains(@class,'ico_notice') or contains(@class,'notice')]"):
            return True
        if cells:
            first = _text(cells[0])
            if first and not first.replace(",", "").isdigit():
                return True
        return False

    def _has_next(self, doc, page_index: int) -> bool:
        numbers: list[int] = []
        for anchor in doc.xpath(
            "//*[contains(@class,'pager') or contains(@class,'paging') or contains(@class,'pagination')]//a"
        ):
            value = _text(anchor)
            if value.isdigit():
                numbers.append(int(value))
            # 숫자 묶음 마지막(예: 10쪽)에서도 다음 묶음·끝 링크의 실제
            # 목적 페이지를 읽는다. 숫자 라벨만 보면 11쪽 이후를 누락한다.
            target = " ".join((anchor.get("href") or "", anchor.get("onclick") or ""))
            for match in re.finditer(r"(?:fnSubmitForm|linkPage|searchNc)\(\s*['\"]?(\d+)", target):
                numbers.append(int(match.group(1)))
        if numbers:
            return max(numbers) > page_index
        return bool(
            doc.xpath(
                "//*[contains(@class,'pager') or contains(@class,'paging')]"
                "//a[contains(@class,'next') or contains(@class,'last')]"
            )
        )

    # ------------------------------------------------------------------ 상세
    async def detail(self, fetcher: Fetcher, config: dict[str, Any], item: ListedItem) -> FetchedDetail:
        form = {
            "menuNo": self._require(config, "menu_no"),
            "boardId": item.external_id,
            "catId": item.detail_hint.get("catId", ""),
            "pageIndex": "1",
        }
        resp = await fetcher.post_form(
            f"{self._dir(config)}/view.do", form, allowed_hosts=self.allowed_hosts(config)
        )
        return self.parse_detail(resp.text, config, item)

    def parse_detail(self, text: str, config: dict[str, Any], item: ListedItem) -> FetchedDetail:
        doc = lxml_html.fromstring(text)
        base_url = self._base(config)

        board = _first(doc, "//div[contains(@class,'board02')]")
        article = _first(doc, "//*[contains(@class,'bbs-view')]")

        if board is None and article is None:
            if _DENIED_HINT.search(text) or (len(text) < 6000 and "alert(" in text):
                raise ParseError("상세를 열 권한이 없거나 로그인이 필요한 게시글입니다.")
            raise ParseError("상세 본문 영역을 찾지 못했습니다. 원문 구조 변경 가능성.")

        if board is not None:
            parsed = self._parse_layout_a(board, item, base_url)
        else:
            parsed = self._parse_layout_b(article, item, base_url)

        title, badge, published_raw, author, contents, attachments = parsed
        if contents is None:
            raise ParseError("본문 영역을 찾지 못했습니다.")

        body_html = clean_body_html(contents, base_url=base_url)
        body_text = html_to_text(contents)

        return FetchedDetail(
            external_id=item.external_id,
            url=item.url,
            title=title or item.title,
            body_text=body_text,
            body_html=body_html,
            raw_html=text,
            published_raw=published_raw or item.published_raw or None,
            board_category=badge or item.board_category or None,
            author=author or item.author or None,
            attachments=attachments,
            extraction_notes={
                "extractor": EXTRACTOR_VERSION,
                "layout": "board02" if board is not None else "bbs-view",
                "body_chars": len(body_text),
                "attachment_count": len(attachments),
            },
        )

    def _parse_layout_a(self, board, item: ListedItem, base_url: str):
        title = _text(_first(board, ".//div[contains(@class,'tit')]//p[contains(@class,'txt06')]"))
        if not title:
            title = _text(_first(board, ".//div[contains(@class,'tit')]"))
        badge = _text(_first(board, ".//span[contains(@class,'txtBox01')]")) or item.board_category
        if badge and title.startswith(badge):
            title = title[len(badge) :].strip()

        published_raw = _text(_first(board, ".//span[contains(@class,'date')]"))
        author = _text(_first(board, ".//div[contains(@class,'txtWriter')]"))
        contents = _first(board, ".//div[contains(@class,'contents')]")
        attachments = self._attachments(_first(board, ".//div[contains(@class,'addFile')]"), base_url)
        return title, badge, published_raw, author, contents, attachments

    def _parse_layout_b(self, article, item: ListedItem, base_url: str):
        title = _text(_first(article, ".//header//*[contains(@class,'t')]"))
        if not title:
            title = _text(_first(article, ".//h2"))
        badge = item.board_category

        published_raw = ""
        author = ""
        for node in article.xpath(".//*[contains(@class,'info')]//*[self::div or self::li]"):
            label = _text(_first(node, ".//strong"))
            value = _clean(_text(node)[len(label) :]) if label else ""
            if not value:
                continue
            if "등록" in label or "작성일" in label:
                published_raw = value
            elif "작성자" in label:
                author = value

        contents = _first(article, ".//*[contains(@class,'bbs-view_c')]")
        file_area = _first(article, ".//*[contains(@class,'file')]")
        attachments = self._attachments(file_area, base_url)
        return title, badge, published_raw, author, contents, attachments

    def _attachments(self, area, base_url: str) -> tuple[FetchedAttachment, ...]:
        if area is None:
            return ()
        found: list[FetchedAttachment] = []
        seen: set[str] = set()
        for anchor in area.xpath(".//a"):
            filename = _text(anchor)
            if not filename or filename in seen:
                continue
            href = (anchor.get("href") or "").strip()
            url = None
            if href and not href.lower().startswith("javascript:"):
                url = _absolute(base_url, href)
            elif "fileDown" in href or "download" in href.lower():
                url = None  # 스크립트 내려받기는 주소를 만들지 않는다.

            size = None
            size_match = re.search(r"\(([\d.,]+)\s*(KB|MB|Bytes?)\)", filename, re.IGNORECASE)
            if size_match:
                try:
                    number = float(size_match.group(1).replace(",", ""))
                    unit = size_match.group(2).lower()
                    size = int(number * {"kb": 1024, "mb": 1024 * 1024}.get(unit, 1))
                except ValueError:
                    size = None
                filename = filename[: size_match.start()].strip()

            if not filename:
                continue
            seen.add(filename)
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


adapter: Adapter = register(KhuBoardAdapter())
