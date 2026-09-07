// 조직 계층 트리. 경희대학교 → 캠퍼스 → 단과대 → 학과.
//
// 공개 organizations.json 은 484곳 중 상위(parent_id)가 붙은 것이 50곳, 캠퍼스가
// 붙은 것이 122곳뿐이다. 나머지 조직이 목록에서 사라지면 안 되므로 "모르는 만큼만"
// 위로 올려 붙인다: 상위를 모르면 캠퍼스 바로 밑에, 캠퍼스도 모르면 경희대학교 바로 밑에.
// '미상' 같은 가짜 묶음은 만들지 않는다 — 사용자에게는 그냥 목록의 한 줄이다.
// 등록부에 계층이 더 채워지면 같은 규칙이 그 조직을 저절로 제자리로 내려보낸다.
//
// 캠퍼스가 둘 다 붙은 조직(16곳)도 경희대학교 바로 밑에 둔다. 한쪽 캠퍼스로 내려보내면
// 거짓이 되고, 양쪽에 복제하면 같은 줄이 두 번 보인다.
import type { Campus, Organization } from "./types";

export const ROOT_KEY = "root";
export const ROOT_NAME = "경희대학교";

export const campusKey = (id: string) => `campus:${id}`;
export const orgKey = (id: string) => `org:${id}`;

export interface OrgNode {
  /** 접힘 상태·수치를 붙일 때 쓰는 고유 키. "root" | "campus:<id>" | "org:<id>" */
  key: string;
  kind: "root" | "campus" | "org";
  /** 체크로 고를 수 있는 조직 id. 대학·캠퍼스 마디는 조직이 아니므로 null. */
  id: string | null;
  name: string;
  /** 경희대학교가 0. 화면 들여쓰기에 그대로 쓴다. */
  depth: number;
  organization: Organization | null;
  children: OrgNode[];
}

/** 같은 단계에서는 단과대를 위로. 목업의 세로 순서(단과대 → 학과 → 그 밖)와 같다. */
const TYPE_RANK: Record<string, number> = { college: 0, department: 1, council: 2, office: 3, institute: 4 };
const rankOf = (org: Organization | null) => (org ? TYPE_RANK[org.type.code] ?? 5 : -1);

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

function parentKeyOf(org: Organization, byId: Map<string, Organization>, campusIds: Set<string>): string {
  if (org.parent_id && org.parent_id !== org.id && byId.has(org.parent_id) && !inCycle(org, byId)) {
    return orgKey(org.parent_id);
  }
  const campuses = org.campuses ?? (org.campus_id ? [{ id: org.campus_id, name: "" }] : []);
  // 캠퍼스가 정확히 하나일 때만 그 밑으로 내린다. 없거나 둘 다면 대학 바로 밑이다.
  if (campuses.length === 1 && campusIds.has(campuses[0].id)) return campusKey(campuses[0].id);
  return ROOT_KEY;
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
  const campusRows = campusList(orgs, campuses);
  const campusIds = new Set(campusRows.map((c) => c.id));

  const root: OrgNode = { key: ROOT_KEY, kind: "root", id: null, name: ROOT_NAME, depth: 0, organization: null, children: [] };
  const nodes = new Map<string, OrgNode>([[ROOT_KEY, root]]);
  const campusNodes = campusRows.map((campus) => {
    const node: OrgNode = { key: campusKey(campus.id), kind: "campus", id: null, name: campus.name || campus.id, depth: 1, organization: null, children: [] };
    nodes.set(node.key, node);
    return node;
  });
  for (const org of orgs) {
    nodes.set(orgKey(org.id), { key: orgKey(org.id), kind: "org", id: org.id, name: org.name, depth: 0, organization: org, children: [] });
  }

  for (const org of orgs) {
    const parent = nodes.get(parentKeyOf(org, byId, campusIds)) ?? root;
    parent.children.push(nodes.get(orgKey(org.id))!);
  }
  // 깊이를 매기기 전에 캠퍼스도 대학의 자식으로 붙여 둔다. 빠뜨리면 캠퍼스 밑의
  // 가지들이 깊이 0으로 남아 들여쓰기가 통째로 사라진다.
  root.children.push(...campusNodes);

  const sortChildren = (node: OrgNode, depth: number) => {
    node.depth = depth;
    node.children.sort(
      (a, b) =>
        rankOf(a.organization) - rankOf(b.organization)
        || (b.organization?.source_count ?? 0) - (a.organization?.source_count ?? 0)
        || a.name.localeCompare(b.name, "ko"),
    );
    for (const child of node.children) sortChildren(child, depth + 1);
  };
  sortChildren(root, 0);
  // 캠퍼스는 늘 대학 바로 밑 맨 앞에, catalog 가 준 순서 그대로 둔다.
  root.children = [...campusNodes, ...root.children.filter((n) => n.kind !== "campus")];
  return root;
}

/**
 * 조직 하나가 공지 한 건을 올릴 때 그 건이 얹히는 모든 마디의 키.
 * 자기 자신 + 조상들 + (닿으면) 캠퍼스 마디. 트리에서 위 칸의 수치가
 * 아래 칸을 품도록 하려는 것이다.
 */
export function nodeKeyChains(orgs: Organization[], campuses: Campus[] | undefined): Map<string, string[]> {
  const byId = new Map(orgs.map((o) => [o.id, o]));
  const campusIds = new Set(campusList(orgs, campuses).map((c) => c.id));
  const chains = new Map<string, string[]>();
  for (const org of orgs) {
    const keys: string[] = [];
    const seen = new Set<string>();
    let current: Organization | undefined = org;
    while (current && !seen.has(current.id)) {
      seen.add(current.id);
      keys.push(orgKey(current.id));
      const parent = parentKeyOf(current, byId, campusIds);
      if (parent === ROOT_KEY) break;
      if (parent.startsWith("campus:")) {
        keys.push(parent);
        break;
      }
      current = byId.get(parent.slice(4));
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
