import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { dirname, isAbsolute, resolve } from "node:path";
import { test } from "node:test";
import * as ts from "typescript";
import * as vm from "node:vm";
import { fileURLToPath } from "node:url";

const frontendRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const sourceRoot = resolve(frontendRoot, "src");

async function loadApi(fetchImpl) {
  process.env.NEXT_PUBLIC_API_BASE = "https://static.example";
  const context = vm.createContext({
    console,
    process,
    fetch: fetchImpl,
    btoa,
    atob,
    setTimeout,
    clearTimeout,
    Date,
    Promise,
    URL,
  });
  const modules = new Map();

  async function load(specifier, parent) {
    let file;
    if (isAbsolute(specifier)) file = specifier;
    else if (specifier.startsWith("@/")) file = resolve(sourceRoot, specifier.slice(2));
    else if (specifier.startsWith(".")) file = resolve(dirname(parent), specifier);
    else throw new Error(`Unexpected import in API test: ${specifier}`);
    if (!file.endsWith(".ts") && !file.endsWith(".tsx")) file += ".ts";
    if (modules.has(file)) return modules.get(file);

    const source = await readFile(file, "utf8");
    const output = ts.transpileModule(source, {
      compilerOptions: {
        target: ts.ScriptTarget.ES2022,
        module: ts.ModuleKind.ESNext,
        jsx: ts.JsxEmit.ReactJSX,
        esModuleInterop: true,
      },
      fileName: file,
    }).outputText;
    const vmModule = new vm.SourceTextModule(output, { context, identifier: file });
    modules.set(file, vmModule);
    await vmModule.link((child, referencingModule) => load(child, referencingModule.identifier));
    return vmModule;
  }

  const api = await load(resolve(sourceRoot, "lib/api.ts"), resolve(sourceRoot, "lib/api.ts"));
  await api.evaluate();
  return api.namespace;
}

const page = (data, revision = "r1", hasNext = false) => ({
  data,
  page: { next_cursor: hasNext ? "2" : null, has_next: hasNext, snapshot_at: "2026-09-06T00:00:00Z", dataset_revision: revision },
  meta: { request_id: `static-${revision}`, generated_at: "2026-09-06T00:00:00Z" },
});

const latest = (revision = "r1") => ({
  revision,
  generated_at: "2026-09-06T00:00:00Z",
  base_path: `v1/r/${revision}`,
  contract_version: "v1",
  notice_pages: 2,
  notices_total: 100,
  contacts_total: 51,
});

const coded = (code, label = code) => ({ code, label });

function notice(id, audiences = [{ type: "university", id: null, name: "경희대학교" }]) {
  return {
    id,
    kind: "notice",
    title: `공지 ${id}`,
    excerpt: null,
    primary_category: coded("academic", "학사"),
    secondary_categories: [],
    audiences,
    audience_note: null,
    primary_source: { id: "src-web", name: "웹 출처", medium: coded("web", "웹") },
    source_count: 1,
    published_date: "2026-09-06",
    published_at: null,
    published_precision: "date",
    first_visible_at: "2026-09-06T00:00:00Z",
    updated_at: "2026-09-06T00:00:00Z",
    deadline: null,
    original_url: `https://example.test/${id}`,
    original_status: coded("available", "확인됨"),
    freshness: { ...coded("fresh", "최신"), last_checked_at: "2026-09-06T00:00:00Z" },
  };
}

function backendDetail(base, source = { id: "src-web", name: "웹 출처", medium: coded("web", "웹") }) {
  return {
    ...base,
    body_text: "본문",
    body_html: null,
    sources: [{ source_item_id: `${base.id}-item`, source, url: base.original_url, published_date: base.published_date, original_status: base.original_status, is_primary: true }],
    attachments: [{ id: `${base.id}-att`, filename: "신청서.hwp", kind: "hwp", size_bytes: 12, url: "https://example.test/file", status: coded("listed", "목록 확인됨") }],
    contact_mentions: [{ raw_text: "02-961-0000", channels: [{ id: "ch", kind: coded("phone", "전화"), display_value: "02-961-0000", value: "02-961-0000", action_url: "tel:02-961-0000" }], contact_id: "contact-1" }],
    related_notices: [],
    resolved_from_id: null,
  };
}

function staticPath(url) {
  return new URL(url).pathname;
}

const sourceRefs = {
  "src-web": { id: "src-web", name: "웹 출처", medium: coded("web", "웹") },
  "src-ig": { id: "src-ig", name: "인스타 출처", medium: coded("instagram", "인스타그램") },
};

/** 운영 catalog.json 의 분류 이름 그대로. 검색이 분류 이름을 보므로 시험도 같은 글자를 쓴다. */
const CATEGORIES = [
  coded("academic", "학사"),
  coded("graduation", "졸업"),
  coded("scholarship", "장학"),
  coded("career", "취업·인턴"),
  coded("event", "행사"),
  coded("other", "기타"),
];

const catalogFile = (categories = CATEGORIES) => ({
  data: { campuses: [], organization_types: [], categories, media: [], features: {} },
  meta: { request_id: "static-r1", generated_at: "2026-09-06T00:00:00Z" },
});

/** sources.json 한 줄. 검색은 이름(name)만 본다. */
const sourceFile = (ref, organizationName = "경희대학교") => ({
  ...ref,
  organization: { id: "org-any", name: organizationName, campuses: [] },
  content_kind: coded("notice", "공지"),
  url: null,
  status: coded("active", "정상"),
  status_message: null,
  last_success_at: null,
  initial_window_days: null,
});

const audienceFromKey = (key) => {
  if (key === "university") return { type: "university", id: null, name: "경희대학교" };
  if (key.startsWith("campus:")) return { type: "campus", id: key.slice(7), name: key };
  if (key.startsWith("org:")) return { type: "organization", id: key.slice(4), name: key };
  return { type: "undetermined", id: null, name: key };
};

/**
 * 색인 항목과 같은 순서·같은 내용의 목록 페이지 파일을 만든다.
 *
 * 화면은 목록 한 줄을 목록 페이지 파일에서 그대로 꺼내 쓴다(상세 파일을 부르지 않는다).
 * 그래서 시험에서도 색인과 목록 페이지가 같은 목록을 같은 순서로 담고 있어야 한다 —
 * 백엔드(export/static.py)가 실제로 그렇게 내보낸다.
 */
function noticeFromEntry(entry) {
  const base = notice(entry.id, entry.a.map(audienceFromKey));
  return { ...base, primary_source: sourceRefs[entry.s] ?? base.primary_source };
}

function noticePages(entries, revision = "r1") {
  const list = entries.map(noticeFromEntry);
  return (path) => {
    const match = /\/notices\/page\/(\d+)\.json$/.exec(path);
    if (!match) return null;
    const pageNumber = Number(match[1]);
    return page(list.slice((pageNumber - 1) * 50, pageNumber * 50), revision, pageNumber * 50 < list.length);
  };
}

test("공지 목록은 50건 페이지 경계를 넘어 연속으로 반환한다", async () => {
  const first = Array.from({ length: 50 }, (_, i) => notice(`n-${i + 1}`));
  const second = Array.from({ length: 50 }, (_, i) => notice(`n-${i + 51}`));
  const calls = [];
  const fetchImpl = async (url) => {
    const path = staticPath(url);
    calls.push(path);
    if (path === "/v1/latest.json") return { ok: true, json: async () => latest() };
    if (path.endsWith("/notices/page/1.json")) return { ok: true, json: async () => page(first, "r1", true) };
    if (path.endsWith("/notices/page/2.json")) return { ok: true, json: async () => page(second) };
    throw new Error(`Unexpected path ${path}`);
  };
  const api = await loadApi(fetchImpl);
  const cursor = btoa("r1:40");
  const response = await api.listNotices({ cursor, limit: 20 });
  assert.deepEqual(Array.from(response.data, (item) => item.id), Array.from({ length: 20 }, (_, i) => `n-${i + 41}`));
  assert.equal(atob(response.page.next_cursor).split(":")[1], "60");
  assert.ok(calls.some((path) => path.endsWith("/notices/page/2.json")));
});

