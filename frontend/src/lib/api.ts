// API 클라이언트. NEXT_PUBLIC_API_BASE가 없으면 가상 데이터로 동작한다.
// 실제 서버가 생기면 각 함수의 fetch 분기만 살아남고 mock 분기는 제거한다.
import type {
  Catalog,
  Coded,
  Contact,
  FeedPreviewBody,
  ItemResponse,
  ListResponse,
  Notice,
  NoticeDetail,
  NoticeSource,
  NoticeQuery,
  Organization,
  Source,
} from "./types";
import * as M from "@/mocks/data";

const BASE = process.env.NEXT_PUBLIC_API_BASE ?? "";
const useMock = BASE === "";
const STATIC_BASE = BASE.replace(/\/+$/, "").replace(/\/v1$/, "");

/**
 * 공지 본문 그림의 실제 주소를 만든다.
 *
 * 원문 그림 주소는 대부분 http 라 https 화면에서 브라우저가 막는다. 그래서 조회 서버가
 * 중계하고, 공개 파일에는 그 서버 기준 상대 경로만 들어 있다. 여기서 앞을 붙인다.
 * 가상 데이터로 도는 동안에는 중계할 서버가 없으므로 아무것도 주지 않는다.
 */
export function imageUrl(path: string | null | undefined): string | null {
  if (!path || useMock) return null;
  return `${STATIC_BASE}${path.startsWith("/") ? path : `/${path}`}`;
}

type StaticLatest = {
  revision: string;
  generated_at: string;
  base_path: string;
  contract_version: string;
  notice_pages: number;
  notices_total: number;
  contacts_total: number;
  initial_window_start?: string | null;
};

type StaticIndexEntry = {
  id: string;
  t: string;
  c: string;
  o: string | null;
  s: string;
  m?: string;
  d: string | null;
  v: string;
  a: string[];
};

type StaticIndex = {
  revision: string;
  generated_at: string;
  count: number;
  entries?: StaticIndexEntry[];
  shards?: { path: string; count?: number }[];
};

type StaticIndexShard = StaticIndexEntry[] | {
  revision?: string;
  generated_at?: string;
  count?: number;
  entries?: StaticIndexEntry[];
};

function relativeStaticPath(path: string, pointer: StaticLatest): string {
  const clean = path.replace(/^\/+/, "");
  const base = pointer.base_path.replace(/^\/+/, "").replace(/\/+$/, "");
  return clean.startsWith(`${base}/`) ? clean.slice(base.length + 1) : clean;
}

type BackendNoticeSource = {
  source_item_id?: string;
  source?: { id: string; name: string; medium: Coded };
  id?: string;
  source_id?: string;
  source_name?: string;
  medium?: Coded;
  url: string;
  published_date: string | null;
  original_status: Coded;
  is_primary: boolean;
};

type BackendAttachment = {
  id: string;
  filename: string;
  kind?: string | null;
  type?: string;
  size_bytes: number | null;
  url: string | null;
  status: Coded;
};

type BackendChannel = {
  id?: string;
  kind: Coded;
  display_value?: string;
  value?: string | null;
  action_url?: string | null;
};

type BackendContactMention = {
  raw_text?: string;
  text?: string;
  channels?: BackendChannel[];
  channel?: BackendChannel | null;
  contact_id: string | null;
};

type StaticNoticeDetail = Omit<NoticeDetail, "sources" | "attachments" | "contact_mentions"> & {
  sources?: (NoticeSource | BackendNoticeSource)[];
  attachments?: BackendAttachment[];
  contact_mentions?: BackendContactMention[];
};

type LatestCache = { promise: Promise<StaticLatest>; expires_at: number };
const LATEST_TTL_MS = 10_000;
let latestCache: LatestCache | null = null;

async function staticLatest(force = false): Promise<StaticLatest> {
  if (!force && latestCache && latestCache.expires_at > Date.now()) return latestCache.promise;

  const request = fetch(`${STATIC_BASE}/v1/latest.json`, { cache: "no-store" }).then(async (res) => {
      if (!res.ok) throw await res.json();
      return res.json() as Promise<StaticLatest>;
    });
  const tracked = request.catch((error) => {
    if (latestCache?.promise === tracked) latestCache = null;
    throw error;
  });
  latestCache = { promise: tracked, expires_at: Date.now() + LATEST_TTL_MS };
  return tracked;
}

