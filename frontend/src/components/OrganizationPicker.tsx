"use client";
// 조직 다중 선택 — 계층 트리. 경희대학교 → 캠퍼스 → 단과대 → 학과.
//
// 계층을 아는 만큼만 내려 붙인다(lib/orgTree.ts). 상위를 모르는 조직은 캠퍼스 밑,
// 캠퍼스도 모르는 조직은 경희대학교 바로 밑에 그냥 한 줄로 선다. 484곳 중 상위가
// 붙은 것이 50곳뿐이라, 모른다는 이유로 감추면 대부분이 목록에서 사라져 버린다.
//
// 맨 위 '경희대학교' 줄은 테두리·배경 없는 보통 줄이다. 맨 위라는 것과 골라져 있다는
// 것만 글자 굵기·색·구분선으로 드러낸다(사용자 지적: "면으로 들어오는 디자인").
import { useEffect, useMemo, useRef, useState } from "react";
import type { Organization } from "@/lib/types";
import { useCatalog, useOrganizationNoticeCounts, useOrganizations } from "@/lib/queries";
import { useSettings } from "@/lib/settings";
import { buildOrgTree, campusKey, ROOT_KEY, type OrgNode } from "@/lib/orgTree";
import { IconChevron, IconSearch } from "./icons";

interface Props {
  selected: Set<string>;
  onToggle: (id: string) => void;
  onClear: () => void;
  /** true면 데스크톱 좌측 열(220px)용으로 촘촘하게. false면 모바일 본문용 44px 행. */
  dense?: boolean;
}

/** 화면에 세울 한 줄. 트리를 펼친 순서대로 늘어놓은 것이다. */
interface Row {
  node: OrgNode;
  hasChildren: boolean;
  open: boolean;
}

function matches(node: OrgNode, keyword: string) {
  return node.name.toLowerCase().includes(keyword);
}

