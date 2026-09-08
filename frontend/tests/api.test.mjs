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