async function staticGet<T>(relative: string, latest?: StaticLatest): Promise<T> {
  const pointer = latest ?? (await staticLatest());
  const path = `${STATIC_BASE}/${pointer.base_path.replace(/^\/+/, "")}/${relative.replace(/^\/+/, "")}`;
  const res = await fetch(path, { cache: "no-store" });
  if (!res.ok) throw await res.json();
  return res.json() as Promise<T>;
}

const staticCursor = (offset: number, revision: string) => btoa(`${revision}:${offset}`);
const staticOffset = (cursor: string | null | undefined, revision: string) => {
  if (!cursor) return 0;
  const [seenRevision, raw] = atob(cursor).split(":");
  if (seenRevision !== revision) throw feedChanged();
  return Number(raw) || 0;
};

async function staticIndex(pointer: StaticLatest): Promise<StaticIndex> {
  const index = await staticGet<StaticIndex>("notices/index.json", pointer);
  if (index.revision !== pointer.revision) throw feedChanged();
  if (index.generated_at && index.generated_at !== pointer.generated_at) {
    throw { error: { code: "STATIC_INDEX_INVALID", message: "공지 색인의 개정 시각이 포인터와 다릅니다.", retryable: true, request_id: `static-${pointer.revision}` } };
  }
  // 새 계약은 entries를 유지하면서 shards를 선택적으로 제공한다. 구형/경량
  // 배포에서 entries가 빠진 경우에만 모든 조각을 읽어 같은 색인으로 합친다.
  if (index.entries && index.entries.length > 0) {
    if (index.count !== index.entries.length) {
      throw { error: { code: "STATIC_INDEX_INVALID", message: "공지 색인의 전체 건수가 실제 항목 수와 다릅니다.", retryable: true, request_id: `static-${pointer.revision}` } };
    }
    return index;
  }
  if (!index.shards?.length) {
    if (index.count !== (index.entries ?? []).length) {
      throw { error: { code: "STATIC_INDEX_INVALID", message: "공지 색인이 비어 있고 전체 건수도 일치하지 않습니다.", retryable: true, request_id: `static-${pointer.revision}` } };
    }
    return { ...index, entries: index.entries ?? [] };
  }

  // 조각 하나라도 실패하면 []로 조용히 바꾸지 않는다. 호출자가 오류 상태를
  // 보여 주고 같은 개정으로 재시도할 수 있도록 원래 오류를 전달한다.
  const payloads = await Promise.all(index.shards.map(async (shard) => {
    const payload = await staticGet<StaticIndexShard>(relativeStaticPath(shard.path, pointer), pointer);
    const payloadEntries = Array.isArray(payload) ? payload : payload.entries ?? [];
    if (!Array.isArray(payload)) {
      if (payload.revision && payload.revision !== pointer.revision) throw feedChanged();
      if (payload.generated_at && payload.generated_at !== pointer.generated_at) {
        throw { error: { code: "STATIC_INDEX_INVALID", message: "공지 색인 조각의 개정 시각이 포인터와 다릅니다.", retryable: true, request_id: `static-${pointer.revision}` } };
      }
      if (payload.count !== undefined && payload.count !== payloadEntries.length) {
        throw { error: { code: "STATIC_INDEX_INVALID", message: "공지 색인 조각의 건수가 실제 항목 수와 다릅니다.", retryable: true, request_id: `static-${pointer.revision}` } };
      }
    }
    if (shard.count !== undefined && shard.count !== payloadEntries.length) {
      throw { error: { code: "STATIC_INDEX_INVALID", message: "공지 색인 조각 설명의 건수가 실제 항목 수와 다릅니다.", retryable: true, request_id: `static-${pointer.revision}` } };
    }
    return payloadEntries;
  }));
  const entries = payloads.flat();
  if (index.count !== entries.length) {
    throw { error: { code: "STATIC_INDEX_INVALID", message: "공지 색인 조각 합계가 전체 건수와 다릅니다.", retryable: true, request_id: `static-${pointer.revision}` } };
  }
  return { ...index, entries };
}

function normalizeOrganization(organization: Organization, campusId?: string): Organization {
  const campuses = organization.campuses ?? (organization.campus_id ? [{ id: organization.campus_id, name: "" }] : []);
  const campus_id = campusId && campuses.some((campus) => campus.id === campusId)
    ? campusId
    : organization.campus_id ?? campuses[0]?.id ?? null;
  return { ...organization, campuses, campus_id };
}

