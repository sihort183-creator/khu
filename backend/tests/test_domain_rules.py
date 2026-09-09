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


# 2026-09-09 장학 탭 실측(공개 색인 22,941건)에서 나온 실제 제목들. 규칙을 고칠 때
# 이 글들이 다시 엉뚱한 탭으로 가지 않도록 붙잡아 둔다.


def test_title_keyword_beats_body_keyword():
    # 조교 모집 본문의 "장학금 지급" 한 마디가 제목의 채용을 이기던 문제.
    body = "행정 업무를 돕는 조교를 모집합니다. " * 20 + "선발된 조교에게는 장학금을 지급합니다."
    decision = cat.classify("[법학계열 종합행정실] 2026-2학기 조교 모집", body)
    assert decision.primary == "career"
    assert decision.rule_name == "title_keyword"
    decision = cat.classify("[NH농협손해보험] 2026년 상반기 신규직원 채용", "장학생 우대 " * 5)
    assert decision.primary == "career"


def test_scholarship_body_evidence_only_counts_in_the_lead():
    # 본문 뒤쪽에서 장학금 조건을 길게 적는 고시반·연수·발굴단 안내는 장학이 아니다.
    late = "학생정책발굴단을 모집합니다. " * 30 + "활동 우수자에게는 장학금을 지급합니다. 장학금 장학금 장학금"
    decision = cat.classify("2026년 대학혁신지원사업 학생정책발굴단 모집", late)
    assert decision.primary != "scholarship"
    assert "scholarship" not in decision.all_codes
    # 앞머리에서 곧장 장학을 말하면 제목에 없어도 장학이다.
    lead = "2026학년도 2학기 국가근로장학금 신청을 안내합니다. " + "세부 내용 " * 100
    decision = cat.classify("2026학년도 2학기 국가근로 신청 안내", lead)
    assert decision.primary == "scholarship"
    assert decision.rule_name == "body_lead_keyword"


def test_bare_scholarship_word_in_title_is_scholarship():
    # "장학금·장학생" 만 보던 규칙이 교내 장학 이름 99건을 기타로 보냈다.
    for title in (
        "2026-1학기 융합인재장학 신청 안내",
        "[공통] 2026학년도 2학기 우정장학(학업장려금) 신청 안내",
        "일반대학원 우수연구자 추천장학 선발 안내",
        "2026학년도 2학기 장학대상자 재직증명서 제출 안내",
    ):
        assert cat.classify(title, "").primary == "scholarship", title


def test_scholarship_officer_hiring_is_not_scholarship():
    assert cat.classify("2026년 교육청 장학사 채용 공고", "").primary == "career"
    assert cat.classify("2026년 장학관 임용 후보자 공모", "").primary != "scholarship"


def test_scholarship_outranks_graduation_startup_international_keywords():
    for title in (
        "2026학년도 1학기 일반대학원 학술지게재 및 학술대회 논문발표장학 신청 안내",
        "[창업교육센터] 2026-1학기 스타트업(StartUp) 장학 신청 안내",
        "제 36차 미래인재 해외교환 장학생 선발 안내 (2027-1학기 파견 교환학생 대상)",
        "[수원시국제교류센터] 2026년도 중국 지난대학교 어학연수 장학생 추가모집 안내",
    ):
        decision = cat.classify(title, "")
        assert decision.primary == "scholarship", title
    # 밀린 주제는 보조로 남는다.
    assert "startup" in cat.classify("2026-1학기 스타트업 장학 안내", "").secondary


def test_campus_topic_prefix_reads_the_topic_not_the_campus():
    # "[국제_국가장학]" 의 국제는 국제캠퍼스다. 최장 일치로 국가장학이 먼저 잡혀야 한다.
    for title in (
        "[국제_국가장학] [주거안정장학] 2026-2학기 1차 주거안정장학금 학생 신청 홍보 안내",
        "[국제_국가근로] 26-1학기 하계방학 국가근로장학 모집 안내 공고문",
        "[국제_교내장학] [국제C] 2026학년도 1학기 마일리지 장학금 지급 신청 안내",
    ):
        assert cat.classify(title, "").primary == "scholarship", title