test("latest 포인터 실패는 캐시하지 않고 다음 조회에서 재시도한다", async () => {
  let latestCalls = 0;
  const fetchImpl = async (url) => {
    const path = staticPath(url);
    if (path === "/v1/latest.json") {
      latestCalls += 1;
      if (latestCalls === 1) return { ok: false, json: async () => ({ error: { code: "TEMPORARY" } }) };
      return { ok: true, json: async () => latest() };
    }
    if (path.endsWith("/catalog.json")) return { ok: true, json: async () => ({ data: { campuses: [], organization_types: [], categories: [], media: [], features: {} }, meta: {} }) };
    throw new Error(`Unexpected path ${path}`);
  };
  const api = await loadApi(fetchImpl);
  await assert.rejects(api.getCatalog(), (error) => error.error.code === "TEMPORARY");
  await api.getCatalog();
  assert.equal(latestCalls, 2);
});

test("latest TTL 만료 뒤 새 개정을 읽고 이전 개정 cursor를 거부한다", async () => {
  const realNow = Date.now;
  let now = realNow();
  let revision = "r1";
  let latestCalls = 0;
  Date.now = () => now;
  try {
    const fetchImpl = async (url) => {
      const path = staticPath(url);
      if (path === "/v1/latest.json") {
        latestCalls += 1;
        return { ok: true, json: async () => latest(revision) };
      }
      if (path.endsWith("/notices/page/1.json")) return { ok: true, json: async () => page([notice(`${revision}-notice`)], revision, false) };
      throw new Error(`Unexpected path ${path}`);
    };
    const api = await loadApi(fetchImpl);
    const first = await api.listNotices({ limit: 1 });
    assert.equal(first.page.dataset_revision, "r1");
    revision = "r2";
    now += 10_001;
    await assert.rejects(
      api.listNotices({ cursor: btoa("r1:0"), limit: 1 }),
      (error) => error.error.code === "FEED_CHANGED",
    );
    const refreshed = await api.listNotices({ limit: 1 });
    assert.equal(refreshed.page.dataset_revision, "r2");
    assert.equal(latestCalls, 2);
  } finally {
    Date.now = realNow;
  }
});

test("캠퍼스·매체 필터와 상위 조직 범위를 실제 색인 계약에 적용한다", async () => {
  const entries = [
    { id: "campus-web", t: "캠퍼스 웹", c: "academic", o: null, s: "src-web", d: "2026-09-06", v: "2026-09-06T00:00:00Z", a: ["campus:campus-seoul"] },
    { id: "campus-ig", t: "캠퍼스 인스타", c: "academic", o: null, s: "src-ig", d: "2026-09-06", v: "2026-09-06T00:00:00Z", a: ["campus:campus-seoul"] },
    { id: "global-ig", t: "다른 캠퍼스 인스타", c: "academic", o: null, s: "src-ig", d: "2026-09-06", v: "2026-09-06T00:00:00Z", a: ["campus:campus-global"] },
    { id: "college", t: "단과대 공지", c: "academic", o: null, s: "src-web", d: "2026-09-06", v: "2026-09-06T00:00:00Z", a: ["org:org-college"] },
    { id: "dept", t: "학과 공지", c: "academic", o: null, s: "src-web", d: "2026-09-06", v: "2026-09-06T00:00:00Z", a: ["org:org-dept"] },
    { id: "child", t: "하위 공지", c: "academic", o: null, s: "src-web", d: "2026-09-06", v: "2026-09-06T00:00:00Z", a: ["org:org-child"] },
    { id: "alias", t: "별칭 공지", c: "academic", o: null, s: "src-web", d: "2026-09-06", v: "2026-09-06T00:00:00Z", a: ["org:org-alias"] },
    { id: "sibling", t: "형제 학과 공지", c: "academic", o: null, s: "src-web", d: "2026-09-06", v: "2026-09-06T00:00:00Z", a: ["org:org-sibling"] },
  ];
  const organizations = page([
    { id: "org-college", name: "단과대", type: coded("college", "단과대"), parent_id: null, campuses: [{ id: "campus-seoul", name: "서울" }], has_children: true },
    { id: "org-dept", name: "학과", type: coded("department", "학과"), parent_id: "org-college", campuses: [{ id: "campus-seoul", name: "서울" }], has_children: true },
    { id: "org-child", name: "세부 조직", type: coded("office", "부서"), parent_id: "org-dept", campuses: [{ id: "campus-seoul", name: "서울" }], has_children: false },
    { id: "org-alias", name: "학과(옛 이름)", type: coded("department", "학과"), parent_id: "org-dept", campuses: [{ id: "campus-seoul", name: "서울" }], has_children: false, is_alias: true },
    { id: "org-sibling", name: "형제 학과", type: coded("department", "학과"), parent_id: "org-college", campuses: [{ id: "campus-seoul", name: "서울" }], has_children: false },
  ]);
  const sources = page([
    { id: "src-web", name: "웹 출처", organization: { id: "org-college", name: "단과대" }, medium: coded("web", "웹"), content_kind: coded("notice", "공지"), url: "https://example.test/web", status: coded("active", "정상"), status_message: null, last_success_at: null, initial_window_days: null },
    { id: "src-ig", name: "인스타 출처", organization: { id: "org-college", name: "단과대" }, medium: coded("instagram", "인스타그램"), content_kind: coded("notice", "공지"), url: "https://example.test/ig", status: coded("active", "정상"), status_message: null, last_success_at: null, initial_window_days: null },
  ]);
  const details = Object.fromEntries(entries.map((entry) => [entry.id, backendDetail(notice(entry.id, entry.a.map((audience) => ({ type: audience.startsWith("org:") ? "organization" : audience.startsWith("campus:") ? "campus" : "university", id: audience.includes(":") ? audience.split(":")[1] : null, name: audience })) ))]));
  const pages = noticePages(entries);
  const fetchImpl = async (url) => {
    const path = staticPath(url);
    if (path === "/v1/latest.json") return { ok: true, json: async () => latest() };
    if (path.endsWith("/notices/index.json")) return { ok: true, json: async () => ({ ...latest(), count: entries.length, entries }) };
    if (path.endsWith("/organizations.json")) return { ok: true, json: async () => organizations };
    if (path.endsWith("/sources.json")) return { ok: true, json: async () => sources };
    const pageFile = pages(path);
    if (pageFile) return { ok: true, json: async () => pageFile };
    const detail = Object.entries(details).find(([id]) => path.endsWith(`/notices/${id}.json`))?.[1];
    if (detail) return { ok: true, json: async () => ({ data: detail, meta: {} }) };
    throw new Error(`Unexpected path ${path}`);
  };
  const api = await loadApi(fetchImpl);
  const filtered = await api.listNotices({ campus_id: ["campus-seoul"], medium: ["instagram"], limit: 20 });
  // Array.from 으로 감싼다. 목록을 vm 안에서 만들면 배열의 원형이 달라 deepEqual 이 어긋난다.
  assert.deepEqual(Array.from(filtered.data, (item) => item.id), ["campus-ig"]);
  // 조직을 고르면 그 조직 + 그 아래 전부 + 그 위가 보인다(사용자 결정 2026-09-07).
  // 아래를 합치지 않으면 단과대를 고른 사람의 홈이 비어 버린다 - 공지 대부분이
  // 단과대가 아니라 학과 게시판에서 오기 때문이다. 별칭 조직도 부모로 이어져 딸려 온다.
  // 위(상위)는 그대로 둔다: 학사·장학 같은 굵직한 공지가 위에서 나온다.
  // 캠퍼스 전체 공지는 뺀다. 그것이 실제로는 학과 공지인 경우가 많기 때문이다 -
  // 2026-09-07 운영 기준 캠퍼스 대상 1,546건 중 916건이 학과·단과대 게시판 글이었다.
  const preview = await api.previewFeed({ campus_id: "campus-seoul", organization_ids: ["org-dept"], subscribed_source_ids: [], limit: 20 });
  assert.deepEqual(Array.from(preview.data, (item) => item.id).sort(), ["alias", "child", "college", "dept"]);
  assert.equal(preview.data.some((item) => item.id === "campus-web"), false);
  // 상위를 얹는다고 그 상위의 다른 자식까지 딸려 오면 안 된다.
  assert.equal(preview.data.some((item) => item.id === "sibling"), false);
  // 단과대를 고르면 그 아래 학과·별칭 공지가 모두 들어온다.
  const collegePreview = await api.previewFeed({ campus_id: "campus-seoul", organization_ids: ["org-college"], subscribed_source_ids: [], limit: 20 });
  assert.deepEqual(Array.from(collegePreview.data, (item) => item.id).sort(), ["alias", "child", "college", "dept", "sibling"]);
  const subscribed = await api.previewFeed({ campus_id: "campus-seoul", organization_ids: [], subscribed_source_ids: ["src-ig"], limit: 20 });
  assert.equal(subscribed.data.some((item) => item.id === "global-ig"), false);
  const mediumPreview = await api.previewFeed({ campus_id: "campus-seoul", organization_ids: [], subscribed_source_ids: [], filters: { medium: ["instagram"] }, limit: 20 });
  assert.deepEqual(Array.from(mediumPreview.data, (item) => item.id), ["campus-ig"]);
});

