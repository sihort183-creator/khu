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
  ];
  const organizations = page([
    { id: "org-college", name: "단과대", type: coded("college", "단과대"), parent_id: null, campuses: [{ id: "campus-seoul", name: "서울" }], has_children: true },
    { id: "org-dept", name: "학과", type: coded("department", "학과"), parent_id: "org-college", campuses: [{ id: "campus-seoul", name: "서울" }], has_children: true },
    { id: "org-child", name: "세부 조직", type: coded("office", "부서"), parent_id: "org-dept", campuses: [{ id: "campus-seoul", name: "서울" }], has_children: false },
  ]);
  const sources = page([
    { id: "src-web", name: "웹 출처", organization: { id: "org-college", name: "단과대" }, medium: coded("web", "웹"), content_kind: coded("notice", "공지"), url: "https://example.test/web", status: coded("active", "정상"), status_message: null, last_success_at: null, initial_window_days: null },
    { id: "src-ig", name: "인스타 출처", organization: { id: "org-college", name: "단과대" }, medium: coded("instagram", "인스타그램"), content_kind: coded("notice", "공지"), url: "https://example.test/ig", status: coded("active", "정상"), status_message: null, last_success_at: null, initial_window_days: null },
  ]);
  const details = Object.fromEntries(entries.map((entry) => [entry.id, backendDetail(notice(entry.id, entry.a.map((audience) => ({ type: audience.startsWith("org:") ? "organization" : audience.startsWith("campus:") ? "campus" : "university", id: audience.includes(":") ? audience.split(":")[1] : null, name: audience })) ))]));
  const fetchImpl = async (url) => {
    const path = staticPath(url);
    if (path === "/v1/latest.json") return { ok: true, json: async () => latest() };
    if (path.endsWith("/notices/index.json")) return { ok: true, json: async () => ({ ...latest(), count: entries.length, entries }) };
    if (path.endsWith("/organizations.json")) return { ok: true, json: async () => organizations };
    if (path.endsWith("/sources.json")) return { ok: true, json: async () => sources };
    const detail = Object.entries(details).find(([id]) => path.endsWith(`/notices/${id}.json`))?.[1];
    if (detail) return { ok: true, json: async () => ({ data: detail, meta: {} }) };
    throw new Error(`Unexpected path ${path}`);
  };
  const api = await loadApi(fetchImpl);
  const filtered = await api.listNotices({ campus_id: ["campus-seoul"], medium: ["instagram"], limit: 20 });
  assert.deepEqual(filtered.data.map((item) => item.id), ["campus-ig"]);
  const preview = await api.previewFeed({ campus_id: "campus-seoul", organization_ids: ["org-dept"], subscribed_source_ids: [], limit: 20 });
  assert.deepEqual(Array.from(preview.data, (item) => item.id).sort(), ["campus-ig", "campus-web", "college", "dept"]);
  assert.equal(preview.data.some((item) => item.id === "child"), false);
  const subscribed = await api.previewFeed({ campus_id: "campus-seoul", organization_ids: [], subscribed_source_ids: ["src-ig"], limit: 20 });
  assert.equal(subscribed.data.some((item) => item.id === "global-ig"), false);
  const mediumPreview = await api.previewFeed({ campus_id: "campus-seoul", organization_ids: [], subscribed_source_ids: [], filters: { medium: ["instagram"] }, limit: 20 });
  assert.deepEqual(Array.from(mediumPreview.data, (item) => item.id), ["campus-ig"]);
});

test("entries가 없는 색인은 모든 조각을 합치고 개정·건수 불일치를 거부한다", async () => {
  const baseOne = notice("shard-1");
  const baseTwo = notice("shard-2");
  let invalid = false;
  const fetchImpl = async (url) => {
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
    if (path.endsWith("/notices/index/1.json")) return { ok: true, json: async () => ({ revision: "r1", generated_at: "2026-09-06T00:00:00Z", count: 1, entries: [{ id: "shard-1", t: "첫 조각", c: "academic", o: null, s: "src", d: null, v: "2026-09-06T00:00:00Z", a: ["university"] }] }) };
    if (path.endsWith("/notices/index/2.json")) return { ok: true, json: async () => ({ revision: "r1", generated_at: "2026-09-06T00:00:00Z", count: 1, entries: [{ id: "shard-2", t: "둘째 조각", c: "academic", o: null, s: "src", d: null, v: "2026-09-06T00:00:00Z", a: ["university"] }] }) };
    if (path.endsWith("/notices/shard-1.json")) return { ok: true, json: async () => ({ data: backendDetail(baseOne), meta: {} }) };
    if (path.endsWith("/notices/shard-2.json")) return { ok: true, json: async () => ({ data: backendDetail(baseTwo), meta: {} }) };
    throw new Error(`Unexpected path ${path}`);
  };
  const api = await loadApi(fetchImpl);
  const response = await api.listNotices({ q: "조각", limit: 20 });
  assert.deepEqual(Array.from(response.data, (item) => item.id).sort(), ["shard-1", "shard-2"]);
  invalid = true;
  await assert.rejects(api.listNotices({ q: "조각", limit: 20 }), (error) => error.error.code === "STATIC_INDEX_INVALID");
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
