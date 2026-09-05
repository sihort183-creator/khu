// API 클라이언트. NEXT_PUBLIC_API_BASE가 없으면 가상 데이터로 동작한다.
// 실제 서버가 생기면 각 함수의 fetch 분기만 살아남고 mock 분기는 제거한다.
import type {
  Catalog,
  Contact,
  FeedPreviewBody,
  ItemResponse,
  ListResponse,
  Notice,
  NoticeDetail,
  NoticeQuery,
  Organization,
  Source,
} from "./types";
import * as M from "@/mocks/data";

const BASE = process.env.NEXT_PUBLIC_API_BASE ?? "";
const useMock = BASE === "";

/* ---------- helpers ---------- */
const delay = (ms: number) => new Promise((r) => setTimeout(r, ms));
let reqSeq = 0;
const meta = () => ({ request_id: `mock-${++reqSeq}`, generated_at: new Date().toISOString() });
const REVISION = "mock-revision-1";

const encodeCursor = (offset: number) => btoa(`${REVISION}:${offset}`);
const decodeCursor = (cursor: string | null | undefined): number => {
  if (!cursor) return 0;
  const [rev, off] = atob(cursor).split(":");
  if (rev !== REVISION) throw feedChanged();
  return Number(off) || 0;
};
const feedChanged = () => ({
  error: { code: "FEED_CHANGED", message: "목록이 갱신되어 처음부터 다시 불러옵니다.", retryable: true, request_id: "mock" },
});

function paginate<T>(items: T[], limit = 20, cursor?: string | null): ListResponse<T> {
  const offset = decodeCursor(cursor);
  const slice = items.slice(offset, offset + limit);
  const has_next = offset + limit < items.length;
  return {
    data: slice,
    page: { next_cursor: has_next ? encodeCursor(offset + limit) : null, has_next, snapshot_at: new Date().toISOString(), dataset_revision: REVISION },
    meta: meta(),
  };
}

async function http<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { ...init, headers: { "content-type": "application/json", ...(init?.headers ?? {}) } });
  if (!res.ok) throw await res.json();
  return res.json();
}

const qs = (params: object) => {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params as Record<string, unknown>)) {
    if (v === undefined || v === null || v === "") continue;
    if (Array.isArray(v)) v.forEach((x) => p.append(k, String(x)));
    else p.set(k, String(v));
  }
  const s = p.toString();
  return s ? `?${s}` : "";
};

/* ---------- 조직 관계 (mock 전용) ---------- */
export function descendantIds(orgId: string): Set<string> {
  const out = new Set<string>([orgId]);
  let grew = true;
  while (grew) {
    grew = false;
    for (const o of M.organizations) {
      if (o.parent_id && out.has(o.parent_id) && !out.has(o.id)) {
        out.add(o.id);
        grew = true;
      }
    }
  }
  return out;
}
export function ancestorIds(orgId: string): string[] {
  const out: string[] = [];
  let cur = M.organizations.find((o) => o.id === orgId);
  while (cur?.parent_id) {
    out.push(cur.parent_id);
    cur = M.organizations.find((o) => o.id === cur!.parent_id);
  }
  return out;
}

const matchesCampus = (n: Notice, campusIds: string[]) => {
  if (!campusIds.length) return true;
  return n.audiences.some((a) => {
    if (a.type === "university" || a.type === "unknown") return true;
    if (a.type === "campus") return campusIds.includes(a.id!);
    if (a.type === "organization") {
      const org = M.organizations.find((o) => o.id === a.id);
      return !org?.campus_id || campusIds.includes(org.campus_id);
    }
    return false;
  });
};

const sortNotices = (list: Notice[], sort: NoticeQuery["sort"] = "recent") => {
  const copy = [...list];
  if (sort === "published") {
    copy.sort((a, b) => (b.published_date ?? "").localeCompare(a.published_date ?? "") || a.id.localeCompare(b.id));
  } else {
    copy.sort((a, b) => b.first_visible_at.localeCompare(a.first_visible_at) || a.id.localeCompare(b.id));
  }
  return copy;
};

const textMatch = (n: Notice, q?: string) => {
  if (!q) return true;
  const hay = `${n.title} ${n.excerpt ?? ""} ${n.primary_source.name}`.toLowerCase();
  return q.toLowerCase().split(/\s+/).every((w) => hay.includes(w));
};

/* ---------- public API ---------- */
export async function getCatalog(): Promise<ItemResponse<Catalog>> {
  if (!useMock) return http("/v1/catalog");
  await delay(60);
  return { data: M.catalog, meta: meta() };
}

export async function listOrganizations(params: { campus_id?: string; parent_id?: string | null } = {}): Promise<ListResponse<Organization>> {
  if (!useMock) return http(`/v1/organizations${qs(params)}`);
  await delay(60);
  let list = M.organizations;
  if (params.campus_id) list = list.filter((o) => o.campus_id === params.campus_id || o.campus_id === null);
  if (params.parent_id !== undefined) list = list.filter((o) => o.parent_id === params.parent_id);
  return paginate(list, 100);
}

