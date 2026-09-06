"use client";
// 조직 다중 선택. 공개 파일의 organizations.json은 parent_id가 전부 비어 있어
// 캠퍼스→단과대→학과 트리를 만들 수 없다. 계층 대신 org_type으로 묶고, 그 안은
// 게시판 수(source_count) 많은 순으로 세워 실제로 공지가 오는 곳을 위로 올린다.
import { useMemo, useState } from "react";
import type { Organization, OrgType } from "@/lib/types";
import { useOrganizations } from "@/lib/queries";
import { IconChevron, IconSearch } from "./icons";

/** 학생이 자기 소속을 먼저 찾는 순서. catalog의 나열 순서와 일부러 다르게 둔다. */
const GROUPS: { code: OrgType; label: string }[] = [
  { code: "college", label: "단과대·대학원" },
  { code: "department", label: "학과·전공" },
  { code: "council", label: "학생회" },
  { code: "office", label: "행정부서" },
  { code: "institute", label: "부속·연구기관" },
];

interface Props {
  selected: Set<string>;
  onToggle: (id: string) => void;
  onClear: () => void;
  /** true면 데스크톱 좌측 열(220px)용으로 촘촘하게. false면 모바일 본문용 44px 행. */
  dense?: boolean;
}

export function OrganizationPicker({ selected, onToggle, onClear, dense = false }: Props) {
  const orgs = useOrganizations();
  const [q, setQ] = useState("");
  const [open, setOpen] = useState<Set<string>>(new Set());
  // 484곳 중 349곳은 아직 붙은 게시판이 없다. 기본으로 감춰야 목록을 훑을 수 있다.
  const [showEmpty, setShowEmpty] = useState(false);

  const keyword = q.trim().toLowerCase();
  const groups = useMemo(() => {
    const byType = new Map<string, Organization[]>();
    for (const o of orgs) byType.set(o.type.code, [...(byType.get(o.type.code) ?? []), o]);
    return GROUPS.map((g) => {
      const all = (byType.get(g.code) ?? []).sort(
        (a, b) => (b.source_count ?? 0) - (a.source_count ?? 0) || a.name.localeCompare(b.name, "ko"),
      );
      // 검색 중에는 감추기 규칙을 풀어 준다. 이름을 아는 곳은 반드시 찾아져야 한다.
      const items = keyword
        ? all.filter((o) => o.name.toLowerCase().includes(keyword))
        : all.filter((o) => showEmpty || (o.source_count ?? 0) > 0 || selected.has(o.id));
      return { ...g, items, total: all.length, hidden: all.length - items.length };
    }).filter((g) => g.items.length > 0);
  }, [orgs, keyword, showEmpty, selected]);

  const selectedOrgs = orgs.filter((o) => selected.has(o.id));
  const rowPad = dense ? "py-[5px]" : "min-h-11 py-2";

  return (
    <div className="text-[13px]">
      <div className="border-b border-line-2 p-2">
        <button
          type="button"
          onClick={onClear}
          aria-pressed={selected.size === 0}
          className={`flex min-h-11 w-full items-center rounded-lg border px-3 text-left ${
            selected.size === 0 ? "border-red bg-[#FBF0F0] font-bold text-red" : "border-line bg-white text-ink-2 hover:border-gray-2"
          } ${dense ? "min-h-0 py-2" : ""}`}
        >
          경희대학교 전체
          <span className="ml-auto text-xs font-normal text-gray-2">모든 공지</span>
        </button>

        {selectedOrgs.length > 0 && (
          <div className="mt-2 flex flex-wrap gap-1">
            {selectedOrgs.map((o) => (
              <button
                key={o.id}
                type="button"
                onClick={() => onToggle(o.id)}
                aria-label={`${o.name} 선택 해제`}
                className="flex max-w-full items-center gap-1 rounded-full border border-navy bg-navy px-2.5 py-1 text-xs text-white"
              >
                <span className="truncate">{o.name}</span>
                <span aria-hidden className="opacity-70">
                  ×
                </span>
              </button>
            ))}
          </div>
        )}
      </div>

      <div className="flex items-center gap-2 border-b border-line-2 px-3 py-2">
        <IconSearch className="shrink-0 text-gray" width={14} height={14} />
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="학과·기관 이름 검색"
          aria-label="조직 검색"
          className="w-full min-w-0 bg-transparent py-1 text-[13px] outline-none"
        />
        {q && (
          <button type="button" onClick={() => setQ("")} className="shrink-0 text-xs text-gray">
            지우기
          </button>
        )}
      </div>

      <div className={`overflow-y-auto ${dense ? "max-h-[320px]" : "max-h-[52vh]"}`}>
        {orgs.length === 0 && <p className="px-3.5 py-5 text-center text-gray">조직 목록을 불러오는 중…</p>}
        {orgs.length > 0 && groups.length === 0 && <p className="px-3.5 py-5 text-center text-gray">검색 결과가 없습니다</p>}

        {groups.map((g) => {
          // 검색 중에는 결과를 바로 보여 준다. 접힌 채로 두면 못 찾은 것처럼 보인다.
          const expanded = !!keyword || open.has(g.code);
          const picked = g.items.filter((o) => selected.has(o.id)).length;
          return (
            <section key={g.code} className="border-b border-line-2 last:border-b-0">
              <h3>
                <button
                  type="button"
                  aria-expanded={expanded}
                  onClick={() =>
                    setOpen((prev) => {
                      const next = new Set(prev);
                      if (next.has(g.code)) next.delete(g.code);
                      else next.add(g.code);
                      return next;
                    })
                  }
                  className={`flex w-full items-center gap-1.5 bg-cream px-3.5 text-left font-bold text-ink-2 ${dense ? "py-2" : "min-h-11 py-2.5"}`}
                >
                  <IconChevron className={`shrink-0 opacity-60 transition-transform ${expanded ? "" : "-rotate-90"}`} width={11} height={11} strokeWidth={3} />
                  <span className="truncate">{g.label}</span>
                  {picked > 0 && <span className="shrink-0 rounded-full bg-red px-1.5 text-[11px] font-semibold text-white">{picked}</span>}
                  <span className="ml-auto shrink-0 text-xs font-normal text-gray-2">{g.items.length}</span>
                </button>
              </h3>

              {expanded && (
                <ul>
                  {g.items.map((o) => {
                    const on = selected.has(o.id);
                    const boards = o.source_count ?? 0;
                    return (
                      <li key={o.id}>
                        <label className={`flex cursor-pointer items-center gap-2.5 px-3.5 ${rowPad} ${on ? "bg-[#FBF0F0]" : "hover:bg-bg"}`}>
                          <input type="checkbox" checked={on} onChange={() => onToggle(o.id)} className="h-4 w-4 shrink-0 accent-red" />
                          <span className={`min-w-0 flex-1 break-keep ${on ? "font-medium text-red" : "text-ink-2"}`}>{o.name}</span>
                          <span className="shrink-0 text-[11px] text-gray-2">{boards ? `게시판 ${boards}` : "준비 중"}</span>
                        </label>
                      </li>
                    );
                  })}
                  {!keyword && g.hidden > 0 && (
                    <li className="px-3.5 pb-2 pt-1 text-[11px] text-gray-2">게시판이 아직 없는 {g.hidden}곳은 숨겼습니다</li>
                  )}
                </ul>
              )}
            </section>
          );
        })}
      </div>

      {!keyword && (
        <button
          type="button"
          onClick={() => setShowEmpty((v) => !v)}
          className={`w-full border-t border-line-2 bg-cream text-xs text-gray hover:text-ink-2 ${dense ? "py-2" : "min-h-11 py-2.5"}`}
        >
          {showEmpty ? "게시판 없는 조직 숨기기" : "게시판 없는 조직도 보기"}
        </button>
      )}
    </div>
  );
}

/** 선택 상태를 한 줄로 요약한다. 접힌 상자의 제목과 목록 머리글이 같은 문구를 쓴다. */
export function organizationScopeLabel(orgs: Organization[], selected: Set<string>): string {
  if (selected.size === 0) return "경희대학교 전체";
  const names = orgs.filter((o) => selected.has(o.id)).map((o) => o.name);
  if (names.length === 0) return `조직 ${selected.size}곳`;
  return names.length === 1 ? names[0] : `${names[0]} 외 ${names.length - 1}곳`;
}