function organizationMatchesCampus(organization: Organization | undefined, campusId: string): boolean {
  if (!organization) return true;
  const campuses = organization.campuses ?? (organization.campus_id ? [{ id: organization.campus_id, name: "" }] : []);
  return campuses.length === 0 || campuses.some((campus) => campus.id === campusId);
}

function audienceMatchesCampus(
  audience: string,
  campusIds: Set<string>,
  organizations: Map<string, Organization>,
): boolean {
  if (!campusIds.size) return true;
  if (audience === "university" || audience === "undetermined") return true;
  if (audience.startsWith("campus:")) return campusIds.has(audience.slice(7));
  if (audience.startsWith("org:")) {
    const organization = organizations.get(audience.slice(4));
    return [...campusIds].some((campusId) => organizationMatchesCampus(organization, campusId));
  }
  return false;
}

type NoticeMatchContext = {
  organizationIds: Set<string>;
  campusIds?: Set<string>;
  organizations?: Map<string, Organization>;
  sourceMedia?: Map<string, string>;
};

function staticNoticeMatches(entry: StaticIndexEntry, query: NoticeQuery, context: NoticeMatchContext) {
  if (query.q) {
    const words = query.q.toLowerCase().split(/\s+/).filter(Boolean);
    if (!words.every((word) => entry.t.toLowerCase().includes(word))) return false;
  }
  if (query.category_code?.length && !query.category_code.includes(entry.c)) return false;
  if (query.source_id?.length && !query.source_id.includes(entry.s)) return false;
  if (query.medium?.length) {
    const medium = entry.m ?? context.sourceMedia?.get(entry.s);
    if (!medium || !query.medium.includes(medium)) return false;
  }
  if (context.campusIds?.size) {
    const organizations = context.organizations ?? new Map<string, Organization>();
    if (!entry.a.some((audience) => audienceMatchesCampus(audience, context.campusIds!, organizations))) return false;
  }
  if (query.organization_id?.length) {
    const wanted = query.include_descendants ? context.organizationIds : new Set(query.organization_id);
    if (!entry.a.some((audience) => audience.startsWith("org:") && wanted.has(audience.slice(4)))) return false;
  }
  return true;
}

async function staticOrganizations(pointer: StaticLatest): Promise<Organization[]> {
  const response = await staticGet<ListResponse<Organization>>("organizations.json", pointer);
  if (response.page.dataset_revision !== pointer.revision) throw feedChanged();
  return response.data.map((organization) => normalizeOrganization(organization));
}

async function staticSourceMedia(pointer: StaticLatest): Promise<Map<string, string>> {
  const response = await staticGet<ListResponse<Source>>("sources.json", pointer);
  if (response.page.dataset_revision !== pointer.revision) throw feedChanged();
  return new Map(response.data.map((source) => [source.id, source.medium.code]));
}

function normalizeNotice(notice: Notice): Notice {
  const raw = notice as Notice & { deadline?: (Notice["deadline"] & { evidence_text?: string | null }) | null };
  return {
    ...notice,
    deadline: raw.deadline
      ? { ...raw.deadline, evidence: raw.deadline.evidence ?? raw.deadline.evidence_text ?? "" }
      : null,
  };
}

function normalizeNoticeSource(source: NoticeSource | BackendNoticeSource, index: number): NoticeSource {
  const backend = source as BackendNoticeSource;
  if (backend.source) {
    return {
      id: backend.source_item_id ?? backend.id ?? `${backend.source.id}:${backend.url}:${index}`,
      source_id: backend.source.id,
      source_name: backend.source.name,
      medium: backend.source.medium,
      url: backend.url,
      published_date: backend.published_date,
      original_status: backend.original_status,
      is_primary: backend.is_primary,
    };
  }
  return {
    ...(source as NoticeSource),
    id: backend.id ?? backend.source_item_id ?? `${backend.source_id ?? "source"}:${backend.url}:${index}`,
    source_id: backend.source_id ?? "",
    source_name: backend.source_name ?? "",
    medium: backend.medium ?? { code: "web", label: "웹" },
  };
}

