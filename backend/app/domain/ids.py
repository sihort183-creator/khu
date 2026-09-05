"""식별자 생성.

계약 2절: 식별자는 문자열이고 의미를 파싱하지 않는다. 명칭이 바뀌어도 유지되어야 한다.
그래서 자연키(출처+원본 번호)에서 결정론적으로 만든다. 같은 입력은 언제나 같은 식별자다
(4절 13항: 재실행·복구 후에도 같은 입력은 같은 유효 레코드로 수렴한다).
"""

from __future__ import annotations

import hashlib
import re
import uuid

_SLUG = re.compile(r"[^a-z0-9]+")


def _digest(*parts: str, length: int = 12) -> str:
    payload = "\x1f".join(p.strip() for p in parts)
    return hashlib.blake2b(payload.encode("utf-8"), digest_size=16).hexdigest()[:length]


def slug(value: str, *, limit: int = 24) -> str:
    out = _SLUG.sub("-", (value or "").lower()).strip("-")
    return out[:limit] or "x"


def university_id(code: str) -> str:
    return f"univ-{slug(code)}"


def campus_id(university_code: str, campus_code: str) -> str:
    return f"campus-{slug(campus_code)}"


def organization_id(university_code: str, path: tuple[str, ...]) -> str:
    """조직 경로에서 만든다. 이름을 바꿔도 경로가 같으면 유지된다."""
    return f"org-{_digest(university_code, *path)}"


def source_id(adapter: str, list_url: str) -> str:
    return f"src-{_digest(adapter, _canonical(list_url))}"


def source_item_id(source_id_value: str, external_id: str) -> str:
    return f"item-{_digest(source_id_value, external_id)}"


def revision_id(item_id: str, content_hash: str) -> str:
    return f"rev-{_digest(item_id, content_hash)}"


def notice_id(primary_item_id: str) -> str:
    """대표 원본에서 만든다. 병합으로 대표가 바뀌면 notice_redirects 로 이전 주소를 잇는다."""
    return f"ntc-{_digest(primary_item_id)}"


def attachment_id(revision: str, filename: str, index: int) -> str:
    return f"att-{_digest(revision, filename, str(index))}"


def contact_id(organization: str, service_name: str, *scope: str) -> str:
    """연락처 식별자.

    캠퍼스 같은 구분 값을 scope 로 함께 넘긴다. 조직과 업무명만으로 만들면
    서울·국제의 동명 행정실이 하나로 합쳐진다(10절).
    """
    return f"cnt-{_digest(organization, service_name, *scope)}"


def run_id() -> str:
    """실행마다 새로 만든다. 시간순 정렬이 되도록 uuid7 대신 시각 접두를 쓰지 않고 uuid4 를 쓴다."""
    return f"run-{uuid.uuid4().hex[:20]}"


def revision_label(started_at_epoch: int, run: str) -> str:
    """정적 파일 개정 번호. 경로에 들어가므로 안전한 문자만 쓴다."""
    return f"r{started_at_epoch}-{run.split('-')[-1][:8]}"


def _canonical(url: str) -> str:
    """추적용 항목만 제거한다. 게시물·게시판 번호는 지우지 않는다(8.1절)."""
    from urllib.parse import parse_qsl, urlsplit, urlunsplit

    parts = urlsplit(url.strip())
    drop = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "fbclid", "gclid"}
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k.lower() not in drop]
    query.sort()
    rebuilt = "&".join(f"{k}={v}" for k, v in query)
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), rebuilt, ""))


canonical_url = _canonical
