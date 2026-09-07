// 가상 데이터. 실제 경희대 공지·연락처가 아니다. 규격 초안 9절의 시나리오를 덮도록 구성.
import type {
  Catalog,
  Contact,
  Notice,
  NoticeDetail,
  Organization,
  Source,
} from "@/lib/types";

const c = (code: string, label: string) => ({ code, label });
const WEB = c("web", "웹");
const IG = c("instagram", "인스타그램");
const AVAIL = c("available", "접근 가능");
const FRESH = (t = "2026-09-06T00:45:00Z") => ({ code: "fresh", label: "최근 확인됨", last_checked_at: t });

export const CATEGORIES = [
  c("academic", "학사"),
  c("graduation", "졸업"),
  c("scholarship", "장학"),
  c("career", "취업·인턴"),
  c("startup", "창업"),
  c("program", "프로그램·공모전"),
  c("international", "국제"),
  c("event", "행사"),
  c("student_council", "학생회"),
  c("campus_life", "생활"),
  c("other", "기타"),
];
const CAT = Object.fromEntries(CATEGORIES.map((x) => [x.code, x]));

export const catalog: Catalog = {
  campuses: [
    { id: "campus-seoul", name: "서울캠퍼스" },
    { id: "campus-global", name: "국제캠퍼스" },
  ],
  organization_types: [
    c("university", "대학"),
    c("campus", "캠퍼스"),
    c("college", "단과대학"),
    c("department", "학과"),
    c("office", "행정부서·센터"),
    c("council", "학생자치기구"),
    c("institute", "사업단·연구소"),
  ],
  categories: CATEGORIES,
  media: [WEB, IG, c("submitted", "기관 제공 자료")],
  features: { accounts: false, instagram: true, deadlines: false },
};

/* ---------- organizations ---------- */
const O = (
  id: string,
  name: string,
  type: Organization["type"]["code"],
  parent_id: string | null,
  campus_id: string | null,
  has_children = false,
  is_alias = false,
): Organization => ({
  id,
  name,
  type: catalog.organization_types.find((t) => t.code === type) as Organization["type"],
  parent_id,
  campus_id,
  has_children,
  is_alias,
});

export const organizations: Organization[] = [
  O("org-khu", "경희대학교", "university", null, null, true),
  O("org-seoul", "서울캠퍼스", "campus", "org-khu", "campus-seoul", true),
  O("org-global", "국제캠퍼스", "campus", "org-khu", "campus-global", true),
  // 서울 단과대
  O("org-sma", "정경대학", "college", "org-seoul", "campus-seoul", true),
  O("org-biz", "경영대학", "college", "org-seoul", "campus-seoul", true),
  O("org-hum", "문과대학", "college", "org-seoul", "campus-seoul", true),
  O("org-sci", "이과대학", "college", "org-seoul", "campus-seoul", true),
  O("org-hotel", "호텔관광대학", "college", "org-seoul", "campus-seoul", true),
  // 정경대 학과
  O("org-pa", "행정학과", "department", "org-sma", "campus-seoul"),
  O("org-pol", "정치외교학과", "department", "org-sma", "campus-seoul"),
  O("org-econ", "경제학과", "department", "org-sma", "campus-seoul"),
  O("org-soc", "사회학과", "department", "org-sma", "campus-seoul"),
  O("org-trade", "무역학과", "department", "org-sma", "campus-seoul"),
  O("org-media", "미디어학과", "department", "org-sma", "campus-seoul", true),
  // 별칭: 같은 학과가 옛 이름으로 한 번 더 등록된 것. 트리에 줄을 만들지 않고
  // 게시판 수·공지 수를 미디어학과 줄에 합쳐 보여 준다.
  O("org-media-old", "언론정보학과", "department", "org-media", "campus-seoul", false, true),
  O("org-biz-ba", "경영학과", "department", "org-biz", "campus-seoul"),
  O("org-hum-kor", "국어국문학과", "department", "org-hum", "campus-seoul"),
  // 서울 기관
  O("org-career", "미래인재센터", "office", "org-seoul", "campus-seoul"),
  O("org-scholar", "학생지원센터", "office", "org-seoul", "campus-seoul"),
  O("org-oia", "국제교류처", "office", "org-khu", null),
  O("org-startup", "창업지원단", "office", "org-seoul", "campus-seoul"),
  O("org-bi", "창업보육센터", "office", "org-seoul", "campus-seoul"),
  O("org-lib", "중앙도서관", "office", "org-seoul", "campus-seoul"),
  O("org-dorm", "서울 생활관", "office", "org-seoul", "campus-seoul"),
  O("org-hr", "인권센터", "office", "org-khu", null),
  O("org-council-seoul", "서울캠퍼스 총학생회", "council", "org-seoul", "campus-seoul"),
  O("org-council-sma", "정경대학 학생회", "council", "org-sma", "campus-seoul"),
  O("org-council-pa", "행정학과 학생회", "council", "org-pa", "campus-seoul"),
  O("org-clubs", "동아리연합회", "council", "org-seoul", "campus-seoul"),
  // 국제
  O("org-eng", "공과대학", "college", "org-global", "campus-global", true),
  O("org-me", "기계공학과", "department", "org-eng", "campus-global"),
  O("org-sw", "SW중심대학사업단", "institute", "org-global", "campus-global"),
  O("org-rise", "RISE사업단", "institute", "org-khu", null),
  O("org-council-global", "국제캠퍼스 총학생회", "council", "org-global", "campus-global"),
];