test("전체 공지와 내 공지는 같은 조직 범위 규칙을 쓴다", async () => {
  // 2026-09-08 사용자 실측: 외국어대학 + 국제처 국제교류팀만 골랐는데 '내 공지'에만
  // 한의과대학·경영대학원 글이 섞였다. '내 공지'가 전교(university) 대상 공지를
  // 조직 선택과 무관하게 통과시켰기 때문이다. 아래 셋을 못으로 박는다.
  const entries = [
    { id: "univ", t: "전교 공지", c: "academic", o: null, s: "src-web", d: "2026-09-06", v: "2026-09-06T00:00:00Z", a: ["university"] },
    { id: "campus", t: "캠퍼스 공지", c: "academic", o: null, s: "src-web", d: "2026-09-06", v: "2026-09-06T00:00:00Z", a: ["campus:campus-seoul"] },
    { id: "college", t: "단과대 공지", c: "academic", o: null, s: "src-web", d: "2026-09-06", v: "2026-09-06T00:00:00Z", a: ["org:org-college"] },
    { id: "dept", t: "학과 공지", c: "academic", o: null, s: "src-web", d: "2026-09-06", v: "2026-09-06T00:00:00Z", a: ["org:org-dept"] },
    { id: "child", t: "하위 공지", c: "academic", o: null, s: "src-web", d: "2026-09-06", v: "2026-09-06T00:00:00Z", a: ["org:org-child"] },
    { id: "sibling", t: "형제 학과 공지", c: "academic", o: null, s: "src-web", d: "2026-09-06", v: "2026-09-06T00:00:00Z", a: ["org:org-sibling"] },
    // 고른 곳과 아무 상관 없는 단과대. 사용자가 본 '한의과대학' 자리다.
    { id: "other", t: "남의 단과대 공지", c: "academic", o: null, s: "src-web", d: "2026-09-06", v: "2026-09-06T00:00:00Z", a: ["org:org-other"] },
  ];
  const campuses = [{ id: "campus-seoul", name: "서울" }];
  const organizations = page([
    { id: "org-college", name: "단과대", type: coded("college", "단과대"), parent_id: null, campuses, has_children: true },
    { id: "org-dept", name: "학과", type: coded("department", "학과"), parent_id: "org-college", campuses, has_children: true },
    { id: "org-child", name: "세부 조직", type: coded("office", "부서"), parent_id: "org-dept", campuses, has_children: false },
    { id: "org-sibling", name: "형제 학과", type: coded("department", "학과"), parent_id: "org-college", campuses, has_children: false },
    { id: "org-other", name: "남의 단과대", type: coded("college", "단과대"), parent_id: null, campuses, has_children: false },
  ]);
  const pages = noticePages(entries);
  const fetchImpl = async (url) => {
    const path = staticPath(url);
    if (path === "/v1/latest.json") return { ok: true, json: async () => latest() };
    if (path.endsWith("/notices/index.json")) return { ok: true, json: async () => ({ ...latest(), count: entries.length, entries }) };
    if (path.endsWith("/organizations.json")) return { ok: true, json: async () => organizations };
    const pageFile = pages(path);
    if (pageFile) return { ok: true, json: async () => pageFile };
    const entry = entries.find((one) => path.endsWith(`/notices/${one.id}.json`));
    if (entry) return { ok: true, json: async () => ({ data: backendDetail(notice(entry.id)), meta: {} }) };
    throw new Error(`Unexpected path ${path}`);
  };
  const api = await loadApi(fetchImpl);
  const mine = (organization_ids) => api.previewFeed({ campus_id: "campus-seoul", organization_ids, subscribed_source_ids: [], limit: 20 });
  const all = (organization_id) => api.listNotices({ campus_id: ["campus-seoul"], organization_id, include_descendants: true, limit: 20 });

  // (가) 조직을 고르면 전교 대상 공지는 '내 공지'에서 빠진다. 남의 단과대도 마찬가지다.
  const picked = await mine(["org-dept"]);
  const pickedIds = Array.from(picked.data, (item) => item.id);
  assert.equal(pickedIds.includes("univ"), false);
  assert.equal(pickedIds.includes("campus"), false);
  assert.equal(pickedIds.includes("other"), false);
  assert.deepEqual([...pickedIds].sort(), ["child", "college", "dept"]);

  // (나) 조직을 하나도 안 고르면 전교·캠퍼스 대상 공지가 들어온다.
  const open = await mine([]);
  const openIds = Array.from(open.data, (item) => item.id);
  assert.equal(openIds.includes("univ"), true);
  assert.equal(openIds.includes("campus"), true);

  // (다) 같은 선택에는 두 탭이 같은 집합을 낸다.
  assert.deepEqual(pickedIds, Array.from((await all(["org-dept"])).data, (item) => item.id));
  assert.deepEqual(openIds, Array.from((await all([])).data, (item) => item.id));
});