function normalizeNoticeDetail(response: ItemResponse<StaticNoticeDetail>): ItemResponse<NoticeDetail> {
  const raw = response.data;
  const sources = (raw.sources ?? []).map(normalizeNoticeSource);
  const attachments = (raw.attachments ?? []).map((attachment) => ({
    ...attachment,
    type: attachment.type ?? attachment.kind ?? "",
  }));
  const contact_mentions = (raw.contact_mentions ?? []).flatMap((mention) => {
    const channels = mention.channels?.length
      ? mention.channels
      : mention.channel
        ? [mention.channel]
        : [null];
    return channels.map((channel) => ({
      text: mention.raw_text ?? mention.text ?? "",
      channel: channel
        ? {
            kind: channel.kind,
            value: channel.value ?? channel.display_value ?? "",
            action_url: channel.action_url ?? null,
          }
        : null,
      contact_id: mention.contact_id,
    }));
  });
  return {
    ...response,
    data: {
      ...normalizeNotice(raw),
      body_text: raw.body_text ?? null,
      body_html: raw.body_html ?? null,
      sources,
      attachments,
      contact_mentions,
      related_notices: raw.related_notices ?? [],
      resolved_from_id: raw.resolved_from_id ?? null,
    },
  };
}

async function loadNoticePageSlice(pointer: StaticLatest, offset: number, limit: number) {
  let pageNumber = Math.floor(offset / 50) + 1;
  let localOffset = offset % 50;
  let remaining = limit;
  const data: Notice[] = [];
  let hasNext = false;

  while (remaining > 0) {
    const file = await staticGet<ListResponse<Notice>>(`notices/page/${pageNumber}.json`, pointer);
    if (file.page.dataset_revision !== pointer.revision) throw feedChanged();
    const chunk = file.data.slice(localOffset, localOffset + remaining).map(normalizeNotice);
    data.push(...chunk);
    remaining -= chunk.length;
    hasNext = file.page.has_next || localOffset + chunk.length < file.data.length;
    if (remaining <= 0 || !file.page.has_next) break;
    pageNumber += 1;
    localOffset = 0;
  }

  return {
    data,
    hasNext,
    nextOffset: offset + data.length,
  };
}

async function staticNotices(query: NoticeQuery): Promise<ListResponse<Notice>> {
  const pointer = await staticLatest();
  const offset = staticOffset(query.cursor, pointer.revision);
  const limit = query.limit ?? 20;
  const needsIndex = Boolean(
    query.q
      || query.category_code?.length
      || query.source_id?.length
      || query.medium?.length
      || query.campus_id?.length
      || query.organization_id?.length
      || query.sort === "published",
  );

  if (!needsIndex && (!query.sort || query.sort === "recent")) {
    const page = await loadNoticePageSlice(pointer, offset, limit);
    return {
      data: page.data,
      page: {
        next_cursor: page.hasNext ? staticCursor(page.nextOffset, pointer.revision) : null,
        has_next: page.hasNext,
        snapshot_at: pointer.generated_at,
        dataset_revision: pointer.revision,
      },
      meta: { request_id: `static-${pointer.revision}`, generated_at: pointer.generated_at },
    };
  }

  const index = await staticIndex(pointer);
  const needsOrganizations = Boolean(query.campus_id?.length || (query.organization_id?.length && query.include_descendants));
  const organizations = needsOrganizations ? await staticOrganizations(pointer) : [];
  const organizationMap = new Map(organizations.map((organization) => [organization.id, organization]));
  const sourceMedia = query.medium?.length ? await staticSourceMedia(pointer) : undefined;
  const organizationIds = new Set(query.organization_id ?? []);
  if (query.include_descendants) {
    let changed = true;
    while (changed) {
      changed = false;
      for (const org of organizations) {
        if (org.parent_id && organizationIds.has(org.parent_id) && !organizationIds.has(org.id)) {
          organizationIds.add(org.id);
          changed = true;
        }
      }
    }
  }
  let entries = (index.entries ?? []).filter((entry) => staticNoticeMatches(entry, query, {
    organizationIds,
    campusIds: query.campus_id?.length ? new Set(query.campus_id) : undefined,
    organizations: organizationMap,
    sourceMedia,
  }));
  if (query.sort === "published") {
    entries = [...entries].sort((a, b) => (b.d ?? "").localeCompare(a.d ?? "") || a.id.localeCompare(b.id));
  }
  const selected = entries.slice(offset, offset + limit);
  const data = await Promise.all(selected.map((entry) => staticGet<ItemResponse<StaticNoticeDetail>>(`notices/${entry.id}.json`, pointer).then(normalizeNoticeDetail).then((response) => response.data)));
  const hasNext = offset + data.length < entries.length;
  return {
    data,
    page: { next_cursor: hasNext ? staticCursor(offset + limit, pointer.revision) : null, has_next: hasNext, snapshot_at: pointer.generated_at, dataset_revision: pointer.revision },
    meta: { request_id: `static-${pointer.revision}`, generated_at: pointer.generated_at },
  };
}

