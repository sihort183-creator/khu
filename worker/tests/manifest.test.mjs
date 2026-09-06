import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { gzipSync } from 'node:zlib';
import { test } from 'node:test';
import { Miniflare } from 'miniflare';

test('개정 명세 합성은 재사용, 신선도, 숨김, 압축, 구형 개정을 보존한다', async () => {
  const mf = new Miniflare({ modules: true,
    script: await readFile(new URL('../src/index.js', import.meta.url), 'utf8'),
    compatibilityDate: '2026-06-01', r2Buckets: ['PUBLIC'], port: 0 });
  try {
    const bucket = await mf.getR2Bucket('PUBLIC');
    const base = await mf.ready;
    async function put(key, data) {
      await bucket.put(key, Uint8Array.from(gzipSync(JSON.stringify(data))).buffer,
        { httpMetadata: { contentType: 'application/json', contentEncoding: 'gzip' } });
    }
    const payload = { data: { id: 'n1', title: '공지', primary_source: { id: 's1' }, freshness: null }, meta: null };
    const key = `v1/objects/${createHash('sha256').update(JSON.stringify(payload)).digest('hex')}.json`;
    await put(key, payload);
    for (const rev of ['r1', 'r2', 'r3']) {
      await put(`v1/r/${rev}/manifest.json`, { version: 1, revision: rev,
        generated_at: `${rev}-time`, entries: rev === 'r3' ? {} : { 'notices/n1.json': key },
        freshness: { s1: { code: 'fresh', last_checked_at: `${rev}-check` } } });
    }
    for (const rev of ['r1', 'r2', 'r1', 'r2']) {
      const response = await fetch(new URL(`/v1/r/${rev}/notices/n1.json`, base));
      assert.equal(response.status, 200);
      assert.match(response.headers.get('cache-control'), /immutable/);
      const body = await response.json();
      assert.equal(body.meta.request_id, `static-${rev}`);
      assert.equal(body.meta.generated_at, `${rev}-time`);
      assert.equal(body.data.freshness.last_checked_at, `${rev}-check`);
      assert.equal(body.data.title, '공지');
    }
    assert.equal((await fetch(new URL('/v1/r/r3/notices/n1.json', base))).status, 404);
    assert.equal((await fetch(new URL(`/${key}`, base))).status, 404);
    const head = await fetch(new URL('/v1/r/r2/notices/n1.json', base), { method: 'HEAD' });
    assert.equal(head.status, 200);
    assert.equal(await head.text(), '');
    await put('v1/r/legacy/catalog.json', { data: { legacy: true }, meta: { request_id: 'old' } });
    assert.equal((await (await fetch(new URL('/v1/r/legacy/catalog.json', base))).json()).data.legacy, true);
    await put('v1/r/broken/manifest.json', { version: 1, revision: 'broken', generated_at: 'now',
      entries: { 'notices/n1.json': `v1/objects/${'0'.repeat(64)}.json` }, freshness: {} });
    const broken = await fetch(new URL('/v1/r/broken/notices/n1.json', base));
    assert.equal(broken.status, 503);
    assert.equal(broken.headers.get('cache-control'), 'no-store');
  } finally { await mf.dispose(); }
});
