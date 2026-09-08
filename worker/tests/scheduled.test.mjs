import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { test } from 'node:test';

const source = await readFile(new URL('../src/index.js', import.meta.url), 'utf8');
const { default: worker } = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);

/**
 * 예약 한 번을 돌리고 실제로 나간 바깥 요청 목록을 준다.
 * `answer` 는 요청 주소를 받아 응답을 돌려준다(또는 Error 를 던진다).
 */
async function run(env, answer) {
  const asked = [];
  const pending = [];
  globalThis.fetch = async (url, options = {}) => {
    asked.push({ url: String(url), method: options.method || 'GET', headers: options.headers || {}, body: options.body });
    const reply = answer(String(url), options);
    if (reply instanceof Error) throw reply;
    return reply;
  };
  await worker.scheduled({ cron: '17 * * * *' }, env, { waitUntil: (promise) => pending.push(promise) });
  await Promise.all(pending);
  return asked;
}

const json = (data, status = 200) => new Response(JSON.stringify(data), { status });
const empty = () => json({ total_count: 0, workflow_runs: [] });
const ENV = { KHU_GITHUB_TOKEN: 'test-token', KHU_GITHUB_REPO: 'sihort183-creator/khu', KHU_COLLECT_WORKFLOW: 'collect.yml' };

test('도는 회차가 없으면 workflow_dispatch 를 보낸다', async () => {
  const asked = await run(ENV, (url) => (url.includes('/dispatches') ? new Response(null, { status: 204 }) : empty()));

  assert.equal(asked.length, 4);
  assert.match(asked[0].url, /\/repos\/sihort183-creator\/khu\/actions\/workflows\/collect\.yml\/runs\?status=in_progress&per_page=1$/);
  assert.match(asked[1].url, /status=queued&per_page=1$/);
  assert.match(asked[2].url, /status=pending&per_page=1$/);

  const dispatch = asked[3];
  assert.match(dispatch.url, /\/repos\/sihort183-creator\/khu\/actions\/workflows\/collect\.yml\/dispatches$/);
  assert.equal(dispatch.method, 'POST');
  assert.deepEqual(JSON.parse(dispatch.body), { ref: 'main' });
  assert.equal(dispatch.headers.authorization, 'Bearer test-token');
  assert.equal(dispatch.headers.accept, 'application/vnd.github+json');
  assert.equal(dispatch.headers['x-github-api-version'], '2022-11-28');
  assert.equal(dispatch.headers['user-agent'], 'khu-notice-scheduler');
});

test('이미 도는 회차가 있으면 시작하지 않는다', async () => {
  // 실행 중인 회차가 있는 경우: queued 는 볼 필요도 없다.
  const running = await run(ENV, () => json({ total_count: 1, workflow_runs: [{ id: 1 }] }));
  assert.equal(running.length, 1);
  assert.ok(!running.some((call) => call.url.includes('/dispatches')));

  // 기다리는 회차가 있는 경우: in_progress 는 비어 있어도 보내지 않는다.
  const queued = await run(ENV, (url) => (url.includes('status=queued') ? json({ total_count: 1, workflow_runs: [{ id: 2 }] }) : empty()));
  assert.equal(queued.length, 2);
  assert.ok(!queued.some((call) => call.url.includes('/dispatches')));

  // concurrency 로 줄 선 회차를 GitHub 이 pending 으로 보고하는 경우도 보내지 않는다.
  const pending = await run(ENV, (url) => (url.includes('status=pending') ? json({ total_count: 1, workflow_runs: [{ id: 3 }] }) : empty()));
  assert.equal(pending.length, 3);
  assert.ok(!pending.some((call) => call.url.includes('/dispatches')));
});

test('토큰이 없으면 아무 요청도 보내지 않는다', async () => {
  for (const env of [{}, { KHU_GITHUB_TOKEN: '' }, { KHU_GITHUB_REPO: 'sihort183-creator/khu' }]) {
    const asked = await run(env, () => empty());
    assert.deepEqual(asked, []);
  }
});

test('GitHub 가 실패해도 예외를 던지지 않는다', async () => {
  // 회차 확인 실패
  const denied = await run(ENV, () => json({ message: 'Bad credentials' }, 401));
  assert.equal(denied.length, 1);

  // 시작 요청 실패
  const rejected = await run(ENV, (url) => (url.includes('/dispatches') ? json({ message: 'Not Found' }, 404) : empty()));
  assert.equal(rejected.length, 4);

  // 연결 자체가 안 되는 경우
  const broken = await run(ENV, () => new Error('연결 실패'));
  assert.equal(broken.length, 1);
});

test('설정이 없으면 기본 저장소·워크플로를 쓴다', async () => {
  const asked = await run({ KHU_GITHUB_TOKEN: 'test-token' }, (url) => (url.includes('/dispatches') ? new Response(null, { status: 204 }) : empty()));
  assert.equal(asked.length, 4);
  for (const call of asked) {
    assert.ok(call.url.startsWith('https://api.github.com/repos/sihort183-creator/khu/actions/workflows/collect.yml'), call.url);
  }
});

test('예약 처리기는 조회 경로를 건드리지 않는다', async () => {
  // R2 바인딩 없이도 돌아야 한다. 조회로 부를 수 있는 길도 없어야 한다.
  const asked = await run({ KHU_GITHUB_TOKEN: 'test-token' }, (url) => (url.includes('/dispatches') ? new Response(null, { status: 204 }) : empty()));
  assert.ok(asked.every((call) => call.url.startsWith('https://api.github.com/')));
  assert.ok(!/wakeCollector|dispatches/.test(source.slice(source.indexOf('async fetch(request'))));
});