/* ---------- sources ---------- */
const S = (
  id: string,
  name: string,
  orgId: string,
  medium: typeof WEB,
  status: [string, string],
  last_success_at: string | null,
  extra: Partial<Source> = {},
): Source => {
  const org = organizations.find((o) => o.id === orgId)!;
  return {
    id,
    name,
    organization: { id: org.id, name: org.name },
    campus_id: org.campus_id,
    medium,
    content_kind: c("notice", "공지"),
    url: medium.code === "web" ? `https://example.org/${id}` : `https://www.instagram.com/${id}/`,
    status: c(status[0], status[1]),
    status_message: null,
    last_success_at,
    initial_window_days: null,
    ...extra,
  };
};
const ACTIVE: [string, string] = ["active", "수집 중"];

export const sources: Source[] = [
  S("src-pa-web", "행정학과 홈페이지", "org-pa", WEB, ACTIVE, "2026-09-06T00:45:00Z"),
  S("src-pa-ig", "행정학과 학생회 Instagram", "org-council-pa", IG, ACTIVE, "2026-09-05T22:40:00Z"),
  S("src-sma-web", "정경대학 교학팀 공지", "org-sma", WEB, ACTIVE, "2026-09-06T00:45:00Z"),
  S("src-sma-ig", "정경대학 학생회 Instagram", "org-council-sma", IG, ["delayed", "갱신 지연"], "2026-09-04T09:00:00Z", {
    status_message: "마지막 성공 이후 하루 넘게 갱신되지 않았습니다.",
  }),
  S("src-econ-web", "경제학과 홈페이지", "org-econ", WEB, ACTIVE, "2026-09-06T00:45:00Z"),
  S("src-career-web", "미래인재센터 취업공지", "org-career", WEB, ACTIVE, "2026-09-06T00:45:00Z"),
  S("src-scholar-web", "학생지원센터 장학공지", "org-scholar", WEB, ACTIVE, "2026-09-06T00:45:00Z", {
    initial_window_days: 90,
  }),
  S("src-startup-web", "창업지원단 공지", "org-startup", WEB, ["blocked", "접근 제한"], "2026-09-03T00:45:00Z", {
    status_message: "3일째 원문 사이트 응답이 없습니다. 저장된 공지는 계속 볼 수 있습니다.",
  }),
  S("src-sw-web", "SW중심대학사업단 공지", "org-sw", WEB, ACTIVE, "2026-09-06T00:30:00Z"),
  S("src-sw-ig", "SW중심대학사업단 Instagram", "org-sw", IG, ["pending", "연결·검증 대기"], null, {
    status_message: "공식 계정 연결을 기다리는 중입니다. 아직 수집하지 않습니다.",
  }),
  S("src-lib-web", "중앙도서관 공지", "org-lib", WEB, ACTIVE, "2026-09-06T00:45:00Z"),
  S("src-dorm-web", "서울 생활관 공지", "org-dorm", WEB, ACTIVE, "2026-09-06T00:45:00Z"),
  S("src-council-seoul-web", "서울캠퍼스 총학생회 홈페이지", "org-council-seoul", WEB, ACTIVE, "2026-09-06T00:20:00Z"),
  S("src-council-seoul-ig", "서울캠퍼스 총학생회 Instagram", "org-council-seoul", IG, ACTIVE, "2026-09-06T00:20:00Z"),
  S("src-clubs-ig", "동아리연합회 Instagram", "org-clubs", IG, ACTIVE, "2026-09-05T19:00:00Z"),
  S("src-khu-academic", "경희대학교 학사공지", "org-khu", WEB, ACTIVE, "2026-09-06T00:45:00Z"),
  S("src-oia-web", "국제교류처 공지", "org-oia", WEB, ACTIVE, "2026-09-06T00:45:00Z"),
  S("src-bi-ig", "창업보육센터 Instagram", "org-bi", IG, ACTIVE, "2026-09-05T23:00:00Z"),
  S("src-rise-web", "RISE사업단 공지", "org-rise", WEB, ACTIVE, "2026-09-06T00:45:00Z"),
  S("src-me-web", "기계공학과 홈페이지", "org-me", WEB, ["paused", "운영 중지"], "2026-08-20T00:45:00Z", {
    status_message: "게시판 구조 변경으로 어댑터를 점검 중입니다.",
  }),
  // 별칭 조직에 달린 게시판. 화면에는 미디어학과 줄 하나로만 나와야 한다.
  S("src-media-old-web", "언론정보학과 홈페이지", "org-media-old", WEB, ACTIVE, "2026-09-06T00:45:00Z"),
];

