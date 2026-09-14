/**
 * 바로가기 카드의 규칙(docs/바로가기_카드_설계_2026-09-14.md).
 *
 * 그리는 일은 components/ShortcutCards.tsx 가 하고, 여기에는 **무엇을 그릴지 정하는
 * 규칙만** 둔다. 화면 없이 시험할 수 있어야 하기 때문이다 — 조건을 떼지 않는다는 규칙은
 * 눈으로 보고 지나칠 수 있는 종류가 아니다.
 *
 * 카드는 세 모양이다.
 *   1형 링크    rows 를 라벨·값으로 그리고 more 를 접었다 펴고 foot 에 링크
 *   2형 값 표시 졸업요건. 학번·단과대·학과를 카드 안에서 묻고 학과별 값을 보여 준다
 *   3형 공지 지목 우리 학과 졸업 공지. 이미 받아 둔 색인에서 고르고, 없으면 그렇게 말한다
 */
import type {
  GraduationArticle,
  GraduationDepartment,
  GraduationRequirements,
  Organization,
  ShortcutCard,
  ShortcutCardRow,
} from "./types";
import { compactForSearch } from "./text";
import { drawnOrgIds } from "./orgTree";

/**
 * 검색어에 걸린 카드. 안 걸리면 빈 배열이고 화면은 아무것도 그리지 않는다.
 *
 * 맞춰 보는 방향이 중요하다. **카드의 match 말이 검색어 안에 있어야** 한다(그 반대가
 * 아니다). `휴학 신청 어떻게` 로 쳐도 `휴학` 카드가 떠야 하지만, `휴` 한 글자에
 * 휴학 카드가 뜨면 아직 다 치지도 않은 검색어에 카드가 먼저 튀어나온다.
 *
 * 맞춰 보는 꼴은 검색과 같은 붙여쓰기 판이다(lib/text.ts). `학점 포기` 로 치든
 * `학점포기` 로 치든 같은 카드가 뜬다.
 */
export function matchShortcutCards(q: string | undefined, cards: readonly ShortcutCard[]): ShortcutCard[] {
  const query = compactForSearch(q ?? "");
  if (!query) return [];
  return cards.filter((card) => card.match.some((term) => {
    const needle = compactForSearch(term);
    return needle.length > 1 && query.includes(needle);
  }));
}

/**
 * 그려도 되는 줄만 남긴다. **설계문서 5.8 의 규칙을 코드로 박은 자리다.**
 *
 * 줄에 `cond`(적용 대상 제한) 자리가 있는데 그릴 글자가 없으면 그 줄을 통째로 뺀다.
 * 조건을 떼고 숫자만 보여 주면 편입생이 SW 6학점을 들어야 하는 줄 알고, 한국 학생이
 * TOPIK 을 준비한다. 값만 남기고 조건을 흘리느니 그 줄을 안 보여 주는 편이 낫다.
 */
export function keepRows(rows: readonly (ShortcutCardRow | null)[]): ShortcutCardRow[] {
  return rows.filter((row): row is ShortcutCardRow => {
    if (!row || !row.value.trim()) return false;
    // `cond` 가 아예 없는 줄은 조건이 없는 줄이다. 조건 자리가 있는데 글자가 없을 때만 뺀다.
    return row.cond === undefined || !!row.cond.trim();
  });
}

/**
 * "최근"의 기준 — **30일**.
 *
 * 왜 30일인가(운영 색인 24,330건 실측, 2026-09-14 기준):
 *
 * ```
 * watch 말        30일   60일   90일
 * 학점포기          42     45     46
 * 졸업논문          10     15     17
 * 국가장학금        17     18     18
 * 휴학신청           0     11     52
 * 복학신청           0      4     79
 * 분할납부           0      9     71
 * ```
 *
 * 90일로 늘리면 휴학·복학·분할납부까지 전부 걸린다. 그 셋은 제도가 바뀌어서가 아니라
 * **학기마다 같은 안내가 되풀이되기 때문**에 걸리는 것이고, 그러면 카드 열세 장에 거의
 * 언제나 경고 줄이 붙는다. 늘 붙는 경고는 경고가 아니다.
 *
 * 30일이면 설계문서 5.6 이 든 학점포기 개정(8/7·9/1·9/3·9/8)은 그대로 잡으면서, 열세
 * 장 가운데 일곱 장에만 줄이 붙는다. 한 달은 학사 안내가 도는 주기이기도 하다 —
 * 지난 학기 공지를 "최근"이라고 부르지 않는다.
 */
