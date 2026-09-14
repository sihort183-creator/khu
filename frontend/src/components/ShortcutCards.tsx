"use client";
/**
 * 바로가기 카드(docs/바로가기_카드_설계_2026-09-14.md).
 *
 * 검색 결과 **위**에 답을 바로 띄운다. 검색어가 카드의 `match` 에 걸릴 때만 나오고,
 * 안 걸리면 아무것도 그리지 않는다. 색·여백·구조는 승인받은 시안(card_mockup.html)
 * 그대로이고, 시안이 그랬듯 Box·NoticeRow·CategoryBadge 의 마크업을 따른다.
 *
 * 규칙 쪽은 lib/cards.ts 가 갖는다 — 특히 **조건을 못 그리면 그 줄을 아예 그리지
 * 않는다**(설계문서 5.8). 여기서는 그 규칙이 정해 준 줄만 받아 그린다.
 */
import { useMemo, useSyncExternalStore } from "react";
import Link from "next/link";
import type { GraduationDepartment, ShortcutCard, ShortcutCardRow } from "@/lib/types";
import {
  departmentNoticesMessage,
  departmentOptions,
  findDepartment,
  graduationRows,
  graduationYearNote,
  keepRows,
  matchShortcutCards,
  parseAdmissionYear,
  watchWindowStart,
} from "@/lib/cards";
import {
  useDepartmentGraduationNotices,
  useGraduationRequirements,
  useShortcutCards,
  useShortcutWatch,
} from "@/lib/queries";
import { shortDate } from "@/lib/format";
import { Box } from "./Shell";
import { CategoryBadge } from "./CategoryBadge";
import type { ShortcutNotice } from "@/lib/api";

/**
 * 카드 안에서 물은 것을 이 기기에만 적어 둔다. **서버로 보내지 않는다.**
 *
 * 설정도 가입도 아니다(설계문서 7절). 학번은 학과별 졸업요건을 볼 때 한 번 묻고, 고른
 * 학과도 함께 적어 둔다 — 다음에 같은 카드가 떴을 때 또 고르게 하지 않으려는 것뿐이다.
 * 저장 방식은 지금 설정과 같다(localStorage).
 */
const REMEMBER_KEY = "khu-notice.shortcut-card.v1";
type Remembered = { year?: string; college?: string; department?: string };

const EMPTY: Remembered = {};
let cache: Remembered | null = null;
const listeners = new Set<() => void>();

function readRemembered(): Remembered {
  if (cache) return cache;
  try {
    cache = JSON.parse(localStorage.getItem(REMEMBER_KEY) ?? "{}") as Remembered;
  } catch {
    cache = EMPTY;
  }
  return cache!;
}

function remember(patch: Remembered) {
  cache = { ...readRemembered(), ...patch };
  try {
    localStorage.setItem(REMEMBER_KEY, JSON.stringify(cache));
  } catch {}
  listeners.forEach((listener) => listener());
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => void listeners.delete(listener);
}

/**
 * 적어 둔 값을 그대로 화면 상태로 쓴다(설정 화면과 같은 방식, lib/settings.tsx 참고).
 *
 * 따로 useState 를 두고 효과로 저장값을 옮겨 담지 않는다 — 저장값이 곧 상태다.
 * 서버에서 그릴 때는 빈 값이라 첫 그림이 흔들리지 않는다.
 */
function useRemembered(): Remembered {
  return useSyncExternalStore(subscribe, readRemembered, () => EMPTY);
}

export function ShortcutCards({ query }: { query: string }) {
  const cards = useShortcutCards();
  const matched = useMemo(() => matchShortcutCards(query, cards), [query, cards]);
  if (!matched.length) return null;
  return (
    <>
      {matched.map((card) => (
        <ShortcutCardView key={card.id} card={card} />
      ))}
    </>
  );
}

