import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { gzipSync } from 'node:zlib';
import { test } from 'node:test';

test('실제 조회 코드가 개정별 공통 객체와 메타를 합성하고 없는 참조를 차단한다', async () => {
  const source = await readFile(new URL('../src/index.js', import.meta.url), 'utf8');
  const { default: worker } = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
  const objects = new Map();
  const cached = new Map();
  const pending = [];
  globalThis.caches = { default: {
    match: async (key) => cached.get(key.url)?.clone(),
    put: async (key, response) => { cached.set(key.url, response); },
  } };
  const bucket = { get: async (key) => objects.has(key) ? {
    body: new Response(gzipSync(JSON.stringify(objects.get(key)))).body,
    httpMetadata: { contentEncoding: 'gzip' }, httpEtag: '"object"',
  } : null };
  const context = { waitUntil: (promise) => pending.push(promise) };
  const key = `v1/objects/${'1'.repeat(64)}.json`;
  objects.set(key, { data: [{ id: 'n1', primary_source: { id: 's1' }, freshness: null }],
    meta: null, page: { dataset_revision: null, snapshot_at: null, has_next: false } });
  for (const rev of ['r1', 'r2', 'hidden']) {
    objects.set(`v1/r/${rev}/manifest.json`, { version: 1, revision: rev,
      generated_at: `${rev}-time`, entries: rev === 'hidden' ? {} : { 'notices/page/1.json': key },
      freshness: { s1: { last_checked_at: `${rev}-check` } } });
  }
  const request = (path, method = 'GET') => worker.fetch(new Request(`https://test/${path}`, { method }), { PUBLIC: bucket }, context);
  for (const rev of ['r1', 'r2', 'r1']) {
    const response = await request(`v1/r/${rev}/notices/page/1.json`);
    const body = await response.json();
    assert.equal(body.meta.request_id, `static-${rev}`);
    assert.equal(body.page.dataset_revision, rev);
    assert.equal(body.page.snapshot_at, `${rev}-time`);
    assert.equal(body.data[0].freshness.last_checked_at, `${rev}-check`);
    await Promise.all(pending);
  }
  assert.equal((await request('v1/r/hidden/notices/page/1.json')).status, 404);
  assert.equal((await request(key)).status, 404);
  assert.equal(await (await request('v1/r/r2/notices/page/1.json', 'HEAD')).text(), '');
  objects.set('v1/r/old/catalog.json', { data: 'legacy' });
  assert.equal((await (await request('v1/r/old/catalog.json')).json()).data, 'legacy');
  objects.set('v1/r/broken/manifest.json', { version: 1, revision: 'broken',
    entries: { 'notices/page/1.json': `v1/objects/${'0'.repeat(64)}.json` } });
  assert.equal((await request('v1/r/broken/notices/page/1.json')).status, 503);
});
