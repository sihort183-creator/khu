"""날짜·마감 처리(11.3절).

원문 날짜 문자열, 해석한 날짜, 정밀도를 함께 다룬다.
날짜만 알려진 원문에 임의의 시각을 만들지 않는다(4절 12항).
저장은 UTC, 규칙 판정과 표시는 Asia/Seoul 이다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, timezone

KST = timezone(timedelta(hours=9), name="KST")

_DATE_PATTERNS = (
    re.compile(r"(?P<y>\d{4})[-./년]\s*(?P<m>\d{1,2})[-./월]\s*(?P<d>\d{1,2})"),
    re.compile(r"(?P<y>\d{4})(?P<m>\d{2})(?P<d>\d{2})(?!\d)"),
)
_TIME_PATTERN = re.compile(r"(?P<H>\d{1,2})\s*[:시]\s*(?P<M>\d{2})")

# 마감 문맥. 이 낱말이 근처에 있을 때만 날짜를 마감 후보로 본다.
_DEADLINE_WORDS = re.compile(
    r"(마감|접수\s*기간|신청\s*기간|제출\s*기한|까지|기한|모집\s*기간|응모\s*기간)"
)
_UNTIL = re.compile(r"~|-|부터|까지")


@dataclass(frozen=True)
class ParsedDate:
    raw: str | None
    date: date | None
    at: datetime | None
    precision: str  # date | datetime | unknown

    @property
    def known(self) -> bool:
        return self.date is not None


UNKNOWN = ParsedDate(raw=None, date=None, at=None, precision="unknown")


def parse_published(raw: str | None) -> ParsedDate:
    """게시일 문자열을 해석한다. 시각이 없으면 시각을 만들지 않는다."""
    if not raw:
        return UNKNOWN
    text = raw.strip()
    if not text:
        return UNKNOWN

    found = _find_date(text)
    if found is None:
        return ParsedDate(raw=text, date=None, at=None, precision="unknown")

    day, tail = found
    time_match = _TIME_PATTERN.search(tail)
    if time_match:
        hour, minute = int(time_match.group("H")), int(time_match.group("M"))
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            local = datetime(day.year, day.month, day.day, hour, minute, tzinfo=KST)
            return ParsedDate(raw=text, date=day, at=local.astimezone(UTC), precision="datetime")
    return ParsedDate(raw=text, date=day, at=None, precision="date")


def _find_date(text: str) -> tuple[date, str] | None:
    for pattern in _DATE_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        try:
            day = date(int(match.group("y")), int(match.group("m")), int(match.group("d")))
        except ValueError:
            continue
        if not (2000 <= day.year <= 2100):
            continue
        return day, text[match.end():]
    return None


@dataclass(frozen=True)
class DeadlineGuess:
    date: date | None
    at: datetime | None
    precision: str | None
    evidence: str | None

    @property
    def known(self) -> bool:
        return self.date is not None


NO_DEADLINE = DeadlineGuess(date=None, at=None, precision=None, evidence=None)


def guess_deadline(title: str, body_text: str, *, published: date | None = None) -> DeadlineGuess:
    """보수적인 마감 추정.

    마감 낱말이 있는 줄에서만 날짜를 찾는다. 기간 표기(A ~ B)는 뒤 날짜를 쓴다.
    확신이 없으면 추정하지 않는다. 이 값은 신청 자격 판정이 아니다.
    """
    candidates: list[tuple[date, datetime | None, str, str]] = []
    for line in _candidate_lines(title, body_text):
        if not _DEADLINE_WORDS.search(line):
            continue
        for day, at, precision in _all_dates(line):
            if published is not None and day < published - timedelta(days=1):
                continue
            candidates.append((day, at, precision, line.strip()[:200]))

    if not candidates:
        return NO_DEADLINE

    # 같은 줄에 기간이 있으면 마지막(늦은) 날짜가 마감이다.
    day, at, precision, evidence = max(candidates, key=lambda c: (c[0], c[1] or datetime.min.replace(tzinfo=UTC)))
    return DeadlineGuess(date=day, at=at, precision=precision, evidence=evidence)


def _candidate_lines(title: str, body_text: str) -> list[str]:
    lines = [title] if title else []
    lines.extend(line for line in (body_text or "").splitlines() if line.strip())
    return lines[:200]


def _all_dates(line: str) -> list[tuple[date, datetime | None, str]]:
    found: list[tuple[date, datetime | None, str]] = []
    cursor = 0
    while cursor < len(line):
        chunk = line[cursor:]
        result = _find_date(chunk)
        if result is None:
            break
        day, tail = result
        consumed = len(chunk) - len(tail)
        time_match = _TIME_PATTERN.match(tail.lstrip()[:8])
        if time_match:
            hour, minute = int(time_match.group("H")), int(time_match.group("M"))
            if 0 <= hour <= 23 and 0 <= minute <= 59:
                local = datetime(day.year, day.month, day.day, hour, minute, tzinfo=KST)
                found.append((day, local.astimezone(UTC), "datetime"))
                cursor += consumed
                continue
        found.append((day, None, "date"))
        cursor += consumed
    return found


def utcnow() -> datetime:
    return datetime.now(UTC)


def as_utc(value: datetime | None) -> datetime | None:
    """시간대가 없는 값을 UTC 로 본다.

    저장은 UTC 로 하지만 일부 드라이버(sqlite)는 시간대 없이 돌려준다.
    계약은 시간대가 포함된 시각을 요구하므로(계약 2절) 읽는 쪽에서 한 번 정규화한다.
    """
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def to_seoul(value: datetime) -> datetime:
    return value.astimezone(KST)


def freshness_code(last_checked: datetime | None, *, now: datetime | None = None) -> str:
    """18.1절의 신선도 표시. 시각이 없으면 지어내지 않는다."""
    if last_checked is None:
        return "unknown"
    now = as_utc(now) or utcnow()
    age = now - as_utc(last_checked)
    if age <= timedelta(hours=3):
        return "fresh"
    if age <= timedelta(days=1):
        return "delayed"
    return "stale"