// 게시판 수는 등록부에서 세어 온 값이다. 공개 파일이 주는 것과 같은 방식으로 채워
// 화면 판정(감출지 말지)이 가상 자료에서도 실제와 같게 돌게 한다.
// 폐쇄·대기·운영 중지는 active 에서 뺀다. 접근 제한은 받아 둔 공지가 남아 있으므로 넣는다.
const LIVE_STATUS = new Set(["active", "delayed", "blocked"]);
for (const org of organizations) {
  const own = sources.filter((s) => s.organization.id === org.id);
  org.source_count = own.length;
  org.active_source_count = own.filter((s) => LIVE_STATUS.has(s.status.code)).length;
}
const SRC = Object.fromEntries(sources.map((s) => [s.id, s]));

/* ---------- notices ---------- */
interface Seed {
  id: string;
  title: string;
  cat: string;
  sub?: string[];
  aud: { type: "university" | "campus" | "organization" | "unknown"; id: string | null; name: string }[];
  audNote?: string | null;
  src: string;
  srcs?: { src: string; date?: string | null }[];
  date: string | null;
  at?: string | null;
  precision?: "date" | "datetime" | "unknown";
  visible: string;
  body: string | null;
  html?: string | null;
  att?: { filename: string; type: string; size: number | null; ok?: boolean }[];
  mentions?: { text: string; kind?: "phone" | "email"; value?: string; contact?: string }[];
  status?: [string, string];
  excerpt?: string | null;
}

