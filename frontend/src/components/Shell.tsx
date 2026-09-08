"use client";
// 3열 레이아웃. 좌: 조직 선택·주제, 중: 페이지, 우: 내 설정·광고. 1024px 미만은 중앙만.
// 좁은 화면에서는 좌측 열이 통째로 사라져 조직을 고를 방법이 없었다. 그래서 같은
// 선택기를 본문 맨 위에 접힌 상자로 한 번 더 둔다(둘은 같은 저장값을 본다).
import { useState } from "react";
import type { Coded } from "@/lib/types";
import { useOrganizationSelection } from "@/lib/settings";
import { useCatalog, useOrganizations } from "@/lib/queries";
import { categoryStyle } from "@/lib/category";
import { Ad } from "./Ad";
import { MySettingsBody } from "./MySettings";
import { IconChevron } from "./icons";
import { OrganizationPicker, organizationScopeLabel } from "./OrganizationPicker";

interface Props {
  children: React.ReactNode;
  /** 주제 선택을 사이드바와 공유할 때 */
  categories?: { selected: Set<string>; toggle: (code: string) => void };
  /** 공지 목록 화면에서만 본문 위 조직 상자를 띄운다(출처·연락처에는 필요 없다) */
  orgPicker?: boolean;
}

export function Shell({ children, categories, orgPicker }: Props) {
  const catalog = useCatalog();
  const orgs = useOrganizations();
  const org = useOrganizationSelection(orgs);
  const [mobileOpen, setMobileOpen] = useState(false);

  const scope = organizationScopeLabel(orgs, org.selected);

  return (
    <div className="mx-auto grid max-w-[1180px] grid-cols-[minmax(0,1fr)] gap-4 px-4 pb-10 pt-4 lg:grid-cols-[220px_minmax(0,1fr)_240px] lg:gap-5 lg:pt-5">
      <aside className="hidden lg:sticky lg:top-[112px] lg:block lg:self-start">
        <Box title="조직">
          <OrganizationPicker selected={org.selected} onToggle={org.toggle} onClear={org.clear} dense />
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

      <main className="min-w-0">
        {orgPicker && (
          <Box className="mb-2.5 lg:hidden">
            <button
              type="button"
              aria-expanded={mobileOpen}
              onClick={() => setMobileOpen((v) => !v)}
              className="flex min-h-11 w-full items-center gap-2 px-3.5 py-2.5 text-left"
            >
              <span className="shrink-0 text-[13px] font-bold">조직</span>
              <span className="min-w-0 flex-1 truncate text-[13px] text-ink-2">{scope}</span>
              <span className="shrink-0 text-xs text-gray">{mobileOpen ? "닫기" : "고르기"}</span>
              <IconChevron className={`shrink-0 text-gray transition-transform ${mobileOpen ? "rotate-180" : ""}`} width={12} height={12} strokeWidth={3} />
            </button>
            {mobileOpen && (
              <div className="border-t border-line-2">
                <OrganizationPicker selected={org.selected} onToggle={org.toggle} onClear={org.clear} />
              </div>
            )}
          </Box>
        )}
        {children}
      </main>

      <aside className="hidden lg:sticky lg:top-[112px] lg:block lg:self-start">
        {/* 알맹이는 헤더의 사람 아이콘 패널과 한 벌을 쓴다(MySettings.tsx) */}
        <Box title="내 설정">
          <MySettingsBody />
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
    <div className={`overflow-hidden rounded-box border border-line bg-card shadow-box ${className}`}>
      {title && <div className="flex items-center border-b border-line-2 px-3.5 py-[11px] text-[13px] font-bold">{title}</div>}
      {children}
    </div>
  );
}
