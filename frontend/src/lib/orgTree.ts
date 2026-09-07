// 조직 계층 트리. 경희대학교 → 캠퍼스(국제·서울)·기타 → 단과대 → 학과.
//
// 최상위는 국제캠퍼스·서울캠퍼스, 그리고 둘 중 어디에도 못 넣는 조직을 담는 '기타'다.
// 캠퍼스가 둘 다 붙은 조직(실측 61곳)은 **두 캠퍼스에 모두** 나온다. 한쪽으로 내려보내면
// 거짓이 되고, 위로 빼면 정작 캠퍼스를 고른 사람이 못 찾는다. 캠퍼스가 하나도 안 붙은
// 조직(실측 6곳)만 '기타'로 간다 — 없다는 것은 "그 캠퍼스가 아니다"가 아니라 "아직 모른다"는
// 뜻이므로 감추지 않고 맨 아래에 둔다.
//
// 캠퍼스 안은 두 묶음이다. 먼저 단과대(type.code === "college")를 ㄱㄴㄷ순으로, 그 다음
// 단과대에 속하지 않는 조직(부서·연구소·기구·학생회)을 ㄱㄴㄷ순으로 늘어놓는다.
// 단과대 밑으로는 상위(parent_id)가 붙은 조직이 한 단계씩 더 들어간다.
//
// 조직이 두 캠퍼스에 동시에 그려지므로 줄마다 키가 두 개 필요하다.
//   key      — 화면의 한 줄을 가리키는 경로 키. 접힘 상태·React key 에 쓴다.
//   countKey — 공지 건수를 찾을 때 쓰는 조직 키(org:<id>). 두 줄이 같은 값을 쓴다.
import type { Campus, Organization } from "./types";

export const ROOT_KEY = "root";
export const ROOT_NAME = "경희대학교";
/** 캠퍼스를 하나도 모르는 조직이 모이는 마디. 맨 아래에 둔다. */
export const OTHER_KEY = "other";
export const OTHER_NAME = "기타";

export const campusKey = (id: string) => `campus:${id}`;
export const orgKey = (id: string) => `org:${id}`;

/** 캠퍼스 바로 밑 줄이 속한 묶음. 단과대가 먼저, 그 밖이 나중이다. */
export type OrgGroup = "college" | "other";

export interface OrgNode {
  /** 이 줄만 가리키는 경로 키. "root" | "campus:<id>" | "campus:<id>/org:<id>/org:<id>" … */
  key: string;
  /** 공지 건수를 찾을 때 쓰는 키. 같은 조직이 두 캠퍼스에 나와도 값은 하나다. */
  countKey: string;
  kind: "root" | "campus" | "other" | "org";
  /** 체크로 고를 수 있는 조직 id. 대학·캠퍼스·기타 마디는 조직이 아니므로 null. */
  id: string | null;
  name: string;
  /** 경희대학교가 0. 화면 들여쓰기에 그대로 쓴다. */
  depth: number;
  organization: Organization | null;
  /** 이 줄에 합쳐진 공개 게시판 수. 자기 것 + 그리지 않은 별칭 조직 것. */
  sourceCount: number;
  /** 그중 지금 공지가 들어올 수 있는 것(폐쇄·대기 제외). 감출지 판정은 이 값으로 한다. */
  activeSourceCount: number;
  /** 캠퍼스·기타 바로 밑 줄만 값을 갖는다. 그 아래 단계는 null. */
  group: OrgGroup | null;
  children: OrgNode[];
}

const sourceCountOf = (org: Organization) => org.source_count ?? 0;
/** 옛 공개 파일에는 active_source_count 가 없다. 없으면 전체 수로 갈음한다(감추지 않는 쪽). */
const activeSourceCountOf = (org: Organization) => org.active_source_count ?? org.source_count ?? 0;

const isCollege = (org: Organization | null) => org?.type.code === "college";