test("entries가 없는 색인은 모든 조각을 합치고 개정·건수 불일치를 거부한다", async () => {
  const shardEntries = [
    { id: "shard-1", t: "첫 조각", c: "academic", o: null, s: "src", d: null, v: "2026-09-06T00:00:00Z", a: ["university"] },
    { id: "shard-2", t: "둘째 조각", c: "academic", o: null, s: "src", d: null, v: "2026-09-06T00:00:00Z", a: ["university"] },
  ];
  const pages = noticePages(shardEntries);
  // 색인은 개정마다 한 번만 읽고 그 결과를 다시 쓴다(개정 경로 파일은 불변이다).
  // 그래서 "합치기"와 "건수 불일치 거부"는 각각 새 화면에서 확인한다.
  const build = (invalid) => async (url) => {
    const path = staticPath(url);
    if (path === "/v1/latest.json") return { ok: true, json: async () => latest() };
    if (path.endsWith("/notices/index.json")) {
      return {
        ok: true,
        json: async () => ({
          revision: "r1",
          generated_at: "2026-09-06T00:00:00Z",
          count: invalid ? 3 : 2,
          entries: [],
          shards: [{ path: "notices/index/1.json", count: 1 }, { path: "notices/index/2.json", count: 1 }],
        }),
      };
    }
    if (path.endsWith("/notices/index/1.json")) return { ok: true, json: async () => ({ revision: "r1", generated_at: "2026-09-06T00:00:00Z", count: 1, entries: [shardEntries[0]] }) };
    if (path.endsWith("/notices/index/2.json")) return { ok: true, json: async () => ({ revision: "r1", generated_at: "2026-09-06T00:00:00Z", count: 1, entries: [shardEntries[1]] }) };
    // 검색은 조직 이름까지 본다. 이 시험이 볼 것은 조각 합치기라 조직은 비워 둔다.
    if (path.endsWith("/organizations.json")) return { ok: true, json: async () => page([]) };
    const pageFile = pages(path);
    if (pageFile) return { ok: true, json: async () => pageFile };
    throw new Error(`Unexpected path ${path}`);
  };
  const api = await loadApi(build(false));
  const response = await api.listNotices({ q: "조각", limit: 20 });
  assert.deepEqual(Array.from(response.data, (item) => item.id).sort(), ["shard-1", "shard-2"]);
  const broken = await loadApi(build(true));
  await assert.rejects(broken.listNotices({ q: "조각", limit: 20 }), (error) => error.error.code === "STATIC_INDEX_INVALID");
});

test("검색·조직 걸러내기는 상세 파일 대신 목록 페이지에서 줄을 꺼낸다", async () => {
  // 2026-09-08 실측: 목록 20줄을 그리려고 상세 파일 20개를 불러 첫 화면이 9.1초 걸렸다.
  // 색인이 고른 항목이 전체에서 몇 번째인지 알면 어느 목록 페이지에 있는지도 알고,
  // 목록 페이지에는 줄을 그릴 것이 전부 들어 있다.
  const entries = Array.from({ length: 60 }, (_, i) => ({
    id: `n-${i + 1}`,
    t: i % 3 === 0 ? `찾을 공지 ${i + 1}` : `다른 공지 ${i + 1}`,
    c: "academic", o: null, s: "src-web", d: "2026-09-06", v: "2026-09-06T00:00:00Z", a: ["university"],
  }));
  const pages = noticePages(entries);
  const asked = [];
  const fetchImpl = async (url) => {
    const path = staticPath(url);
    asked.push(path);
    if (path === "/v1/latest.json") return { ok: true, json: async () => latest() };
    if (path.endsWith("/notices/index.json")) return { ok: true, json: async () => ({ ...latest(), count: entries.length, entries }) };
    // 검색은 조직 이름까지 본다. 이 시험이 볼 것은 어떤 파일을 부르느냐라 조직은 비워 둔다.
    if (path.endsWith("/organizations.json")) return { ok: true, json: async () => page([]) };
    const pageFile = pages(path);
    if (pageFile) return { ok: true, json: async () => pageFile };
    throw new Error(`Unexpected path ${path}`);
  };
  const api = await loadApi(fetchImpl);
  const response = await api.listNotices({ q: "찾을", limit: 20 });
  assert.deepEqual(Array.from(response.data, (item) => item.id), entries.filter((entry) => entry.t.startsWith("찾을")).slice(0, 20).map((entry) => entry.id));
  assert.equal(asked.some((path) => /\/notices\/ntc-|\/notices\/n-\d+\.json$/.test(path)), false);
  assert.deepEqual(asked.filter((path) => path.includes("/notices/page/")).sort(), ["/v1/r/r1/notices/page/1.json", "/v1/r/r1/notices/page/2.json"]);
});

test("훑기로 한 화면을 못 채우면 색인 경로로 넘어가고 이어보기도 그쪽 자리표를 쓴다", async () => {
  // 아주 드문 주제 하나만 고르면 20줄이 아주 멀리 흩어져 있다. 목록 페이지를 끝없이
  // 넘기지 않고 색인으로 자리를 먼저 찾는다. 두 경로의 자리표는 세는 기준이 달라
  // (전체 순서 / 걸러낸 목록) 섞이면 안 된다 — 자리표에 색인 표시가 붙는 이유다.
  const entries = Array.from({ length: 500 }, (_, i) => ({
    id: `n-${i + 1}`,
    t: `공지 ${i + 1}`,
    c: i % 100 === 0 ? "startup" : "academic",
    o: null, s: "src-web", d: "2026-09-06", v: "2026-09-06T00:00:00Z", a: ["university"],
  }));
  const pages = noticePages(entries);
  const asked = [];
  const fetchImpl = async (url) => {
    const path = staticPath(url);
    asked.push(path);
    if (path === "/v1/latest.json") return { ok: true, json: async () => latest() };
    if (path.endsWith("/notices/index.json")) return { ok: true, json: async () => ({ ...latest(), count: entries.length, entries }) };
    const pageFile = pages(path);
    if (pageFile) return { ok: true, json: async () => pageFile };
    throw new Error(`Unexpected path ${path}`);
  };
  const api = await loadApi(fetchImpl);
  const first = await api.listNotices({ category_code: ["startup"], limit: 3 });
  assert.deepEqual(Array.from(first.data, (item) => item.id), ["n-1", "n-101", "n-201"]);
  assert.ok(asked.some((path) => path.endsWith("/notices/index.json")), "색인을 읽어야 한다");
  // 색인 자리표는 걸러낸 목록에서의 자리다. 훑기 자리표로 잘못 읽히면 안 된다.
  assert.equal(atob(first.page.next_cursor).split(":")[1], "i3");
  const second = await api.listNotices({ category_code: ["startup"], limit: 3, cursor: first.page.next_cursor });
  assert.deepEqual(Array.from(second.data, (item) => item.id), ["n-301", "n-401"]);
  assert.equal(second.page.has_next, false);
});

test("연락처는 50건 경계를 넘어 전체 페이지를 읽고 필터링한다", async () => {
  const contacts = Array.from({ length: 51 }, (_, i) => ({
    id: `contact-${i + 1}`,
    kind: "contact",
    organization: { id: `org-${i + 1}`, name: `기관 ${i + 1}`, path: [`기관 ${i + 1}`], type: coded("office", "부서") },
    campuses: [{ id: "campus-seoul", name: "서울" }],
    service_name: `서비스 ${i + 1}`,
    channels: [],
    location: null,
    office_hours: null,
    official_url: null,
    verification: { ...coded("verified", "확인됨"), verified_at: null, message: null },
    evidence: [],
  }));
  const fetchImpl = async (url) => {
    const path = staticPath(url);
    if (path === "/v1/latest.json") return { ok: true, json: async () => ({ ...latest(), contacts_total: 51 }) };
    if (path.endsWith("/contacts/page/1.json")) return { ok: true, json: async () => page(contacts.slice(0, 50), "r1", true) };
    if (path.endsWith("/contacts/page/2.json")) return { ok: true, json: async () => page(contacts.slice(50), "r1", false) };
    throw new Error(`Unexpected path ${path}`);
  };
  const api = await loadApi(fetchImpl);
  const response = await api.listContacts({ campus_id: "campus-seoul" });
  assert.equal(response.data.length, 51);
  assert.equal(response.data.at(-1).id, "contact-51");
  assert.equal(response.page.has_next, false);
});