async function staticPreview(body: FeedPreviewBody): Promise<ListResponse<Notice>> {
  const pointer = await staticLatest();
  const index = await staticIndex(pointer);
  const organizations = body.organization_ids.length || body.campus_id
    ? await staticOrganizations(pointer)
    : [];
  const organizationMap = new Map(organizations.map((organization) => [organization.id, organization]));
  // 맞춤 목록은 선택 조직 자체와 그 상위 조직만 포함한다. 상위 조직을
  // 선택했다고 자식 조직 전체를 자동 구독시키면 범위가 과도하게 넓어진다.
  const organizationIds = new Set(body.organization_ids);
  for (const selectedId of body.organization_ids) {
    let current = organizationMap.get(selectedId);
    const seen = new Set<string>();
    while (current?.parent_id && !seen.has(current.parent_id)) {
      seen.add(current.parent_id);
      organizationIds.add(current.parent_id);
      current = organizationMap.get(current.parent_id);
    }
  }
  const subscribed = new Set(body.subscribed_source_ids);
  const filters = body.filters ?? {};
  const sourceMedia = filters.medium?.length ? await staticSourceMedia(pointer) : undefined;
  const selected = (index.entries ?? []).filter((entry) => {
    const campusIds = body.campus_id ? new Set([body.campus_id]) : new Set<string>();
    const inCampus = !campusIds.size || entry.a.some((audience) => audienceMatchesCampus(audience, campusIds, organizationMap));
    const inOrganization = entry.a.some((audience) => audience.startsWith("org:") && organizationIds.has(audience.slice(4)));
    // 기존 가상 분기와 같은 계약: 먼저 선택 캠퍼스 범위로 줄인 뒤
    // 조직 대상 또는 구독 출처를 적용한다.
    const inScope = inCampus && (subscribed.has(entry.s) || entry.a.some((audience) => audience === "university" || audience === "undetermined" || inOrganization || audience.startsWith("campus:")));
    return inScope && staticNoticeMatches(entry, filters, {
      organizationIds,
      campusIds: undefined,
      organizations: organizationMap,
      sourceMedia,
    });
  });
  const offset = staticOffset(body.cursor, pointer.revision);
  const limit = body.limit ?? 20;
  const slice = selected.slice(offset, offset + limit);
  const data = await Promise.all(slice.map((entry) => staticGet<ItemResponse<StaticNoticeDetail>>(`notices/${entry.id}.json`, pointer).then(normalizeNoticeDetail).then((response) => response.data)));
  const hasNext = offset + data.length < selected.length;
  return {
    data,
    page: { next_cursor: hasNext ? staticCursor(offset + limit, pointer.revision) : null, has_next: hasNext, snapshot_at: pointer.generated_at, dataset_revision: pointer.revision },
    meta: { request_id: `static-${pointer.revision}`, generated_at: pointer.generated_at },
  };
}

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
  if (!useMock) return staticLatest().then((pointer) => staticGet<ItemResponse<Catalog>>("catalog.json", pointer));
  await delay(60);
  return { data: M.catalog, meta: meta() };
}

export async function listOrganizations(params: { campus_id?: string; parent_id?: string | null } = {}): Promise<ListResponse<Organization>> {
  if (!useMock) {
    const pointer = await staticLatest();
    const response = await staticGet<ListResponse<Organization>>("organizations.json", pointer);
    let data = response.data.map((organization) => normalizeOrganization(organization, params.campus_id));
    if (params.campus_id) data = data.filter((organization) => organizationMatchesCampus(organization, params.campus_id!));
    if (params.parent_id !== undefined) data = data.filter((organization) => organization.parent_id === params.parent_id);
    return { ...response, data };
  }
  await delay(60);
  let list = M.organizations;
  if (params.campus_id) list = list.filter((o) => o.campus_id === params.campus_id || o.campus_id === null);
  if (params.parent_id !== undefined) list = list.filter((o) => o.parent_id === params.parent_id);
  return paginate(list, 100);
}

