// 조직 트리의 정렬·묶음 규칙을 못 박는다. 화면에서만 확인하면 다음 손질에 조용히 무너진다.
//
// 규칙(2026-09-07 사용자 지시):
//   1. 최상위는 국제캠퍼스 · 서울캠퍼스 · 기타. 기타는 맨 아래.
//   2. 캠퍼스가 둘 다 붙은 조직은 두 캠퍼스에 모두. 하나도 없는 조직만 기타.
//   3. 캠퍼스 안은 단과대(ㄱㄴㄷ) 먼저, 그 밖의 조직(ㄱㄴㄷ) 나중.
//   4. 단과대 밑 학과도 ㄱㄴㄷ, 그 아래도 같은 규칙.
//   5. 정렬 키에서 `[확인 필요]` · `경희대학교 ` 접두어는 뺀다.
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { test } from "node:test";
import * as ts from "typescript";
import * as vm from "node:vm";
import { fileURLToPath } from "node:url";

const frontendRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const orgTreePath = resolve(frontendRoot, "src/lib/orgTree.ts");

// orgTree.ts 는 값 import 가 없다(타입만 쓴다). 그래서 그대로 옮겨 심어 돌린다.
const source = await readFile(orgTreePath, "utf8");
const output = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
  fileName: orgTreePath,
}).outputText;
const context = vm.createContext({ console });
const orgTreeModule = new vm.SourceTextModule(output, { context, identifier: orgTreePath });
await orgTreeModule.link(() => {
  throw new Error("orgTree.ts 는 값 import 를 갖지 않아야 한다");
});
await orgTreeModule.evaluate();
const { buildOrgTree, compareOrgNames, orgSortKey, countNoticesByNode, OTHER_NAME } = orgTreeModule.namespace;

const CAMPUSES = [
  { id: "campus-global", name: "국제캠퍼스" },
  { id: "campus-seoul", name: "서울캠퍼스" },
];

function org(id, name, type, { campuses = ["campus-global"], parent = null, boards = 1, alias = false } = {}) {
  return {
    id,
    name,
    type: { code: type, label: type },
    parent_id: parent,
    campuses: campuses.map((c) => ({ id: c, name: c })),
    has_children: false,
    source_count: boards,
    active_source_count: boards,
    is_alias: alias,
  };
}

// vm 밖으로 나온 배열은 realm 이 달라 deepEqual 이 못 맞춘다. 이쪽 realm 배열로 옮겨 담는다.
const names = (node) => Array.from(node.children, (child) => child.name);
const child = (node, name) => node.children.find((c) => c.name === name);

test("최상위는 국제캠퍼스 · 서울캠퍼스 · 기타 순이고 기타가 맨 아래다", () => {
  const tree = buildOrgTree([org("a", "가나다학과", "department", { campuses: [] })], CAMPUSES);
  assert.deepEqual(names(tree), ["국제캠퍼스", "서울캠퍼스", OTHER_NAME]);
  assert.equal(tree.children.at(-1).name, "기타");
  // 캠퍼스가 하나도 없는 조직만 기타로 간다.
  assert.deepEqual(names(child(tree, "기타")), ["가나다학과"]);
});

test("캠퍼스가 둘 다 붙은 조직은 두 캠퍼스에 모두 나온다", () => {
  const tree = buildOrgTree([org("a", "국제처", "office", { campuses: ["campus-global", "campus-seoul"] })], CAMPUSES);
  assert.deepEqual(names(child(tree, "국제캠퍼스")), ["국제처"]);
  assert.deepEqual(names(child(tree, "서울캠퍼스")), ["국제처"]);
  assert.deepEqual(names(child(tree, "기타")), []);
  // 두 줄이지만 같은 조직이므로 공지 건수를 찾는 키는 하나다. 화면 키는 서로 달라야 한다.
  const left = child(child(tree, "국제캠퍼스"), "국제처");
  const right = child(child(tree, "서울캠퍼스"), "국제처");
  assert.equal(left.countKey, right.countKey);
  assert.notEqual(left.key, right.key);
});