export const WATCH_WINDOW_DAYS = 30;

/**
 * 최근으로 볼 가장 오래된 발행일(`YYYY-MM-DD`).
 *
 * 공지의 발행일은 한국시간 날짜라 여기서도 한국시간으로 센다. 브라우저가 어느 시간대에
 * 있든 같은 날을 가리켜야 한다.
 */
export function watchWindowStart(now: Date = new Date(), days = WATCH_WINDOW_DAYS): string {
  const kst = new Date(now.getTime() + 9 * 60 * 60 * 1000);
  kst.setUTCDate(kst.getUTCDate() - days);
  return kst.toISOString().slice(0, 10);
}

/* ---------------- 2형 — 학과별 졸업요건 ---------------- */

/** 고르개에 쓸 단과대 → 학과 목록. 단과대를 고르면 그 아래 학과만 남는다. */
export function departmentOptions(file: GraduationRequirements | null): { college: string; departments: string[] }[] {
  const byCollege = new Map<string, string[]>();
  for (const dept of file?.departments ?? []) {
    const list = byCollege.get(dept.college) ?? [];
    list.push(dept.department);
    byCollege.set(dept.college, list);
  }
  return [...byCollege]
    .map(([college, departments]) => ({ college, departments: departments.sort((a, b) => a.localeCompare(b, "ko")) }))
    .sort((a, b) => a.college.localeCompare(b.college, "ko"));
}

export function findDepartment(file: GraduationRequirements | null, name: string | null): GraduationDepartment | null {
  if (!file || !name) return null;
  return file.departments.find((dept) => dept.department === name) ?? null;
}

/**
 * 조문에 붙은 대상 제한을 조건 한 줄로 옮긴다.
 *
 * 제한이 **아예 없으면** `undefined` 를 준다(조건 자리가 없는 줄이다). 제한이 적혀
 * 있는데 옮길 글자를 못 만들면 빈 문자열을 주고, 그러면 keepRows 가 그 줄을 통째로
 * 뺀다 — 조건을 흘린 채 값만 남기지 않는다(설계문서 5.8).
 */
function conditionOf(article: GraduationArticle | undefined): string | undefined {
  const targets = article?.applies_to ?? [];
  const conditions = article?.conditions ?? [];
  if (!targets.length && !conditions.length) return undefined;
  const named = targets.filter(Boolean);
  if (named.length) return `대상 제한 — ${named.join(" · ")}`;
  const text = conditions.filter(Boolean).join(" · ");
  // 조문 원문을 그대로 싣는 자리라, 뽑아 둔 조건이 값 안에 이미 글자 그대로 있는 일이
  // 많다(졸업논문 조문). 그때 한 번 더 적으면 같은 말이 두 번 나온다. 조건이 사라지는
  // 것이 아니라 값 안에 그대로 보이므로 5.8 규칙에 어긋나지 않는다.
  return text && article?.text?.includes(text) ? undefined : text;
}

/** `제10조(졸업논문)` 같은 조문 이름을 덧붙임 줄로. 어디서 온 값인지 학생이 짚을 수 있게. */
const articleNote = (article: GraduationArticle | { article?: string } | undefined) =>
  article?.article ? `시행세칙 ${article.article}` : undefined;

const track = (label: string, value?: { basic?: number; required?: number; total?: number }) => {
  if (!value?.total) return null;
  const parts = [
    value.basic ? `전공기초 ${value.basic}` : null,
    value.required ? `전공필수 ${value.required}` : null,
  ].filter(Boolean);
  return `${label} ${value.total}학점${parts.length ? ` (${parts.join(" · ")})` : ""}`;
};