export async function listNotices(query: NoticeQuery = {}): Promise<ListResponse<Notice>> {
  if (!useMock) return staticNotices(query);
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
  if (!useMock) {
    const pointer = await staticLatest();
    return staticGet<ItemResponse<StaticNoticeDetail>>(`notices/${id}.json`, pointer).then(normalizeNoticeDetail);
  }
  await delay(100);
  const d = M.noticeDetails[id];
  if (!d) throw { error: { code: "NOT_FOUND", message: "해당 공지를 찾을 수 없습니다.", retryable: false, request_id: "mock" } };
  return { data: d, meta: meta() };
}

/** 비회원 맞춤 조회: 선택 캠퍼스 + 소속 조직(상위 포함) + 구독 출처 */
export async function previewFeed(body: FeedPreviewBody): Promise<ListResponse<Notice>> {
  if (!useMock) return staticPreview(body);
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
  if (!useMock) {
    const pointer = await staticLatest();
    const [response, organizations] = await Promise.all([
      staticGet<ListResponse<Source>>("sources.json", pointer),
      staticOrganizations(pointer),
    ]);
    if (response.page.dataset_revision !== pointer.revision) throw feedChanged();
    const organizationsById = new Map(organizations.map((organization) => [organization.id, organization]));
    const data = response.data
      .map((source) => {
        const organization = organizationsById.get(source.organization.id);
        const campuses = source.organization.campuses ?? organization?.campuses ?? [];
        const campus_id = params.campus_id && campuses.some((campus) => campus.id === params.campus_id)
          ? params.campus_id
          : source.campus_id ?? campuses[0]?.id ?? null;
        return { ...source, organization: { ...source.organization, campuses }, campus_id };
      })
      .filter((source) =>
        (!params.campus_id || source.campus_id === params.campus_id || source.organization.campuses?.length === 0)
        && (!params.q || `${source.name} ${source.organization.name}`.toLowerCase().includes(params.q.toLowerCase())),
      );
    return { ...response, data };
  }
  await delay(80);
  let list = M.sources;
  if (params.campus_id) list = list.filter((s) => s.campus_id === params.campus_id || s.campus_id === null);
  if (params.q) list = list.filter((s) => `${s.name} ${s.organization.name}`.includes(params.q!));
  return paginate(list, 100);
}

export async function listContacts(params: { campus_id?: string; q?: string; organization_id?: string[] } = {}): Promise<ListResponse<Contact>> {
  if (!useMock) {
    const pointer = await staticLatest();
    const all: Contact[] = [];
    let pageNumber = 1;
    let firstPage: ListResponse<Contact> | null = null;
    while (true) {
      const page = await staticGet<ListResponse<Contact>>(`contacts/page/${pageNumber}.json`, pointer);
      if (page.page.dataset_revision !== pointer.revision) throw feedChanged();
      firstPage ??= page;
      all.push(...page.data);
      if (!page.page.has_next) break;
      pageNumber += 1;
    }
    const data = all.filter((contact) =>
      (!params.campus_id || contact.campuses.some((campus) => campus.id === params.campus_id))
      && (!params.organization_id?.length || params.organization_id.includes(contact.organization.id))
      && (!params.q || `${contact.organization.name} ${contact.organization.path.join(" ")} ${contact.service_name} ${contact.location ?? ""} ${contact.channels.map((channel) => channel.display_value).join(" ")}`.toLowerCase().includes(params.q.toLowerCase())),
    );
    return {
      ...(firstPage ?? {
        data: [],
        page: { next_cursor: null, has_next: false, snapshot_at: pointer.generated_at, dataset_revision: pointer.revision },
        meta: { request_id: `static-${pointer.revision}`, generated_at: pointer.generated_at },
      }),
      data,
      page: {
        ...(firstPage ?? {
          next_cursor: null,
          has_next: false,
          snapshot_at: pointer.generated_at,
          dataset_revision: pointer.revision,
        }).page,
        next_cursor: null,
        has_next: false,
      },
    };
  }
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
  if (!useMock) {
    const pointer = await staticLatest();
    return staticGet<ItemResponse<Contact>>(`contacts/${id}.json`, pointer);
  }
  await delay(60);
  const ct = M.contacts.find((x) => x.id === id);
  if (!ct) throw { error: { code: "NOT_FOUND", message: "해당 연락처를 찾을 수 없습니다.", retryable: false, request_id: "mock" } };
  return { data: ct, meta: meta() };
}