test("실제 NoticeDetail 계약을 화면 소비 형태로 정규화한다", async () => {
  const base = notice("detail-1");
  const detail = backendDetail(base, { id: "src-ig", name: "인스타 출처", medium: coded("instagram", "인스타그램") });
  detail.contact_mentions[0].channels.push({ id: "ch-email", kind: coded("email", "이메일"), display_value: "help@example.org", value: "help@example.org", action_url: "mailto:help@example.org" });
  const fetchImpl = async (url) => {
    const path = staticPath(url);
    if (path === "/v1/latest.json") return { ok: true, json: async () => latest() };
    if (path.endsWith("/notices/detail-1.json")) return { ok: true, json: async () => ({ data: detail, meta: {} }) };
    throw new Error(`Unexpected path ${path}`);
  };
  const api = await loadApi(fetchImpl);
  const response = await api.getNotice("detail-1");
  assert.equal(response.data.sources[0].source_id, "src-ig");
  assert.equal(response.data.sources[0].source_name, "인스타 출처");
  assert.equal(response.data.attachments[0].type, "hwp");
  assert.equal(response.data.contact_mentions[0].text, "02-961-0000");
  assert.equal(response.data.contact_mentions[0].channel?.action_url, "tel:02-961-0000");
  assert.equal(response.data.contact_mentions.length, 2);
  assert.equal(response.data.contact_mentions[1].channel?.action_url, "mailto:help@example.org");
});

test("검색은 제목과 그 공지에 붙은 조직 이름을 함께 본다(상위 경로는 빼고)", async () => {
  // 2026-09-14 사용자 실측: 안내 문구는 '기관명 검색'인데 실제로는 제목만 봤다.
  // '스페인어학과'로 그 학과 공지를 찾으면 운영 색인에서 7건(제목에 이름이 든 것)만
  // 나왔고, 실제로 그 학과에 붙은 공지는 198건이었다.
  //
  // 조직 이름은 색인에 없다. 이름 자리(o)는 24,324건 전부 비어 있고, 대상(a)에
  // "org:<id>"로 식별자만 들어 있다. 이름은 organizations.json 에 있으므로 화면에서
  // 이어 붙인다 - 색인을 다시 만들 일이 아니다.
  const entries = [
    { id: "dept-job", t: "2026 하반기 신입사원 채용", c: "career", o: null, s: "src-web", d: "2026-09-14", p: "2026-09-14T06:00:00Z", v: "2026-09-14T06:00:00Z", a: ["org:org-dept"] },
    { id: "college-note", t: "단과대 행사 안내", c: "event", o: null, s: "src-web", d: "2026-09-14", p: "2026-09-14T05:00:00Z", v: "2026-09-14T05:00:00Z", a: ["org:org-college"] },
    { id: "univ-change", t: "[학적] 2026-2 전과신청안내", c: "academic", o: null, s: "src-web", d: "2026-09-14", p: "2026-09-14T04:00:00Z", v: "2026-09-14T04:00:00Z", a: ["university"] },
    { id: "title-hit", t: "스페인어학과 학생회 안내", c: "event", o: null, s: "src-web", d: "2026-09-14", p: "2026-09-14T03:00:00Z", v: "2026-09-14T03:00:00Z", a: ["campus:campus-global"] },
  ];
  const campuses = [{ id: "campus-global", name: "국제캠퍼스" }];
  // path 를 실제 공개 파일처럼 넣어 둔다. 넣어 두어야 "상위 경로는 안 본다"를 확인할 수 있다.
  const organizations = page([
    { id: "org-college", name: "외국어대학", type: coded("college", "단과대"), parent_id: null, campuses, has_children: true, path: ["경희대학교", "외국어대학"] },
    { id: "org-dept", name: "스페인어학과", type: coded("department", "학과·전공"), parent_id: "org-college", campuses, has_children: false, path: ["경희대학교", "외국어대학", "스페인어학과"] },
  ]);
  const pages = noticePages(entries);
  const asked = [];
  const fetchImpl = async (url) => {
    const path = staticPath(url);
    asked.push(path);
    if (path === "/v1/latest.json") return { ok: true, json: async () => latest() };
    if (path.endsWith("/notices/index.json")) return { ok: true, json: async () => ({ ...latest(), count: entries.length, entries }) };
    if (path.endsWith("/organizations.json")) return { ok: true, json: async () => organizations };
    if (path.endsWith("/catalog.json")) return { ok: true, json: async () => catalogFile() };
    if (path.endsWith("/sources.json")) return { ok: true, json: async () => page(Object.values(sourceRefs).map(sourceFile)) };
    const pageFile = pages(path);
    if (pageFile) return { ok: true, json: async () => pageFile };
    throw new Error(`Unexpected path ${path}`);
  };
  const api = await loadApi(fetchImpl);
  const ids = (response) => Array.from(response.data, (item) => item.id);

  // (가) 제목에 학과 이름이 없어도 그 학과에 붙은 공지가 나온다. 제목으로 걸린 것과 합쳐진다.
  //      차례는 연관도순이다 - 제목에 든 것(title-hit)이 조직으로만 걸린 것(dept-job)보다 위다.
  assert.deepEqual(ids(await api.listNotices({ q: "스페인어학과", limit: 20 })), ["title-hit", "dept-job"]);
  // 최신순을 고르면 같은 결과가 날짜 차례로 선다.
  assert.deepEqual(ids(await api.listNotices({ q: "스페인어학과", sort: "recent", limit: 20 })), ["dept-job", "title-hit"]);
  // 캠퍼스·조직 필터가 없어도 조직 목록을 받아 와야 이름을 볼 수 있다. 예전에는
  // 캠퍼스를 고른 경우에만 받아 와서, 검색이 이름을 아예 못 봤다.
  assert.equal(asked.some((path) => path.endsWith("/organizations.json")), true);

  // (나) 조직 자기 이름만 본다. 상위 경로(경희대학교·외국어대학)까지 합치면 거의 모든
  // 공지가 '경희대학교' 하나로 걸려 검색이 사실상 꺼진다.
  assert.deepEqual(ids(await api.listNotices({ q: "외국어대학", limit: 20 })), ["college-note"]);
  assert.deepEqual(ids(await api.listNotices({ q: "경희대학교", limit: 20 })), []);

  // (다) "낱말이 전부 있어야 한다"는 이제 규칙이 아니라 보너스다(2026-09-14). 예전에는
  // '스페인어학과 전과'가 0건이었다 - 전과 공지는 전교 공지라 그 학과가 붙어 있지 않다.
  // 이제는 한 낱말만 맞은 것도 나오고, 전부 맞은 것이 없으면 제목으로 맞은 것이 위다.
  assert.deepEqual(ids(await api.listNotices({ q: "스페인어학과 전과", limit: 20 })), ["univ-change", "title-hit", "dept-job"]);
  // 전부 맞은 것이 있으면 그것이 맨 위다. dept-job 은 조직(스페인어학과)과 제목(채용)을
  // 둘 다 맞혔고, title-hit 은 제목으로 한 낱말만 맞혔다.
  assert.deepEqual(ids(await api.listNotices({ q: "스페인어학과 채용", limit: 20 })), ["dept-job", "title-hit"]);

  // (라) '내 공지'도 같은 규칙을 쓴다.
  const mine = await api.previewFeed({ campus_id: null, organization_ids: [], subscribed_source_ids: [], filters: { q: "스페인어학과" }, limit: 20 });
  assert.deepEqual(ids(mine), ["title-hit", "dept-job"]);

  // (마) 검색어가 없는 기본 목록은 예전처럼 목록 페이지를 훑는 경로로 돈다.
  assert.deepEqual(ids(await api.listNotices({ limit: 20 })), ["dept-job", "college-note", "univ-change", "title-hit"]);
});

