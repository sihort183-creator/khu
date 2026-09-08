"use client";
// 조직 다중 선택 — 계층 트리. 경희대학교 → 국제·서울캠퍼스·기타 → 단과대 → 학과.
//
// 캠퍼스 안은 단과대(ㄱㄴㄷ) 묶음이 먼저, 단과대에 속하지 않는 부서·기관(ㄱㄴㄷ)이 나중이다.
// 두 묶음 사이는 작은 소제목과 얇은 선으로만 가른다 — 색 면을 만들지 않는다.
// 캠퍼스를 하나도 모르는 조직은 맨 아래 '기타'에 모인다. 자세한 규칙은 lib/orgTree.ts.
//
// 맨 위 '경희대학교' 줄은 테두리·배경 없는 보통 줄이다. 맨 위라는 것과 골라져 있다는
// 것만 글자 굵기·색·구분선으로 드러낸다(사용자 지적: "면으로 들어오는 디자인").
import { useEffect, useMemo, useRef, useState } from "react";
import type { Organization } from "@/lib/types";
import { useCatalog, useOrganizationNoticeCounts, useOrganizations } from "@/lib/queries";
import { useSettings } from "@/lib/settings";
import { buildOrgTree, campusKey, ROOT_KEY, type OrgGroup, type OrgNode } from "@/lib/orgTree";
import { IconChevron, IconSearch } from "./icons";

interface Props {
  selected: Set<string>;
  onToggle: (id: string) => void;
  onClear: () => void;
  /** true면 데스크톱 좌측 열(220px)용으로 촘촘하게. false면 모바일 본문용 44px 행. */
  dense?: boolean;
}

/**
 * 화면에 세울 한 줄. 트리를 펼친 순서대로 늘어놓은 것이다.
 * caption 은 고를 수 없는 소제목("단과대" / "부서·기관")이다.
 */
type Row =
  | { type: "caption"; key: string; text: string; depth: number }
  | { type: "node"; key: string; node: OrgNode; hasChildren: boolean; open: boolean; covered: boolean };

const GROUP_LABEL: Record<OrgGroup, string> = { college: "단과대", other: "부서·기관" };

function matches(node: OrgNode, keyword: string) {
  return node.name.toLowerCase().includes(keyword);
}

/**
 * 트리에 줄이 생기는 조직 수. 별칭은 줄이 없으므로 세지 않는다.
 * 캠퍼스가 둘 다 붙은 조직은 두 캠퍼스에 그려지므로 id 로 겹치는 것을 지운다 —
 * 안 그러면 "더 보기"의 곳 수가 실제보다 부풀어, 눌러도 나오지 않는 곳이 생긴다.
 */
function orgIdsIn(node: OrgNode, out: Set<string> = new Set()): Set<string> {
  if (node.id) out.add(node.id);
  for (const child of node.children) orgIdsIn(child, out);
  return out;
}

