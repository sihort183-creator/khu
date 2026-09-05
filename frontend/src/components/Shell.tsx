"use client";
// 3열 레이아웃. 좌: 조직 트리·주제, 중: 페이지, 우: 내 설정·광고. 1024px 미만은 중앙만.
import type { Coded } from "@/lib/types";
import { useSettings } from "@/lib/settings";
import { useCatalog, useOrganizations } from "@/lib/queries";
import { categoryStyle } from "@/lib/category";
import { Ad } from "./Ad";

interface Props {
  children: React.ReactNode;
  /** 주제 선택을 사이드바와 공유할 때 */
  categories?: { selected: Set<string>; toggle: (code: string) => void };
}

export function Shell({ children, categories }: Props) {
  const { settings, update } = useSettings();
  const catalog = useCatalog();
  const orgs = useOrganizations();

  const byId = (id: string | null) => orgs.find((o) => o.id === id);
  const campus = catalog?.campuses.find((c) => c.id === settings.campus_id);
  const college = byId(settings.college_id);
  const dept = byId(settings.department_id);
  const campusOrg = orgs.find((o) => o.type.code === "campus" && o.campus_id === settings.campus_id);
  const subCount = settings.subscribed_source_ids.length;

  return (
    <div className="mx-auto grid max-w-[1180px] grid-cols-[minmax(0,1fr)] gap-4 px-4 pb-10 pt-4 lg:grid-cols-[220px_minmax(0,1fr)_240px] lg:gap-5 lg:pt-5">
      <aside className="hidden lg:block lg:sticky lg:top-[112px] lg:self-start">
        <Box title="조직">
          <div className="py-1.5 text-[13px]">
            <TreeRow label="경희대학교" count="전체" />
            <TreeRow label={campus?.name ?? "캠퍼스"} depth={1} />
            {college && <TreeRow label={college.name} depth={2} />}
            {dept && <TreeRow label={dept.name} depth={3} mine />}
            {!dept && (
              <button onClick={() => update({ onboarded: false })} className="mx-1.5 mt-1 w-[calc(100%-12px)] rounded-md border border-dashed border-line px-2 py-1.5 text-left text-xs text-gray hover:border-gray">
                단과대·학과 설정하기
              </button>
            )}
            {campusOrg && orgs.filter((o) => o.parent_id === campusOrg.id && o.type.code === "office").slice(0, 3).map((o) => <TreeRow key={o.id} label={o.name} depth={2} />)}
          </div>
        </Box>
        {categories && catalog && (
          <Box title="주제" className="mt-3">
            <div className="px-3.5 pb-2 pt-1.5">
              {catalog.categories.map((c: Coded) => (
                <label key={c.code} className="flex cursor-pointer items-center gap-2 py-1 text-[13px] text-ink-2">
                  <input type="checkbox" className="h-3.5 w-3.5 accent-red" checked={categories.selected.size === 0 || categories.selected.has(c.code)} onChange={() => categories.toggle(c.code)} />
                  <span className="rounded-[5px] px-1.5 text-[11.5px] font-semibold leading-[19px]" style={{ background: categoryStyle(c.code).bg, color: categoryStyle(c.code).fg }}>
                    {c.label}
                  </span>
                </label>
              ))}
            </div>
          </Box>
        )}
      </aside>

      <main className="min-w-0">{children}</main>

      <aside className="hidden lg:block lg:sticky lg:top-[112px] lg:self-start">
        <Box title="내 설정">
          <div className="px-3.5 py-3 text-[13px]">
            <dl className="grid grid-cols-[56px_1fr] gap-y-1">
              <dt className="text-gray">캠퍼스</dt>
              <dd className="font-medium">{campus?.name.replace("캠퍼스", "") ?? "-"}</dd>
              <dt className="text-gray">단과대</dt>
              <dd className="font-medium">{college?.name ?? <span className="font-normal text-gray-2">미설정</span>}</dd>
              <dt className="text-gray">학과</dt>
              <dd className="font-medium">{dept?.name ?? <span className="font-normal text-gray-2">미설정</span>}</dd>
              <dt className="text-gray">구독</dt>
              <dd className="font-medium">{subCount ? `출처 ${subCount}개` : <span className="font-normal text-gray-2">없음</span>}</dd>
            </dl>
            <button onClick={() => update({ onboarded: false })} className="mt-2.5 w-full rounded-lg border border-line bg-white py-1.5 text-[13px] text-ink-2 hover:border-gray">
              변경
            </button>
          </div>
        </Box>
        <div className="mt-3">
          <Ad vertical />
        </div>
      </aside>
    </div>
  );
}

export function Box({ title, children, className = "" }: { title?: string; children: React.ReactNode; className?: string }) {
  return (
    <div className={`overflow-hidden rounded-box border border-line bg-white shadow-box ${className}`}>
      {title && <div className="flex items-center border-b border-line-2 px-3.5 py-[11px] text-[13px] font-bold">{title}</div>}
      {children}
    </div>
  );
}

function TreeRow({ label, depth = 0, count, mine }: { label: string; depth?: number; count?: string; mine?: boolean }) {
  return (
    <div className={`mx-1.5 flex items-center rounded-md py-1.5 pr-2 ${mine ? "bg-[#FBF0F0] font-bold text-red" : "text-ink-2 hover:bg-bg"}`} style={{ paddingLeft: 8 + depth * 12 }}>
      {label}
      {count && <span className="ml-auto text-xs text-gray-2">{count}</span>}
    </div>
  );
}
