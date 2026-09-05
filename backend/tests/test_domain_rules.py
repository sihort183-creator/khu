"""도메인 규칙 검사: 본문 정리·날짜·분류·대상·중복(21.1절)."""

from __future__ import annotations

from datetime import date

from lxml import html as lxml_html

from app.domain import audiences as aud
from app.domain import categories as cat
from app.domain import dates as dt
from app.domain import dedupe as dd
from app.ingestion.sanitize import clean_body_html, html_to_text, make_excerpt


def _node(markup: str):
    return lxml_html.fromstring(f"<div>{markup}</div>")


# ----------------------------------------------------------------- 본문 정리


def test_sanitize_removes_scripts_and_handlers():
    node = _node('<p onclick="steal()">글</p><script>alert(1)</script><iframe src="x"></iframe>')
    out = clean_body_html(node, base_url="https://www.khu.ac.kr")

    assert "script" not in out
    assert "iframe" not in out
    assert "onclick" not in out
    assert "글" in out


def test_sanitize_blocks_javascript_links_but_keeps_text():
    node = _node('<a href="javascript:alert(1)">누르지 마세요</a>')
    out = clean_body_html(node)

    assert "javascript:" not in out
    assert "누르지 마세요" in out


def test_sanitize_keeps_tables_and_makes_links_absolute():
    node = _node('<table><tr><td>기간</td><td>3월</td></tr></table><a href="/kor/a">안내</a>')
    out = clean_body_html(node, base_url="https://www.khu.ac.kr")

    assert "<table" in out and "<td>" in out
    assert 'href="https://www.khu.ac.kr/kor/a"' in out
    assert 'rel="noopener nofollow ugc"' in out


def test_text_extraction_keeps_line_structure():
    node = _node("<p>첫 줄</p><p>둘째 줄</p><ul><li>항목</li></ul>")
    text = html_to_text(node)

    assert text.splitlines() == ["첫 줄", "둘째 줄", "항목"]


def test_excerpt_is_source_text_not_generated():
    assert make_excerpt("") is None
    assert make_excerpt("짧은 본문") == "짧은 본문"
    long_text = "가" * 400
    assert make_excerpt(long_text).endswith("…")


# ----------------------------------------------------------------- 날짜


def test_date_only_never_gets_invented_time():
    parsed = dt.parse_published("2026-05-28")
    assert parsed.date == date(2026, 5, 28)
    assert parsed.at is None
    assert parsed.precision == "date"


def test_datetime_is_stored_in_utc():
    parsed = dt.parse_published("2026-05-28 14:30")
    assert parsed.precision == "datetime"
    assert parsed.at.hour == 5  # 한국 14:30 = 세계 표준시 05:30


def test_unparseable_date_is_unknown_not_today():
    parsed = dt.parse_published("상시")
    assert parsed.date is None
    assert parsed.precision == "unknown"


def test_deadline_requires_deadline_context():
    """마감 낱말이 없으면 날짜가 있어도 마감으로 보지 않는다."""
    guess = dt.guess_deadline("행사 안내", "2026-03-02 에 진행합니다")
    assert guess.known is False


def test_deadline_picks_end_of_range():
    guess = dt.guess_deadline("장학 안내", "신청 기간: 2026-03-02 ~ 2026-03-15 까지")
    assert guess.date == date(2026, 3, 15)
    assert guess.evidence is not None


def test_freshness_unknown_without_check_time():
    assert dt.freshness_code(None) == "unknown"


# ----------------------------------------------------------------- 분류


def test_board_category_wins_over_keywords():
    decision = cat.classify("행사 관련 장학 이야기", "", board_category="장학")
    assert decision.primary == "scholarship"
    assert decision.rule_name == "board_category"


def test_title_prefix_is_used():
    decision = cat.classify("[졸업] 2026학년도 졸업사정 안내", "")
    assert decision.primary == "graduation"


def test_longest_match_wins_for_similar_words():
    assert cat.classify("공지", "", board_category="등록금").primary == "scholarship"


def test_unknown_falls_back_to_other():
    decision = cat.classify("총장 동정", "내용 없음", board_category="공통")
    assert decision.primary == "other"
    assert decision.rule_name in ("board_category", "fallback")


def test_contacts_are_not_a_notice_category():
    assert "contact" not in cat.CATEGORY_LABELS


# ----------------------------------------------------------------- 대상 범위


def _campus_map():
    return {"seoul": ("campus-seoul", "서울캠퍼스"), "global": ("campus-global", "국제캠퍼스")}


def test_source_default_is_used_when_notice_says_nothing():
    default = (aud.AudienceTarget("organization", "org-1", "소프트웨어융합학과"),)
    decision = aud.decide("일반 공지", "본문", source_defaults=default, campus_lookup=_campus_map())
    assert decision.targets == default
    assert decision.narrowed is False


