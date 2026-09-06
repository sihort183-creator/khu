import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { gzipSync } from 'node:zlib';
import { test } from 'node:test';
import { Miniflare } from 'miniflare';

test('압축된 R2 JSON은 최초·캐시 조회 모두 한 번만 해제하면 읽힌다', async () => {
  const mf = new Miniflare({
    modules: true,
    script: await readFile(new URL('../src/index.js', import.meta.url), 'utf8'),
    compatibilityDate: '2026-06-01',
    r2Buckets: ['PUBLIC'],
    port: 0,
  });
  try {
    const bucket = await mf.getR2Bucket('PUBLIC');
    const payload = { revision: 'test-revision', notices_total: 51 };
    await bucket.put('v1/latest.json', Uint8Array.from(gzipSync(JSON.stringify(payload))).buffer, {
      httpMetadata: { contentType: 'application/json', contentEncoding: 'gzip' },
    });
    const base = await mf.ready;
    for (let i = 0; i < 3; i++) {
      const response = await fetch(new URL('/v1/latest.json', base));
      assert.equal(response.status, 200);
      console.log({ attempt: i, encoding: response.headers.get('content-encoding'), cache: response.headers.get('x-cache') });
      assert.deepEqual(await response.json(), payload);
    }
  } finally {
    await mf.dispose();
  }
});
