"""중복 공지 처리(9절).

1차 원칙: 잘못 합치는 비용을 우선 낮춘다.
- 자동 병합은 충분한 본문 길이 + 내용 일치 + 대상·학년도·회차·마감 비충돌이 모두 맞을 때만 한다.
- 본문 해시가 같아도 공통 서식만 있는 글은 제외한다.
- 유사 제목·본문 후보는 자동 병합하지 않고 운영 검토(review)로 보낸다.
- 첨부·이미지 해시는 보조 신호이며 단독 병합 근거가 아니다.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

RULE_VERSION = "dedupe/1"

# 자동 병합에 필요한 최소 본문 길이. 짧은 글은 우연히 같을 수 있다(4절 7항).
MIN_BODY_CHARS_FOR_AUTO_MERGE = 200

# 이 정도 유사하면 검토 대상으로 올린다. 확정 임계값이 아니다(9.1절).
REVIEW_TITLE_SIMILARITY = 0.86

# 본문에 이것만 있으면 내용이 아니라 서식이다.
BOILERPLATE = re.compile(
    r"^(?:붙임|첨부|아래|다음)\s*(?:파일|참조|참고|와\s*같습니다)?[\s.]*$|^\s*$"
)

_WS = re.compile(r"\s+")
_PUNCT = re.compile(r"[\[\]()（）【】<>《》「」·・,.!?~\-_/\\|:;\"'`]+")

# 충돌 신호: 이 값들이 서로 다르면 자동 병합하지 않는다.
_YEAR = re.compile(r"(20\d{2})\s*(?:학년도|년도|년)")
_ROUND = re.compile(r"(\d{1,2})\s*(?:차|기|회)(?![를을])")
_SEMESTER = re.compile(r"([12])\s*학기")


def normalize_title(title: str) -> str:
    text = _PUNCT.sub(" ", title or "")
    return _WS.sub(" ", text).strip().lower()


def content_hash(title: str, body_text: str | None) -> str:
    """이력 중복 방지용 해시. 제목과 본문을 함께 본다."""
    payload = f"{normalize_title(title)}\n{_WS.sub(' ', (body_text or '')).strip()}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def body_fingerprint(body_text: str | None) -> str | None:
    """서식만 있는 본문은 지문을 만들지 않는다."""
    text = _WS.sub(" ", (body_text or "")).strip()
    if not text:
        return None
    meaningful = [line for line in (body_text or "").splitlines() if not BOILERPLATE.match(line.strip())]
    joined = _WS.sub(" ", " ".join(meaningful)).strip()
    if len(joined) < 40:
        return None
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def title_similarity(left: str, right: str) -> float:
    """문자 삼중 조합 자카드 유사도. pg_trgm 과 같은 관점이다."""
    a, b = _trigrams(normalize_title(left)), _trigrams(normalize_title(right))
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _trigrams(text: str) -> set[str]:
    padded = f"  {text} "
    return {padded[i : i + 3] for i in range(len(padded) - 2)}


@dataclass(frozen=True)
class Candidate:
    """비교 대상 하나. 비교 시점의 이력을 고정해서 넘긴다."""

    item_id: str
    revision_id: str
    source_id: str
    organization_id: str | None
    title: str
    body_text: str | None
    published_date: object | None = None
    audience_keys: frozenset[str] = frozenset()
    attachment_hashes: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Decision:
    decision: str  # merge | distinct | review
    score: float
    signals: dict[str, object] = field(default_factory=dict)
    rule_version: str = RULE_VERSION
    reason: str = ""


def _markers(text: str) -> dict[str, set[str]]:
    return {
        "year": set(_YEAR.findall(text)),
        "round": set(_ROUND.findall(text)),
        "semester": set(_SEMESTER.findall(text)),
    }


def _conflicting_markers(left: Candidate, right: Candidate) -> list[str]:
    left_text = f"{left.title}\n{(left.body_text or '')[:2000]}"
    right_text = f"{right.title}\n{(right.body_text or '')[:2000]}"
    left_marks, right_marks = _markers(left_text), _markers(right_text)
    conflicts = []
    for key in ("year", "round", "semester"):
        a, b = left_marks[key], right_marks[key]
        if a and b and not (a & b):
            conflicts.append(key)
    return conflicts


def compare(left: Candidate, right: Candidate) -> Decision:
    """두 원본이 같은 공지인지 판정한다. 확신이 없으면 review 다."""
    if left.item_id == right.item_id:
        return Decision("distinct", 0.0, reason="같은 원본")
    if left.source_id == right.source_id:
        # 같은 출처의 같은 게시물은 원본 식별 단계에서 이미 처리된다(9.1절).
        return Decision("distinct", 0.0, reason="같은 출처의 서로 다른 게시물")

    similarity = title_similarity(left.title, right.title)
    left_print, right_print = body_fingerprint(left.body_text), body_fingerprint(right.body_text)
    body_equal = bool(left_print and right_print and left_print == right_print)
    long_enough = (
        len(left.body_text or "") >= MIN_BODY_CHARS_FOR_AUTO_MERGE
        and len(right.body_text or "") >= MIN_BODY_CHARS_FOR_AUTO_MERGE
    )
    conflicts = _conflicting_markers(left, right)
    audience_conflict = bool(
        left.audience_keys
        and right.audience_keys
        and not (left.audience_keys & right.audience_keys)
    )
    shared_attachments = sorted(left.attachment_hashes & right.attachment_hashes)

    signals: dict[str, object] = {
        "title_similarity": round(similarity, 4),
        "body_equal": body_equal,
        "body_long_enough": long_enough,
        "marker_conflicts": conflicts,
        "audience_conflict": audience_conflict,
        "shared_attachments": len(shared_attachments),
    }

    if conflicts or audience_conflict:
        # 충돌은 자동 병합 중단 사유다(9.2절). 합집합으로 뭉개지 않는다.
        return Decision(
            "review" if (body_equal or similarity >= REVIEW_TITLE_SIMILARITY) else "distinct",
            similarity,
            signals,
            reason="대상·학년도·회차 충돌",
        )

    if body_equal and long_enough and similarity >= 0.5:
        return Decision("merge", max(similarity, 0.95), signals, reason="본문 일치 + 충분한 길이")

    if body_equal and not long_enough:
        return Decision("review", similarity, signals, reason="본문은 같으나 너무 짧음")

    if similarity >= REVIEW_TITLE_SIMILARITY:
        return Decision("review", similarity, signals, reason="제목 유사")

    if shared_attachments and similarity >= 0.6:
        # 첨부 해시는 보조 신호다. 단독으로 병합하지 않는다(9.1절).
        return Decision("review", similarity, signals, reason="첨부 공유 + 제목 일부 유사")

    return Decision("distinct", similarity, signals, reason="유사도 부족")


def pick_primary(candidates: list[Candidate], *, source_rank: dict[str, int] | None = None) -> Candidate:
    """묶음의 대표 원본. 본문이 살아 있는 공식 출처를 우선한다(9.2절)."""
    rank = source_rank or {}

    def sort_key(c: Candidate) -> tuple:
        return (
            rank.get(c.source_id, 500),
            0 if (c.body_text or "").strip() else 1,
            -len(c.body_text or ""),
            str(c.published_date or "9999-12-31"),
            c.item_id,
        )

    return sorted(candidates, key=sort_key)[0]