export function OrganizationPicker({ selected, onToggle, onClear, dense = false }: Props) {
  const orgs = useOrganizations();
  const catalog = useCatalog();
  const { settings } = useSettings();
  const [q, setQ] = useState("");
  const [open, setOpen] = useState<Set<string> | null>(null);
  // 484곳 중 349곳은 아직 붙은 게시판이 없다. 기본으로 감춰야 목록을 훑을 수 있다.
  const [showEmpty, setShowEmpty] = useState(false);

  // 공지 건수는 색인 전체(약 600KB)를 읽어야 나온다. 선택기가 실제로 자리를 차지할
  // 때만 읽는다. 좁은 화면에서 좌측 열은 display:none 이라 자리가 없고(offsetParent 없음),
  // 접힌 모바일 상자는 아예 그려지지도 않는다. 화면 폭이 바뀌면 다시 살핀다.
  // (IntersectionObserver 를 쓰지 않는 이유: 화면이 그려지지 않는 상황에서는 한 번도
  //  불리지 않아 수치가 영영 안 붙는다.)
  const boxRef = useRef<HTMLDivElement>(null);
  const [laidOut, setLaidOut] = useState(false);
  useEffect(() => {
    if (laidOut) return;
    const check = () => {
      const el = boxRef.current;
      if (el && el.offsetParent !== null && el.getBoundingClientRect().width > 0) setLaidOut(true);
    };
    const timer = setTimeout(check, 0);
    window.addEventListener("resize", check);
    return () => {
      clearTimeout(timer);
      window.removeEventListener("resize", check);
    };
  }, [laidOut]);
  const counts = useOrganizationNoticeCounts(laidOut);

  const tree = useMemo(() => buildOrgTree(orgs, catalog?.campuses), [orgs, catalog]);
  const keyword = q.trim().toLowerCase();

  /** 보여 줄 가지만 남긴다. 자식이 남으면 부모도 남는다 — 중간 마디가 끊기면 안 된다. */
  const pruned = useMemo(() => {
    const keepSelf = (node: OrgNode) => {
      if (node.kind !== "org" || !node.id) return false;
      if (keyword) return matches(node, keyword);
      if (selected.has(node.id)) return true;
      if (showEmpty) return true;
      return (node.organization?.source_count ?? 0) > 0 || (counts?.byKey.get(node.key) ?? 0) > 0;
    };
    const walk = (node: OrgNode): OrgNode | null => {
      const children = node.children.map(walk).filter((child): child is OrgNode => child !== null);
      if (node.kind === "root") return { ...node, children };
      if (children.length === 0 && !keepSelf(node)) return null;
      return { ...node, children };
    };
    return walk(tree)!;
  }, [tree, keyword, selected, showEmpty, counts]);

  /**
   * 기본 펼침: 대학, 지금 보고 있는 캠퍼스, 그리고 이미 고른 조직까지 가는 길.
   * 다른 캠퍼스까지 펼쳐 두면 484줄이 한 번에 쏟아져 손가락으로 훑을 수가 없다.
   */
  const defaultOpen = useMemo(() => {
    const keys = new Set<string>([ROOT_KEY]);
    const walk = (node: OrgNode, trail: string[]): boolean => {
      const path = [...trail, node.key];
      let hit = node.kind === "org" && !!node.id && selected.has(node.id);
      for (const child of node.children) if (walk(child, path)) hit = true;
      if (node.kind === "campus" && node.key === campusKey(settings.campus_id ?? "")) keys.add(node.key);
      if (hit) for (const key of path) keys.add(key);
      return hit;
    };
    walk(tree, []);
    return keys;
  }, [tree, selected, settings.campus_id]);

  const openKeys = open ?? defaultOpen;

  const rows = useMemo(() => {
    const out: Row[] = [];
    const walk = (node: OrgNode) => {
      const hasChildren = node.children.length > 0;
      // 검색 중에는 결과가 바로 보여야 한다. 접힌 채로 두면 못 찾은 것처럼 보인다.
      const isOpen = node.kind === "root" || !!keyword || openKeys.has(node.key);
      out.push({ node, hasChildren, open: isOpen && hasChildren });
      if (isOpen) for (const child of node.children) walk(child);
    };
    walk(pruned);
    return out;
  }, [pruned, openKeys, keyword]);

  // 한 번에 여러 줄이 열리고 닫혀도 앞의 결과 위에 쌓이도록 갱신 함수 꼴로 쓴다.
  const toggleOpen = (key: string) =>
    setOpen((prev) => {
      const next = new Set(prev ?? defaultOpen);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  const selectedOrgs = orgs.filter((o) => selected.has(o.id));
  // 접혀 있는 것은 숨긴 것이 아니다. 남긴 가지에 든 조직 수로 센다.
  const keptOrgs = useMemo(() => {
    let n = 0;
    const walk = (node: OrgNode) => {
      if (node.kind === "org") n += 1;
      for (const child of node.children) walk(child);
    };
    walk(pruned);
    return n;
  }, [pruned]);
  const hiddenOrgs = orgs.length - keptOrgs;

  // 촘촘한 좌측 열(220px)에서는 들여쓰기와 여백을 줄여야 이름이 덜 접힌다.
  const step = dense ? 9 : 12;
  const twist = dense ? "w-5" : "w-6";
  const rowMin = dense ? "min-h-[30px]" : "min-h-11";
  const padX = dense ? "pl-2.5 pr-2" : "pl-3 pr-3";

  return (
    <div ref={boxRef} className="text-[13px]">
      {selectedOrgs.length > 0 && (
        <div className={`flex flex-wrap gap-1 border-b border-line-2 ${dense ? "p-2" : "p-2.5"}`}>
          {selectedOrgs.map((o) => (
            <button
              key={o.id}
              type="button"
              onClick={() => onToggle(o.id)}
              aria-label={`${o.name} 선택 해제`}
              className="flex max-w-full items-center gap-1 rounded-full border border-line bg-cream px-2 py-0.5 text-[11.5px] text-ink-2 hover:border-gray-2"
            >
              <span className="truncate">{o.name}</span>
              <span aria-hidden className="text-gray-2">
                ×
              </span>
            </button>
          ))}
        </div>
      )}

      <div className={`flex items-center gap-2 border-b border-line-2 ${dense ? "px-2.5 py-1.5" : "px-3 py-2"}`}>
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

      <div className={`overflow-y-auto overflow-x-hidden ${dense ? "max-h-[360px]" : "max-h-[52vh]"}`}>
        {orgs.length === 0 && <p className="px-3.5 py-5 text-center text-gray">조직 목록을 불러오는 중…</p>}
        {orgs.length > 0 && !!keyword && rows.length <= 1 && <p className="px-3.5 py-5 text-center text-gray">검색 결과가 없습니다</p>}

        {orgs.length > 0 && (
          <ul>
            {rows.map((row) => {
              const { node } = row;
              const indent = node.depth * step;
              const count = counts?.byKey.get(node.key);

              // 맨 위 경희대학교 줄. 면(테두리·배경)을 만들지 않고 굵기와 구분선만 쓴다.
              if (node.kind === "root") {
                const all = selected.size === 0;
                return (
                  <li key={node.key} className="border-b border-line-2">
                    <button
                      type="button"
                      onClick={onClear}
                      aria-pressed={all}
                      title="모든 공지 보기"
                      className={`flex w-full items-center gap-2 text-left ${padX} ${rowMin} ${dense ? "py-1" : "py-2"} hover:bg-bg`}
                    >
                      <span className={`min-w-0 flex-1 break-keep [overflow-wrap:anywhere] ${all ? "font-bold text-red" : "font-semibold text-ink-2"}`}>{node.name}</span>
                      <span className="shrink-0 text-[11.5px] font-normal text-gray-2">
                        {counts ? counts.total.toLocaleString("ko-KR") : "전체"}
                      </span>
                    </button>
                  </li>
                );
              }

              const on = !!node.id && selected.has(node.id);
              const twistBtn = row.hasChildren ? (
                <button
                  type="button"
                  aria-expanded={row.open}
                  aria-label={`${node.name} ${row.open ? "접기" : "펼치기"}`}
                  onClick={() => toggleOpen(node.key)}
                  className={`flex shrink-0 items-center justify-center self-stretch text-gray-2 hover:text-ink-2 ${twist}`}
                >
                  <IconChevron className={`transition-transform ${row.open ? "" : "-rotate-90"}`} width={10} height={10} strokeWidth={3} />
                </button>
              ) : (
                <span aria-hidden className={`shrink-0 ${twist}`} />
              );

              // 캠퍼스 줄은 고르는 자리가 아니라 묶음이다. 줄 전체가 펼침 단추다.
              if (node.kind === "campus") {
                return (
                  <li key={node.key}>
                    <div className={`flex items-center ${padX} ${rowMin} ${dense ? "py-1" : "py-1.5"} hover:bg-bg`}>
                      <span aria-hidden className="shrink-0" style={{ width: indent }} />
                      {twistBtn}
                      <button
                        type="button"
                        aria-expanded={row.open}
                        onClick={() => toggleOpen(node.key)}
                        className="flex min-w-0 flex-1 items-center gap-2 self-stretch text-left"
                      >
                        <span className="min-w-0 flex-1 break-keep [overflow-wrap:anywhere] font-semibold text-ink-2">{node.name}</span>
                        {count !== undefined && <span className="shrink-0 text-[11.5px] text-gray-2">{count.toLocaleString("ko-KR")}</span>}
                      </button>
                    </div>
                  </li>
                );
              }

              const boards = node.organization?.source_count ?? 0;
              return (
                <li key={node.key}>
                  <div className={`flex items-center ${padX} ${rowMin} ${dense ? "py-1" : "py-1.5"} ${on ? "bg-[#FBF0F0]" : "hover:bg-bg"}`}>
                    <span aria-hidden className="shrink-0" style={{ width: indent }} />
                    {twistBtn}
                    <label className="flex min-w-0 flex-1 cursor-pointer items-center gap-2 self-stretch py-0.5">
                      <input
                        type="checkbox"
                        checked={on}
                        onChange={() => node.id && onToggle(node.id)}
                        className={`shrink-0 accent-red ${dense ? "h-3.5 w-3.5" : "h-4 w-4"}`}
                      />
                      <span className={`min-w-0 flex-1 break-keep [overflow-wrap:anywhere] ${on ? "font-semibold text-red" : "text-ink-2"}`}>{node.name}</span>
                      {count !== undefined ? (
                        <span className={`shrink-0 text-[11.5px] ${on ? "text-red" : "text-gray-2"}`}>{count.toLocaleString("ko-KR")}</span>
                      ) : (
                        boards === 0 && <span className="shrink-0 text-[11px] text-gray-2">준비 중</span>
                      )}
                    </label>
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </div>

      {!keyword && orgs.length > 0 && hiddenOrgs > 0 && (
        <button
          type="button"
          onClick={() => setShowEmpty((v) => !v)}
          className={`w-full border-t border-line-2 bg-cream text-xs text-gray hover:text-ink-2 ${dense ? "py-2" : "min-h-11 py-2.5"}`}
        >
          아직 공지가 없는 {hiddenOrgs.toLocaleString("ko-KR")}곳 더 보기
        </button>
      )}
      {!keyword && orgs.length > 0 && hiddenOrgs <= 0 && showEmpty && (
        <button
          type="button"
          onClick={() => setShowEmpty(false)}
          className={`w-full border-t border-line-2 bg-cream text-xs text-gray hover:text-ink-2 ${dense ? "py-2" : "min-h-11 py-2.5"}`}
        >
          공지가 없는 곳 숨기기
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
