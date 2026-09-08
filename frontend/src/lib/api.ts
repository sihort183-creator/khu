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
import { countNoticesByNode, type NodeCounts } from "./orgTree";
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
  /** 발행일(한국시간 날짜) */
  d: string | null;
  /** 발행 시각(UTC ISO). 원문에 시각이 없으면 없다. */
  p?: string | null;
  /** 우리가 처음 본 시각(UTC ISO) */
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

/**
 * 개정 경로 파일 하나를 읽는다.
 *
 * 브라우저 캐시를 끄지 않는다(예전에는 `cache: "no-store"` 였다). 개정 경로
 * (`/v1/r/<개정>/…`)의 파일은 한 번 만들어지면 절대 바뀌지 않고, 조회 서버도
 * `Cache-Control: immutable` 을 붙여 내보낸다. no-store 는 그 약속을 통째로 버려
 * 화면을 옮길 때마다 같은 파일을 다시 받게 했다. 개정이 바뀌면 경로가 바뀌므로
 * 낡은 파일을 잘못 쓸 일은 없다. 개정 포인터(latest.json)만 no-store 로 남긴다.
 */
async function staticGet<T>(relative: string, latest?: StaticLatest): Promise<T> {
  const pointer = latest ?? (await staticLatest());
  const path = `${STATIC_BASE}/${pointer.base_path.replace(/^\/+/, "")}/${relative.replace(/^\/+/, "")}`;
  const res = await fetch(path);
  if (!res.ok) throw await res.json();
  return res.json() as Promise<T>;
}

/** 목록 페이지 파일 한 장에 든 공지 수. 백엔드 export/static.py 의 PAGE_SIZE 와 같다. */
const STATIC_PAGE_SIZE = 50;
/**
 * 목록 한 화면을 채우려고 훑을 목록 페이지 수의 상한.
 *
 * 목록 페이지에는 화면이 그릴 것이 전부 들어 있어(제목·출처·매체·건수·포스터) 색인도
 * 상세도 필요 없다. 다만 고른 주제가 아주 드물면 20줄을 채우는 데 페이지를 한없이
 * 넘겨야 한다. 그때는 여기서 멈추고 색인 경로로 넘긴다. 6장 = 300건이면 대략 7%
 * 이상 남는 조건은 전부 여기서 끝난다.
 */
const SCAN_PAGE_LIMIT = 6;
/** 기억해 둘 목록 페이지 파일 수. 이어보기로 한참 내려가도 메모리가 늘지 않게 한다. */
const PAGE_CACHE_LIMIT = 32;

/**
 * 같은 개정의 같은 파일을 두 번 받지 않는다.
 *
 * 개정 경로 파일은 불변이라 한 번 읽은 약속을 그대로 다시 준다. 예전에는 조직 목록이
 * 한 화면에서 두세 번 오갔다 — 화면(useOrganizations)과 목록 거르기와 조직 수 세기가
 * 각자 받았기 때문이다. 개정이 바뀌면 통째로 버린다.
 */
let sharedFiles: { revision: string; files: Map<string, Promise<unknown>> } | null = null;

function cachedStaticGet<T>(relative: string, pointer: StaticLatest): Promise<T> {
  if (!sharedFiles || sharedFiles.revision !== pointer.revision) {
    sharedFiles = { revision: pointer.revision, files: new Map() };
  }
  const files = sharedFiles.files;
  const hit = files.get(relative);
  if (hit) return hit as Promise<T>;
  const request: Promise<T> = staticGet<T>(relative, pointer).catch((error) => {
    // 실패는 기억하지 않는다. 다음 조회가 다시 시도할 수 있어야 한다.
    if (files.get(relative) === request) files.delete(relative);
    throw error;
  });
  files.set(relative, request);
  if (relative.startsWith("notices/page/")) {
    const pages = [...files.keys()].filter((key) => key.startsWith("notices/page/"));
    for (const key of pages.slice(0, Math.max(0, pages.length - PAGE_CACHE_LIMIT))) files.delete(key);
  }
  return request;
}