def test_explicit_campus_narrows_audience():
    default = (aud.AudienceTarget("university", None, "대학 전체"),)
    decision = aud.decide(
        "서울캠퍼스 학생 대상 안내", "서울캠퍼스에 한함", source_defaults=default, campus_lookup=_campus_map()
    )
    assert decision.narrowed is True
    assert decision.targets[0].id == "campus-seoul"


def test_both_campuses_mentioned_does_not_narrow():
    default = (aud.AudienceTarget("university", None, "대학 전체"),)
    decision = aud.decide(
        "안내", "서울캠퍼스 및 국제캠퍼스 모두 해당", source_defaults=default, campus_lookup=_campus_map()
    )
    assert decision.narrowed is False


def test_restriction_wording_adds_note_without_claiming_eligibility():
    decision = aud.decide(
        "장학 안내", "신청 자격: 3학년 이상", source_defaults=(), campus_lookup=_campus_map()
    )
    assert decision.note is not None
    assert "원문" in decision.note


def test_no_defaults_yields_undetermined():
    decision = aud.decide("안내", "본문", source_defaults=(), campus_lookup={})
    assert decision.targets[0].type == "undetermined"


# ----------------------------------------------------------------- 중복


def _candidate(item: str, source: str, title: str, body: str, **kw) -> dd.Candidate:
    return dd.Candidate(
        item_id=item, revision_id=f"rev-{item}", source_id=source, organization_id=None,
        title=title, body_text=body, **kw
    )


LONG_BODY = "신청 방법과 제출 서류를 아래와 같이 안내합니다. " * 12


def test_identical_long_body_across_sources_merges():
    left = _candidate("a", "s1", "2026학년도 장학 신청 안내", LONG_BODY)
    right = _candidate("b", "s2", "2026학년도 장학 신청 안내", LONG_BODY)
    assert dd.compare(left, right).decision == "merge"


def test_same_form_different_year_does_not_merge():
    """학년도만 다른 공지를 합치지 않는다(21.1절 중복 항목)."""
    left = _candidate("a", "s1", "2025학년도 장학 신청", "2025학년도 " + LONG_BODY)
    right = _candidate("b", "s2", "2026학년도 장학 신청", "2026학년도 " + LONG_BODY)
    assert dd.compare(left, right).decision != "merge"


def test_different_round_does_not_merge():
    left = _candidate("a", "s1", "1차 모집", "1차 " + LONG_BODY)
    right = _candidate("b", "s2", "2차 모집", "2차 " + LONG_BODY)
    assert dd.compare(left, right).decision != "merge"


def test_short_identical_body_goes_to_review_not_merge():
    left = _candidate("a", "s1", "안내", "붙임 참조")
    right = _candidate("b", "s2", "안내", "붙임 참조")
    assert dd.compare(left, right).decision != "merge"


def test_similar_title_alone_is_review():
    left = _candidate("a", "s1", "2026학년도 1학기 장학 신청 안내", "서로 다른 본문 " * 30)
    right = _candidate("b", "s2", "2026학년도 1학기 장학 신청 안내문", "완전히 다른 내용 " * 30)
    assert dd.compare(left, right).decision == "review"


def test_same_source_items_never_auto_merge():
    left = _candidate("a", "s1", "같은 제목", LONG_BODY)
    right = _candidate("b", "s1", "같은 제목", LONG_BODY)
    assert dd.compare(left, right).decision == "distinct"


def test_audience_conflict_blocks_merge():
    left = _candidate("a", "s1", "안내", LONG_BODY, audience_keys=frozenset({"campus:seoul"}))
    right = _candidate("b", "s2", "안내", LONG_BODY, audience_keys=frozenset({"campus:global"}))
    assert dd.compare(left, right).decision != "merge"


def test_boilerplate_body_has_no_fingerprint():
    assert dd.body_fingerprint("붙임 참조") is None
    assert dd.body_fingerprint("") is None
    assert dd.body_fingerprint(LONG_BODY) is not None


def test_content_hash_is_stable_and_order_independent_of_whitespace():
    assert dd.content_hash("제목", "본문  내용") == dd.content_hash("제목", "본문 내용")
    assert dd.content_hash("제목", "본문") != dd.content_hash("제목", "다른 본문")


def test_primary_prefers_source_rank_then_body():
    weak = _candidate("a", "s2", "제목", "")
    strong = _candidate("b", "s1", "제목", LONG_BODY)
    assert dd.pick_primary([weak, strong], source_rank={"s1": 1, "s2": 9}).item_id == "b"
