"""공통 추출 계약(8.1절).

어댑터는 원본 식별자·주소·제목·날짜 원문·본문·첨부·고정 여부·추출 근거·다음 페이지를 반환한다.
저장·중복 처리 권한은 어댑터가 아니라 공통 계층이 가진다.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Protocol

from app.ingestion.http import Fetcher


@dataclass(frozen=True)
class ListedItem:
    """목록 한 줄. 상세를 열기 전에 알 수 있는 것만 담는다."""

    external_id: str
    url: str
    title: str
    published_raw: str | None = None
    board_category: str | None = None
    author: str | None = None
    is_pinned: bool = False
    detail_hint: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ListPage:
    items: tuple[ListedItem, ...]
    page_index: int
    has_next: bool
    total_text: str | None = None


@dataclass(frozen=True)
class FetchedAttachment:
    filename: str
    url: str | None = None
    kind: str | None = None
    size_bytes: int | None = None


@dataclass(frozen=True)
class FetchedDetail:
    """상세 한 건. 원문 HTML 은 증거 보관용이고 사용자에게는 정리된 본문만 나간다."""

    external_id: str
    url: str
    title: str
    body_text: str
    body_html: str
    raw_html: str
    published_raw: str | None = None
    board_category: str | None = None
    author: str | None = None
    attachments: tuple[FetchedAttachment, ...] = ()
    extraction_notes: dict[str, Any] = field(default_factory=dict)


def unpin_whole_page(items: list[ListedItem]) -> list[ListedItem]:
    """한 쪽이 통째로 상단 고정으로 보이면 아무 줄도 고정으로 보지 않는다.

    상단 고정은 "다른 글보다 위에 붙였다"는 뜻이라 붙지 않은 줄이 있어야 성립한다.
    2026-09-07 의과대학 게시판(khusm s6_1)은 4,392개 글 전부에 그누보드 공지 표시
    (tr.bo_notice · 번호 칸 '공지')를 달고 있었다. 진짜 고정 글이라면 쪽마다 같은 줄이
    다시 나와야 하는데 1쪽과 2쪽의 줄이 서로 달랐다. 게시판이 표시를 남발한 것이다.

    고정 글은 수집 기간 밖이어도 버리지 않고 날짜 경계 판정에서도 빼기 때문에, 이
    표시를 그대로 믿으면 게시판 하나가 통째로 기간 제한을 벗어난다. 실제로 그 게시판은
    회차마다 시간 상한에 걸려 백필이 다섯 쪽에서 멈춰 있었다. 구별이 없는 표시는
    표시가 없는 것과 같게 본다.
    """
    if len(items) < 2 or not all(item.is_pinned for item in items):
        return items
    return [replace(item, is_pinned=False) for item in items]


class ParseError(Exception):
    """구조 분석 실패. 수집 성공 0건과 구분해서 기록한다(4절 8항)."""


class RestrictedError(ParseError):
    """로그인해야 볼 수 있는 글. 우리가 고칠 수 없는 실패라 따로 구분한다.

    본문을 못 가져올 뿐 글이 없는 것은 아니다. 실패로만 묶어 버리면 미래인재센터
    채용 게시판처럼 게시판 하나가 통째로 없는 것처럼 보인다.
    """


class Adapter(Protocol):
    """출처 어댑터. 새 사이트가 이미 지원하는 형식이면 설정만 추가한다(5.2절)."""

    name: str

    def allowed_hosts(self, config: dict[str, Any]) -> set[str]:
        """이 출처가 요청해도 되는 도메인. SSRF 검사에 쓰인다."""

    async def list_page(
        self, fetcher: Fetcher, config: dict[str, Any], page_index: int
    ) -> ListPage: ...

    async def detail(
        self, fetcher: Fetcher, config: dict[str, Any], item: ListedItem
    ) -> FetchedDetail: ...


_REGISTRY: dict[str, Adapter] = {}


def register(adapter: Adapter) -> Adapter:
    _REGISTRY[adapter.name] = adapter
    return adapter


def get_adapter(name: str) -> Adapter:
    try:
        return _REGISTRY[name]
    except KeyError:
        raise ParseError(
            f"등록되지 않은 어댑터: {name}. 지원 목록: {sorted(_REGISTRY)}"
        ) from None


def adapter_names() -> list[str]:
    return sorted(_REGISTRY)