/**
 * ㄱㄴㄷ 정렬에 쓰는 이름.
 *
 * 앞에 붙은 `[확인 필요]` 나 `경희대학교 ` 는 어느 조직이냐를 가르는 말이 아니라서,
 * 그대로 두면 `[` 로 시작하는 줄이 전부 맨 앞에 몰리고 '경희대학교 생활과학대학'이
 * ㄱ 자리에 선다. 정렬할 때만 떼어 낸다(화면에 적는 이름은 그대로다).
 */
export function orgSortKey(name: string): string {
  return name.replace(/^\s*(?:\[[^\]]*\]\s*)?(?:경희대학교\s*)?/, "").trim() || name.trim();
}

/** ㄱㄴㄷ순. 같은 이름이면 원래 이름으로 한 번 더 갈라 순서가 흔들리지 않게 한다. */
export function compareOrgNames(a: string, b: string): number {
  return orgSortKey(a).localeCompare(orgSortKey(b), "ko") || a.localeCompare(b, "ko");
}

/** 같은 단계에서는 단과대가 먼저, 그 안에서는 ㄱㄴㄷ순. */
function compareNodes(a: OrgNode, b: OrgNode): number {
  const rank = (node: OrgNode) => (isCollege(node.organization) ? 0 : 1);
  return rank(a) - rank(b) || compareOrgNames(a.name, b.name);
}

/** 상위를 따라 올라가다 제자리로 돌아오면 그 연결은 없는 셈 친다(무한 재귀 방지). */
function inCycle(org: Organization, byId: Map<string, Organization>): boolean {
  const seen = new Set<string>([org.id]);
  let current = byId.get(org.parent_id ?? "");
  while (current) {
    if (seen.has(current.id)) return true;
    seen.add(current.id);
    current = byId.get(current.parent_id ?? "");
  }
  return false;
}

/**
 * 조직 id → 화면에 실제로 그려지는 조직 id.
 *
 * 별칭(`is_alias`)은 같은 조직이 두 이름으로 등록된 것이라 트리에 두 줄로 나오면 안 된다.
 * 그래서 별칭은 자기 부모(=본체)로 접어 넣는다. 별칭이 겹쳐 있으면 본체를 만날 때까지
 * 올라간다. 부모를 명부에서 못 찾으면 접을 곳이 없으므로 **그 줄은 그대로 그린다** —
 * 접을 데가 없다는 이유로 조직을 통째로 잃는 것이 더 나쁘다.
 */
export function drawnOrgIds(orgs: Organization[], byId: Map<string, Organization>): Map<string, string> {
  const out = new Map<string, string>();
  for (const org of orgs) {
    let current: Organization = org;
    const seen = new Set<string>([org.id]);
    while (current.is_alias && current.parent_id && !seen.has(current.parent_id)) {
      const parent = byId.get(current.parent_id);
      if (!parent) break;
      seen.add(parent.id);
      current = parent;
    }
    out.set(org.id, current.id);
  }
  return out;
}

/** 이 조직이 매달릴 상위 조직 id. 없으면(=캠퍼스 바로 밑이면) null. */
function parentOrgIdOf(org: Organization, byId: Map<string, Organization>, drawn: Map<string, string>): string | null {
  if (!org.parent_id || org.parent_id === org.id) return null;
  if (!byId.has(org.parent_id) || inCycle(org, byId)) return null;
  // 부모가 별칭이면 본체 줄에 붙인다. 별칭 줄은 그려지지 않으므로 그대로 두면 고아가 된다.
  const parentId = drawn.get(org.parent_id) ?? org.parent_id;
  return parentId === org.id ? null : parentId;
}

/** 상위가 없는 조직이 설 자리. 캠퍼스 id 목록이거나, 하나도 모르면 '기타'. */
function bucketsOf(org: Organization, campusIds: Set<string>): string[] {
  const campuses = org.campuses ?? (org.campus_id ? [{ id: org.campus_id, name: "" }] : []);
  const ids = campuses.map((c) => c.id).filter((id) => campusIds.has(id));
  return ids.length > 0 ? Array.from(new Set(ids)) : [OTHER_KEY];
}

