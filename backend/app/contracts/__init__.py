"""프론트와의 응답 계약. 정적 JSON과 가상 자료가 같은 모델을 통과한다(12절)."""

from app.contracts.models import (  # noqa: F401
    Attachment,
    Campus,
    Catalog,
    Coded,
    Contact,
    ContactChannel,
    Deadline,
    Evidence,
    Freshness,
    ItemResponse,
    ListResponse,
    Meta,
    Notice,
    NoticeDetail,
    NoticeSourceRef,
    Organization,
    OrganizationRef,
    Page,
    Source,
    Verification,
)
from app.contracts.vocab import (  # noqa: F401
    CATEGORY_LABELS,
    FRESHNESS_LABELS,
    MEDIUM_LABELS,
    ORG_TYPE_LABELS,
    ORIGINAL_STATUS_LABELS,
    SOURCE_STATUS_LABELS,
    VERIFICATION_LABELS,
    coded,
)