test("조직 이름을 붙인 검색 글자는 개정에 묶인다", async () => {
  // 항목마다 검색 글자를 미리 만들어 두지 않으면 글자 하나 칠 때마다 24,000줄을 조인한다.
  // 그 캐시는 반드시 개정에 묶여야 한다 - 개정이 바뀌면 같은 id 라도 이름이 달라질 수 있다.
  const realNow = Date.now;
  let now = realNow();
  let revision = "r1";
  Date.now = () => now;
  try {
    const entry = { id: "only", t: "채용 안내", c: "career", o: null, s: "src-web", d: "2026-09-14", p: "2026-09-14T06:00:00Z", v: "2026-09-14T06:00:00Z", a: ["org:org-dept"] };
    const campuses = [{ id: "campus-global", name: "국제캠퍼스" }];
    const orgName = () => (revision === "r1" ? "스페인어학과" : "서반아어학과");
    const fetchImpl = async (url) => {
      const path = staticPath(url);
      if (path === "/v1/latest.json") return { ok: true, json: async () => latest(revision) };
      if (path.endsWith("/notices/index.json")) return { ok: true, json: async () => ({ ...latest(revision), count: 1, entries: [entry] }) };
      if (path.endsWith("/organizations.json")) {
        return { ok: true, json: async () => page([{ id: "org-dept", name: orgName(), type: coded("department", "학과·전공"), parent_id: null, campuses, has_children: false }], revision) };
      }
      if (/\/notices\/page\/1\.json$/.test(path)) return { ok: true, json: async () => page([noticeFromEntry(entry)], revision, false) };
      throw new Error(`Unexpected path ${path}`);
    };
    const api = await loadApi(fetchImpl);
    const ids = (response) => Array.from(response.data, (item) => item.id);

    assert.deepEqual(ids(await api.listNotices({ q: "스페인어학과", limit: 20 })), ["only"]);

    revision = "r2";
    now += 10_001;
    // 새 개정의 새 이름으로 찾힌다. 캐시가 개정에 묶여 있지 않으면 여기서 0건이 나온다.
    assert.deepEqual(ids(await api.listNotices({ q: "서반아어학과", limit: 20 })), ["only"]);
    // 그리고 옛 이름은 더 이상 찾히지 않는다.
    assert.deepEqual(ids(await api.listNotices({ q: "스페인어학과", limit: 20 })), []);
  } finally {
    Date.now = realNow;
  }
});

test("검색은 캠퍼스 설정을 넘어가고, 검색어가 없으면 넘지 않는다", async () => {
  // 2026-09-14 사용자 실측·결정: 설정이 서울캠퍼스인 채로 '스페인어학과'를 찾으면 1건만
  // 나왔다. 그 학과는 국제캠퍼스 소속이라 캠퍼스 설정이 198건을 막았다. 학과 이름을
  // 똑똑히 적어 찾는 사람에게 설정이 답을 숨기는 것은 이상하다 - 검색할 때는 넘어간다.
  // 다만 그냥 목록을 훑을 때는 설정이 곧 '내가 볼 범위'라, 그때는 그대로 좁힌다.
  const entries = [
    { id: "seoul-note", t: "행정학과 장학 안내", c: "scholarship", o: null, s: "src-web", d: "2026-09-14", p: "2026-09-14T06:00:00Z", v: "2026-09-14T06:00:00Z", a: ["org:org-seoul-dept"] },
    { id: "global-note", t: "가을 학술제 안내", c: "event", o: null, s: "src-web", d: "2026-09-14", p: "2026-09-14T05:00:00Z", v: "2026-09-14T05:00:00Z", a: ["org:org-global-dept"] },
    { id: "global-other", t: "가을 학술제 안내", c: "event", o: null, s: "src-web", d: "2026-09-14", p: "2026-09-14T04:00:00Z", v: "2026-09-14T04:00:00Z", a: ["org:org-global-other"] },
    { id: "seoul-campus", t: "서울캠퍼스 행사 안내", c: "event", o: null, s: "src-web", d: "2026-09-14", p: "2026-09-14T03:00:00Z", v: "2026-09-14T03:00:00Z", a: ["campus:campus-seoul"] },
  ];
  const seoul = [{ id: "campus-seoul", name: "서울캠퍼스" }];
  const global = [{ id: "campus-global", name: "국제캠퍼스" }];
  const organizations = page([
    { id: "org-seoul-dept", name: "행정학과", type: coded("department", "학과·전공"), parent_id: null, campuses: seoul, has_children: false },
    { id: "org-global-dept", name: "스페인어학과", type: coded("department", "학과·전공"), parent_id: null, campuses: global, has_children: false },
    { id: "org-global-other", name: "응용화학과", type: coded("department", "학과·전공"), parent_id: null, campuses: global, has_children: false },
  ]);
  const pages = noticePages(entries);
  const fetchImpl = async (url) => {
    const path = staticPath(url);
    if (path === "/v1/latest.json") return { ok: true, json: async () => latest() };
    if (path.endsWith("/notices/index.json")) return { ok: true, json: async () => ({ ...latest(), count: entries.length, entries }) };
    if (path.endsWith("/organizations.json")) return { ok: true, json: async () => organizations };
    const pageFile = pages(path);
    if (pageFile) return { ok: true, json: async () => pageFile };
    throw new Error(`Unexpected path ${path}`);
  };
  const api = await loadApi(fetchImpl);
  const ids = (response) => Array.from(response.data, (item) => item.id);
  const campus = ["campus-seoul"];

  // (가) 검색어가 없으면 예전 그대로다. 서울 설정이면 서울 것만 보인다.
  assert.deepEqual(ids(await api.listNotices({ campus_id: campus, limit: 20 })), ["seoul-note", "seoul-campus"]);

  // (나) 검색할 때는 넘어간다. 서울 설정인데 국제캠퍼스 학과 이름으로 찾아도 나온다.
  //      고친 자리 그대로 두면 여기가 0건이다.
  assert.deepEqual(ids(await api.listNotices({ campus_id: campus, q: "스페인어학과", limit: 20 })), ["global-note"]);
  assert.deepEqual(
    ids(await api.listNotices({ campus_id: campus, q: "안내", limit: 20 })),
    ["seoul-note", "global-note", "global-other", "seoul-campus"],
  );

  // (다) 조직은 설정이 아니라 학생이 지금 직접 고른 범위다. 검색 중에도 그대로 지킨다 -
  //      같은 캠퍼스의 다른 학과(global-other)까지 딸려 오면 안 된다.
  assert.deepEqual(
    ids(await api.listNotices({ campus_id: campus, organization_id: ["org-global-dept"], include_descendants: true, q: "안내", limit: 20 })),
    ["global-note"],
  );

  // (라) 검색어를 지우면 다시 서울 것만 보인다. 캠퍼스를 영영 푸는 것이 아니다.
  assert.deepEqual(ids(await api.listNotices({ campus_id: campus, limit: 20 })), ["seoul-note", "seoul-campus"]);
});