const seeds: Seed[] = [
  {
    id: "n-001",
    title: "2026학년도 2학기 학점포기 신청 안내",
    cat: "academic",
    aud: [{ type: "organization", id: "org-sma", name: "정경대학" }],
    src: "src-sma-web",
    srcs: [{ src: "src-sma-web" }, { src: "src-pa-web" }, { src: "src-khu-academic", date: "2026-09-02" }],
    date: "2026-09-03",
    visible: "2026-09-03T01:10:00Z",
    body:
      "2026학년도 2학기 학점포기 신청을 아래와 같이 안내합니다.\n\n신청기간: 2026.09.08(월) 10:00 ~ 2026.09.11(목) 17:00\n신청방법: Info21 → 수업/성적 → 학점포기 신청\n대상: 4학년 및 졸업예정자(F학점 제외)\n\n※ 학점포기 후 취소는 불가하니 신중히 신청 바랍니다.",
    att: [{ filename: "학점포기_안내문.hwp", type: "hwp", size: 48_120 }],
    mentions: [{ text: "문의: 정경대학 교학팀 02-961-0499", kind: "phone", value: "02-961-0499", contact: "ct-sma" }],
  },
  {
    id: "n-002",
    title: "2026 대동제 부스 참가 단체 모집",
    cat: "event",
    sub: ["student_council"],
    aud: [{ type: "campus", id: "campus-seoul", name: "서울캠퍼스" }],
    src: "src-council-seoul-ig",
    date: "2026-09-05",
    at: "2026-09-05T09:12:00Z",
    precision: "datetime",
    visible: "2026-09-05T09:30:00Z",
    body:
      "2026 대동제 부스 모집\n\n올해 축제를 함께 만들어갈 부스 참가 단체를 모집합니다.\n\n모집기간: 9/5 ~ 9/14\n신청: 프로필 링크\n문의: DM",
    excerpt: "올해 축제를 함께 만들어갈 부스 참가 단체를 모집합니다. 모집기간 9/5 ~ 9/14",
  },
  {
    id: "n-003",
    title: "2026학년도 2학기 국가장학금 2차 신청 안내 (신입생·복학생·재학생 재신청)",
    cat: "scholarship",
    aud: [{ type: "university", id: "org-khu", name: "경희대학교" }],
    src: "src-scholar-web",
    srcs: [
      { src: "src-scholar-web" },
      { src: "src-sma-web" },
      { src: "src-pa-web" },
      { src: "src-council-seoul-ig", date: "2026-09-05" },
    ],
    date: "2026-09-04",
    visible: "2026-09-04T00:50:00Z",
    body:
      "한국장학재단 국가장학금 2차 신청 기간을 안내합니다.\n\n신청기간: 2026.08.28(목) 09:00 ~ 2026.09.08(월) 18:00\n서류제출 및 가구원 동의: ~ 2026.09.15(월) 18:00\n\n신청대상: 1차 신청을 놓친 재학생, 신입생, 편입생, 복학생\n\n자세한 내용은 첨부파일과 한국장학재단 홈페이지를 확인하세요.",
    att: [{ filename: "국가장학금_2차_신청안내.pdf", type: "pdf", size: 812_400 }],
    mentions: [{ text: "학생지원센터 장학팀 02-961-0067", kind: "phone", value: "02-961-0067", contact: "ct-scholar" }],
  },
  {
    id: "n-004",
    title: "[미래인재센터] 2026-2 하반기 채용연계형 인턴십 참여기업 및 모집 공고",
    cat: "career",
    aud: [{ type: "campus", id: "campus-seoul", name: "서울캠퍼스" }],
    audNote: "3학년 이상 재학생 대상이라고 원문에 적혀 있습니다. 자격은 원문에서 확인하세요.",
    src: "src-career-web",
    srcs: [{ src: "src-career-web" }, { src: "src-khu-academic" }],
    date: "2026-09-04",
    visible: "2026-09-04T02:00:00Z",
    body: null,
    html:
      "<p>2026학년도 2학기 채용연계형 인턴십 프로그램 참여 학생을 모집합니다.</p><table><thead><tr><th>구분</th><th>내용</th></tr></thead><tbody><tr><td>접수기간</td><td>2026.09.01 ~ 2026.09.19 17:00</td></tr><tr><td>참여기업</td><td>삼성SDS, 카카오엔터프라이즈, 한화시스템 외 12개사</td></tr><tr><td>지원자격</td><td>3학년 이상 재학생 (졸업예정자 포함)</td></tr><tr><td>근무기간</td><td>2026.10 ~ 2027.02 (5개월)</td></tr></tbody></table><p>참여기업 목록과 신청서는 첨부파일을 확인하세요.</p>",
    att: [
      { filename: "참여기업_목록.xlsx", type: "xlsx", size: 31_200 },
      { filename: "신청서.hwp", type: "hwp", size: 22_000 },
    ],
  },
  {
    id: "n-005",
    title: "행정학과 학생회 2학기 개강총회 안내",
    cat: "student_council",
    aud: [{ type: "organization", id: "org-pa", name: "행정학과" }],
    src: "src-pa-ig",
    date: "2026-09-05",
    visible: "2026-09-05T13:00:00Z",
    body: "2026-2 행정학과 개강총회\n\n일시: 9/10(수) 18:30\n장소: 청운관 B117\n\n총회 후 뒤풀이가 있습니다. 많은 참여 부탁드립니다.",
  },
  {
    id: "n-006",
    title: "2027학년도 전기 졸업논문(졸업시험) 신청 및 제출 일정",
    cat: "graduation",
    aud: [{ type: "organization", id: "org-pa", name: "행정학과" }],
    src: "src-pa-web",
    date: "2026-09-02",
    visible: "2026-09-02T04:00:00Z",
    body: "졸업논문 신청기간: 2026.09.15 ~ 2026.09.26\n제출기한: 2026.11.20 17:00\n\n지도교수 배정 후 논문계획서를 제출하시기 바랍니다.",
    att: [{ filename: "졸업논문_양식.hwp", type: "hwp", size: 19_800 }],
  },
  {
    id: "n-007",
    title: "SW중심대학사업단 2026 KHU 해커톤 참가팀 모집",
    cat: "program",
    aud: [{ type: "university", id: "org-khu", name: "경희대학교" }],
    src: "src-sw-web",
    srcs: [{ src: "src-sw-web" }],
    date: "2026-09-01",
    visible: "2026-09-01T06:00:00Z",
    body: "모집기간: 2026.09.01 ~ 2026.09.21\n대상: 전공 무관 재학생 3~4인 팀\n시상: 대상 300만원 외\n\n포스터를 참고하세요.",
    att: [{ filename: "해커톤_포스터.jpg", type: "image", size: 1_204_000 }],
  },
  {
    id: "n-008",
    title: "2027-1학기 교환학생 파견 선발 설명회 (서울/국제 동시 진행)",
    cat: "international",
    aud: [{ type: "university", id: "org-khu", name: "경희대학교" }],
    src: "src-oia-web",
    srcs: [{ src: "src-oia-web" }, { src: "src-khu-academic" }],
    date: "2026-09-03",
    visible: "2026-09-03T07:30:00Z",
    body: "설명회 일시: 2026.09.12(금) 15:00\n장소: 서울 청운관 지하1층 / 국제 중앙도서관 시청각실\n\n파견 신청기간: 2026.09.22 ~ 2026.10.02",
    mentions: [{ text: "국제교류처 oia@example.org", kind: "email", value: "oia@example.org", contact: "ct-oia" }],
  },
  {
    id: "n-009",
    title: "제2생활관 2학기 추가모집 및 입사 안내",
    cat: "campus_life",
    aud: [{ type: "campus", id: "campus-seoul", name: "서울캠퍼스" }],
    src: "src-dorm-web",
    date: "2026-08-30",
    visible: "2026-08-30T01:00:00Z",
    body: "추가모집 신청: 2026.09.01 ~ 2026.09.05\n입사일: 2026.09.07",
  },
  {
    id: "n-010",
    title: "[창업지원단] 예비창업패키지 사전 멘토링 프로그램 참가자 모집",
    cat: "startup",
    aud: [{ type: "campus", id: "campus-seoul", name: "서울캠퍼스" }],
    src: "src-startup-web",
    date: "2026-08-29",
    visible: "2026-08-29T05:00:00Z",
    body: "모집기간: 2026.09.01 ~ 2026.09.17\n대상: 창업 아이템을 보유한 재학생·휴학생",
    status: ["unavailable", "확인 불가"],
  },
  {
    id: "n-011",
    title: "정경대 학생회 × 총학 연합 중고서적 나눔장터",
    cat: "student_council",
    sub: ["event"],
    aud: [{ type: "organization", id: "org-sma", name: "정경대학" }],
    src: "src-sma-ig",
    srcs: [{ src: "src-sma-ig" }, { src: "src-council-seoul-ig" }],
    date: "2026-09-04",
    visible: "2026-09-04T10:00:00Z",
    body: "개강 맞이 중고서적 나눔장터\n9/9(화) ~ 9/10(수) 청운관 1층 로비",
  },
  {
    id: "n-012",
    title: "2026학년도 2학기 수강신청 정정기간 및 증원 요청 절차 안내",
    cat: "academic",
    aud: [{ type: "university", id: "org-khu", name: "경희대학교" }],
    src: "src-khu-academic",
    srcs: [
      { src: "src-khu-academic" },
      { src: "src-sma-web" },
      { src: "src-pa-web" },
      { src: "src-econ-web" },
      { src: "src-council-seoul-ig", date: "2026-08-29" },
    ],
    date: "2026-08-28",
    visible: "2026-08-28T01:00:00Z",
    body: "정정기간: 2026.09.01 ~ 2026.09.05\n증원 요청은 각 학과 사무실로 문의하시기 바랍니다.",
  },
  {
    id: "n-013",
    title: "중앙도서관 야간 열람실 운영시간 변경 안내 (9/8~)",
    cat: "campus_life",
    aud: [{ type: "campus", id: "campus-seoul", name: "서울캠퍼스" }],
    src: "src-lib-web",
    date: "2026-09-05",
    visible: "2026-09-05T08:00:00Z",
    body: "9월 8일부터 야간열람실을 24시간 운영합니다.",
  },
  {
    id: "n-014",
    title: "2026 KHU 창업동아리 데모데이 관람 신청",
    cat: "startup",
    sub: ["event"],
    aud: [{ type: "unknown", id: null, name: "대상 미확정" }],
    audNote: "원문에 대상 캠퍼스가 적혀 있지 않습니다.",
    src: "src-bi-ig",
    date: "2026-09-05",
    visible: "2026-09-05T14:30:00Z",
    body: "데모데이 9/18 오후 2시, 청운관 대강당\n관람 신청 링크는 프로필 참고",
  },
  {
    id: "n-015",
    title: "RISE 사업단 지역연계 캡스톤디자인 참여팀 모집",
    cat: "program",
    aud: [{ type: "campus", id: "campus-global", name: "국제캠퍼스" }],
    src: "src-rise-web",
    date: "2026-09-02",
    visible: "2026-09-02T02:00:00Z",
    body: "모집기간: ~ 2026.09.16",
  },
  {
    id: "n-016",
    title: "국제캠퍼스 총학생회 2학기 학생총회 안내",
    cat: "student_council",
    aud: [{ type: "campus", id: "campus-global", name: "국제캠퍼스" }],
    src: "src-council-seoul-web",
    date: null,
    precision: "unknown",
    visible: "2026-09-05T11:00:00Z",
    body: "학생총회 일정은 추후 공지합니다.",
  },
  {
    id: "n-017",
    title: "[첨부 참조] 2026-2 교직과정 이수 신청 안내",
    cat: "academic",
    aud: [{ type: "university", id: "org-khu", name: "경희대학교" }],
    src: "src-khu-academic",
    date: "2026-09-01",
    visible: "2026-09-01T01:00:00Z",
    body: null,
    excerpt: null,
    att: [
      { filename: "교직과정_이수신청_안내.pdf", type: "pdf", size: 402_000 },
      { filename: "신청서_양식.hwp", type: "hwp", size: 18_000, ok: false },
    ],
  },
  {
    id: "n-018",
    title: "2026학년도 2학기 다전공(복수·부전공) 신청 안내",
    cat: "academic",
    aud: [{ type: "university", id: "org-khu", name: "경희대학교" }],
    src: "src-khu-academic",
    date: "2026-08-27",
    visible: "2026-08-27T01:00:00Z",
    body: "신청기간: 2026.09.15 ~ 2026.09.19\n신청방법: Info21",
  },
  {
    id: "n-019",
    title: "동아리연합회 2학기 신입 부원 모집 박람회",
    cat: "event",
    aud: [{ type: "campus", id: "campus-seoul", name: "서울캠퍼스" }],
    src: "src-clubs-ig",
    date: "2026-09-04",
    visible: "2026-09-04T12:00:00Z",
    body: "9/8 ~ 9/10 노천극장 앞",
  },
  {
    id: "n-020",
    title: "경제학과 2026-2 학과 세미나 일정",
    cat: "other",
    aud: [{ type: "organization", id: "org-econ", name: "경제학과" }],
    src: "src-econ-web",
    date: "2026-09-03",
    visible: "2026-09-03T03:00:00Z",
    body: "매주 수요일 17:00, 청운관 512호",
  },
  {
    id: "n-021",
    title: "국제캠퍼스 셔틀버스 2학기 운행 시간표",
    cat: "campus_life",
    aud: [{ type: "campus", id: "campus-global", name: "국제캠퍼스" }],
    src: "src-khu-academic",
    date: "2026-08-31",
    visible: "2026-08-31T01:00:00Z",
    body: "첨부 시간표를 확인하세요.",
    att: [{ filename: "셔틀_시간표.pdf", type: "pdf", size: 120_000 }],
  },
  {
    id: "n-022",
    title: "기계공학과 캡스톤디자인 발표회",
    cat: "event",
    aud: [{ type: "organization", id: "org-me", name: "기계공학과" }],
    src: "src-me-web",
    date: "2026-08-20",
    visible: "2026-08-20T01:00:00Z",
    body: "9/25 공학관 대강당",
  },
  {
    // 별칭 조직이 대상인 공지. 트리에서는 미디어학과 줄에 얹혀야 하고,
    // 미디어학과를 고르면 홈에도 나와야 한다.
    id: "n-023",
    title: "언론정보학과 졸업논문 제출 안내",
    cat: "academic",
    aud: [{ type: "organization", id: "org-media-old", name: "언론정보학과" }],
    src: "src-media-old-web",
    date: "2026-09-02",
    visible: "2026-09-02T02:00:00Z",
    body: "제출기한: 2026.10.15(수) 17:00\n제출처: 학과 사무실",
  },
];