/**
 * 이어보기 자리표.
 *
 * 두 경로가 자리표를 쓴다. 목록 페이지를 훑는 경로는 **전체 순서에서의 자리**를,
 * 색인 경로는 **걸러낸 목록에서의 자리**를 센다. 같은 글자로 적으면 한 경로가 만든
 * 자리표를 다른 경로가 엉뚱하게 읽는다. 그래서 색인 쪽만 `i` 를 붙여 구분하고,
 * 자리표가 있으면 그것이 만들어진 경로를 그대로 따라간다.
 * 접두가 없는 값은 예전 자리표이며 훑기 자리표와 뜻이 같다.
 */
type StaticCursor = { kind: "scan" | "index"; offset: number };

const scanCursor = (offset: number, revision: string) => btoa(`${revision}:${offset}`);
const indexCursor = (offset: number, revision: string) => btoa(`${revision}:i${offset}`);

function staticCursorAt(cursor: string | null | undefined, revision: string): StaticCursor | null {
  if (!cursor) return null;
  const [seenRevision, raw] = atob(cursor).split(":");
  if (seenRevision !== revision) throw feedChanged();
  if (raw?.startsWith("i")) return { kind: "index", offset: Number(raw.slice(1)) || 0 };
  return { kind: "scan", offset: Number(raw) || 0 };
}

const staticOffset = (cursor: string | null | undefined, revision: string) =>
  staticCursorAt(cursor, revision)?.offset ?? 0;

/**
 * 합쳐 놓은 공지 색인.
 *
 * `positions` 는 항목이 전체 순서에서 몇 번째인가다. 색인과 목록 페이지는 같은 목록을
 * 같은 순서로 내보내므로, 자리를 알면 그 공지가 어느 목록 페이지 파일에 있는지도 안다.
 * 화면은 그 파일에서 공지를 그대로 꺼내 쓴다 — 상세 파일을 20개 부르지 않는 이유다.
 */
type LoadedIndex = { revision: string; entries: StaticIndexEntry[]; positions: Map<string, number> };

let indexCache: { revision: string; promise: Promise<LoadedIndex> } | null = null;

/**
 * 색인은 개정마다 한 번만 읽는다.
 *
 * 조각이 12개에 6MB다. 목록을 한 쪽 넘길 때마다, 60초마다 다시 읽으면 받아오는
 * 비용은 브라우저 캐시가 막아 주지만 해석 비용은 그대로 든다. 개정 경로 파일은
 * 불변이라 한 번 합친 결과를 그대로 다시 쓴다.
 */
function staticIndex(pointer: StaticLatest): Promise<LoadedIndex> {
  if (indexCache?.revision === pointer.revision) return indexCache.promise;
  const promise: Promise<LoadedIndex> = loadStaticIndex(pointer).catch((error) => {
    if (indexCache?.promise === promise) indexCache = null;
    throw error;
  });
  indexCache = { revision: pointer.revision, promise };
  return promise;
}

async function loadStaticIndex(pointer: StaticLatest): Promise<LoadedIndex> {
  const entries = await loadStaticIndexEntries(pointer);
  return {
    revision: pointer.revision,
    entries,
    positions: new Map(entries.map((entry, position) => [entry.id, position])),
  };
}

