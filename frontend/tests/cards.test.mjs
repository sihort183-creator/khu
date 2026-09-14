// 바로가기 카드의 규칙을 못 박는다(docs/바로가기_카드_설계_2026-09-14.md).
//
// 화면에서만 확인하면 다음 손질에 조용히 무너지는 것들이다. 특히 **조건을 떼지 않는다**는
// 규칙(5.8)은 눈으로 보고 지나치기 쉬운데, 틀리면 편입생이 SW 6학점을 들어야 하는 줄
// 알고 한국 학생이 TOPIK 을 준비한다.
//
// 저장소에 실제로 실린 카드 13장(data/shortcuts/cards.json)과 학과 88곳
// (data/shortcuts/graduation_requirements.json)으로 시험한다. 가상 자료로 시험하면
// 자료가 바뀔 때 규칙이 깨진 것을 못 잡는다.
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { dirname, isAbsolute, resolve } from "node:path";
import { test } from "node:test";
import * as ts from "typescript";
import * as vm from "node:vm";
import { fileURLToPath } from "node:url";

const frontendRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const sourceRoot = resolve(frontendRoot, "src");
const repoRoot = resolve(frontendRoot, "..");

const context = vm.createContext({ console, Date, Intl });
const modules = new Map();

async function load(specifier, parent) {
  let file = specifier.startsWith("@/") ? resolve(sourceRoot, specifier.slice(2)) : resolve(dirname(parent), specifier);
  if (!isAbsolute(file)) throw new Error(`Unexpected import in cards test: ${specifier}`);
  if (!file.endsWith(".ts")) file += ".ts";
  if (modules.has(file)) return modules.get(file);
  const output = ts.transpileModule(await readFile(file, "utf8"), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
    fileName: file,
  }).outputText;
  const module = new vm.SourceTextModule(output, { context, identifier: file });
  modules.set(file, module);
  await module.link((child, referencing) => load(child, referencing.identifier));
  return module;
}

const cardsPath = resolve(sourceRoot, "lib/cards.ts");
const cardsModule = await load(cardsPath, cardsPath);
await cardsModule.evaluate();
const {
  WATCH_WINDOW_DAYS,
  departmentNoticesMessage,
  departmentOptions,
  findDepartment,
  findDepartmentOrganization,
  graduationRows,
  graduationYearNote,
  keepRows,
  matchShortcutCards,
  parseAdmissionYear,
  watchWindowStart,
} = cardsModule.namespace;

const json = async (path) => JSON.parse(await readFile(resolve(repoRoot, path), "utf8"));
const CARDS = (await json("data/shortcuts/cards.json")).cards;
const GRADUATION = await json("data/shortcuts/graduation_requirements.json");

/* ---------------- 1. 검색어가 match 에 걸릴 때만 카드가 뜬다 ---------------- */

test("검색어가 match 에 걸릴 때만 카드가 뜬다", () => {
  // vm 밖으로 나온 배열은 realm 이 달라 deepEqual 이 못 맞춘다. 이쪽 realm 으로 옮겨 담는다.
  const ids = (q) => Array.from(matchShortcutCards(q, CARDS), (card) => card.id);

  assert.deepEqual(ids("학점포기"), ["haksa-credit-waiver"]);
  // 카드의 말이 검색어 안에 있으면 된다 — 학생은 한 낱말만 치지 않는다.
  assert.deepEqual(ids("학점포기 어떻게 하나요"), ["haksa-credit-waiver"]);
  // 가운뎃점·띄어쓰기를 넘어간다(검색과 같은 규칙, lib/text.ts).
  assert.deepEqual(ids("학점 포기"), ["haksa-credit-waiver"]);
  assert.deepEqual(ids("졸업요건"), ["haksa-graduation"]);

  // 안 걸리면 아무것도 그리지 않는다. 공지가 넘치는 말에는 카드를 두지 않았다.
  assert.deepEqual(ids("채용"), []);
  assert.deepEqual(ids("인턴 모집"), []);
  assert.deepEqual(ids(""), []);
  assert.deepEqual(ids(undefined), []);
  // 아직 다 치지 않은 검색어에 카드가 먼저 튀어나오지 않는다.
  assert.deepEqual(ids("학"), []);
  assert.deepEqual(ids("학점"), []);
});

