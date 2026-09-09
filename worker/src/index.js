/**
 * 경희 공지 조회 제공(15.1절).
 *
 * 하는 일은 하나다. R2 조회 버킷의 정적 JSON 을 캐시 규칙과 CORS 를 붙여 내보낸다.
 * 계산도 데이터베이스 접근도 없다. 그래서 사용자가 늘어도 조회 비용이 오르지 않는다.
 *
 * 캐시 규칙(12절):
 *   /v1/latest.json  60초    - 프론트가 먼저 읽는 개정 포인터
 *   /v1/status.json  60초    - 공개 상태
 *   /v1/r/<개정>/... 1년 불변 - 개정 경로의 파일은 절대 바뀌지 않는다
 *
 * 공개 자료이므로 모든 출처에서 읽을 수 있게 한다. 쓰기 요청은 받지 않는다.
 */

const SHORT_CACHE = "public, max-age=60, s-maxage=60";
const IMAGE_CACHE = "public, max-age=604800, s-maxage=604800";
// 중계는 학교 주소로만 한다. 아무 주소나 받으면 남의 서버를 대신 때리는 통로가 된다.
const IMAGE_HOST = "khu.ac.kr";
const IMAGE_HOST_SUFFIX = ".khu.ac.kr";
const IMAGE_MAX_BYTES = 20 * 1024 * 1024;
const IMMUTABLE_CACHE = "public, max-age=31536000, immutable";
const ALLOWED_METHODS = "GET, HEAD, OPTIONS";

/** 경로 검사. 상위 경로 탈출과 예상 밖 접두를 막는다. */
function resolveKey(pathname) {
  const path = decodeURIComponent(pathname).replace(/^\/+/, "");
  if (!path.startsWith("v1/")) return null;
  if (path.includes("..") || path.includes("//")) return null;
  if (path.length > 512) return null;

  // 확장자를 생략해도 되게 한다. /v1/notices/page/1 -> /v1/notices/page/1.json
  if (!path.endsWith(".json")) return `${path}.json`;
  return path;
}