async function loadStaticIndexEntries(pointer: StaticLatest): Promise<StaticIndexEntry[]> {
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
    return index.entries;
  }
  if (!index.shards?.length) {
    if (index.count !== (index.entries ?? []).length) {
      throw { error: { code: "STATIC_INDEX_INVALID", message: "공지 색인이 비어 있고 전체 건수도 일치하지 않습니다.", retryable: true, request_id: `static-${pointer.revision}` } };
    }
    return index.entries ?? [];
  }

  // 조각 하나라도 실패하면 []로 조용히 바꾸지 않는다. 호출자가 오류 상태를
  // 보여 주고 같은 개정으로 재시도할 수 있도록 원래 오류를 전달한다.
  const payloads = await Promise.all(index.shards.map(async (shard) => {
    // 조각은 여기서 기억하지 않는다. 합친 결과(staticIndex)를 개정마다 한 번만 만들므로
    // 조각을 따로 붙들면 같은 22,000건을 두 벌 들고 있게 된다.
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
  return entries;
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

/**
 * 두 탭이 함께 쓰는 조직 범위. 여기 말고 다른 곳에서 조직을 거르지 않는다.
 *
 * 2026-09-08 사용자 결정: '전체 공지'와 '내 공지'는 같은 조직 범위를 쓴다. 그전에는
 * 규칙이 두 벌이었고, '내 공지'만 전교(university) 대상 공지를 조직 선택과 무관하게
 * 통과시켰다. 외국어대학·국제교류팀만 고른 사람의 목록에 한의과대학·경영대학원 글이
 * 섞인 원인이 이것이다. 이제 두 경로 모두 이 함수 하나를 통과한다.
 * (차이는 '내 공지'가 구독한 게시판을 따로 얹는 것뿐이다.)
 */
export type OrganizationScope = {
  /** 조직을 하나라도 골랐는가. 안 골랐으면 아무것도 거르지 않는다. */
  picked: boolean;
  ids: Set<string>;
};

/**
 * 고른 조직 + 그 아래 조직 전부 + 고른 것의 위 조직.
 *
 * 아래(자손): 사용자 결정 2026-09-07. "상위 조직을 고르면 그 하위 조직 공지가 전부
 * 보인다." 단과대를 골랐는데 목록이 비어 있던 원인이 이것이었다 — 공지 대부분은
 * 단과대가 아니라 학과 게시판에서 온다. 별칭 조직도 parent_id 로 이어져 함께 딸려 온다.
 *
 * 위(조상): 학과를 고른 사람에게 그 단과대·대학 공지가 보이는 것은 사용자가 원래
 * 원하던 동작이고, 학사·장학 같은 굵직한 공지가 대개 위에서 나온다.
 *
 * 순서가 중요하다. 자손을 먼저 넓히고 그다음에 조상을 얹는다. 뒤집으면 조상의
 * 자손까지 딸려 들어와, 행정학과를 고른 사람에게 정경대학 전 학과가 쏟아진다.
 *
 * `expand` 가 false 면 고른 조직만 본다(계약의 include_descendants 가 꺼진 경우).
 * 화면의 두 탭은 둘 다 켠 채로 부르므로 같은 범위가 나온다.
 */
export function organizationScope(
  organizations: Organization[],
  selected: readonly string[],
  expand: boolean,
): OrganizationScope {
  const picked = new Set(selected);
  if (!expand) return { picked: picked.size > 0, ids: picked };

  const ids = new Set(picked);
  let grew = true;
  while (grew) {
    grew = false;
    for (const organization of organizations) {
      if (organization.parent_id && ids.has(organization.parent_id) && !ids.has(organization.id)) {
        ids.add(organization.id);
        grew = true;
      }
    }
  }
  const byId = new Map(organizations.map((organization) => [organization.id, organization]));
  for (const selectedId of picked) {
    let current = byId.get(selectedId);
    const seen = new Set<string>();
    while (current?.parent_id && !seen.has(current.parent_id)) {
      seen.add(current.parent_id);
      ids.add(current.parent_id);
      current = byId.get(current.parent_id);
    }
  }
  return { picked: picked.size > 0, ids };
}

/**
 * 공지 하나가 그 범위 안인가.
 *
 * 조직을 고른 사람에게는 그 범위 대상 공지만 보인다. `university`·`campus:*`·
 * `undetermined` 처럼 조직이 붙지 않은 대상은 조직을 고르지 않았을 때만 통과한다.
 * 캠퍼스 대상만 해도 1,483건이라 통과시키면 필터가 없는 것과 같아진다.
 */
export function audiencesInOrganizationScope(audiences: string[], scope: OrganizationScope): boolean {
  if (!scope.picked) return true;
  return audiences.some((audience) => audience.startsWith("org:") && scope.ids.has(audience.slice(4)));
}

type NoticeMatchContext = {
  campusIds?: Set<string>;
  organizations?: Map<string, Organization>;
  sourceMedia?: Map<string, string>;
};

/** 조직 범위를 뺀 나머지 필터(검색어·주제·출처·매체·캠퍼스). 조직은 위 두 함수가 맡는다. */
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
  return true;
}

async function staticOrganizations(pointer: StaticLatest): Promise<Organization[]> {
  const response = await cachedStaticGet<ListResponse<Organization>>("organizations.json", pointer);
  if (response.page.dataset_revision !== pointer.revision) throw feedChanged();
  return response.data.map((organization) => normalizeOrganization(organization));
}

async function staticSourceMedia(pointer: StaticLatest): Promise<Map<string, string>> {
  const response = await cachedStaticGet<ListResponse<Source>>("sources.json", pointer);
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

/**
 * 목록 페이지 파일을 순서대로 훑어 조건에 맞는 공지를 모은다.
 *
 * 목록 한 줄을 그리는 데 필요한 것(주제·제목·출처 이름·시각·매체·동일 공지 수·포스터)은
 * 이미 목록 페이지 파일에 전부 들어 있다. 그래서 여기서는 색인도 상세도 부르지 않는다.
 * 예전에는 캠퍼스를 고른 것만으로 색인 12조각(1.1MB)을 먼저 받고, 그다음 상세 파일
 * 20개를 줄 세워 받았다 — 첫 화면이 9초 걸린 이유다.
 *
 * 한 건을 더 찾아 두고 그것은 돌려주지 않는다. 그래야 "다음이 있다"를 어림하지 않고
 * 정확히 말할 수 있다. 자리표는 전체 순서에서의 자리라, 다음 조회가 같은 자리에서
 * 이어 훑는다.
 *
 * 훑을 페이지 수에는 상한이 있다. 넘으면 null 을 주고 부르는 쪽이 색인 경로로 넘긴다
 * (드문 주제 하나만 골라 20줄이 아주 멀리 흩어져 있는 경우다). 다만 이미 이어보기
 * 중이면 그 자리표는 전체 순서 기준이라 색인 경로로 넘길 수 없다. 그때는 찾은 만큼만
 * 준다.
 */
async function scanNoticePages(
  pointer: StaticLatest,
  offset: number,
  limit: number,
  match: (notice: Notice) => boolean,
): Promise<{ data: Notice[]; hasNext: boolean; nextOffset: number } | null> {
  let pageNumber = Math.floor(offset / STATIC_PAGE_SIZE) + 1;
  let start = offset % STATIC_PAGE_SIZE;
  const found: Notice[] = [];
  let nextOffset = offset;
  let scanned = 0;

  for (;;) {
    const file = await cachedStaticGet<ListResponse<Notice>>(`notices/page/${pageNumber}.json`, pointer);
    if (file.page.dataset_revision !== pointer.revision) throw feedChanged();
    scanned += 1;
    for (let i = start; i < file.data.length; i += 1) {
      if (!match(file.data[i])) continue;
      if (found.length >= limit) return { data: found, hasNext: true, nextOffset };
      found.push(normalizeNotice(file.data[i]));
      nextOffset = (pageNumber - 1) * STATIC_PAGE_SIZE + i + 1;
    }
    if (!file.page.has_next) return { data: found, hasNext: false, nextOffset };
    if (scanned >= SCAN_PAGE_LIMIT) {
      if (found.length >= limit || offset > 0) return { data: found, hasNext: true, nextOffset };
      return null;
    }
    pageNumber += 1;
    start = 0;
  }
}

/**
 * 색인이 고른 항목을 목록 페이지 파일에서 꺼낸다.
 *
 * 색인과 목록 페이지는 같은 목록을 같은 순서로 내보내므로, 항목이 전체에서 몇 번째인지
 * 알면 어느 페이지 파일에 있는지도 안다. 20줄이 흩어져 있어도 필요한 페이지 파일만
 * 한꺼번에 받으면 되고, 대개는 몇 장으로 끝난다. 상세 파일 20개를 부르던 자리다.
 *
 * 자리 계산이 어긋나 그 페이지에 없으면 그 한 건만 상세 파일로 메운다. 옛 개정이든
 * 새 개정이든 이 되돌림이 있어 목록에 구멍이 나지 않는다.
 */
async function loadNoticesForEntries(
  pointer: StaticLatest,
  index: LoadedIndex,
  selected: StaticIndexEntry[],
): Promise<Notice[]> {
  const pageNumbers = new Set<number>();
  for (const entry of selected) {
    const position = index.positions.get(entry.id);
    if (position !== undefined) pageNumbers.add(Math.floor(position / STATIC_PAGE_SIZE) + 1);
  }
  const wanted = new Set(selected.map((entry) => entry.id));
  const byId = new Map<string, Notice>();
  await Promise.all([...pageNumbers].map(async (pageNumber) => {
    const file = await cachedStaticGet<ListResponse<Notice>>(`notices/page/${pageNumber}.json`, pointer);
    if (file.page.dataset_revision !== pointer.revision) throw feedChanged();
    for (const notice of file.data) if (wanted.has(notice.id)) byId.set(notice.id, normalizeNotice(notice));
  }));

  const missing = selected.filter((entry) => !byId.has(entry.id));
  await Promise.all(missing.map(async (entry) => {
    const response = await staticGet<ItemResponse<StaticNoticeDetail>>(`notices/${entry.id}.json`, pointer);
    byId.set(entry.id, normalizeNoticeDetail(response).data);
  }));

  return selected.map((entry) => byId.get(entry.id)).filter((notice): notice is Notice => !!notice);
}

async function staticNotices(query: NoticeQuery): Promise<ListResponse<Notice>> {
  const pointer = await staticLatest();
  const cursor = staticCursorAt(query.cursor, pointer.revision);
  const limit = query.limit ?? 20;
  const meta = { request_id: `static-${pointer.revision}`, generated_at: pointer.generated_at };
  /**
   * 목록 페이지만으로 답할 수 있는 조건인가.
   *
   * 주제·매체·캠퍼스는 목록 페이지의 공지 하나하나에 그대로 들어 있어 색인이 필요 없다.
   * 검색어·출처·조직은 다르다. 20줄이 22,410건 어디에 흩어져 있을지 모르므로 색인으로
   * 자리를 먼저 찾아야 한다.
   */
  const scannable = cursor?.kind !== "index"
    && !query.q
    && !query.source_id?.length
    && !query.organization_id?.length
    && (!query.sort || query.sort === "recent");

  if (scannable) {
    const organizations = query.campus_id?.length ? await staticOrganizations(pointer) : [];
    const organizationMap = new Map(organizations.map((organization) => [organization.id, organization]));
    const campusIds = new Set(query.campus_id ?? []);
    const categories = query.category_code ?? [];
    const media = query.medium ?? [];
    // 색인 경로(staticNoticeMatches·audienceMatchesCampus)와 글자 그대로 같은 판정이다.
    // 두 경로가 같은 조건에 다른 답을 내면 '더 보기'에서 목록이 어긋난다.
    const match = (notice: Notice) => {
      if (categories.length && !categories.includes(notice.primary_category.code)) return false;
      if (media.length && !media.includes(notice.primary_source.medium.code)) return false;
      if (campusIds.size && !noticeAudienceKeys(notice).some((audience) => audienceMatchesCampus(audience, campusIds, organizationMap))) return false;
      return true;
    };
    const scan = await scanNoticePages(pointer, cursor?.offset ?? 0, limit, match);
    if (scan) {
      return {
        data: scan.data,
        page: {
          next_cursor: scan.hasNext ? scanCursor(scan.nextOffset, pointer.revision) : null,
          has_next: scan.hasNext,
          snapshot_at: pointer.generated_at,
          dataset_revision: pointer.revision,
        },
        meta,
      };
    }
    // 훑기로는 한 화면을 채우지 못했다. 아래 색인 경로가 처음부터 다시 센다.
  }

  const offset = scannable ? 0 : cursor?.offset ?? 0;
  const index = await staticIndex(pointer);
  const needsOrganizations = Boolean(query.campus_id?.length || (query.organization_id?.length && query.include_descendants));
  const organizations = needsOrganizations ? await staticOrganizations(pointer) : [];
  const organizationMap = new Map(organizations.map((organization) => [organization.id, organization]));
  const sourceMedia = query.medium?.length ? await staticSourceMedia(pointer) : undefined;
  const scope = organizationScope(organizations, query.organization_id ?? [], !!query.include_descendants);
  let entries = index.entries.filter((entry) => audiencesInOrganizationScope(entry.a, scope) && staticNoticeMatches(entry, query, {
    campusIds: query.campus_id?.length ? new Set(query.campus_id) : undefined,
    organizations: organizationMap,
    sourceMedia,
  }));
  entries = sortIndexEntries(entries);
  const selected = entries.slice(offset, offset + limit);
  const data = await loadNoticesForEntries(pointer, index, selected);
  const hasNext = offset + data.length < entries.length;
  return {
    data,
    page: { next_cursor: hasNext ? indexCursor(offset + limit, pointer.revision) : null, has_next: hasNext, snapshot_at: pointer.generated_at, dataset_revision: pointer.revision },
    meta,
  };
}

async function staticPreview(body: FeedPreviewBody): Promise<ListResponse<Notice>> {
  const pointer = await staticLatest();
  const index = await staticIndex(pointer);
  const organizations = body.organization_ids.length || body.campus_id
    ? await staticOrganizations(pointer)
    : [];
  const organizationMap = new Map(organizations.map((organization) => [organization.id, organization]));
  // 조직 범위는 '전체 공지'와 똑같은 함수를 쓴다. 내 공지만의 차이는 아래에서
  // 구독한 게시판을 따로 얹는 것 하나뿐이다.
  const scope = organizationScope(organizations, body.organization_ids, true);
  const subscribed = new Set(body.subscribed_source_ids);
  const filters = body.filters ?? {};
  const sourceMedia = filters.medium?.length ? await staticSourceMedia(pointer) : undefined;
  const selected = index.entries.filter((entry) => {
    const campusIds = body.campus_id ? new Set([body.campus_id]) : new Set<string>();
    const inCampus = !campusIds.size || entry.a.some((audience) => audienceMatchesCampus(audience, campusIds, organizationMap));
    const inScope = inCampus && (subscribed.has(entry.s) || audiencesInOrganizationScope(entry.a, scope));
    return inScope && staticNoticeMatches(entry, filters, {
      campusIds: undefined,
      organizations: organizationMap,
      sourceMedia,
    });
  });
  const ordered = sortIndexEntries(selected);
  const offset = staticOffset(body.cursor, pointer.revision);
  const limit = body.limit ?? 20;
  const slice = ordered.slice(offset, offset + limit);
  const data = await loadNoticesForEntries(pointer, index, slice);
  const hasNext = offset + data.length < ordered.length;
  return {
    data,
    page: { next_cursor: hasNext ? indexCursor(offset + limit, pointer.revision) : null, has_next: hasNext, snapshot_at: pointer.generated_at, dataset_revision: pointer.revision },
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

/* ---------- 조직 관계 ---------- */
/**
 * 공지의 대상을 색인과 같은 글자로 바꾼다.
 *
 * 공지 계약은 `{ type: "organization", id }` 꼴이고 색인은 `"org:<id>"` 꼴이다.
 * 두 경로가 같은 범위 함수(organizationScope / audiencesInOrganizationScope /
 * audienceMatchesCampus)를 쓰려면 여기서 형태를 맞춰야 한다. 맞추지 않으면 목록
 * 페이지를 훑는 경로와 색인 경로가 같은 조건에 다른 답을 낸다.
 */
const noticeAudienceKeys = (notice: Notice): string[] =>
  notice.audiences.map((audience) => {
    if (audience.type === "organization") return `org:${audience.id}`;
    if (audience.type === "campus") return `campus:${audience.id}`;
    if (audience.type === "university") return "university";
    return "undetermined";
  });

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

/**
 * 색인 항목을 목록 페이지와 똑같은 "최신순"으로 세운다.
 *
 * 색인은 검색·필터·맞춤 목록이 쓰는 경로다. 걸러내고 나면 남은 항목만 다시 세워야
 * 하는데, 예전에는 발행일(d)과 id 로만 세웠다. 색인에 발행 시각이 없었기 때문이다.
 * 그 결과 같은 날 공지들이 id(무작위 16진수) 순으로 섞였다. 2026-09-07 사용자가
 * 09:14 → 09:16 → 09:24 처럼 같은 날 시각이 뒤죽박죽인 목록을 보고 지적했다.
 *
 * 이제 색인이 발행 시각(p)과 처음 본 시각(v)까지 담으므로, 서버(export/static.py 의
 * newest_first)와 글자 그대로 같은 키를 쓴다. 두 경로가 같은 키를 쓰면 목록 페이지와
 * 검색 결과가 어긋날 수 없다.
 *
 * p 가 없던 옛 개정은 다르게 다룬다. 그때는 발행일로만 세우고 같은 날 안은 서버가
 * 내보낸 순서 그대로 둔다(정렬 안정성은 ES2019 이후 규격이 보장한다). 그 개정의
 * 색인에는 같은 날 순서를 되만들 값이 아예 없어서, v 로 세우면 발행 순서가 아니라
 * 우리가 긁은 순서가 되어 도리어 서버가 옳게 내보낸 순서를 망가뜨린다.
 *
 * 반대로 p 가 있을 때 안정성에 기대지 않는 이유는, 그러면 화면의 정확성이 산출물의
 * 순서에 조용히 매여 스스로 검증할 수 없기 때문이다.
 */
const sortIndexEntries = (entries: StaticIndexEntry[]) => {
  const byDay = (a: StaticIndexEntry, b: StaticIndexEntry) => (b.d ?? "").localeCompare(a.d ?? "");
  // 옛 개정 판별: 항목에 p 키 자체가 없다. p 가 null 인 것(시각을 모르는 공지)과 다르다.
  if (!entries.some((entry) => entry.p !== undefined)) return [...entries].sort(byDay);
  return [...entries].sort((a, b) => (
    byDay(a, b)
    // 시각을 모르는 공지는 ""가 되어 그날의 맨 아래로 간다. 언제 올라왔는지 모르는
    // 글이 방금 올라온 것이 확실한 글을 밀어내지 않게 한다.
    || (b.p ?? "").localeCompare(a.p ?? "")
    || (b.v ?? "").localeCompare(a.v ?? "")
    || a.id.localeCompare(b.id)
  ));
};

/**
 * "최신순"은 발행일 기준이다.
 *
 * 처음 본 시각으로 세우면 수집이 과거 페이지를 긁는 동안 오래된 공지가 맨 위로 올라와
 * 목록이 뒤죽박죽으로 보인다. 발행일이 같으면 시각으로, 시각을 모르면 처음 본 시각으로
 * 가른다. 서버가 내보내는 순서와 같은 규칙이다.
 *
 * 검색 결과도 같은 순서로 준다. 예전에는 sort 값에 따라 갈랐으나 어느 쪽이든 처음 본
 * 시각으로 세워 결과가 같았고, 화면에도 "최신순" 한 가지만 있다.
 */
const sortNotices = (list: Notice[]) => {
  const key = (n: Notice) => [
    n.published_date ?? n.published_at?.slice(0, 10) ?? "",
    n.published_at ?? "",
    n.first_visible_at,
  ];
  return [...list].sort((a, b) => {
    const [ad, at, af] = key(a);
    const [bd, bt, bf] = key(b);
    return bd.localeCompare(ad) || bt.localeCompare(at) || bf.localeCompare(af) || a.id.localeCompare(b.id);
  });
};

const textMatch = (n: Notice, q?: string) => {
  if (!q) return true;
  const hay = `${n.title} ${n.excerpt ?? ""} ${n.primary_source.name}`.toLowerCase();
  return q.toLowerCase().split(/\s+/).every((w) => hay.includes(w));
};

/* ---------- public API ---------- */
export async function getCatalog(): Promise<ItemResponse<Catalog>> {
  if (!useMock) return staticLatest().then((pointer) => cachedStaticGet<ItemResponse<Catalog>>("catalog.json", pointer));
  await delay(60);
  return { data: M.catalog, meta: meta() };
}

export async function listOrganizations(params: { campus_id?: string; parent_id?: string | null } = {}): Promise<ListResponse<Organization>> {
  if (!useMock) {
    const pointer = await staticLatest();
    const response = await cachedStaticGet<ListResponse<Organization>>("organizations.json", pointer);
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

/**
 * 조직 트리의 칸마다 붙일 공지 건수.
 *
 * 공지 색인(약 600KB)을 통째로 읽어야 나오는 값이라 조직 선택기가 실제로 화면에
 * 보일 때만 부른다. 세지 못하면 화면에는 아무 수치도 내보내지 않는다 — 빈 숫자나
 * 어림수를 대신 넣지 않는다.
 */
export async function listOrganizationNoticeCounts(): Promise<NodeCounts> {
  if (!useMock) {
    const pointer = await staticLatest();
    const [index, organizations, catalog] = await Promise.all([
      staticIndex(pointer),
      staticOrganizations(pointer),
      cachedStaticGet<ItemResponse<Catalog>>("catalog.json", pointer),
    ]);
    return countNoticesByNode(organizations, catalog.data.campuses, index.entries.map((entry) => entry.a));
  }
  await delay(60);
  // 가상 데이터의 audience.type 은 "organization"이고 색인 쪽 접두사는 "org"다.
  const audiences = M.notices.map((notice) =>
    notice.audiences
      .filter((audience) => !!audience.id && (audience.type === "organization" || audience.type === "campus"))
      .map((audience) => `${audience.type === "organization" ? "org" : "campus"}:${audience.id}`),
  );
  return countNoticesByNode(M.organizations, M.catalog.campuses, audiences);
}

export async function listNotices(query: NoticeQuery = {}): Promise<ListResponse<Notice>> {
  if (!useMock) return staticNotices(query);
  await delay(120);
  let list = M.notices.filter((n) => matchesCampus(n, query.campus_id ?? []));
  if (query.category_code?.length) list = list.filter((n) => query.category_code!.includes(n.primary_category.code));
  if (query.medium?.length) list = list.filter((n) => query.medium!.includes(n.primary_source.medium.code));
  if (query.source_id?.length) list = list.filter((n) => query.source_id!.includes(n.primary_source.id));
  const scope = organizationScope(M.organizations, query.organization_id ?? [], !!query.include_descendants);
  list = list.filter((n) => audiencesInOrganizationScope(noticeAudienceKeys(n), scope));
  list = list.filter((n) => textMatch(n, query.q));
  return paginate(sortNotices(list), query.limit ?? 20, query.cursor);
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

/** 비회원 맞춤 조회: 선택 캠퍼스 + 소속 조직(하위·상위 포함) + 구독 출처 */
export async function previewFeed(body: FeedPreviewBody): Promise<ListResponse<Notice>> {
  if (!useMock) return staticPreview(body);
  await delay(140);
  // staticPreview 와 같은 규칙을 같은 함수로 적용한다. 예전에는 여기서 university·campus
  // 대상을 조직 선택과 무관하게 통과시켜, 가상 데이터로 도는 화면만 규칙이 달랐다.
  const scope = organizationScope(M.organizations, body.organization_ids, true);
  const subs = new Set(body.subscribed_source_ids);
  let list = M.notices.filter((n) => {
    if (!matchesCampus(n, body.campus_id ? [body.campus_id] : [])) return false;
    return subs.has(n.primary_source.id) || audiencesInOrganizationScope(noticeAudienceKeys(n), scope);
  });
  const f = body.filters ?? {};
  if (f.category_code?.length) list = list.filter((n) => f.category_code!.includes(n.primary_category.code));
  if (f.medium?.length) list = list.filter((n) => f.medium!.includes(n.primary_source.medium.code));
  list = list.filter((n) => textMatch(n, f.q));
  return paginate(sortNotices(list), body.limit ?? 20, body.cursor);
}

export async function listSources(params: { campus_id?: string; q?: string } = {}): Promise<ListResponse<Source>> {
  if (!useMock) {
    const pointer = await staticLatest();
    const [response, organizations] = await Promise.all([
      cachedStaticGet<ListResponse<Source>>("sources.json", pointer),
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
