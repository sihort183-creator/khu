"""공지 주제 분류(8.2절).

우선순위: 원 게시판 카테고리 → 제목 말머리 → 명시적인 제목·본문 키워드 → 기타.
규칙명·버전·근거를 함께 남긴다. 언어 모델을 부르지 않는다(1절).
연락처는 이 분류에 섞지 않는다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.contracts.vocab import CATEGORY_LABELS

RULE_VERSION = "categories/1"

# 게시판이 스스로 붙인 분류 이름 → 주제 코드. 가장 신뢰도가 높다.
BOARD_CATEGORY_MAP: dict[str, str] = {
    "학사": "academic",
    "수업": "academic",
    "수강": "academic",
    "등록": "academic",
    "학적": "academic",
    "졸업": "graduation",
    "장학": "scholarship",
    "등록금": "scholarship",
    "취업": "career",
    "채용": "career",
    "인턴": "career",
    "현장실습": "career",
    "진로": "career",
    "창업": "startup",
    "공모": "program",
    "공모전": "program",
    "프로그램": "program",
    "비교과": "program",
    "특강": "program",
    "국제": "international",
    "교환학생": "international",
    "해외": "international",
    "어학": "international",
    "행사": "event",
    "축제": "event",
    "세미나": "event",
    "학술": "event",
    "학생회": "student_council",
    "총학생회": "student_council",
    "생활": "campus_life",
    "기숙사": "campus_life",
    "시설": "campus_life",
    "봉사": "campus_life",
    "일반": "other",
    "공통": "other",
    "기타": "other",
}

# 제목 말머리 [..] 안의 낱말. 게시판 분류 다음으로 신뢰한다.
PREFIX_MAP = dict(BOARD_CATEGORY_MAP)

# 명시적 키워드. 오탐을 줄이려 짧은 낱말은 넣지 않는다.
KEYWORD_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("graduation", re.compile(r"졸업(요건|사정|예정자|논문|자격|시험|앨범)|학위\s*수여")),
    ("scholarship", re.compile(r"장학(금|생)|국가장학|교내장학|학자금|등록금\s*(납부|고지|분할)")),
    ("career", re.compile(r"채용|취업|인턴(십)?|현장실습|직무|공채|리크루팅|잡페어|커리어")),
    ("startup", re.compile(r"창업|스타트업|기업가정신|액셀러레이팅|창업경진")),
    (
        "international",
        re.compile(r"교환학생|해외\s*(파견|연수|봉사|인턴)|국제\s*(교류|처)|유학생|어학연수|TOEIC|IELTS|TOEFL"),
    ),
    ("program", re.compile(r"공모전|경진대회|아이디어\s*공모|비교과|특강|워크숍|워크샵|캠프|아카데미|수료|교육\s*과정")),
    ("event", re.compile(r"축제|행사|콘서트|전시|공연|세미나|심포지엄|포럼|학술대회|설명회|간담회")),
    ("student_council", re.compile(r"총학생회|학생회|동아리연합회|중앙운영위원회|학생\s*자치|선거\s*시행세칙")),
    ("campus_life", re.compile(r"기숙사|생활관|셔틀|통학|식당|주차|시설\s*(공사|점검|이용)|분실물|봉사활동|헌혈")),
    (
        "academic",
        re.compile(r"수강\s*(신청|정정|철회)|시간표|계절학기|학사\s*일정|휴학|복학|전과|재입학|학점\s*교류|성적\s*(입력|정정|공시)"),
    ),
)


@dataclass(frozen=True)
class CategoryDecision:
    primary: str
    secondary: tuple[str, ...] = ()
    rule_name: str = "fallback"
    rule_version: str = RULE_VERSION
    evidence: str | None = None
    all_codes: tuple[str, ...] = field(default=())

    @property
    def primary_label(self) -> str:
        return CATEGORY_LABELS.get(self.primary, self.primary)


def _normalize(value: str | None) -> str:
    return re.sub(r"\s+", "", value or "")


def _from_map(value: str | None, table: dict[str, str]) -> tuple[str, str] | None:
    """가장 긴 일치를 먼저 본다. '등록금' 이 '등록' 보다 앞선다."""
    key = _normalize(value)
    if not key:
        return None
    for word in sorted(table, key=len, reverse=True):
        if word in key:
            return table[word], word
    return None


def _title_prefixes(title: str) -> list[str]:
    return re.findall(r"[\[［(【]([^\]］)】]{1,20})[\]］)】]", title or "")


def classify(
    title: str,
    body_text: str | None = None,
    *,
    board_category: str | None = None,
    source_default: str | None = None,
) -> CategoryDecision:
    """대표 주제 1개와 보조 주제를 정한다. 판단 근거를 함께 돌려준다."""
    hits: list[tuple[str, str, str]] = []  # (code, rule_name, evidence)

    board_hit = _from_map(board_category, BOARD_CATEGORY_MAP)
    if board_hit and board_hit[0] != "other":
        hits.append((board_hit[0], "board_category", board_hit[1]))

    for prefix in _title_prefixes(title):
        prefix_hit = _from_map(prefix, PREFIX_MAP)
        if prefix_hit and prefix_hit[0] != "other":
            hits.append((prefix_hit[0], "title_prefix", prefix))

    haystack_title = title or ""
    haystack_body = (body_text or "")[:4000]
    for code, pattern in KEYWORD_RULES:
        match = pattern.search(haystack_title)
        where = "title_keyword"
        if not match:
            match = pattern.search(haystack_body)
            where = "body_keyword"
        if match:
            hits.append((code, where, match.group(0)))

    if not hits:
        if source_default and source_default in CATEGORY_LABELS:
            return CategoryDecision(
                primary=source_default,
                rule_name="source_default",
                evidence=None,
                all_codes=(source_default,),
            )
        if board_hit:
            return CategoryDecision(
                primary=board_hit[0],
                rule_name="board_category",
                evidence=board_hit[1],
                all_codes=(board_hit[0],),
            )
        return CategoryDecision(primary="other", rule_name="fallback", all_codes=("other",))

    primary_code, rule_name, evidence = hits[0]
    ordered: list[str] = []
    for code, _, _ in hits:
        if code not in ordered:
            ordered.append(code)
    secondary = tuple(code for code in ordered[1:4])
    return CategoryDecision(
        primary=primary_code,
        secondary=secondary,
        rule_name=rule_name,
        evidence=evidence,
        all_codes=tuple(ordered),
    )