test("캠퍼스 안은 단과대가 먼저, 그 다음 나머지 — 각각 ㄱㄴㄷ순", () => {
  const orgs = [
    org("o1", "학생지원팀", "office"),
    org("c1", "호텔관광대학", "college"),
    org("i1", "가천연구소", "institute"),
    org("c2", "간호과학대학", "college"),
    org("c3", "문과대학", "college"),
    org("k1", "총학생회", "council"),
  ];
  const global = child(buildOrgTree(orgs, CAMPUSES), "국제캠퍼스");
  assert.deepEqual(names(global), ["간호과학대학", "문과대학", "호텔관광대학", "가천연구소", "총학생회", "학생지원팀"]);
  // 묶음 표시는 캠퍼스 바로 밑 줄에만 붙는다.
  assert.deepEqual(
    Array.from(global.children, (c) => c.group),
    ["college", "college", "college", "other", "other", "other"],
  );
});

test("단과대 밑 학과도 ㄱㄴㄷ순이고, 그 아래로 한 단계 더 들어간다", () => {
  const orgs = [
    org("c1", "경영대학", "college"),
    org("d2", "회계학과", "department", { parent: "c1" }),
    org("d1", "경영학과", "department", { parent: "c1" }),
    org("d3", "무역학과", "department", { parent: "c1" }),
    org("d4", "세부전공", "department", { parent: "d1" }),
  ];
  const college = child(child(buildOrgTree(orgs, CAMPUSES), "국제캠퍼스"), "경영대학");
  assert.deepEqual(names(college), ["경영학과", "무역학과", "회계학과"]);
  assert.equal(college.depth, 2);
  const dept = child(college, "경영학과");
  assert.deepEqual(names(dept), ["세부전공"]);
  assert.equal(dept.children[0].depth, 4);
});

test("정렬 키에서 [확인 필요]·경희대학교 접두어를 뺀다", () => {
  assert.equal(orgSortKey("[확인 필요] 나노학과"), "나노학과");
  assert.equal(orgSortKey("경희대학교 생활과학대학"), "생활과학대학");
  assert.equal(orgSortKey("경영대학"), "경영대학");
  assert.ok(compareOrgNames("[확인 필요] 가나", "나다") < 0);

  const orgs = [
    org("c1", "경희대학교 생활과학대학", "college"),
    org("c2", "간호과학대학", "college"),
    org("c3", "약학대학", "college"),
  ];
  // 접두어를 그대로 두면 '경희대학교 …'가 ㄱ 자리에 서서 생활과학대학을 못 찾는다. 뗀 뒤에는 ㅅ 자리다.
  assert.deepEqual(names(child(buildOrgTree(orgs, CAMPUSES), "국제캠퍼스")), ["간호과학대학", "경희대학교 생활과학대학", "약학대학"]);
});

test("별칭 조직은 줄을 만들지 않고 게시판 수만 본체에 얹는다", () => {
  const orgs = [
    org("c1", "언론정보학과", "department", { boards: 2 }),
    org("c2", "미디어학과", "department", { parent: "c1", boards: 3, alias: true }),
  ];
  const global = child(buildOrgTree(orgs, CAMPUSES), "국제캠퍼스");
  assert.deepEqual(names(global), ["언론정보학과"]);
  assert.equal(global.children[0].activeSourceCount, 5);
});

test("공지 건수는 조직 → 상위 → 두 캠퍼스까지 함께 얹힌다", () => {
  const orgs = [
    org("c1", "경영대학", "college", { campuses: ["campus-global", "campus-seoul"] }),
    org("d1", "경영학과", "department", { parent: "c1" }),
  ];
  const counts = countNoticesByNode(orgs, CAMPUSES, [["org:d1"]]);
  assert.equal(counts.byKey.get("org:d1"), 1);
  assert.equal(counts.byKey.get("org:c1"), 1);
  assert.equal(counts.byKey.get("campus:campus-global"), 1);
  assert.equal(counts.byKey.get("campus:campus-seoul"), 1);
});

test("상위가 자기를 가리키는 고리가 있어도 트리를 만든다", () => {
  const orgs = [org("a", "가", "office", { parent: "b" }), org("b", "나", "office", { parent: "a" })];
  const tree = buildOrgTree(orgs, CAMPUSES);
  const all = [];
  const walk = (n) => {
    if (n.id) all.push(n.id);
    Array.from(n.children).forEach(walk);
  };
  walk(tree);
  assert.deepEqual(all.sort(), ["a", "b"]);
});