const mkNotice = (s: Seed): Notice => ({
  id: s.id,
  kind: "notice",
  title: s.title,
  excerpt: s.excerpt === undefined ? (s.body ? s.body.split("\n")[0] : null) : s.excerpt,
  primary_category: CAT[s.cat],
  secondary_categories: (s.sub ?? []).map((x) => CAT[x]),
  audiences: s.aud,
  audience_note: s.audNote ?? null,
  primary_source: { id: s.src, name: SRC[s.src].name, medium: SRC[s.src].medium },
  source_count: s.srcs?.length ?? 1,
  published_date: s.date,
  published_at: s.at ?? null,
  published_precision: s.precision ?? (s.date ? "date" : "unknown"),
  first_visible_at: s.visible,
  updated_at: s.visible,
  deadline: null,
  original_url: `${SRC[s.src].url}${SRC[s.src].medium.code === "web" ? "/" + s.id : "p/" + s.id + "/"}`,
  original_status: s.status ? c(s.status[0], s.status[1]) : AVAIL,
  freshness: s.status
    ? { code: "stale", label: "원문 확인 지연", last_checked_at: "2026-09-03T00:45:00Z" }
    : FRESH(),
});

export const notices: Notice[] = seeds.map(mkNotice);

export const noticeDetails: Record<string, NoticeDetail> = Object.fromEntries(
  seeds.map((s) => {
    const base = mkNotice(s);
    const srcList = s.srcs ?? [{ src: s.src }];
    const detail: NoticeDetail = {
      ...base,
      body_text: s.body,
      body_html: s.html ?? null,
      sources: srcList.map((x, i) => ({
        id: `${s.id}-src-${i}`,
        source_id: x.src,
        source_name: SRC[x.src].name,
        medium: SRC[x.src].medium,
        url: `${SRC[x.src].url}${SRC[x.src].medium.code === "web" ? "/" + s.id : "p/" + s.id + "/"}`,
        published_date: x.date === undefined ? s.date : x.date,
        original_status: i === 0 && s.status ? c(s.status[0], s.status[1]) : AVAIL,
        is_primary: i === 0,
      })),
      attachments: (s.att ?? []).map((a, i) => ({
        id: `${s.id}-att-${i}`,
        filename: a.filename,
        type: a.type,
        size_bytes: a.size,
        url: a.ok === false ? null : `https://example.org/files/${s.id}/${i}`,
        status: a.ok === false ? c("unavailable", "다운로드 불가") : c("available", "다운로드 가능"),
      })),
      contact_mentions: (s.mentions ?? []).map((m) => ({
        text: m.text,
        channel: m.kind
          ? {
              kind: m.kind === "phone" ? c("phone", "전화") : c("email", "이메일"),
              value: m.value!,
              action_url: (m.kind === "phone" ? "tel:" : "mailto:") + m.value,
            }
          : null,
        contact_id: m.contact ?? null,
      })),
      related_notices: [],
      resolved_from_id: null,
    };
    return [s.id, detail];
  }),
);
noticeDetails["n-003"].related_notices = [{ id: "n-018", title: "2026학년도 2학기 다전공(복수·부전공) 신청 안내", relation: c("related", "관련 공지") }];

