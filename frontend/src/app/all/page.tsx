"use client";
// 전체 공지: GET /v1/notices + 조직·출처 유형 필터 + 검색
import { Suspense, useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import { useSettings } from "@/lib/settings";
import { useCatalog, useOrganizations } from "@/lib/queries";
import { useNoticeFeed } from "@/lib/useNoticeFeed";
import { useCategorySelection } from "@/lib/useCategorySelection";
import { Shell } from "@/components/Shell";
import { CategoryChips } from "@/components/CategoryChips";
import { NoticeList } from "@/components/NoticeList";
import { IconChevron, IconSearch } from "@/components/icons";

export default function AllPage() {
  const { settings } = useSettings();
  return (
    <Suspense>
      {/* 캠퍼스가 바뀌면 조직 필터를 초기화해야 하므로 통째로 다시 마운트 */}
      <AllFeed key={settings.campus_id ?? "none"} />
    </Suspense>
  );
}

function AllFeed() {
  const { settings } = useSettings();
  const params = useSearchParams();
  const catalog = useCatalog();
  const orgs = useOrganizations();
  const cats = useCategorySelection();
  const [college, setCollege] = useState("");
  const [dept, setDept] = useState("");
  const [office, setOffice] = useState("");
  const [medium, setMedium] = useState("");
  const [q, setQ] = useState("");
  const [qInput, setQInput] = useState("");
  const qRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (params.get("focus") === "q") qRef.current?.focus();
  }, [params]);

  const campusOrg = orgs.find((o) => o.type.code === "campus" && o.campus_id === settings.campus_id);
  const colleges = orgs.filter((o) => o.type.code === "college" && o.parent_id === campusOrg?.id);
  const depts = orgs.filter((o) => o.type.code === "department" && o.parent_id === college);
  const offices = orgs.filter((o) => (o.type.code === "office" || o.type.code === "institute" || o.type.code === "council") && (o.campus_id === settings.campus_id || o.campus_id === null));

  const orgFilter = dept || college || office;
  const feed = useNoticeFeed({
    kind: "list",
    query: {
      campus_id: settings.campus_id ? [settings.campus_id] : [],
      organization_id: orgFilter ? [orgFilter] : [],
      include_descendants: true,
      category_code: cats.codes,
      medium: medium ? [medium] : [],
      q: q || undefined,
      sort: q ? "relevance" : "recent",
    },
  });

  const campusName = catalog?.campuses.find((c) => c.id === settings.campus_id)?.name ?? "";
  const label = q ? `‘${q}’ 검색 결과` : `${campusName} 전체`;

  return (
    <Shell categories={{ selected: cats.selected, toggle: cats.toggle }}>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          setQ(qInput.trim().slice(0, 100));
        }}
        className="mb-2.5 flex items-center gap-2 rounded-[10px] border border-line bg-white px-[13px] py-[9px] focus-within:border-navy"
      >
        <IconSearch className="text-gray" width={15} height={15} />
        <input ref={qRef} value={qInput} onChange={(e) => setQInput(e.target.value)} placeholder="제목·본문·기관명 검색" className="flex-1 bg-transparent text-sm outline-none" maxLength={100} aria-label="공지 검색" />
        {q && (
          <button
            type="button"
            onClick={() => {
              setQ("");
              setQInput("");
            }}
            className="text-xs text-gray"
          >
            지우기
          </button>
        )}
      </form>

      <div className="no-scrollbar flex gap-1.5 overflow-x-auto pb-2">
        <Select
          value={college}
          onChange={(v) => {
            setCollege(v);
            setDept("");
            setOffice("");
          }}
          placeholder="단과대"
          options={colleges.map((o) => [o.id, o.name])}
        />
        <Select value={dept} onChange={setDept} placeholder="학과" options={depts.map((o) => [o.id, o.name])} disabled={!college} />
        <Select
          value={office}
          onChange={(v) => {
            setOffice(v);
            setCollege("");
            setDept("");
          }}
          placeholder="기관·사업단"
          options={offices.map((o) => [o.id, o.name])}
        />
        <Select value={medium} onChange={setMedium} placeholder="출처 유형" options={(catalog?.media ?? []).map((m) => [m.code, m.label])} />
      </div>

      {catalog && <CategoryChips categories={catalog.categories} selected={cats.selected} onToggle={cats.toggle} />}
      <NoticeList notices={feed.items} label={label} loading={feed.loading} error={feed.error} hasNext={feed.hasNext} onMore={feed.more} onRetry={feed.retry} />
    </Shell>
  );
}

function Select({ value, onChange, placeholder, options, disabled }: { value: string; onChange: (v: string) => void; placeholder: string; options: [string, string][]; disabled?: boolean }) {
  const on = !!value;
  return (
    <label className={`relative inline-flex flex-none items-center rounded-lg border bg-white text-[13px] ${on ? "border-navy font-medium text-navy" : "border-line text-ink-2"} ${disabled ? "opacity-50" : ""}`}>
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
