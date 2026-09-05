"use client";
// 출처: GET /v1/sources + 구독 토글(localStorage)
import { useMemo, useState } from "react";
import type { Source } from "@/lib/types";
import { useSettings } from "@/lib/settings";
import { useSources } from "@/lib/queries";
import { relativeTime } from "@/lib/format";
import { Shell, Box } from "@/components/Shell";
import { IconSearch, MediumIcon } from "@/components/icons";

const STATUS_DOT: Record<string, string> = {
  active: "bg-[#3aa76d]",
  delayed: "bg-[#d9a400]",
  blocked: "bg-red",
  pending: "bg-gray-2",
  paused: "bg-gray-2",
  retired: "bg-gray-2",
};

export default function SourcesPage() {
  const { settings, toggleSource } = useSettings();
  const { sources, loading } = useSources(settings.campus_id);
  const [q, setQ] = useState("");

  const groups = useMemo(() => {
    const filtered = sources.filter((s) => !q || `${s.name} ${s.organization.name}`.includes(q));
    const map = new Map<string, Source[]>();
    for (const s of filtered) map.set(s.organization.name, [...(map.get(s.organization.name) ?? []), s]);
    return [...map.entries()];
  }, [sources, q]);

  const subs = new Set(settings.subscribed_source_ids);

  return (
    <Shell>
      <div className="mb-2.5 flex items-center gap-2 rounded-[10px] border border-line bg-white px-[13px] py-[9px] focus-within:border-navy">
        <IconSearch className="text-gray" width={15} height={15} />
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="기관·학과·학생회 검색" className="flex-1 bg-transparent text-sm outline-none" aria-label="출처 검색" />
      </div>
      <p className="mb-2.5 px-1 text-xs text-gray">구독한 출처의 공지는 소속과 관계없이 &lsquo;내 공지&rsquo;에 함께 나옵니다.</p>
      <Box>
        {loading && <div className="px-4 py-10 text-center text-[13px] text-gray">불러오는 중…</div>}
        {!loading && groups.length === 0 && <div className="px-4 py-10 text-center text-[13px] text-gray">검색 결과가 없습니다</div>}
        {groups.map(([org, items]) => (
          <div key={org}>
            <div className="flex border-y border-line-2 bg-cream px-4 pb-2 pt-2.5 text-[13px] font-bold first:border-t-0">
              {org}
              <small className="ml-auto font-normal text-gray-2">
                구독 {items.filter((s) => subs.has(s.id)).length} / {items.length}
              </small>
            </div>
            {items.map((s) => {
              const on = subs.has(s.id);
              const collecting = s.status.code === "active" || s.status.code === "delayed" || s.status.code === "blocked";
              return (
                <div key={s.id} className="grid grid-cols-[18px_1fr_auto] items-center gap-2 border-b border-line-2 px-4 py-[11px] sm:grid-cols-[18px_1fr_auto_auto] sm:gap-2.5">
                  <MediumIcon code={s.medium.code} width={14} height={14} className="text-gray-2" />
                  <div className="min-w-0 text-[13.5px]">
                    <div className="truncate">{s.name}</div>
                    <small className="block text-xs text-gray">
                      {s.medium.label}
                      {s.initial_window_days ? ` · 최근 ${s.initial_window_days}일치부터 수집` : ""}
                      {s.status.code !== "active" && <span className="sm:hidden"> · {s.status.label}</span>}
                    </small>
                  </div>
                  <span className={`hidden items-center gap-1.5 whitespace-nowrap text-xs sm:inline-flex ${s.status.code === "blocked" ? "text-red" : "text-gray"}`} title={s.status_message ?? undefined}>
                    <span className={`h-1.5 w-1.5 rounded-full ${STATUS_DOT[s.status.code] ?? "bg-gray-2"}`} />
                    {collecting ? `${s.status.label} · ${relativeTime(s.last_success_at)}` : s.status.label}
                  </span>
                  <button onClick={() => toggleSource(s.id)} aria-label={`${s.name} ${on ? "구독 해제" : "구독"}`} aria-pressed={on} className={`relative h-5 w-9 rounded-full transition-colors ${on ? "bg-red" : "bg-[#D6D2CA]"}`}>
                    <span className={`absolute top-0.5 h-4 w-4 rounded-full bg-white shadow transition-all ${on ? "left-[18px]" : "left-0.5"}`} />
                  </button>
                </div>
              );
            })}
          </div>
        ))}
      </Box>
    </Shell>
  );
}