/**
 * 검색 채점 시험에서 함께 쓰는 조회 응답 한 벌.
 *
 * 검색어가 있으면 화면이 조직·분류·게시판 이름 사전을 함께 받는다. 셋 다 있어야 항목별
 * 검색 밭이 개정 캐시에 남으므로, 시험도 셋을 모두 내어 준다.
 */
function searchFetch({ entries, organizations = [], categories = CATEGORIES, sources = Object.values(sourceRefs), revision = "r1", asked = [] }) {
  const pages = noticePages(entries, revision);
  return async (url) => {
    const path = staticPath(url);
    asked.push(path);
    if (path === "/v1/latest.json") return { ok: true, json: async () => latest(revision) };
    if (path.endsWith("/notices/index.json")) return { ok: true, json: async () => ({ ...latest(revision), count: entries.length, entries }) };
    if (path.endsWith("/organizations.json")) return { ok: true, json: async () => page(organizations, revision) };
    if (path.endsWith("/catalog.json")) return { ok: true, json: async () => catalogFile(categories) };
    if (path.endsWith("/sources.json")) return { ok: true, json: async () => page(sources.map((ref) => sourceFile(ref)), revision) };
    const pageFile = pages(path);
    if (pageFile) return { ok: true, json: async () => pageFile };
    throw new Error(`Unexpected path ${path}`);
  };
}

const entry = (id, title, extra = {}) => ({
  id,
  t: title,
  c: "other",
  o: null,
  s: "src-web",
  d: "2026-09-14",
  p: "2026-09-14T06:00:00Z",
  v: "2026-09-14T06:00:00Z",
  a: ["university"],
  ...extra,
});

test("검색어를 고르는 글자만 남기고 맞춰 본다 - 가운뎃점·괄호를 넘어간다", async () => {
  // 원문에 가운뎃점이 네 가지나 섞여 있다: `･`(U+FF65) `·`(U+00B7) `∙`(U+2219) `・`(U+30FB).
  // 그래서 '학석사연계'로 찾으면 '학･석사연계'가 안 잡혔다(2026-09-14 사용자 실측 0건,
  // 실제로는 47건). 지울 구두점을 늘어놓는 대신 남길 글자(숫자·영문·한글)만 정해 두면
  // 이 문제가 통째로 사라진다.
  const entries = [
    entry("ff65", "[일반대학원] 2027학년도 전기 학･석사연계과정 모집", { p: "2026-09-14T09:00:00Z" }),
    entry("b7", "공과대학 (학·석사연계) 설명회 안내", { p: "2026-09-14T08:00:00Z" }),
    entry("plain", "대학원 신입생 모집 안내", { p: "2026-09-14T07:00:00Z" }),
  ];
  const api = await loadApi(searchFetch({ entries }));
  const ids = (response) => Array.from(response.data, (item) => item.id);

  // 붙여쓰기 판이 하는 일이다. 정규화만으로는 '학 석사연계과정'이라 낱말이 갈라진다.
  assert.equal(api.compactForSearch("학･석사연계"), "학석사연계");
  assert.equal(api.compactForSearch("[일반대학원] 2027학년도 전기 학･석사연계과정"), "일반대학원2027학년도전기학석사연계과정");
  assert.equal(api.normalizeForSearch("취업·인턴"), "취업 인턴");

  assert.deepEqual(ids(await api.listNotices({ q: "학석사연계", limit: 20 })), ["ff65", "b7"]);
  // 어느 가운뎃점으로 쳐도 앞의 둘이 맨 위다. 학생이 원문의 어느 글자를 복사해 왔는지
  // 알 수 없으므로 넷 다 같아야 한다. 가운뎃점으로 치면 낱말이 '학'과 '석사연계' 둘로
  // 갈리는데, 홑글자 '학'은 '대학원'에도 들어 있어 plain 도 딸려 온다 - 낱말 하나만
  // 맞혔으므로 맨 아래다. 예전 규칙(전부 맞아야 통과)이라면 아예 안 나왔을 자리다.
  assert.deepEqual(ids(await api.listNotices({ q: "학·석사연계", limit: 20 })), ["ff65", "b7", "plain"]);
  assert.deepEqual(ids(await api.listNotices({ q: "학･석사연계", limit: 20 })), ["ff65", "b7", "plain"]);
});

test("낱말이 일부만 맞아도 나오되, 전부 맞은 것이 위에 온다", async () => {
  // 예전에는 낱말이 전부 있어야 통과였다. '스페인어학과 전과'가 0건이던 이유다 -
  // 전과 공지는 전교 공지라 학과가 붙어 있지 않다. 이제 "전부 맞음"은 규칙이 아니라
  // 보너스여서, 하나만 맞은 것도 나오되 반드시 아래에 선다.
  const campuses = [{ id: "campus-global", name: "국제캠퍼스" }];
  const organizations = [
    { id: "org-dept", name: "스페인어학과", type: coded("department", "학과·전공"), parent_id: null, campuses, has_children: false },
  ];
  const entries = [
    entry("dept-only", "스페인어학과 종강 총회 안내", { a: ["org:org-dept"], c: "event", p: "2026-09-14T09:00:00Z" }),
    entry("change-only", "2026-2학기 전과 신청 안내", { c: "academic", p: "2026-09-14T08:00:00Z" }),
    // 일부러 가장 오래된 것으로 두었다. 점수가 날짜를 이겨야 한다.
    entry("both", "[외국어대학] 스페인어학과 전과 신청 안내", { a: ["org:org-dept"], c: "academic", p: "2026-09-13T07:00:00Z" }),
    entry("none", "도서관 휴관 안내", { p: "2026-09-14T10:00:00Z" }),
  ];
  const api = await loadApi(searchFetch({ entries, organizations }));
  const ids = (response) => Array.from(response.data, (item) => item.id);

  // 맨 위는 두 낱말을 다 맞힌 공지다. 가장 오래된 글인데도 그렇다.
  // 그다음은 제목+조직으로 한 낱말을 맞힌 것, 그다음이 제목으로만 맞힌 것이다.
  assert.deepEqual(ids(await api.listNotices({ q: "스페인어학과 전과", limit: 20 })), ["both", "dept-only", "change-only"]);
  // 한 낱말도 못 맞힌 공지는 나오지 않는다. '안내'는 넷 다 들어 있지만 검색어에 없다.
  assert.equal(ids(await api.listNotices({ q: "스페인어학과 전과", limit: 20 })).includes("none"), false);

  // 최신순으로 바꾸면 같은 결과가 날짜 차례로 선다. 결과의 '무엇'은 그대로고 '차례'만 바뀐다.
  assert.deepEqual(ids(await api.listNotices({ q: "스페인어학과 전과", sort: "recent", limit: 20 })), ["dept-only", "change-only", "both"]);
});