/**
 * 학과 하나의 졸업요건을 카드 줄로 옮긴다.
 *
 * 원문 조문 글자를 줄이지 않는다. 특히 **졸업논문 대체조건**은 "논문 안 써도 되는 길"이라
 * 학생에게 가장 실용적인 칸이고, 학과마다 완전히 달라 요약하면 뜻이 바뀐다(설계문서 7절).
 * 대상 제한이 붙은 조문은 조건을 함께 싣고, 조건을 못 실으면 keepRows 가 그 줄을 뺀다.
 */
export function graduationRows(dept: GraduationDepartment): ShortcutCardRow[] {
  const rows: (ShortcutCardRow | null)[] = [];

  if (dept.graduation_credits?.value) {
    rows.push({
      label: "졸업학점",
      value: `${dept.graduation_credits.value}학점`,
      sub: articleNote(dept.graduation_credits),
      strong: true,
    });
  } else if (dept.not_extracted?.graduation_credits) {
    // 값을 지어내지 않는다. 못 뽑았다는 사실과 어디서 확인하는지를 대신 적는다.
    rows.push({ label: "졸업학점", value: "원문에서 확인하지 못했습니다 — 아래 시행세칙 원문을 보세요" });
  }

  const tracks = dept.major_credits?.tracks;
  const single = track("단일전공", tracks?.single);
  if (single) {
    const others = [track("다전공", tracks?.multi), track("부전공", tracks?.minor)].filter(Boolean).join(" · ");
    rows.push({ label: "전공학점", value: single, sub: others || articleNote(dept.major_credits), strong: true });
  }

  if (dept.thesis?.text) {
    rows.push({
      label: "졸업논문",
      value: dept.thesis.text,
      cond: conditionOf(dept.thesis),
      sub: articleNote(dept.thesis),
    });
  }

  if (dept.area_requirements?.text) {
    rows.push({
      label: "전공 영역별 의무",
      value: dept.area_requirements.text,
      cond: conditionOf(dept.area_requirements),
      sub: articleNote(dept.area_requirements),
    });
  }

  // 졸업논문 조문에서 어학 요건을 함께 뽑은 학과가 있다. 같은 글을 두 번 그리지 않는다.
  for (const cert of dept.language_cert ?? []) {
    if (cert.text === dept.thesis?.text) continue;
    rows.push({
      label: "어학·자격증",
      value: cert.text,
      cond: conditionOf(cert),
      sub: articleNote(cert),
    });
  }

  // 전교 공통 조문이지만 이 학과는 값이 다른 것. 지웠으면 학생이 졸업을 못 한다(4.2).
  for (const override of Object.values(dept.shared_article_overrides ?? {})) {
    if (!override?.text) continue;
    rows.push({
      label: "우리 학과 기준",
      value: override.text,
      cond: conditionOf(override),
      sub: articleNote(override),
    });
  }

  for (const transfer of dept.transfer_students ?? []) {
    rows.push({
      label: "편입생",
      value: transfer.text,
      cond: conditionOf(transfer),
      sub: articleNote(transfer),
    });
  }

  return keepRows(rows);
}

/**
 * 학번과 시행세칙 학년도가 어긋난다는 사실을 밝히는 한 줄.
 *
 * 우리가 가진 것은 **지금 걸려 있는 시행세칙 한 판**뿐이다. 학번마다 다른 경과조치는
 * 부칙에 있고 아직 뽑지 않았다. 그러니 학번을 받아 놓고 같은 값을 보여 주면서 "당신
 * 학번 기준"이라고 말하면 거짓말이 된다. 학번이 없으면 최신 학년도 기준임을 밝히고,
 * 학번이 시행세칙보다 오래됐으면 경과조치를 확인하라고 말한다(설계문서 7절).
 */