/** 중계해도 되는 그림 주소인지 본다. 내보내는 쪽(app/domain/images.py)과 같은 규칙이다. */
function allowedImageUrl(raw) {
  let url;
  try {
    url = new URL(raw);
  } catch {
    return null;
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") return null;
  if (url.hostname !== IMAGE_HOST && !url.hostname.endsWith(IMAGE_HOST_SUFFIX)) return null;
  return url;
}

/**
 * 본문에 박힌 그림을 중계한다.
 *
 * 원문 그림 주소는 대부분 http 다. https 인 화면에서 그대로 쓰면 브라우저가 막으므로
 * 여기서 받아 https 로 내보낸다. 그림 파일은 저장하지 않는다. 원문이 살아 있는 동안만
 * 보이며, 그래서 저장 용량이 들지 않는다.
 *
 * 주소는 학교 것만 받는다. 돌림(redirect)도 따라가지 않는다. 따라가면 학교 주소로 시작해
 * 밖으로 나가는 요청을 만들 수 있다.
 */
async function proxyImage(token, request, origin, ctx) {
  let target;
  try {
    const padded = token + "=".repeat((4 - (token.length % 4)) % 4);
    target = allowedImageUrl(atob(padded.replace(/-/g, "+").replace(/_/g, "/")));
  } catch {
    target = null;
  }
  if (!target) return jsonError(404, "NOT_FOUND", "요청한 자료가 없습니다.", origin);

  const cache = caches.default;
  const cacheKey = new Request(new URL(`/v1/img/${token}`, request.url).toString(), { method: "GET" });
  const cached = await cache.match(cacheKey);
  if (cached && request.method === "GET") return cached;

  let upstream;
  try {
    upstream = await fetch(target.toString(), {
      method: "GET",
      redirect: "manual",
      headers: { accept: "image/*" },
      cf: { cacheTtl: 604800, cacheEverything: true },
    });
  } catch {
    return jsonError(503, "TEMPORARILY_UNAVAILABLE", "원문 그림을 읽지 못했습니다.", origin);
  }
  if (!upstream.ok) {
    return jsonError(404, "NOT_FOUND", "요청한 자료가 없습니다.", origin);
  }
  const type = upstream.headers.get("content-type") || "";
  if (!type.startsWith("image/")) {
    return jsonError(404, "NOT_FOUND", "요청한 자료가 없습니다.", origin);
  }
  const length = Number(upstream.headers.get("content-length") || 0);
  if (length > IMAGE_MAX_BYTES) {
    return jsonError(404, "NOT_FOUND", "요청한 자료가 없습니다.", origin);
  }

  const headers = new Headers(baseHeaders(origin));
  headers.set("content-type", type);
  headers.set("cache-control", IMAGE_CACHE);
  headers.set("content-security-policy", "default-src 'none'; sandbox");
  const response = new Response(request.method === "HEAD" ? null : upstream.body, { headers });
  if (request.method === "GET") ctx.waitUntil(cache.put(cacheKey, response.clone()));
  return response;
}

function cacheControlFor(key) {
  return key.startsWith("v1/r/") ? IMMUTABLE_CACHE : SHORT_CACHE;
}

function baseHeaders(origin) {
  return {
    "access-control-allow-origin": origin || "*",
    "access-control-allow-methods": ALLOWED_METHODS,
    "access-control-max-age": "86400",
    "vary": "Origin, Accept-Encoding",
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
  };
}

function jsonError(status, code, message, origin) {
  const body = JSON.stringify({
    error: { code, message, retryable: status >= 500, request_id: crypto.randomUUID() },
  });
  return new Response(body, {
    status,
    headers: {
      ...baseHeaders(origin),
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
    },
  });
}

async function objectJson(object) {
  const body = object.httpMetadata?.contentEncoding === "gzip"
    ? object.body.pipeThrough(new DecompressionStream("gzip")) : object.body;
  return new Response(body).json();
}

async function revisionObject(key, bucket) {
  const match = /^v1\/r\/([^/]+)\/(.+)$/.exec(key);
  if (!match || match[2] === "manifest.json") return null;
  const manifestObject = await bucket.get(`v1/r/${match[1]}/manifest.json`);
  if (!manifestObject) return null;
  const manifest = await objectJson(manifestObject);
  if (manifest.version !== 1 || manifest.revision !== match[1]) throw new Error("Invalid manifest");
  const target = manifest.entries[match[2]];
  if (!target) return null;
  if (!/^v1\/objects\/[a-f0-9]{64}\.json$/.test(target)) throw new Error("Invalid object key");
  const object = await bucket.get(target);
  if (!object) throw new Error("Missing revision object");
  const payload = await objectJson(object);
  if (Object.hasOwn(payload, "meta")) payload.meta = {
    request_id: `static-${manifest.revision}`, generated_at: manifest.generated_at,
  };
  if (payload.page) {
    payload.page.snapshot_at = manifest.generated_at;
    payload.page.dataset_revision = manifest.revision;
  }
  if (Object.hasOwn(payload, "revision")) payload.revision = manifest.revision;
  if (Object.hasOwn(payload, "generated_at")) payload.generated_at = manifest.generated_at;
  function restore(value) {
    if (!value || typeof value !== "object") return;
    if (Object.hasOwn(value, "freshness") && value.primary_source) {
      const freshness = manifest.freshness[value.primary_source.id];
      if (!freshness) throw new Error("Missing freshness");
      value.freshness = freshness;
    }
    for (const child of Object.values(value)) restore(child);
  }
  restore(payload);
  return {
    body: JSON.stringify(payload),
    httpEtag: `"${manifest.revision}-${target.split("/").pop()}"`,
    httpMetadata: {},
  };
}

/**
 * 수집 깨우기(13절).
 *
 * GitHub 예약 실행은 2026-09-07 밤부터 여러 시간씩 빠졌다. 그래서 매시 17분에 여기서
 * 수집 워크플로에 workflow_dispatch 를 보낸다. 조회 경로와는 아무 관계가 없다.
 * R2 도 쓰지 않고, fetch 요청으로 부를 수 있는 길도 만들지 않는다.
 *
 * 이미 도는 회차가 있으면 보내지 않는다. 수집은 회차가 겹치면 안 된다(7.4절).
 * 토큰(KHU_GITHUB_TOKEN)이 없으면 아무 요청도 하지 않는다. 실패는 기록만 하고 삼킨다.
 * 여기서 예외를 던져도 수집이 살아나지는 않고, 조회만 시끄러워진다.
 */
const GITHUB_API = "https://api.github.com";
const DISPATCH_REF = "main";

async function wakeCollector(env) {
  const token = env?.KHU_GITHUB_TOKEN;
  if (!token) {
    console.log("수집 깨우기: KHU_GITHUB_TOKEN 비밀값이 없어 아무것도 하지 않습니다.");
    return;
  }
  const repo = env.KHU_GITHUB_REPO || "sihort183-creator/khu";
  const workflow = env.KHU_COLLECT_WORKFLOW || "collect.yml";
  const base = `${GITHUB_API}/repos/${repo}/actions/workflows/${encodeURIComponent(workflow)}`;
  const headers = {
    "authorization": `Bearer ${token}`,
    "accept": "application/vnd.github+json",
    "x-github-api-version": "2022-11-28",
    "user-agent": "khu-notice-scheduler",
  };

  try {
    // 도는 회차가 하나라도 있으면 건너뛴다. queued·pending 은 앞 회차가 끝나기를
    // 기다리는 것이다(concurrency 로 줄 선 회차를 GitHub 이 pending 으로 보고한 적이 있다).
    for (const status of ["in_progress", "queued", "pending"]) {
      const runs = await fetch(`${base}/runs?status=${status}&per_page=1`, { headers });
      if (!runs.ok) {
        console.error(`수집 깨우기: 회차 확인 실패 (${status}, HTTP ${runs.status}). 이번 시각은 건너뜁니다.`);
        return;
      }
      const body = await runs.json();
      const count = body?.total_count ?? body?.workflow_runs?.length ?? 0;
      if (count > 0) {
        console.log(`수집 깨우기: 이미 ${status} 회차가 있어 시작하지 않습니다.`);
        return;
      }
    }

    const dispatch = await fetch(`${base}/dispatches`, {
      method: "POST",
      headers: { ...headers, "content-type": "application/json" },
      body: JSON.stringify({ ref: DISPATCH_REF }),
    });
    if (!dispatch.ok) {
      console.error(`수집 깨우기: 시작 요청 실패 (HTTP ${dispatch.status}). 이번 시각은 건너뜁니다.`);
      return;
    }
    console.log(`수집 깨우기: 회차 시작 (${repo} / ${workflow} / ${DISPATCH_REF}).`);
  } catch (err) {
    console.error(`수집 깨우기: 요청 중 오류 — ${err}`);
  }
}

export default {
  /** 매시 17분(wrangler.toml [triggers]). 조회 경로와 분리된 유일한 진입점이다. */
  async scheduled(event, env, ctx) {
    await wakeCollector(env);
  },

  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const origin = request.headers.get("origin");

    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: baseHeaders(origin) });
    }
    if (request.method !== "GET" && request.method !== "HEAD") {
      return jsonError(405, "METHOD_NOT_ALLOWED", "읽기 요청만 받습니다.", origin);
    }

    // 편의 경로: / 와 /v1 은 개정 포인터로 보낸다.
    if (url.pathname === "/" || url.pathname === "/v1" || url.pathname === "/v1/") {
      return Response.redirect(new URL("/v1/latest.json", url).toString(), 302);
    }

    // 본문 그림 중계. JSON 경로 검사보다 먼저 본다. 여기만 R2 를 쓰지 않는다.
    const image = /^\/v1\/img\/([A-Za-z0-9_-]{1,700})$/.exec(url.pathname);
    if (image) return proxyImage(image[1], request, origin, ctx);

    const key = resolveKey(url.pathname);
    // v1/state/ 는 다음 회차가 "무엇이 바뀌었나"를 알기 위해 두는 내부 기록이다.
    // 조회 계약에 없는 경로이므로 밖으로 내보내지 않는다.
    if (!key || key.startsWith("v1/objects/") || key.startsWith("v1/state/")
        || key.endsWith("/manifest.json")) {
      return jsonError(404, "NOT_FOUND", "요청한 자료가 없습니다.", origin);
    }

    // 같은 개정 파일은 가장자리 캐시에서 바로 돌려준다.
    const cache = caches.default;
    const cacheKey = new Request(new URL(`/${key}?encoding=v2`, url).toString(), { method: "GET" });
    const cached = await cache.match(cacheKey);
    if (cached && request.method === "GET") {
      const hit = new Response(cached.body, cached);
      hit.headers.set("x-cache", "HIT");
      for (const [name, value] of Object.entries(baseHeaders(origin))) {
        hit.headers.set(name, value);
      }
      return hit;
    }

    let object;
    try {
      object = await env.PUBLIC.get(key);
      if (object === null) object = await revisionObject(key, env.PUBLIC);
    } catch (err) {
      return jsonError(503, "TEMPORARILY_UNAVAILABLE", "조회 저장소를 읽지 못했습니다.", origin);
    }
    if (object === null) {
      return jsonError(404, "NOT_FOUND", "요청한 자료가 없습니다.", origin);
    }

    const headers = new Headers(baseHeaders(origin));
    headers.set("content-type", "application/json; charset=utf-8");
    headers.set("cache-control", cacheControlFor(key));
    headers.set("etag", object.httpEtag);
    headers.set("x-cache", "MISS");
    // 캐시에는 해제한 JSON을 넘긴다. 전송 압축은 런타임이 한 번만 수행한다.
    // 사전 압축 본문을 그대로 cache.put() 하면 캐시 경로에서 중복 압축될 수 있다.
    const body = object.httpMetadata?.contentEncoding === "gzip"
      ? object.body.pipeThrough(new DecompressionStream("gzip"))
      : object.body;
    const response = new Response(request.method === "HEAD" ? null : body, { headers });
    if (request.method === "GET") {
      ctx.waitUntil(cache.put(cacheKey, response.clone()));
    }
    return response;
  },
};