function ShortcutCardView({ card }: { card: ShortcutCard }) {
  const rows = useMemo(() => keepRows(card.rows), [card.rows]);
  // 기준일은 그리는 동안 고정한다. 렌더마다 새로 세면 조회 열쇠가 계속 바뀐다.
  const since = useMemo(() => watchWindowStart(), []);
  const watch = useShortcutWatch(card.watch, since);
  const department = useRemembered().department ?? "";

  return (
    <>
      <Box className="mb-2.5">
        <div className="flex flex-wrap items-center gap-2 border-b border-line-2 px-3.5 py-[11px] text-[13px] font-bold">
          {card.title}
          <a
            href={card.source.url}
            target="_blank"
            rel="noopener noreferrer"
            className="ml-auto min-w-0 text-xs font-normal text-gray hover:text-ink-2"
          >
            {card.source.name} ›
          </a>
        </div>

        <RowList rows={rows} />

        {card.more && card.more.items.length > 0 && (
          <details className="mt-2 border-t border-line-2">
            {/* 시안의 ▸/▾ 표시. 접힌 것을 펼 수 있다는 것이 글자만으로는 안 보인다. */}
            <summary className="cursor-pointer select-none list-none px-3.5 py-[9px] text-[12.5px] text-ink-2 marker:content-none before:mr-1 before:text-gray-2 before:content-['▸'] [[open]>&]:before:content-['▾'] hover:bg-cream">
              {card.more.summary}
            </summary>
            <ul className="list-disc px-3.5 pb-3 pl-[31px] pt-0.5 text-[13px] text-ink-2">
              {card.more.items.map((item) => (
                <li key={item} className="my-[3px]">{item}</li>
              ))}
            </ul>
          </details>
        )}

        {/* 2형 — 학과별 값을 카드 안에서 보여 준다. */}
        {card.dept && <GraduationSection />}

        {watch && <WatchLine notice={watch} />}

        {card.foot && card.foot.length > 0 && (
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 border-t border-line-2 bg-cream px-3.5 py-[9px] text-[12.5px] max-[430px]:gap-y-1">
            {card.foot.map((foot) =>
              foot.url && foot.link ? (
                <a key={foot.url + foot.link} href={foot.url} target="_blank" rel="noopener noreferrer" className="max-w-full truncate text-navy hover:underline">
                  {foot.link} ›
                </a>
              ) : (
                <span key={foot.text} className="text-gray">{foot.text}</span>
              ),
            )}
          </div>
        )}
      </Box>

      {/* 3형 — 우리 학과 공지. 2형에서 학과를 골랐을 때만 물어본다. */}
      {card.dept && department && <DepartmentNoticesCard department={department} />}
    </>
  );
}

/**
 * 라벨·값·조건 줄.
 *
 * 조건(`cond`)은 값 옆에 회색으로 **반드시** 붙는다. 좁은 화면에서는 라벨이 값을
 * 짓누르지 않도록 라벨을 값 위로 올린다(시안의 430px 규칙 그대로).
 */
function RowList({ rows }: { rows: ShortcutCardRow[] }) {
  if (!rows.length) return null;
  return (
    <dl className="m-0 grid grid-cols-[max-content_1fr] gap-x-3.5 gap-y-[7px] px-3.5 pb-1 pt-3 max-[430px]:grid-cols-1 max-[430px]:gap-y-px max-[430px]:pb-1.5">
      {rows.map((row) => (
        <div key={row.label + row.value} className="contents">
          <dt className="whitespace-nowrap text-[12.5px] text-gray max-[430px]:mt-[9px] max-[430px]:[&:first-of-type]:mt-0">{row.label}</dt>
          <dd className="m-0 min-w-0 text-[13.5px] text-ink">
            <span className={row.strong ? "font-bold" : undefined}>{row.value}</span>
            {/* 조건은 괄호로 묶는다. 시안은 짧은 값 옆에 회색 글자만 두었는데, 실제 값에는
                숫자로 끝나는 것이 많아(…자유이수 3) 조건 첫 글자가 값에 달라붙어 읽혔다.
                괄호가 있으면 값과 조건의 경계가 글자 크기에 기대지 않는다. */}
            {row.cond && <span className="ml-1 text-[12.5px] text-gray">({row.cond})</span>}
            {row.sub && <span className="mt-0.5 block text-[12.5px] text-ink-2">{row.sub}</span>}
          </dd>
        </div>
      ))}
    </dl>
  );
}

