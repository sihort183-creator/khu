"""공지 주제 분류(8.2절).

우선순위: 원 게시판 카테고리 → 제목 말머리 → 제목 키워드 → 본문 키워드 → 기타.
규칙명·버전·근거를 함께 남긴다. 언어 모델을 부르지 않는다(1절).
연락처는 이 분류에 섞지 않는다.

2026-09-09 (categories/2): 제목 키워드가 본문 키워드보다 항상 앞선다. 전에는 규칙
순서대로 한 바퀴 돌며 제목·본문을 같이 봐서, 조교 모집 본문의 "장학금 지급" 한 마디가
제목의 "채용"을 이겼다. 실측으로 장학 탭의 27%가 그렇게 들어온 글이었다.
장학 규칙은 키워드 규칙 중 맨 앞이고, 제목에서는 "장학" 한 낱말도 인식한다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.contracts.vocab import CATEGORY_LABELS

RULE_VERSION = "categories/2"

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
    # 말머리가 "[국제_국가장학]" 처럼 캠퍼스와 주제를 붙여 쓰는 게시판이 있다.
    # 최장 일치라 아래 넉 자 낱말이 "국제" 보다 먼저 잡힌다.
    "국가장학": "scholarship",
    "국가근로": "scholarship",
    "근로장학": "scholarship",
    "교내장학": "scholarship",
    "교외장학": "scholarship",
    "주거안정장학": "scholarship",
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
# 순서가 곧 우선순위다. 같은 제목(또는 같은 본문)에서 여럿이 걸리면 앞의 것이 대표가 된다.
# 장학이 맨 앞이다. "논문발표장학"(졸업), "스타트업 장학"(창업), "교환학생 장학"(국제)은
# 모두 장학 공지이지 그 주제의 공지가 아니다.
KEYWORD_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("scholarship", re.compile(r"장학(금|생)|국가장학|교내장학|학자금|등록금\s*(납부|고지|분할)")),
    ("graduation", re.compile(r"졸업(요건|사정|예정자|논문|자격|시험|앨범)|학위\s*수여")),
    ("career", re.compile(r"채용|취업|인턴(십)?|현장실습|직무|공채|리크루팅|잡페어|커리어|조교\s*(모집|선발|채용)")),
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

# 제목에서만 더 보는 키워드. 제목은 짧고 주제를 곧장 말하므로 본문보다 느슨하게 본다.
# "융합인재장학 신청", "우정장학 안내" 처럼 "장학" 한 낱말로 끝나는 교내 장학 이름이 많다.
# 교육청 "장학사·장학관" 채용, "장학지도" 는 장학금이 아니라서 뺀다.
TITLE_ONLY_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("scholarship", re.compile(r"장학(?!사|관|지도)")),
)

# 본문에서는 이 주제를 앞머리에서만 인정한다. 조교 모집·고시반·연수 안내는 본문 뒤쪽에
# "장학금 지급" 조건을 길게 적어서 등장 횟수로는 진짜 장학 공지와 갈라지지 않았다.
# 실측(2026-09-09, 표본 150+150)에서 진짜 장학 공지는 본문 첫 200자 안에서 장학을 말하고,
# 아닌 글은 대개 600자 뒤에서야 말했다.
BODY_LEAD_WINDOW: dict[str, int] = {"scholarship": 200}

# 제목 키워드가 말머리를 이기는 주제. 말머리에 조직 이름이 흔한 탓이다(classify 참고).
TITLE_BEATS_PREFIX: frozenset[str] = frozenset({"scholarship"})


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


_PREFIX_TOKEN = re.compile(r"\s*[\[［(【]([^\]］)】]{1,20})[\]］)】]")


def _title_prefixes(title: str) -> list[str]:
    """제목 앞머리에 잇달아 붙은 말머리만 본다.

    제목 뒤쪽 괄호는 말머리가 아니라 설명이다. "해외교환 장학생 선발 (교환학생 대상)" 의
    끝 괄호를 말머리로 읽으면 국제가 장학을 이겨 버린다.
    """
    out: list[str] = []
    pos = 0
    text = title or ""
    while True:
        match = _PREFIX_TOKEN.match(text, pos)
        if not match:
            return out
        out.append(match.group(1))
        pos = match.end()


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

    prefix_hits: list[tuple[str, str, str]] = []
    for prefix in _title_prefixes(title):
        prefix_hit = _from_map(prefix, PREFIX_MAP)
        if prefix_hit and prefix_hit[0] != "other":
            prefix_hits.append((prefix_hit[0], "title_prefix", prefix))

    haystack_title = title or ""
    haystack_body = (body_text or "")[:4000]
    # 제목을 모든 규칙으로 먼저 훑고, 그다음에야 본문을 본다. 본문은 한 규칙이 제목에서
    # 이미 걸렸으면 다시 보지 않는다(같은 주제를 두 번 세지 않으려고).
    title_codes: set[str] = set()
    for code, pattern in TITLE_ONLY_RULES + KEYWORD_RULES:
        if code in title_codes:
            continue
        match = pattern.search(haystack_title)
        if match:
            hits.append((code, "title_keyword", match.group(0)))
            title_codes.add(code)
    # 말머리는 "[창업교육센터]", "[국제교류팀]" 처럼 조직 이름인 경우가 많다. 장학은 제목
    # 본문에 "장학" 이 있으면 그 조직이 주는 장학이지 그 조직 주제의 글이 아니므로,
    # 장학 제목 키워드만은 말머리보다 앞에 둔다. 게시판 분류는 여전히 그 위다.
    leading = [hit for hit in hits if hit[1] == "title_keyword" and hit[0] in TITLE_BEATS_PREFIX]
    rest = [hit for hit in hits if hit not in leading]
    board_hits = [hit for hit in rest if hit[1] == "board_category"]
    title_hits = [hit for hit in rest if hit[1] == "title_keyword"]
    hits = board_hits + leading + prefix_hits + title_hits

    for code, pattern in KEYWORD_RULES:
        if code in title_codes:
            continue
        window = BODY_LEAD_WINDOW.get(code)
        match = pattern.search(haystack_body[:window] if window else haystack_body)
        if match:
            hits.append((code, "body_lead_keyword" if window else "body_keyword", match.group(0)))

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