test("카드 13장이 저마다 뜨고, 한 검색어에 카드가 우르르 뜨지 않는다", () => {
  for (const card of CARDS) {
    const hit = matchShortcutCards(card.match[0], CARDS);
    assert.ok(hit.some((found) => found.id === card.id), `${card.id} 가 '${card.match[0]}' 에 안 뜬다`);
    assert.ok(hit.length <= 2, `'${card.match[0]}' 에 카드가 ${hit.length}장 떴다`);
  }
});

/* ---------------- 2. 조건이 있는 줄은 조건째로 그린다 (설계문서 5.8) ---------------- */

test("조건이 있는 줄은 조건을 달고 살아남는다", () => {
  const withCond = CARDS.flatMap((card) => keepRows(card.rows)).filter((row) => row.cond);
  // 실린 카드 64줄 가운데 34줄에 조건이 붙어 있다. 규칙이 조용히 줄을 지우면 여기서 걸린다.
  assert.equal(withCond.length, 34);
  for (const row of withCond) assert.ok(row.cond.trim().length > 0);

  const sw = keepRows(CARDS.find((card) => card.id === "haksa-graduation").rows)
    .find((row) => row.label === "SW 기초교육");
  assert.match(sw.cond, /편입생, 순수외국인, 재직자 특별전형자는 제외/);
});

test("조건을 못 그리는 줄은 아예 그리지 않는다", () => {
  const rows = [
    { label: "SW 기초교육", value: "6학점", cond: "편입생 제외" },
    { label: "SW 기초교육", value: "6학점", cond: "   " },
    { label: "SW 기초교육", value: "6학점", cond: "" },
    { label: "영어강의", value: "3과목" },
    { label: "빈 값", value: "  " },
  ];
  assert.deepEqual(Array.from(keepRows(rows), (row) => row.label), ["SW 기초교육", "영어강의"]);
  assert.equal(keepRows(rows)[0].cond, "편입생 제외");
});

test("학과별 졸업요건도 대상 제한을 조건으로 달고 나온다", () => {
  const french = findDepartment(GRADUATION, "프랑스어학과");
  const rows = graduationRows(french);

  // 한국어트랙 외국인 학생에게만 걸리는 조문이다. 조건을 떼면 한국 학생이 TOPIK 을 준비한다.
  const topik = rows.find((row) => row.value.includes("한국어능력시험"));
  assert.match(topik.cond, /대상 제한/);
  assert.match(topik.cond, /한국어트랙 외국인학생/);

  // 전교 기준과 값이 다른 조문은 학과 고유 요건으로 남는다(편입생 1과목).
  const english = rows.find((row) => row.value.includes("영어강좌"));
  assert.match(english.cond, /편입생/);

  // 조건 자리가 없는 줄은 조건 없이 그대로 나온다.
  const credits = rows.find((row) => row.label === "졸업학점");
  assert.equal(credits.value, "120학점");
  assert.equal(credits.cond, undefined);

  // 졸업논문 대체조건은 줄이지 않는다 — "논문 안 써도 되는 길"이 여기 있다.
  const thesis = rows.find((row) => row.label === "졸업논문");
  assert.match(thesis.value, /DELF B1/);
  assert.match(thesis.value, /캡스톤디자인/);
});

test("실린 학과 88곳 어디에도 조건이 빠진 줄이 없다", () => {
  for (const dept of GRADUATION.departments) {
    for (const row of graduationRows(dept)) {
      if (row.cond !== undefined) assert.ok(row.cond.trim(), `${dept.department} 의 '${row.label}' 줄에 빈 조건이 남았다`);
    }
  }
});

/* ---------------- 3. 학번을 안 넣어도 빈 카드가 아니다 ---------------- */

test("학번을 안 넣어도 값이 나오고, 어느 학년도 기준인지 밝힌다", () => {
  const nuclear = findDepartment(GRADUATION, "원자력공학과");
  const rows = graduationRows(nuclear);

  assert.ok(rows.length > 0, "학번이 없다고 빈 카드로 두지 않는다");
  assert.equal(rows.find((row) => row.label === "졸업학점").value, "130학점");

  const note = graduationYearNote(nuclear, null);
  assert.match(note, /학번을 넣지 않아/);
  assert.match(note, /2025학년도/);

  // 학번이 시행세칙보다 오래되면 같은 값을 "당신 학번 기준"이라고 말하지 않는다.
  assert.match(graduationYearNote(nuclear, 2021), /경과조치가 다를 수 있어/);
  assert.match(graduationYearNote(nuclear, 2026), /2026학번/);
});