/**
 * "이 제도는 최근 안내가 올라왔습니다" 줄(설계문서 5.6).
 *
 * 문구와 링크를 한 덩어리씩 감싼다. 좁은 화면에서 문구 끝 글자 하나만 다음 줄로
 * 떨어지지 않게 하려는 것이다 — NoticeList 의 note 와 같은 처리다. 공지 제목은 길어서
 * 두 줄까지 접는다(NoticeRow 와 같은 line-clamp-2). 한 줄로 자르면 끝의 › 까지 잘려
 * 눌러 볼 수 있다는 표시가 사라진다.
 */
function WatchLine({ notice }: { notice: ShortcutNotice }) {
  return (
    <div className="flex flex-wrap items-baseline gap-x-[7px] gap-y-0.5 border-t border-warn-line bg-warn-bg px-3.5 py-2 text-[12.5px] text-warn-ink">
      <span className="min-w-0 flex-auto">이 제도는 최근 안내가 올라왔습니다</span>
      <Link href={`/notices/${notice.id}`} className="line-clamp-2 max-w-full font-semibold text-warn-ink hover:underline">
        {shortDate(notice.date) ?? ""} {notice.title} ›
      </Link>
    </div>
  );
}

/* ---------------- 2형 — 졸업요건 ---------------- */

/**
 * 학번·단과대·학과를 카드 안에서 묻고 학과별 값을 보여 준다.
 *
 * **공지 조직과 맞물리지 않는다.** 카드가 88개 학과 목록을 직접 들고 고르게 한다.
 * 이름이 맞는 공지 조직은 88곳 중 59곳뿐이고 나머지는 공지를 올리는 조직 자체가 없다
 * (설계문서 4.2). 이름 맞추기는 3형 카드 하나만 한다.
 */
function GraduationSection() {
  const file = useGraduationRequirements(true);
  const options = useMemo(() => departmentOptions(file), [file]);
  const { year = "", college = "", department = "" } = useRemembered();

  const departments = options.find((option) => option.college === college)?.departments ?? [];
  const dept = findDepartment(file, department || null);
  const rows = useMemo(() => (dept ? graduationRows(dept) : []), [dept]);

  // 단과대를 바꾸면 학과 선택은 지운다. 다른 단과대의 학과가 남아 있으면 고르개가
  // 보여 주는 것과 값이 어긋난다.
  const chooseCollege = (next: string) => remember({ college: next, department: "" });
  const chooseDepartment = (next: string) => remember({ department: next });
  const chooseYear = (next: string) => remember({ year: next.replace(/[^0-9]/g, "").slice(0, 4) });

  return (
    <div className="border-t border-line-2">
      <div className="flex flex-wrap items-center gap-2 px-3.5 pt-3">
        <label className="text-[12.5px] text-gray" htmlFor="shortcut-admission-year">학번</label>
        <input
          id="shortcut-admission-year"
          value={year}
          onChange={(event) => chooseYear(event.target.value)}
          inputMode="numeric"
          placeholder="2023"
          aria-label="학번(입학 연도)"
          className="w-[82px] flex-none rounded-lg border border-line bg-card px-[11px] py-1.5 text-[13px] text-ink outline-none focus:border-navy"
        />
        <Picker value={college} onChange={chooseCollege} label="단과대학" options={options.map((option) => option.college)} />
        <Picker value={department} onChange={chooseDepartment} label="학과" options={departments} disabled={!college} />
      </div>

      {dept ? (
        <>
          <RowList rows={rows} />
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 px-3.5 pb-3 pt-1 text-[12.5px]">
            <span className="text-gray">{graduationYearNote(dept, parseAdmissionYear(year))}</span>
            <DeptSource dept={dept} />
          </div>
        </>
      ) : (
        <p className="m-0 px-3.5 pb-3 pt-2 text-[12.5px] text-gray">
          {file
            ? "단과대학과 학과를 고르면 우리 학과 졸업학점·전공학점·졸업논문 요건을 보여 드립니다. 위 값은 전교 공통입니다."
            : "학과별 졸업요건을 불러오는 중입니다…"}
        </p>
      )}
    </div>
  );
}