/** 목록(catalog)에 없는 캠퍼스가 조직 쪽에만 있어도 트리에서 빠지지 않게 합친다. */
export function campusList(orgs: Organization[], campuses: Campus[] | undefined): Campus[] {
  const out: Campus[] = [];
  const seen = new Set<string>();
  for (const campus of campuses ?? []) {
    if (seen.has(campus.id)) continue;
    seen.add(campus.id);
    out.push(campus);
  }
  for (const org of orgs) {
    for (const campus of org.campuses ?? []) {
      if (seen.has(campus.id)) continue;
      seen.add(campus.id);
      out.push({ id: campus.id, name: campus.name || campus.id });
    }
  }
  return out;
}

export function buildOrgTree(orgs: Organization[], campuses: Campus[] | undefined): OrgNode {
  const byId = new Map(orgs.map((o) => [o.id, o]));
  const drawn = drawnOrgIds(orgs, byId);
  const campusRows = campusList(orgs, campuses);
  const campusIds = new Set(campusRows.map((c) => c.id));

  // 별칭은 줄을 만들지 않는다. 대신 게시판 수를 본체 줄에 얹는다.
  const drawnOrgs = orgs.filter((org) => drawn.get(org.id) === org.id);
  const drawnIds = new Set(drawnOrgs.map((o) => o.id));
  const boards = new Map<string, { source: number; active: number }>();
  for (const org of orgs) {
    const id = drawn.get(org.id) ?? org.id;
    const acc = boards.get(id) ?? { source: 0, active: 0 };
    acc.source += sourceCountOf(org);
    acc.active += activeSourceCountOf(org);
    boards.set(id, acc);
  }

  // 상위 조직별 자식 목록과, 상위가 없어 캠퍼스(또는 기타)에 바로 서는 조직 목록.
  const childrenOf = new Map<string, Organization[]>();
  const topLevel = new Map<string, Organization[]>();
  for (const key of [...campusRows.map((c) => c.id), OTHER_KEY]) topLevel.set(key, []);
  for (const org of drawnOrgs) {
    const parentId = parentOrgIdOf(org, byId, drawn);
    if (parentId && drawnIds.has(parentId)) {
      const list = childrenOf.get(parentId) ?? [];
      list.push(org);
      childrenOf.set(parentId, list);
      continue;
    }
    for (const bucket of bucketsOf(org, campusIds)) {
      const list = topLevel.get(bucket) ?? [];
      list.push(org);
      topLevel.set(bucket, list);
    }
  }

  /** 조직 한 곳을 줄로 만든다. 같은 조직이 두 캠퍼스에 나오므로 키에 경로를 담는다. */
  const makeOrgNode = (org: Organization, parentKey: string, depth: number, group: OrgGroup | null, seen: Set<string>): OrgNode => {
    const counts = boards.get(org.id) ?? { source: 0, active: 0 };
    const key = `${parentKey}/${orgKey(org.id)}`;
    const nextSeen = new Set(seen).add(org.id);
    const children = (childrenOf.get(org.id) ?? [])
      .filter((child) => !nextSeen.has(child.id))
      .map((child) => makeOrgNode(child, key, depth + 1, null, nextSeen))
      .sort(compareNodes);
    return {
      key,
      countKey: orgKey(org.id),
      kind: "org",
      id: org.id,
      name: org.name,
      depth,
      organization: org,
      sourceCount: counts.source,
      activeSourceCount: counts.active,
      group,
      children,
    };
  };

  const bucketNode = (bucket: string, key: string, name: string, kind: "campus" | "other"): OrgNode => ({
    key,
    countKey: key,
    kind,
    id: null,
    name,
    depth: 1,
    organization: null,
    sourceCount: 0,
    activeSourceCount: 0,
    group: null,
    children: (topLevel.get(bucket) ?? [])
      .map((org) => makeOrgNode(org, key, 2, isCollege(org) ? "college" : "other", new Set<string>()))
      .sort(compareNodes),
  });

  // 캠퍼스는 catalog 가 준 순서대로. '기타'는 언제나 맨 아래다.
  const children = campusRows.map((campus) => bucketNode(campus.id, campusKey(campus.id), campus.name || campus.id, "campus"));
  children.push(bucketNode(OTHER_KEY, OTHER_KEY, OTHER_NAME, "other"));

  return {
    key: ROOT_KEY,
    countKey: ROOT_KEY,
    kind: "root",
    id: null,
    name: ROOT_NAME,
    depth: 0,
    organization: null,
    sourceCount: 0,
    activeSourceCount: 0,
    group: null,
    children,
  };
}

