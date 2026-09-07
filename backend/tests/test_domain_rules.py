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


def test_campus_mention_keeps_the_source_organization():
    """학과 게시판 글이 캠퍼스를 언급해도 학과를 잃지 않는다.

    2026-09-07 실제로 소프트웨어융합학과 글이 제목의 "(국제)" 하나 때문에
    국제캠퍼스 전체 공지가 되어 학과를 고른 사람에게서 사라졌다. 그 회귀를 막는다.
    """
    default = (aud.AudienceTarget("organization", "org-swcon", "소프트웨어융합학과"),)
    decision = aud.decide(
        "[홍보] [미래인재센터(국제)] 2026학년도 1학기 현장실습",
        "국제캠퍼스에서 진행합니다.",
        source_defaults=default,
        campus_lookup=_campus_map(),
    )
    keys = {target.key() for target in decision.targets}
    assert keys == {"org:org-swcon", "campus:campus-global"}


def test_university_wide_wording_stays_university_wide():
    """스스로 전교생 대상이라 밝힌 글을 학과 공지로 좁히지 않는다."""
    default = (aud.AudienceTarget("organization", "org-swcon", "소프트웨어융합학과"),)
    decision = aud.decide(
        "2026학년도 1학기 성적입력 및 공시(정정)기간 안내",
        "재학생 전원이 확인해야 합니다.",
        source_defaults=default,
        campus_lookup=_campus_map(),
    )
    assert [target.type for target in decision.targets] == ["university"]


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


def test_campus_mention_does_not_erase_the_department():
    """본문이 캠퍼스를 언급해도 게시판 주인 학과와의 연결을 지우지 않는다.

    지우면 영어영문학과 게시판 글이 "국제캠퍼스 전체" 공지가 되어, 학과를 고른 사람에게는
    사라지고 엉뚱한 사람에게 뜬다. 2026-09-07 사용자가 이 상태를 지적했다.
    """
    department = (aud.AudienceTarget(type="organization", id="org-eng", name="영어영문학과"),)
    decision = aud.decide(
        "슬기로운 경희생활 시리즈 특강", "국제캠퍼스 청운관에서 진행합니다",
        source_defaults=department, campus_lookup=_campus_map(),
    )
    kinds = {(t.type, t.id) for t in decision.targets}
    assert ("organization", "org-eng") in kinds
    assert any(kind == "campus" for kind, _ in kinds)


# ------------------------------------------------- 포스터 묶음(9.1절 보조 신호)

POSTER = frozenset({"bucket:com.khu.ac.kr/001187", "file:com.khu.ac.kr/001187/붙임4_안내문"})


def test_shared_poster_never_merges_by_itself():
    """포스터 묶음만으로는 절대 병합하지 않는다.

    올림 폴더 번호는 같은 포스터라는 뜻이 아니라 편집기의 날짜 칸이다. 실측으로
    한 폴더에 서로 무관한 공지가 20~60건씩 들어 있다. 제목까지 완전히 같아도
    본문이라는 뒷받침이 없으므로 검토까지만 간다.
    """
    left = _candidate("a", "s1", "2026학년도 2학기 학위지도교수 신청 안내", "", poster_keys=POSTER)
    right = _candidate("b", "s2", "2026학년도 2학기 학위지도교수 신청 안내", "", poster_keys=POSTER)
    decision = dd.compare(left, right)
    assert decision.decision == "review"
    assert decision.signals["shared_poster_buckets"] == 1
    assert decision.signals["shared_poster_files"] == 1


def test_same_poster_bucket_with_clearly_different_titles_stays_distinct():
    """같은 폴더에 있어도 제목이 확실히 다르면 묶지 않는다.

    실측: 폴더 001198 한 곳에 '울산연구원 장학생 선발'과 '현대모비스 신입 채용'이
    함께 들어 있다. 며칠 안에 올라왔다는 것 말고는 공통점이 없다.
    """
    left = _candidate("a", "s1", "하반기 울산연구원 장학생 선발 안내", "", poster_keys=POSTER)
    right = _candidate("b", "s2", "현대모비스 신입 채용 모집", "", poster_keys=POSTER)
    assert dd.compare(left, right).decision == "distinct"


def test_same_poster_bucket_with_slightly_different_titles_is_not_promoted():
    """제목 문턱은 본문 문턱보다 높다. 0.86~0.95 사이는 포스터 근거로 올리지 않는다."""
    left = _candidate("a", "s1", "2026학년도 1학기 교내장학 신청 안내", "", poster_keys=POSTER)
    right = _candidate("b", "s2", "2026학년도 1학기 교내장학 신청 방법", "", poster_keys=POSTER)
    decision = dd.compare(left, right)
    assert decision.decision != "merge"
    assert decision.reason != "포스터 묶음 공유 + 제목 거의 일치"


def test_same_poster_bucket_different_year_or_round_does_not_merge():
    """학년도·회차가 다르면 포스터가 같아도 병합도 포스터 검토도 아니다."""
    year_left = _candidate("a", "s1", "2025학년도 장학 신청 안내", "", poster_keys=POSTER)
    year_right = _candidate("b", "s2", "2026학년도 장학 신청 안내", "", poster_keys=POSTER)
    year = dd.compare(year_left, year_right)
    assert year.decision != "merge"
    assert year.signals["marker_conflicts"] == ["year"]

    round_left = _candidate("c", "s1", "모두의 창업 1차 모집 안내", "", poster_keys=POSTER)
    round_right = _candidate("d", "s2", "모두의 창업 2차 모집 안내", "", poster_keys=POSTER)
    rounds = dd.compare(round_left, round_right)
    assert rounds.decision != "merge"
    assert rounds.signals["marker_conflicts"] == ["round"]


def test_poster_signal_does_not_lower_the_body_merge_bar():
    """포스터가 같다고 본문 조건을 깎지 않는다. 짧은 본문은 여전히 검토다."""
    left = _candidate("a", "s1", "안내", "붙임 참조", poster_keys=POSTER)
    right = _candidate("b", "s2", "안내", "붙임 참조", poster_keys=POSTER)
    assert dd.compare(left, right).decision != "merge"