/** 시행세칙 원문으로 보내는 줄. 어디서 온 값인지 학생이 직접 확인할 수 있어야 한다. */
function DeptSource({ dept }: { dept: GraduationDepartment }) {
  const detail = [dept.source.kind, dept.source.page ? `${dept.source.page}쪽` : null].filter(Boolean).join(" · ");
  return (
    <>
      <a href={dept.source.url} target="_blank" rel="noopener noreferrer" className="max-w-full truncate text-navy hover:underline">
        {dept.source.doc ?? `${dept.college} 시행세칙`} 원문 ›
        {detail && <span className="ml-1 text-[11.5px] text-gray-2">{detail}</span>}
      </a>
      {dept.not_extracted && (
        <span className="text-gray">일부 칸은 원문에서 뽑지 못해 비워 뒀습니다 — 지어내지 않았습니다</span>
      )}
      {dept.shared_document_note && <span className="text-gray">{dept.shared_document_note}</span>}
    </>
  );
}

/** 시안의 고르개. 좁아지면 한 줄에 하나씩 내려간다. */
function Picker({ value, onChange, label, options, disabled }: { value: string; onChange: (v: string) => void; label: string; options: string[]; disabled?: boolean }) {
  return (
    <label className={`relative inline-flex min-w-0 flex-[1_1_128px] items-center rounded-lg border bg-card text-[13px] max-[430px]:flex-[1_1_100%] ${value ? "border-navy text-navy" : "border-line text-ink-2"} ${disabled ? "opacity-50" : ""}`}>
      <select
        value={value}
        onChange={(event) => onChange(event.target.value)}
        disabled={disabled}
        aria-label={label}
        className="w-full appearance-none truncate bg-transparent py-1.5 pl-[11px] pr-7 outline-none"
      >
        <option value="">{label}</option>
        {options.map((option) => (
          <option key={option} value={option}>{option}</option>
        ))}
      </select>
      <span className="pointer-events-none absolute right-2.5 text-[10px] text-gray">▾</span>
    </label>
  );
}

/* ---------------- 3형 — 우리 학과 졸업 공지 ---------------- */

/**
 * 학과가 매 학기 올리는 졸업 공지를 우리 공지에서 지목한다.
 *
 * 학과 이름을 공지 조직에 못 맞추면 카드를 통째로 그리지 않는다(융합전공 11곳처럼
 * 공지를 올리는 조직 자체가 없는 학과가 29곳이다). 맞췄는데 글이 없으면 그때는
 * **"아직 올라오지 않았습니다"** 라고 말한다 — 빈 상자로 두지 않는다.
 */
function DepartmentNoticesCard({ department }: { department: string }) {
  const result = useDepartmentGraduationNotices(department);
  const empty = departmentNoticesMessage(result);
  if (!result) return null;
  return (
    <Box className="mb-2.5">
      <div className="flex flex-wrap items-center gap-2 border-b border-line-2 px-3.5 py-[11px] text-[13px] font-bold">
        우리 학과 졸업 공지
        <span className="ml-auto min-w-0 truncate text-xs font-normal text-gray">{result.organization}</span>
      </div>
      {empty ? (
        <p className="m-0 px-4 py-5 text-center text-[13px] text-gray">{empty}</p>
      ) : (
        result.notices.map((notice) => (
          <Link key={notice.id} href={`/notices/${notice.id}`} className="block border-b border-line-2 px-4 py-3.5 transition-colors last:border-b-0 hover:bg-cream">
            <div className="text-[14px] leading-[1.45]">
              <CategoryBadge category={notice.category} className="mr-1.5" />
              {notice.title}
            </div>
            <div className="mt-1 text-xs text-gray">
              {notice.source}
              {notice.date && ` · ${shortDate(notice.date)}`}
            </div>
          </Link>
        ))
      )}
    </Box>
  );
}