/**
 * 조직 하나가 공지 한 건을 올릴 때 그 건이 얹히는 모든 마디의 키.
 * 자기 자신 + 조상들 + (닿으면) 캠퍼스 마디들. 트리에서 위 칸의 수치가
 * 아래 칸을 품도록 하려는 것이다. 캠퍼스가 둘 다 붙은 조직은 두 캠퍼스에 모두 얹는다.
 */
export function nodeKeyChains(orgs: Organization[], campuses: Campus[] | undefined): Map<string, string[]> {
  const byId = new Map(orgs.map((o) => [o.id, o]));
  const drawn = drawnOrgIds(orgs, byId);
  const campusIds = new Set(campusList(orgs, campuses).map((c) => c.id));
  const chains = new Map<string, string[]>();
  for (const org of orgs) {
    const keys: string[] = [];
    const seen = new Set<string>();
    // 별칭 조직의 공지는 본체 줄에 얹힌다. 별칭 줄은 트리에 없기 때문이다.
    let current: Organization | undefined = byId.get(drawn.get(org.id) ?? org.id) ?? org;
    while (current && !seen.has(current.id)) {
      seen.add(current.id);
      keys.push(orgKey(current.id));
      const parentId = parentOrgIdOf(current, byId, drawn);
      const parent = parentId ? byId.get(parentId) : undefined;
      if (!parent || seen.has(parent.id)) {
        for (const bucket of bucketsOf(current, campusIds)) {
          if (bucket !== OTHER_KEY) keys.push(campusKey(bucket));
        }
        break;
      }
      current = parent;
    }
    chains.set(org.id, keys);
  }
  return chains;
}

export interface NodeCounts {
  /** 마디 키 → 그 마디를 골랐을 때 나오는 공지 건수(중복 없이 센 것) */
  byKey: Map<string, number>;
  total: number;
}

/**
 * 공지마다의 대상 목록(audience 문자열)으로 마디별 건수를 센다.
 * 한 공지가 여러 조직을 대상으로 하면 마디마다 한 번씩만 센다. 그래서 형제 마디의
 * 수치를 더해도 위 칸 수치가 되지는 않는다 — 각 수치는 "그 칸을 고르면 보이는 건수"다.
 */
export function countNoticesByNode(orgs: Organization[], campuses: Campus[] | undefined, audienceLists: string[][]): NodeCounts {
  const chains = nodeKeyChains(orgs, campuses);
  const campusIds = new Set(campusList(orgs, campuses).map((c) => c.id));
  const byKey = new Map<string, number>();
  for (const audiences of audienceLists) {
    const keys = new Set<string>();
    for (const audience of audiences) {
      if (audience.startsWith("org:")) {
        for (const key of chains.get(audience.slice(4)) ?? []) keys.add(key);
      } else if (audience.startsWith("campus:")) {
        const id = audience.slice(7);
        if (campusIds.has(id)) keys.add(campusKey(id));
      }
    }
    for (const key of keys) byKey.set(key, (byKey.get(key) ?? 0) + 1);
  }
  return { byKey, total: audienceLists.length };
}