export function graduationYearNote(dept: GraduationDepartment, admissionYear: number | null): string {
  const year = dept.year ? `${dept.year}학년도` : "가장 최근";
  if (!admissionYear) return `학번을 넣지 않아 ${year} 시행세칙 기준으로 보여 드립니다`;
  if (dept.year && admissionYear < dept.year) {
    return `${year} 시행세칙 기준입니다 — ${admissionYear}학번은 부칙 경과조치가 다를 수 있어 학과 행정실에 확인하세요`;
  }
  return `${admissionYear}학번 · ${year} 시행세칙 기준입니다`;
}

/** 학번 칸에 들어온 글자. 네 자리 연도만 받고 아니면 없는 것으로 친다. */
export function parseAdmissionYear(value: string | null | undefined): number | null {
  const year = Number((value ?? "").trim());
  return Number.isInteger(year) && year >= 1950 && year <= 2100 ? year : null;
}

/* ---------------- 3형 — 우리 학과 공지 ---------------- */

/**
 * 학과 이름을 공지 조직에 맞춘다. **3형 카드만 이것이 필요하다.**
 *
 * 이름이 그대로 같은 것은 88곳 중 39곳뿐이고, 공백·가운뎃점을 지우고 `학과/학부/전공`
 * 어미를 떼야 59곳이 된다. 나머지 29곳(융합전공 11곳·의예·경영학과 등)은 **공지를
 * 올리는 조직 자체가 없다.** 그래서 못 맞추면 3형 카드를 안 그리는 것이 답이다 —
 * 2형(값 표시)은 공지 조직과 아예 맞물리지 않으므로 여기 걸리지 않는다.
 *
 * 같은 이름이 여럿이면 고르지 않는다. 엉뚱한 학과의 졸업 공지를 "우리 학과 공지"라고
 * 내미는 것보다 안 보여 주는 편이 낫다.
 */
export function departmentKey(name: string): string {
  return compactForSearch(name).replace(/(학과|학부|전공|과|학)$/u, "");
}

export function findDepartmentOrganization(
  name: string,
  organizations: readonly Organization[],
): { name: string; ids: string[] } | null {
  const exact = organizations.filter((org) => org.name === name);
  const key = departmentKey(name);
  const candidates = exact.length
    ? exact
    : key.length < 2
      ? []
      : organizations.filter((org) => org.type.code === "department" && departmentKey(org.name) === key);
  if (!candidates.length) return null;

  // 같은 이름이 둘로 등록된 학과가 있다. 한쪽이 다른 쪽의 별칭(is_alias)인 짝이고
  // **공지는 별칭 쪽에 붙어 있다**(스페인어학과: 본체 0건, 별칭 197건). 그래서 하나만
  // 골라서는 안 되고, 접히는 곳이 같으면 한 조직으로 보아 둘 다 물어본다.
  const byId = new Map(organizations.map((org) => [org.id, org]));
  const drawn = drawnOrgIds([...organizations], byId);
  const roots = new Set(candidates.map((org) => drawn.get(org.id) ?? org.id));
  // 접히는 곳이 둘 이상이면 정말 다른 조직이다. 엉뚱한 학과의 졸업 공지를 내밀지 않는다.
  if (roots.size !== 1) return null;
  const root = [...roots][0];
  return {
    name: byId.get(root)?.name ?? name,
    ids: organizations.filter((org) => (drawn.get(org.id) ?? org.id) === root).map((org) => org.id),
  };
}

/**
 * 학과 공지 카드가 목록 대신 할 말. 없으면 null 이고 그때는 공지를 줄줄이 그린다.
 *
 * 물어볼 게시판을 못 찾은 것(`null`)과 게시판은 찾았는데 글이 없는 것(빈 배열)은 다르다.
 * 앞은 카드를 아예 안 그리고, 뒤는 **"아직 올라오지 않았습니다"** 라고 말한다. 빈 상자를
 * 내미는 것이 가장 나쁘다 — 학생은 우리가 멈춘 것인지 학교가 안 올린 것인지 알 수 없다.
 */
export function departmentNoticesMessage(
  result: { organization: string; notices: readonly unknown[] } | null | undefined,
): string | null {
  if (!result) return null;
  return result.notices.length ? null : `${result.organization} 게시판에는 졸업 공지가 아직 올라오지 않았습니다`;
}
