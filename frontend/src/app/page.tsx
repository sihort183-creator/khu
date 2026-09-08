"use client";
// 전체 공지: GET /v1/notices + 조직·출처 유형 필터 + 검색
// 조직은 Shell의 선택기(좌측 열 / 모바일 접힘 상자)가 저장한 값을 그대로 쓴다.
//
// 이 화면이 첫 화면('/')이다. 2026-09-08 사용자 지시로 '/all' 에서 옮겨 왔고, 옛 주소는
// src/app/all/page.tsx 가 물음표 뒤 값까지 들고 여기로 넘긴다.
// 조직 범위는 '내 공지'와 같은 함수(lib/api.ts 의 organizationScope)를 쓴다.
import { Suspense, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import { useOrganizationSelection, useSettings } from "@/lib/settings";
import { useCatalog, useOrganizations } from "@/lib/queries";
import { campusScopeLabel } from "@/lib/campus";
import { useNoticeFeed } from "@/lib/useNoticeFeed";
import { useCategorySelection } from "@/lib/useCategorySelection";
import { Shell } from "@/components/Shell";
import { CategoryChips } from "@/components/CategoryChips";
import { NoticeList } from "@/components/NoticeList";
import { organizationScopeLabel } from "@/components/OrganizationPicker";
import { IconChevron, IconSearch } from "@/components/icons";

export default function AllPage() {
  return (
    <Suspense>
      <AllFeed />
    </Suspense>
  );
}

function AllFeed() {
  const { settings } = useSettings();
  const params = useSearchParams();
  const catalog = useCatalog();
  const orgs = useOrganizations();
  const categoryCodes = useMemo(() => (catalog?.categories ?? []).map((c) => c.code), [catalog]);
  const cats = useCategorySelection(categoryCodes);
  const org = useOrganizationSelection(orgs);
  const [medium, setMedium] = useState("");
  const [q, setQ] = useState("");
  const [qInput, setQInput] = useState("");
  const qRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (params.get("focus") === "q") qRef.current?.focus();
  }, [params]);

  const feed = useNoticeFeed({
    kind: "list",
    query: {
      campus_id: settings.campus_id ? [settings.campus_id] : [],
      organization_id: org.ids,
      include_descendants: true,
      category_code: cats.codes,
      medium: medium ? [medium] : [],
      q: q || undefined,
      sort: q ? "relevance" : "recent",
    },
  });

  const scope = org.ids.length ? organizationScopeLabel(orgs, org.selected) : campusScopeLabel(catalog?.campuses, settings.campus_id);
  const label = q ? `‘${q}’ 검색 결과` : scope;

  return (
    <Shell categories={{ selected: cats.selected, toggle: cats.toggle }} orgPicker>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          setQ(qInput.trim().slice(0, 100));
        }}
        className="mb-2.5 flex items-center gap-2 rounded-[10px] border border-line bg-card px-[13px] py-[9px] focus-within:border-navy"
      >
        <IconSearch className="text-gray" width={15} height={15} />
        <input ref={qRef} value={qInput} onChange={(e) => setQInput(e.target.value)} placeholder="제목·본문·기관명 검색" className="min-w-0 flex-1 bg-transparent text-sm outline-none" maxLength={100} aria-label="공지 검색" />
        {q && (
          <button
            type="button"
            onClick={() => {
              setQ("");
              setQInput("");
            }}
            className="shrink-0 text-xs text-gray"
          >
            지우기
          </button>
        )}
      </form>

      <div className="no-scrollbar mb-0.5 flex gap-1.5 overflow-x-auto pb-2">
        <Select value={medium} onChange={setMedium} placeholder="출처 유형" options={(catalog?.media ?? []).map((m) => [m.code, m.label])} />
        {org.ids.length > 0 && (
          <button type="button" onClick={org.clear} className="flex-none rounded-lg border border-line bg-card px-[11px] py-1.5 text-[13px] text-ink-2 hover:border-gray-2">
            조직 {org.ids.length}곳 해제
          </button>
        )}
      </div>

      {catalog && <CategoryChips categories={catalog.categories} selected={cats.selected} onToggle={cats.choose} />}
      <NoticeList notices={feed.items} label={label} loading={feed.loading} error={feed.error} hasNext={feed.hasNext} onMore={feed.more} onRetry={feed.retry} />
    </Shell>
  );
}

function Select({ value, onChange, placeholder, options, disabled }: { value: string; onChange: (v: string) => void; placeholder: string; options: [string, string][]; disabled?: boolean }) {
  const on = !!value;
  return (
    <label className={`relative inline-flex flex-none items-center rounded-lg border bg-card text-[13px] ${on ? "border-navy font-medium text-navy" : "border-line text-ink-2"} ${disabled ? "opacity-50" : ""}`}>
      <select value={value} onChange={(e) => onChange(e.target.value)} disabled={disabled} aria-label={placeholder} className="appearance-none bg-transparent py-1.5 pl-[11px] pr-7 outline-none">
        <option value="">{placeholder}</option>
        {options.map(([v, l]) => (
          <option key={v} value={v}>
            {l}
          </option>
        ))}
      </select>
      <IconChevron className="pointer-events-none absolute right-2.5 opacity-60" width={10} height={10} strokeWidth={3} />
    </label>
  );
}
