"""공통 추출 계약(8.1절).

어댑터는 원본 식별자·주소·제목·날짜 원문·본문·첨부·고정 여부·추출 근거·다음 페이지를 반환한다.
저장·중복 처리 권한은 어댑터가 아니라 공통 계층이 가진다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
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


class ParseError(Exception):
    """구조 분석 실패. 수집 성공 0건과 구분해서 기록한다(4절 8항)."""


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
