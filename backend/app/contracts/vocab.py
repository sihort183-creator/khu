"""코드와 한국어 이름의 단일 사전.

계약 초안 2·3절: 상태·분류는 코드로 처리하고 사람에게는 한국어 이름을 보여준다.
백엔드와 프론트가 각자 목록을 관리하지 않도록 catalog 응답이 이 사전에서 생성된다.
"""

from __future__ import annotations

from typing import Any

# 공지 주제(계약 초안 3절). 연락처는 이 목록에 넣지 않는다.
CATEGORY_LABELS: dict[str, str] = {
    "academic": "학사",
    "graduation": "졸업",
    "scholarship": "장학",
    "career": "취업·인턴",
    "startup": "창업",
    "program": "프로그램·공모전",
    "international": "국제",
    "event": "행사",
    "student_council": "학생회",
    "campus_life": "생활",
    "other": "기타",
}

MEDIUM_LABELS: dict[str, str] = {
    "web": "웹",
    "instagram": "인스타그램",
    "submitted": "기관 제공 자료",
}

ORG_TYPE_LABELS: dict[str, str] = {
    "university": "대학",
    "campus": "캠퍼스",
    "college": "단과대학",
    "department": "학과·전공",
    "office": "행정부서",
    "council": "학생자치기구",
    "institute": "부속·연구기관",
}

# 출처 상태(계약 초안 6절)
SOURCE_STATUS_LABELS: dict[str, str] = {
    "active": "수집 중",
    "delayed": "갱신 지연",
    "blocked": "접근 제한",
    "pending": "연결·검증 대기",
    "paused": "운영 중지",
    "retired": "폐쇄",
}

ORIGINAL_STATUS_LABELS: dict[str, str] = {
    "available": "접근 가능",
    "unavailable": "확인 불가",
    "restricted": "로그인 필요",
    "removed": "삭제 확인",
}

FRESHNESS_LABELS: dict[str, str] = {
    "fresh": "최근 확인됨",
    "delayed": "확인 지연",
    "stale": "오래 확인되지 않음",
    "unknown": "확인 정보 없음",
}

VERIFICATION_LABELS: dict[str, str] = {
    "verified": "원문 확인됨",
    "stale": "확인 오래됨",
    "conflict": "내용 충돌 검토 중",
    "hidden": "비공개",
    "retired": "폐기됨",
}

CONTACT_CHANNEL_LABELS: dict[str, str] = {
    "phone": "전화",
    "email": "이메일",
    "fax": "팩스",
    "website": "공식 링크",
}

# 공지 대상 범위 유형
AUDIENCE_TYPE_LABELS: dict[str, str] = {
    "university": "대학 전체",
    "campus": "캠퍼스",
    "organization": "조직",
    "undetermined": "대상 미확정",
}

# 원문 등록일 정밀도
PRECISION_LABELS: dict[str, str] = {
    "date": "날짜",
    "datetime": "시각 포함",
    "unknown": "미확정",
}


def coded(table: dict[str, str], code: str | None, *, fallback: str | None = None) -> dict[str, Any] | None:
    """코드를 {"code","label"} 묶음으로 만든다. 모르는 코드는 코드 자체를 이름으로 쓴다."""
    if code is None:
        if fallback is None:
            return None
        code = fallback
    return {"code": code, "label": table.get(code, code)}


def code_list(table: dict[str, str]) -> list[dict[str, str]]:
    return [{"code": c, "label": label} for c, label in table.items()]