/* ---------- contacts ---------- */
interface CSeed {
  id: string;
  org: string;
  service: string;
  phones?: (string | { v: string; ext?: string })[];
  email?: string;
  fax?: string;
  site?: string;
  loc?: string | null;
  hours?: string | null;
  ver?: "verified" | "stale" | "conflict";
  campuses?: string[];
}
const VER = {
  verified: { code: "verified", label: "원문 확인됨", message: "공식 원문의 안내와 일치함을 확인한 시각입니다." },
  stale: { code: "stale", label: "확인 오래됨", message: "마지막 확인 후 60일이 지났습니다. 원문에서 다시 확인해 주세요." },
  conflict: { code: "conflict", label: "내용 충돌 검토 중", message: "두 원문의 번호가 달라 검토 중입니다. 표시된 값은 최근 원문 기준입니다." },
};
const orgPath = (id: string): string[] => {
  const path: string[] = [];
  let cur = organizations.find((o) => o.id === id);
  while (cur) {
    path.unshift(cur.name);
    cur = cur.parent_id ? organizations.find((o) => o.id === cur!.parent_id) : undefined;
  }
  return path;
};
const cseeds: CSeed[] = [
  { id: "ct-pa", org: "org-pa", service: "학과 사무실 (학적·수강)", phones: ["02-961-0489"], email: "pa@example.org", site: "https://example.org/pa", loc: "서울 청운관 4층 404호", hours: "평일 09:00–17:30 (점심 12:00–13:00)" },
  { id: "ct-sma", org: "org-sma", service: "교학팀 (학사 전반)", phones: ["02-961-0499", "02-961-0500"], fax: "02-961-0501", email: "khsma@example.org", loc: "서울 청운관 1층", hours: "평일 09:00–17:30" },
  { id: "ct-scholar", org: "org-scholar", service: "장학 문의", phones: [{ v: "02-961-0067" }, { v: "02-961-0060", ext: "2301" }], email: "scholarship@example.org", loc: "서울 학생회관 2층", hours: "평일 09:00–17:30" },
  { id: "ct-haksa", org: "org-khu", service: "교무처 학사팀 (휴학·복학·전과)", phones: ["02-961-0031"], email: "haksa@example.org", loc: "서울 본관 1층", hours: "평일 09:00–17:30", campuses: ["campus-seoul", "campus-global"] },
  { id: "ct-career", org: "org-career", service: "취업·인턴십 상담", phones: ["02-961-0173"], email: "career@example.org", site: "https://example.org/career", loc: "서울 청운관 지하 1층", hours: "평일 09:00–17:30" },
  { id: "ct-oia", org: "org-oia", service: "교환학생·국제교류", phones: ["02-961-0033"], email: "oia@example.org", loc: "서울 본관 2층", hours: null, campuses: ["campus-seoul", "campus-global"] },
  { id: "ct-council-seoul", org: "org-council-seoul", service: "총학생회 대표 문의", phones: ["02-961-0821"], loc: "서울 학생회관 3층", hours: "학기 중 10:00–18:00", ver: "stale" },
  { id: "ct-dorm", org: "org-dorm", service: "생활관 행정실", phones: ["02-961-0221"], email: "dorm@example.org", loc: null, hours: "평일 09:00–17:30", ver: "conflict" },
  { id: "ct-hr", org: "org-hr", service: "인권센터 상담·신고", phones: ["02-961-0891"], email: "humanrights@example.org", loc: "서울 학생회관 4층", hours: "평일 09:00–17:30", campuses: ["campus-seoul", "campus-global"] },
  { id: "ct-sw", org: "org-sw", service: "SW중심대학사업단 행정", phones: [{ v: "031-201-3873", ext: "3873" }], email: "sw@example.org", loc: "국제 전자정보대학관 3층", hours: "평일 09:00–17:30" },
  { id: "ct-lib", org: "org-lib", service: "도서관 이용 문의", phones: [{ v: "", ext: "0072" }], loc: "서울 중앙도서관 1층", hours: "평일 09:00–21:00" },
  { id: "ct-econ", org: "org-econ", service: "학과 사무실", phones: ["02-961-0482"], email: "econ@example.org", loc: "서울 청운관 5층", hours: "평일 09:00–17:30" },
];

