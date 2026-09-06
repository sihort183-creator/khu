"""수집 계층. 어댑터 등록은 이 모듈을 불러올 때 이루어진다."""

from app.ingestion import gnuboard, khu_board  # noqa: F401  어댑터 등록
from app.ingestion.base import (  # noqa: F401
    Adapter,
    FetchedAttachment,
    FetchedDetail,
    ListedItem,
    ListPage,
    ParseError,
    adapter_names,
    get_adapter,
    register,
)
