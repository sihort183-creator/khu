import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { test } from 'node:test';

const source = await readFile(new URL('../src/index.js', import.meta.url), 'utf8');
const { default: worker } = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);

const token = (url) => Buffer.from(url, 'utf8').toString('base64url');

/** 요청 한 번을 돌리고 응답과 실제로 나간 바깥 요청 목록을 준다. */
async function call(path, upstream) {
  const asked = [];
  const cached = new Map();
  const pending = [];
  globalThis.caches = { default: {
    match: async (key) => cached.get(key.url)?.clone(),
    put: async (key, response) => { cached.set(key.url, response); },
  } };
  globalThis.fetch = async (url, options) => {
    asked.push({ url: String(url), redirect: options?.redirect });
    if (upstream instanceof Error) throw upstream;
    return upstream;
  };
  const response = await worker.fetch(
    new Request(`https://test/${path}`), { PUBLIC: { get: async () => null } },
    { waitUntil: (promise) => pending.push(promise) },
  );
  await Promise.all(pending);
  return { response, asked };
}

const png = () => new Response('그림', { status: 200, headers: { 'content-type': 'image/png' } });

test('학교 주소의 http 그림은 중계되고 오래 캐시된다', async () => {
  const { response, asked } = await call(
    `v1/img/${token('http://com.khu.ac.kr/upload/poster.jpg')}`, png(),
  );
  assert.equal(response.status, 200);
  assert.equal(response.headers.get('content-type'), 'image/png');
  assert.match(response.headers.get('cache-control'), /max-age=604800/);
  assert.equal(response.headers.get('access-control-allow-origin'), '*');
  assert.equal(asked.length, 1);
  assert.equal(asked[0].url, 'http://com.khu.ac.kr/upload/poster.jpg');
  // 돌림을 따라가면 학교 주소로 시작해 밖으로 나가는 요청을 만들 수 있다.
  assert.equal(asked[0].redirect, 'manual');
});

test('학교 밖 주소는 바깥 요청조차 하지 않는다', async () => {
  for (const url of [
    'https://evil.example.com/a.png',
    'https://khu.ac.kr.evil.example.com/a.png',
    'file:///etc/passwd',
    'http://127.0.0.1/a.png',
  ]) {
    const { response, asked } = await call(`v1/img/${token(url)}`, png());
    assert.equal(response.status, 404, url);
    assert.equal(asked.length, 0, url);
  }
});

test('그림이 아니거나 너무 크거나 돌림이면 내보내지 않는다', async () => {
  const good = 'http://com.khu.ac.kr/a.png';
  const cases = [
    new Response('<html>', { status: 200, headers: { 'content-type': 'text/html' } }),
    new Response('그림', { status: 200, headers: { 'content-type': 'image/png', 'content-length': String(21 * 1024 * 1024) } }),
    new Response(null, { status: 302, headers: { location: 'https://evil.example.com/a.png' } }),
    new Response('없음', { status: 404 }),
  ];
  for (const [index, upstream] of cases.entries()) {
    const { response } = await call(`v1/img/${token(good)}`, upstream);
    assert.equal(response.status, 404, `case ${index}`);
  }
});

test('원문 서버가 죽어도 조회 서버는 살아 있다', async () => {
  const { response } = await call(`v1/img/${token('http://com.khu.ac.kr/a.png')}`, new Error('연결 실패'));
  assert.equal(response.status, 503);
});

test('망가진 토큰은 조용히 없는 자료로 처리한다', async () => {
  for (const bad of ['@@@', token('그냥 글자'), 'a'.repeat(800)]) {
    const { response } = await call(`v1/img/${bad}`, png());
    assert.equal(response.status, 404, bad.slice(0, 12));
  }
});