export function OrganizationPicker({ selected, onToggle, onClear, dense = false }: Props) {
  const orgs = useOrganizations();
  const catalog = useCatalog();
  const { settings } = useSettings();
  const [q, setQ] = useState("");
  const [open, setOpen] = useState<Set<string> | null>(null);
  // 484곳 중 349곳은 아직 붙은 게시판이 없다. 기본으로 감춰야 목록을 훑을 수 있다.
  const [showEmpty, setShowEmpty] = useState(false);

  // 공지 건수는 색인 전체(조각 12개, 압축해서 1.1MB)를 읽어야 나온다. 선택기가 실제로
  // 자리를 차지할 때만 읽는다. 좁은 화면에서 좌측 열은 display:none 이라 자리가 없고
  // (offsetParent 없음), 접힌 모바일 상자는 아예 그려지지도 않는다. 화면 폭이 바뀌면
  // 다시 살핀다.
  // (IntersectionObserver 를 쓰지 않는 이유: 화면이 그려지지 않는 상황에서는 한 번도
  //  불리지 않아 수치가 영영 안 붙는다.)
  //
  // 그리고 첫 화면이 다 그려진 뒤에 읽는다. 예전에는 setTimeout(0) 이라 공지 목록과
  // 동시에 출발했고, 조각 12개가 목록 파일과 회선을 나눠 쓰며 첫 화면을 늦췄다
  // (2026-09-08 실측: 첫 진입 9.1초). 수치는 조금 늦게 붙어도 되는 값이다.
  const boxRef = useRef<HTMLDivElement>(null);
  const [laidOut, setLaidOut] = useState(false);
  useEffect(() => {
    if (laidOut) return;
    let idle = 0;
    let timer = 0;
    const check = () => {
      const el = boxRef.current;
      if (el && el.offsetParent !== null && el.getBoundingClientRect().width > 0) setLaidOut(true);
    };
    // 한가할 때 살핀다. requestIdleCallback 이 없는 브라우저(사파리 옛 판)는 시간으로 민다.
    const later = () => {
      if (typeof requestIdleCallback === "function") idle = requestIdleCallback(check, { timeout: 3000 });
      else timer = window.setTimeout(check, 1200);
    };
    const start = () => { timer = window.setTimeout(later, 600); };
    if (document.readyState === "complete") start();
    else window.addEventListener("load", start, { once: true });
    window.addEventListener("resize", check);
    return () => {
      if (idle && typeof cancelIdleCallback === "function") cancelIdleCallback(idle);
      clearTimeout(timer);
      window.removeEventListener("load", start);
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
      // 단과대는 게시판도 공지도 없을 때조차 늘 보인다(사용자 결정 2026-09-07).
      // "단과대는 공지가 아예 안 올라와도 목록에는 있어야 한다"는 요구다. 지금 화면에서
      // 사라지던 단과대가 13곳이었다(docs/단과대_학과_수집_전수조사.md 6절).
      if (node.organization?.type.code === "college") return true;
      if (showEmpty) return true;
      // 폐쇄·대기 게시판은 세지 않는다. 정경대학은 게시판 4개가 전부 폐쇄인데도
      // source_count 로는 4라서 목록에 남았고, 골라 보면 0건이었다.
      return node.activeSourceCount > 0 || (counts?.byKey.get(node.countKey) ?? 0) > 0;
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
      if (node.kind === "campus" && node.countKey === campusKey(settings.campus_id ?? "")) keys.add(node.key);
      if (hit) for (const key of path) keys.add(key);
      return hit;
    };
    walk(tree, []);
    return keys;
  }, [tree, selected, settings.campus_id]);

  const openKeys = open ?? defaultOpen;

  const rows = useMemo(() => {
    const out: Row[] = [];
    const walk = (node: OrgNode, covered: boolean) => {
      const hasChildren = node.children.length > 0;
      // 검색 중에는 결과가 바로 보여야 한다. 접힌 채로 두면 못 찾은 것처럼 보인다.
      const isOpen = node.kind === "root" || !!keyword || openKeys.has(node.key);
      out.push({ type: "node", key: node.key, node, hasChildren, open: isOpen && hasChildren, covered });
      if (!isOpen) return;
      // 캠퍼스 안에서만 묶음을 가른다. 한 묶음뿐이면 소제목이 군더더기가 되므로 붙이지 않는다.
      const split =
        (node.kind === "campus" || node.kind === "other")
        && node.children.some((c) => c.group === "college")
        && node.children.some((c) => c.group === "other");
      const childCovered = covered || (!!node.id && selected.has(node.id));
      let group: OrgGroup | null = null;
      for (const child of node.children) {
        if (split && child.group && child.group !== group) {
          group = child.group;
          out.push({ type: "caption", key: `${node.key}#${group}`, text: GROUP_LABEL[group], depth: child.depth });
        }
        walk(child, childCovered);
      }
    };
    walk(pruned, false);
    return out;
  }, [pruned, openKeys, keyword, selected]);

  // 한 번에 여러 줄이 열리고 닫혀도 앞의 결과 위에 쌓이도록 갱신 함수 꼴로 쓴다.
  const toggleOpen = (key: string) =>
    setOpen((prev) => {
      const next = new Set(prev ?? defaultOpen);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  /**
   * 체크는 줄마다 따로 논다. 단과대를 체크해도 하위 학과가 저절로 체크되지는 않는다.
   * 상위를 고르면 하위 공지가 이미 전부 들어오기 때문이다(홈이 자손까지 넓힌다).
   * 대신 체크하는 순간 그 줄을 펼쳐, 학과를 따로 고를 수 있다는 것을 눈으로 보게 한다.
   */
  const toggleOrg = (node: OrgNode) => {
    if (!node.id) return;
    const turningOn = !selected.has(node.id);
    onToggle(node.id);
    if (turningOn && node.children.length > 0) setOpen((prev) => new Set(prev ?? defaultOpen).add(node.key));
  };

  // 고른 조직은 **캠퍼스·검색·접힘과 무관하게** 하나도 빠짐없이 칩이 된다. 칩이 곧
  // 체크를 푸는 유일한 손잡이라, 한 곳이라도 빠지면 목록은 좁아졌는데 되돌릴 길이 없다.
  // 목록 머리글(organizationScopeLabel)도 같은 함수를 써서 곳 수가 어긋나지 않게 한다.
  const selectedOrgs = selectedOrganizations(orgs, selected);
  // 접혀 있는 것은 숨긴 것이 아니다. 남긴 가지에 든 조직 수로 센다.
  // 별칭 조직은 애초에 트리에 줄이 없다. orgs.length 로 빼면 "숨은 곳"이 부풀어
  // 눌러도 나오지 않는 곳이 생긴다. 그래서 자르기 전 트리의 줄 수로 센다.
  const totalOrgs = useMemo(() => orgIdsIn(tree).size, [tree]);
  const keptOrgs = useMemo(() => orgIdsIn(pruned).size, [pruned]);
  const hiddenOrgs = totalOrgs - keptOrgs;

  // 촘촘한 좌측 열(220px)에서는 들여쓰기와 여백을 줄여야 이름이 덜 접힌다.
  const step = dense ? 9 : 12;
  const twist = dense ? "w-5" : "w-6";
  const rowMin = dense ? "min-h-[30px]" : "min-h-11";
  const padX = dense ? "pl-2.5 pr-2" : "pl-3 pr-3";

  return (
    <div ref={boxRef} className="text-[13px]">
      {orgs.length > 0 && selectedOrgs.length > 0 && (
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
              // 묶음 소제목. 고를 수 없는 안내 줄이라 얇은 선과 작은 글자만 쓴다.
              if (row.type === "caption") {
                return (
                  <li key={row.key} className="mt-1 border-t border-line-2 pt-1.5 first:mt-0 first:border-t-0">
                    <div className={`${padX} pb-0.5 text-[11px] font-semibold tracking-wide text-gray-2`} style={{ paddingLeft: row.depth * step + 10 }}>
                      {row.text}
                    </div>
                  </li>
                );
              }

              const { node } = row;
              const indent = node.depth * step;
              const count = counts?.byKey.get(node.countKey);

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

              // 캠퍼스·기타 줄은 고르는 자리가 아니라 묶음이다. 줄 전체가 펼침 단추다.
              if (node.kind === "campus" || node.kind === "other") {
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

              // 별칭 조직 것까지 합친 수. 폐쇄·대기만 남은 조직은 0이 되어 "준비 중"이 붙는다.
              const boards = node.activeSourceCount;
              return (
                <li key={node.key}>
                  <div className={`flex items-center ${padX} ${rowMin} ${dense ? "py-1" : "py-1.5"} ${on ? "bg-tint-red-2" : "hover:bg-bg"}`}>
                    <span aria-hidden className="shrink-0" style={{ width: indent }} />
                    {twistBtn}
                    <label className="flex min-w-0 flex-1 cursor-pointer items-center gap-2 self-stretch py-0.5">
                      <input
                        type="checkbox"
                        checked={on}
                        onChange={() => toggleOrg(node)}
                        className={`shrink-0 accent-red ${dense ? "h-3.5 w-3.5" : "h-4 w-4"}`}
                      />
                      <span className={`min-w-0 flex-1 break-keep [overflow-wrap:anywhere] ${on ? "font-semibold text-red" : "text-ink-2"}`}>{node.name}</span>
                      {/* 위 칸을 이미 골랐으면 이 줄의 공지도 함께 나온다. 따로 체크할 수도 있다. */}
                      {row.covered && !on && <span className="shrink-0 text-[11px] text-gray-2">포함됨</span>}
                      {count !== undefined ? (
                        <span className={`shrink-0 text-[11.5px] ${on ? "text-red" : "text-gray-2"}`}>{count.toLocaleString("ko-KR")}</span>
                      ) : (
                        // '포함됨'과 '준비 중'을 나란히 붙이면 220px 열에서 이름이 세 줄로 접힌다.
                        boards === 0 && !row.covered && <span className="shrink-0 text-[11px] text-gray-2">준비 중</span>
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

/**
 * 고른 조직을 고른 순서대로. 칩 줄과 머리글이 같은 목록을 봐야 "외 3곳"인데 칩은 둘 같은
 * 어긋남이 안 생긴다. 명부에 없는 id 도 버리지 않는다 — 버리면 화면에서 지울 수가 없다.
 */
export function selectedOrganizations(orgs: Organization[], selected: Set<string>): { id: string; name: string }[] {
  const byId = new Map(orgs.map((o) => [o.id, o]));
  return [...selected].map((id) => ({ id, name: byId.get(id)?.name ?? "이름 없는 조직" }));
}

/** 선택 상태를 한 줄로 요약한다. 접힌 상자의 제목과 목록 머리글이 같은 문구를 쓴다. */
export function organizationScopeLabel(orgs: Organization[], selected: Set<string>): string {
  if (selected.size === 0) return "경희대학교 전체";
  // 조직 명부가 아직 안 왔으면 이름 대신 곳 수만 말한다. "이름 없는 조직 외 3곳"은 거짓말이다.
  if (orgs.length === 0) return `조직 ${selected.size}곳`;
  const names = selectedOrganizations(orgs, selected).map((o) => o.name);
  return names.length === 1 ? names[0] : `${names[0]} 외 ${names.length - 1}곳`;
}