def test_audit_findings_from_the_first_dry_run():
    # 2026-09-09 재분류 모의 실행 검수(567건 중 118건 오류)에서 나온 패턴들.
    assert cat.classify("2026년 하반기 효명장학사업", "", board_category="공통").primary == "scholarship"
    # 부분 문자열: 현장학습의 "장학", 전공연수의 "공연".
    assert cat.classify("미술교육전공 역량 강화 세종문화회관 미술관 현장학습", "").primary != "scholarship"
    assert cat.classify("2026학년도 사학과 하계 해외 전공연수 안내", "").primary == "international"
    # 등록·분할납부 안내는 장학 탭에 남는다. 본문 뒤쪽 "유학생" 이 끌고 가지 않는다.
    body = "등록 절차를 안내합니다. " * 30 + "외국인 유학생은 국제처로 문의하세요."
    assert cat.classify("2026학년도 2학기 학부생 등록 및 분할납부 신청 안내", body).primary == "scholarship"
    assert cat.classify("2026학년도 2학기 재학생(복학생), 수료생 등록일정 및 분할납부 신청 안내", "").primary == "scholarship"
    assert cat.classify("2026-2 Announcement of Tuition Payment Period", "", board_category="공통").primary == "scholarship"
    # 기관 이름·채용·기부의 "장학" 은 장학이 아니다.
    assert cat.classify("롯데장학재단_사회공헌 사진 공모전", "").primary == "program"
    assert cat.classify("[현대모비스] 26년 상반기 장학전환인턴 모집", "").primary == "career"
    assert cat.classify("[기부캠페인] 경희목련 희망 장학 기금", "").primary != "scholarship"
    # "수료" 는 더 이상 프로그램 신호가 아니다.
    assert cat.classify("2025학년도 전기 수료사정 결과 및 연구등록 안내", "").primary != "program"


def test_audit_findings_from_the_second_dry_run():
    # 본문 앞머리의 "휴학생 신청 불가" 가 지원금 안내를 학사로 보냈다.
    assert cat.classify("2026학년도 상반기 토익지원비 신청 안내", "휴학생은 신청할 수 없습니다. 재학생만 " * 5).primary != "academic"
    assert cat.classify("2026학년도 2학기 휴학 신청 안내", "").primary == "academic"
    # "사전등록 및", "평가위원 등록 및" 은 등록금이 아니다.
    assert cat.classify("[핀테크 생태계 만남의 장] 행사 사전등록 및 1:1 상담", "").primary == "event"
    assert cat.classify("2026학년도 2학기 학부생 등록 및 분할납부 신청 안내", "").primary == "scholarship"
    # 학위자격시험은 본문의 "등록금 납부자만 응시" 보다 제목이 먼저다.
    assert cat.classify("2026학년도 2학기 대학원 학위자격시험(종합시험) 신청 안내", "등록금 납부자만 응시할 수 있습니다.").primary == "graduation"
    # 수강료 환급 강좌와 장학 수기 공모전은 장학이 아니다.
    assert cat.classify("[국제교육원] 여름학기 장학환급 공인영어시험 대비 외국어 강좌", "").primary != "scholarship"
    assert cat.classify("2026년 (재)김해시미래인재장학재단 제3회 장학수기 공모전 공고", "").primary == "program"
    # 조교 모집의 여러 표기.
    for title in (
        "[정치외교학과] 2026학년도 2학기 학과 교육조교(TA) 모집",
        "2026학년도 1학기 경영대학 일반대학원 행정실 조교 추가 모집",
        "서울캠퍼스 교육대학원 조교 공고",
        "[서울C] 2026-2학기 ★국제교류팀★ 교육조교(TA) I형 모집",
    ):
        assert cat.classify(title, "").primary == "career", title
    assert cat.classify("경희대학교 의상학과 해외 전공 연수 프로그램 (뉴욕 FIT)", "").primary == "international"
    # 지원금·국비유학은 장학 탭이다.
    assert cat.classify("2026학년도 상반기 토익지원비 신청 안내", "", board_category="공통").primary == "scholarship"
    assert cat.classify("2026년도 국비유학(연수)생 선발 공고", "국제교류 " * 10).primary == "scholarship"