export async function listNotices(query: NoticeQuery = {}): Promise<ListResponse<Notice>> {
  if (!useMock) return http(`/v1/notices${qs(query)}`);
  await delay(120);
  let list = M.notices.filter((n) => matchesCampus(n, query.campus_id ?? []));
  if (query.category_code?.length) list = list.filter((n) => query.category_code!.includes(n.primary_category.code));
  if (query.medium?.length) list = list.filter((n) => query.medium!.includes(n.primary_source.medium.code));
  if (query.source_id?.length) list = list.filter((n) => query.source_id!.includes(n.primary_source.id));
  if (query.organization_id?.length) {
    const ids = new Set<string>();
    for (const id of query.organization_id) (query.include_descendants ? descendantIds(id) : new Set([id])).forEach((x) => ids.add(x));
    list = list.filter((n) => n.audiences.some((a) => a.type === "organization" && ids.has(a.id!)));
  }
  list = list.filter((n) => textMatch(n, query.q));
  return paginate(sortNotices(list, query.sort), query.limit ?? 20, query.cursor);
}

export async function getNotice(id: string): Promise<ItemResponse<NoticeDetail>> {
  if (!useMock) return http(`/v1/notices/${id}`);
  await delay(100);
  const d = M.noticeDetails[id];
  if (!d) throw { error: { code: "NOT_FOUND", message: "해당 공지를 찾을 수 없습니다.", retryable: false, request_id: "mock" } };
  return { data: d, meta: meta() };
}

/** 비회원 맞춤 조회: 선택 캠퍼스 + 소속 조직(상위 포함) + 구독 출처 */
export async function previewFeed(body: FeedPreviewBody): Promise<ListResponse<Notice>> {
  if (!useMock) return http("/v1/feeds/preview", { method: "POST", body: JSON.stringify(body) });
  await delay(140);
  const orgScope = new Set<string>();
  for (const id of body.organization_ids) {
    orgScope.add(id);
    ancestorIds(id).forEach((x) => orgScope.add(x));
  }
  const subs = new Set(body.subscribed_source_ids);
  let list = M.notices.filter((n) => {
    if (!matchesCampus(n, body.campus_id ? [body.campus_id] : [])) return false;
    const inOrg = n.audiences.some((a) => a.type === "university" || a.type === "campus" || (a.type === "organization" && orgScope.has(a.id!)));
    const inSub = subs.has(n.primary_source.id);
    return inOrg || inSub;
  });
  const f = body.filters ?? {};
  if (f.category_code?.length) list = list.filter((n) => f.category_code!.includes(n.primary_category.code));
  if (f.medium?.length) list = list.filter((n) => f.medium!.includes(n.primary_source.medium.code));
  list = list.filter((n) => textMatch(n, f.q));
  return paginate(sortNotices(list, "recent"), body.limit ?? 20, body.cursor);
}

export async function listSources(params: { campus_id?: string; q?: string } = {}): Promise<ListResponse<Source>> {
  if (!useMock) return http(`/v1/sources${qs(params)}`);
  await delay(80);
  let list = M.sources;
  if (params.campus_id) list = list.filter((s) => s.campus_id === params.campus_id || s.campus_id === null);
  if (params.q) list = list.filter((s) => `${s.name} ${s.organization.name}`.includes(params.q!));
  return paginate(list, 100);
}

export async function listContacts(params: { campus_id?: string; q?: string; organization_id?: string[] } = {}): Promise<ListResponse<Contact>> {
  if (!useMock) return http(`/v1/contacts${qs(params)}`);
  await delay(80);
  let list = M.contacts;
  if (params.campus_id) list = list.filter((ct) => ct.campuses.some((cp) => cp.id === params.campus_id));
  if (params.q) {
    const q = params.q.toLowerCase();
    list = list.filter((ct) =>
      `${ct.organization.name} ${ct.organization.path.join(" ")} ${ct.service_name} ${ct.location ?? ""} ${ct.channels.map((ch) => ch.display_value).join(" ")}`
        .toLowerCase()
        .includes(q),
    );
  }
  list = [...list].sort((a, b) => a.organization.name.localeCompare(b.organization.name, "ko") || a.service_name.localeCompare(b.service_name, "ko"));
  return paginate(list, 100);
}

export async function getContact(id: string): Promise<ItemResponse<Contact>> {
  if (!useMock) return http(`/v1/contacts/${id}`);
  await delay(60);
  const ct = M.contacts.find((x) => x.id === id);
  if (!ct) throw { error: { code: "NOT_FOUND", message: "해당 연락처를 찾을 수 없습니다.", retryable: false, request_id: "mock" } };
  return { data: ct, meta: meta() };
}