export const contacts: Contact[] = cseeds.map((s) => {
  const org = organizations.find((o) => o.id === s.org)!;
  const channels: Contact["channels"] = [];
  let n = 0;
  for (const p of s.phones ?? []) {
    const v = typeof p === "string" ? { v: p } : p;
    n++;
    channels.push({
      id: `${s.id}-ch-${n}`,
      kind: c("phone", "전화") as Contact["channels"][number]["kind"],
      display_value: v.v ? (v.ext ? `${v.v} (내선 ${v.ext})` : v.v) : `내선 ${v.ext}`,
      value: v.v || null,
      action_url: v.v ? `tel:${v.v.replace(/-/g, "")}` : null,
      extension: v.ext ?? null,
    });
  }
  if (s.fax) channels.push({ id: `${s.id}-fax`, kind: c("fax", "팩스") as Contact["channels"][number]["kind"], display_value: s.fax, value: s.fax, action_url: null, extension: null });
  if (s.email) channels.push({ id: `${s.id}-mail`, kind: c("email", "이메일") as Contact["channels"][number]["kind"], display_value: s.email, value: s.email, action_url: `mailto:${s.email}`, extension: null });
  if (s.site) channels.push({ id: `${s.id}-web`, kind: c("website", "공식 링크") as Contact["channels"][number]["kind"], display_value: s.site.replace("https://", ""), value: s.site, action_url: s.site, extension: null });
  const ver = VER[s.ver ?? "verified"];
  const verified_at = s.ver === "stale" ? "2026-06-20T03:00:00Z" : "2026-09-05T03:00:00Z";
  return {
    id: s.id,
    kind: "contact",
    organization: { id: org.id, name: org.name, path: orgPath(org.id), type: org.type },
    campuses: (s.campuses ?? (org.campus_id ? [org.campus_id] : ["campus-seoul", "campus-global"])).map((id) => catalog.campuses.find((cp) => cp.id === id)!),
    service_name: s.service,
    channels,
    location: s.loc ?? null,
    office_hours: s.hours ?? null,
    official_url: s.site ?? null,
    verification: { ...ver, verified_at },
    evidence: channels.map((ch) => ({ field: `channels.${ch.id}`, url: `https://example.org/${org.id}/contact`, source_name: `${org.name} 연락처 안내`, observed_at: verified_at })),
  };
});