test("분류(주제) 이름으로도 찾힌다", async () => {
  // 화면에는 '취업·인턴'이라고 적혀 있는데 그대로 치면 0건이었다(2026-09-14 사용자 실측).
  // 색인에는 분류 코드(c)만 있고 이름은 catalog.json 에 있다 - 조직 이름을 이어 붙인 것과
  // 같은 방법으로 화면에서 잇는다. 색인 파일은 한 글자도 커지지 않는다.
  const entries = [
    entry("job-1", "2026 하반기 신입사원 모집", { c: "career", p: "2026-09-14T09:00:00Z" }),
    entry("job-2", "동계 현장실습 신청 안내", { c: "career", p: "2026-09-14T08:00:00Z" }),
    entry("fest", "가을 축제 안내", { c: "event", p: "2026-09-14T07:00:00Z" }),
  ];
  const api = await loadApi(searchFetch({ entries }));
  const ids = (response) => Array.from(response.data, (item) => item.id);

  assert.deepEqual(ids(await api.listNotices({ q: "취업·인턴", limit: 20 })), ["job-1", "job-2"]);
  // 가운뎃점을 빼고 쳐도 같다(붙여쓰기 판).
  assert.deepEqual(ids(await api.listNotices({ q: "취업인턴", limit: 20 })), ["job-1", "job-2"]);
  // 분류 이름의 한 낱말만 쳐도 잡힌다.
  assert.deepEqual(ids(await api.listNotices({ q: "인턴", limit: 20 })), ["job-1", "job-2"]);
  assert.deepEqual(ids(await api.listNotices({ q: "행사", limit: 20 })), ["fest"]);
  // 분류 이름은 제목보다 무게가 가볍다. 제목에 '행사'가 든 공지가 분류가 '행사'인 것보다 위다.
  const titleHit = await loadApi(searchFetch({
    entries: [
      entry("cat", "가을 축제 안내", { c: "event", p: "2026-09-14T09:00:00Z" }),
      entry("title", "행사 도우미 모집", { c: "other", p: "2026-09-14T08:00:00Z" }),
    ],
  }));
  assert.deepEqual(ids(await titleHit.listNotices({ q: "행사", limit: 20 })), ["title", "cat"]);
});

test("검색어가 없으면 연관도는 뜻이 없다 - 언제나 최신순이다", async () => {
  // 색인은 서버가 이미 최신순으로 내보낸다. 시험 자료도 그 순서로 둔다.
  const entries = [
    entry("new", "가을 축제 안내", { c: "event", d: "2026-09-14", p: "2026-09-14T09:00:00Z" }),
    entry("mid", "등록금 납부 안내", { c: "scholarship", d: "2026-09-13", p: "2026-09-13T09:00:00Z" }),
    entry("old", "교내 장학금 신청 안내", { c: "scholarship", d: "2026-09-12", p: "2026-09-12T09:00:00Z" }),
  ];
  const asked = [];
  const api = await loadApi(searchFetch({ entries, asked }));
  const ids = (response) => Array.from(response.data, (item) => item.id);

  // 화면은 검색어가 없으면 정렬 단추를 감춘다. 주소에 옛 sort 값이 남아 있어도
  // 여기서 최신순으로 되돌아가야 목록과 머리글이 어긋나지 않는다.
  assert.deepEqual(ids(await api.listNotices({ sort: "relevance", limit: 20 })), ["new", "mid", "old"]);
  assert.deepEqual(ids(await api.listNotices({ limit: 20 })), ["new", "mid", "old"]);
  // 이름 사전은 검색어가 있을 때만 받는다. 그냥 목록을 볼 때 29KB 를 더 받을 이유가 없다.
  assert.equal(asked.some((path) => path.endsWith("/sources.json")), false);
  assert.equal(asked.some((path) => path.endsWith("/catalog.json")), false);

  // 검색어가 있으면 연관도가 날짜를 이긴다. old 는 제목과 분류가 둘 다 '장학'이고
  // mid 는 분류만 그렇다. 최신순으로 바꾸면 다시 날짜 차례다.
  assert.deepEqual(ids(await api.listNotices({ q: "장학", limit: 20 })), ["old", "mid"]);
  assert.deepEqual(ids(await api.listNotices({ q: "장학", sort: "recent", limit: 20 })), ["mid", "old"]);
  await api.listNotices({ q: "공지", limit: 20 });
  assert.equal(asked.some((path) => path.endsWith("/sources.json")), true);
  assert.equal(asked.some((path) => path.endsWith("/catalog.json")), true);
});

test("한 낱말이 여러 밭에서 맞아도 가장 높은 밭 하나만 센다", async () => {
  // 2026-09-14 사용자 결정. 게시판 이름은 거의 언제나 조직 이름을 되풀이하고
  // ('스페인어학과 커뮤니티'), 학과 이름은 그 학과 공지 제목에도 흔히 들어간다. 밭별
  // 점수를 더하면 '스페인어학과' 한 낱말이 제목·조직·출처에서 세 번 세어져(100+55+25=180)
  // 제목 한 밭에서 맞은 '전과'(100)를 이겼다. 같은 사실을 세 번 세는 것이라 뒤집었다.
  const campuses = [{ id: "campus-global", name: "국제캠퍼스" }];
  const organizations = [
    { id: "org-dept", name: "스페인어학과", type: coded("department", "학과·전공"), parent_id: null, campuses, has_children: false },
  ];
  const board = { id: "src-dept", name: "스페인어학과 커뮤니티", medium: coded("web", "웹") };
  const sources = [...Object.values(sourceRefs), board];
  const entries = [
    // 제목에만 맞는데 가장 새것도 아니다. 그런데도 세 밭에서 겹쳐 맞은 것을 앞선다.
    entry("board-only", "가을 축제 안내", { s: "src-dept", c: "event", p: "2026-09-14T11:00:00Z" }),
    entry("change", "2026-2학기 전과 신청 안내", { c: "academic", p: "2026-09-14T10:00:00Z" }),
    entry("title-only", "스페인어학과 소식", { c: "other", p: "2026-09-14T09:00:00Z" }),
    entry("overlap", "스페인어학과 학생회 안내", { a: ["org:org-dept"], s: "src-dept", c: "event", p: "2026-09-14T08:00:00Z" }),
  ];
  const api = await loadApi(searchFetch({ entries, organizations, sources }));
  const ids = (response) => Array.from(response.data, (item) => item.id);

  // (가) 낱말 하나짜리 검색. overlap 은 제목·조직·출처 셋에서 맞고 title-only 는 제목
  //      하나에서만 맞는다. 둘 다 제목에서 맞았으니 점수가 같고, 같으면 새것이 위다.
  //      밭별로 더하던 옛 규칙이라면 overlap(180)이 title-only(100)를 눌렀을 자리다.
  assert.deepEqual(ids(await api.listNotices({ q: "스페인어학과", limit: 20 })), ["title-only", "overlap", "board-only"]);
  // 그래도 밭의 차례는 그대로다. board-only 는 게시판 이름에서만 맞아(가장 낮은 밭)
  //      가장 새 공지인데도 맨 아래다 - 최댓값으로 바꿔도 밭 무게가 날짜를 이긴다.

  // (나) 낱말 둘짜리 검색. 학생이 찾는 것은 전과 쪽이다. 옛 규칙이라면 overlap(180)이
  //      change(100)를 눌러 전과 공지가 아래로 밀렸다.
  assert.deepEqual(ids(await api.listNotices({ q: "스페인어학과 전과", limit: 20 })), ["change", "title-only", "overlap", "board-only"]);

  // (다) 낱말을 전부 맞히면 여전히 맨 위다. 보너스는 그대로 둔다.
  const both = await loadApi(searchFetch({
    entries: [
      entry("one-word", "2026-2학기 전과 신청 안내", { c: "academic", p: "2026-09-14T11:00:00Z" }),
      entry("two-words", "스페인어학과 전과 신청 안내", { c: "academic", p: "2026-09-13T08:00:00Z" }),
    ],
    organizations,
    sources,
  }));
  assert.deepEqual(ids(await both.listNotices({ q: "스페인어학과 전과", limit: 20 })), ["two-words", "one-word"]);
});