def test_body_keywords_only_decide_when_the_body_opens_with_them():
    late = "활동 내용을 안내합니다. " * 30 + "교환학생 경험자 우대, 휴학생 제외, 인턴십 연계"
    decision = cat.classify("2026년 대학혁신지원사업 학생정책발굴단 모집", late)
    assert decision.primary == "other"
    lead = "교환학생 파견 설명회를 엽니다. " + "세부 내용 " * 100
    assert cat.classify("2027학년도 1학기 파견 안내", lead).primary == "international"


def test_rule_version_marks_the_title_first_rules():
    assert cat.RULE_VERSION == "categories/2"
    assert cat.classify("아무 제목", "").rule_version == "categories/2"


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
    """스스로 전교생 대상이라 밝힌 글을 학과 공지로 좁히지 않는다.

    진짜 전교 공지가 전교 대상을 잃으면 조직을 고른 사람 모두에게서 사라진다.
    성적입력 안내가 그런 글이다. 전교 대상은 무슨 일이 있어도 남는다.
    """
    default = (aud.AudienceTarget("organization", "org-swcon", "소프트웨어융합학과"),)
    decision = aud.decide(
        "2026학년도 1학기 성적입력 및 공시(정정)기간 안내",
        "기말 강의평가 실시 여부와 상관없이 전체 학생 성적 열람 가능",
        source_defaults=default,
        campus_lookup=_campus_map(),
    )
    keys = {target.key() for target in decision.targets}
    assert "university" in keys
    # 올라온 학과와의 연결도 함께 남는다. 전교 공지라고 조직을 버리지 않는다.
    assert keys == {"university", "org:org-swcon"}


def test_university_wide_wording_keeps_the_source_organization():
    """전교생 표현이 본문에 스쳐도 출처 학과를 잃지 않는다.

    2026-09-07 실제로 의과대학 게시판의 "[의학4] 선택실습" 글이 본문의
    "의학과 4학년 … 재학생 전원" 때문에 전교 공지가 되고 의과대학과의 연결을
    잃었다. 의과대학을 고른 사람에게서 사라졌다. 그 회귀를 막는다.
    """
    default = (aud.AudienceTarget("organization", "org-med", "의과대학"),)
    decision = aud.decide(
        "[의학4] 선택실습(SDE, 학생설계선택과정) 안내",
        "1. 대상: 2026학년도 의학과 4학년(27년 2월 졸업예정자) 재학생 전원",
        source_defaults=default,
        campus_lookup=_campus_map(),
    )
    keys = {target.key() for target in decision.targets}
    assert "org:org-med" in keys


def test_university_wide_wording_does_not_add_targets_to_a_broad_source():
    """출처 기본 대상이 이미 넓으면 더할 조직이 없다. 대학 전체 하나로 남는다."""
    for default in (
        (aud.AudienceTarget("university", None, "대학 전체"),),
        (aud.AudienceTarget("campus", "campus-seoul", "서울캠퍼스"),),
    ):
        decision = aud.decide(
            "포털시스템 개선 안내",
            "전 구성원 대상입니다.",
            source_defaults=default,
            campus_lookup=_campus_map(),
        )
        assert [target.key() for target in decision.targets] == ["university"]


def test_university_wide_branch_never_drops_a_target():
    """전교 갈래는 순수 보태기다. 출처 기본 대상의 조직은 하나도 잃지 않는다."""
    default = (
        aud.AudienceTarget("organization", "org-a", "가학과"),
        aud.AudienceTarget("organization", "org-b", "나학과"),
    )
    decision = aud.decide(
        "안내", "전 구성원 공지", source_defaults=default, campus_lookup=_campus_map()
    )
    keys = {target.key() for target in decision.targets}
    assert keys == {"org:org-a", "org:org-b", "university"}


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