test("학번 칸은 네 자리 연도만 받는다", () => {
  assert.equal(parseAdmissionYear("2023"), 2023);
  assert.equal(parseAdmissionYear(""), null);
  assert.equal(parseAdmissionYear("스물셋"), null);
  assert.equal(parseAdmissionYear("23"), null);
  assert.equal(parseAdmissionYear(null), null);
});

test("학과 고르개는 단과대 아래 학과만 보여 준다", () => {
  const options = departmentOptions(GRADUATION);
  assert.equal(options.reduce((sum, option) => sum + option.departments.length, 0), 88);
  const foreign = options.find((option) => option.college === "외국어대학");
  assert.ok(foreign.departments.includes("스페인어학과"));
  assert.ok(!foreign.departments.includes("원자력공학과"));
  assert.equal(departmentOptions(null).length, 0);
});

/* ---------------- 4. 학과 공지가 없으면 그렇게 말한다 ---------------- */

test("학과 공지가 없으면 '아직 올라오지 않았습니다' 라고 말한다", () => {
  assert.equal(
    departmentNoticesMessage({ organization: "스페인어학과", notices: [] }),
    "스페인어학과 게시판에는 졸업 공지가 아직 올라오지 않았습니다",
  );
  // 글이 있으면 할 말이 없다(공지를 그대로 그린다).
  assert.equal(departmentNoticesMessage({ organization: "스페인어학과", notices: [{ id: "ntc-1" }] }), null);
  // 물어볼 게시판 자체를 못 찾았으면 카드를 안 그린다. 빈 상자를 내밀지 않는다.
  assert.equal(departmentNoticesMessage(null), null);
  assert.equal(departmentNoticesMessage(undefined), null);
});

test("학과 이름을 공지 조직에 맞추고, 애매하면 고르지 않는다", () => {
  const org = (id, name, extra = {}) => ({
    id,
    name,
    type: { code: extra.type ?? "department", label: "학과" },
    parent_id: extra.parent ?? null,
    is_alias: extra.alias ?? false,
    has_children: false,
  });
  const orgs = [
    // 운영 명부에 실제로 있는 꼴: 같은 이름 둘인데 한쪽이 다른 쪽의 별칭이고,
    // 공지는 별칭 쪽에 붙어 있다(스페인어학과 본체 0건 · 별칭 197건).
    org("org-spanish", "스페인어학과"),
    org("org-spanish-alias", "스페인어학과", { parent: "org-spanish", alias: true }),
    org("org-media", "미디어학과"),
    org("org-media-2", "미디어학부"),
    org("org-college", "외국어대학", { type: "college" }),
  ];
  const spanish = findDepartmentOrganization("스페인어학과", orgs);
  assert.equal(spanish.name, "스페인어학과");
  // 별칭 짝은 한 조직이다. 둘 다 물어봐야 공지가 붙은 쪽을 놓치지 않는다.
  assert.deepEqual(Array.from(spanish.ids).sort(), ["org-spanish", "org-spanish-alias"]);

  // 별칭으로 이어지지 않은 동명이조직은 고르지 않는다 — 엉뚱한 학과의 졸업 공지를
  // 내미느니 카드를 안 그린다.
  assert.equal(findDepartmentOrganization("미디어학", orgs), null);
  // 공지를 올리는 조직 자체가 없는 학과가 88곳 중 29곳이다.
  assert.equal(findDepartmentOrganization("빅데이터응용학과", orgs), null);
});

/* ---------------- watch — "최근"의 기준 ---------------- */

test("최근은 30일이고 한국시간 날짜로 센다", () => {
  assert.equal(WATCH_WINDOW_DAYS, 30);
  // 한국시간 9월 14일 새벽 0시 10분(=UTC 9월 13일 15시 10분)에 센 기준일.
  assert.equal(watchWindowStart(new Date("2026-09-13T15:10:00Z")), "2026-08-15");
  assert.equal(watchWindowStart(new Date("2026-09-14T12:00:00Z")), "2026-08-15");
});
