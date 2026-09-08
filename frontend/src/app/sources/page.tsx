"use client";
// 출처: GET /v1/sources + 구독 토글(localStorage)
//
// 2026-09-08 사용자 지시: 이 화면의 말은 전부 학생의 말이어야 한다. 그래서
//   - 폐쇄(retired)된 게시판은 목록에서 뺀다. 구독 중이던 것만 "더 이상 수집하지 않음"으로
//     남긴다 — 안 남기면 구독을 풀 손잡이가 사라진다.
//   - 대기(pending)는 "준비 중" 한 마디.
//   - 살아 있는 게시판(active)은 상태 글자 없이 매체 아이콘 + 마지막으로 확인한 시각만.
//   - 연결이 막힌 것(blocked·delayed·paused)은 "잠시 연결 안 됨".
//   - 백필·초기 범위 문구(초기 수집 전 / 이후 범위 / 전체 확인)는 전부 없앴다. 공개 파일
//     (sources.json)에 backfill_status·initial_window_start 가 여전히 있지만 학생이 알
//     필요가 없는 운영 값이다.
// 화면이 쓰는 값은 status.code 와 last_success_at 둘뿐이고, 둘 다 공개 파일에 있다
// (2026-09-08 실측: 게시판 514개 = active 451 · pending 47 · retired 16,
//  active 451개 전부 last_success_at 이 있고 pending 은 하나도 없다).
import { useMemo, useState } from "react";
import type { Source } from "@/lib/types";
import { useSettings } from "@/lib/settings";
import { useSources } from "@/lib/queries";
import { lastCheckedLabel } from "@/lib/format";
import { Shell, Box } from "@/components/Shell";
import { IconSearch, MediumIcon } from "@/components/icons";

/** 살아 있는 게시판인가. 폐쇄된 것은 목록에서 뺀다. */
function isLive(s: Source) {
  return s.status.code !== "retired";
}

/** 이름 옆 작은 줄에 적을 한마디. 살아 있는 게시판은 시각만 적는다. */
function stateLine(s: Source): { text: string; tone: string } {
  switch (s.status.code) {
    case "active":
      return { text: lastCheckedLabel(s.last_success_at), tone: "text-gray" };
    case "pending":
      return { text: "준비 중", tone: "text-gray-2" };
    case "retired":
      return { text: "더 이상 수집하지 않음", tone: "text-gray-2" };
    default:
      // delayed · blocked · paused. 왜 막혔는지는 학생이 어쩔 수 없는 일이다.
      return { text: "잠시 연결 안 됨", tone: "text-warn-ink" };
  }
}

export default function SourcesPage() {
  const { settings, toggleSource } = useSettings();
  const { sources, loading } = useSources(settings.campus_id);
  const [q, setQ] = useState("");

  const subs = useMemo(() => new Set(settings.subscribed_source_ids), [settings.subscribed_source_ids]);

  const groups = useMemo(() => {
    const filtered = sources.filter(
      (s) => (isLive(s) || subs.has(s.id)) && (!q || `${s.name} ${s.organization.name}`.includes(q)),
    );
    const map = new Map<string, Source[]>();
    for (const s of filtered) map.set(s.organization.name, [...(map.get(s.organization.name) ?? []), s]);
    // 살아 있는 게시판을 먼저, 남겨 둔 폐쇄 게시판을 뒤에.
    for (const [, items] of map) items.sort((a, b) => Number(isLive(b)) - Number(isLive(a)));
    return [...map.entries()];
  }, [sources, q, subs]);

  return (
    <Shell>
      <div className="mb-2.5 flex items-center gap-2 rounded-[10px] border border-line bg-card px-[13px] py-[9px] focus-within:border-navy">
        <IconSearch className="text-gray" width={15} height={15} />
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="기관·학과·학생회 검색" className="flex-1 bg-transparent text-sm outline-none" aria-label="출처 검색" />
      </div>
      <p className="mb-2.5 px-1 text-xs text-gray">구독한 출처의 공지는 소속과 관계없이 &lsquo;내 공지&rsquo;에 함께 나옵니다.</p>
      <Box>
        {loading && <div className="px-4 py-10 text-center text-[13px] text-gray">불러오는 중…</div>}
        {!loading && groups.length === 0 && <div className="px-4 py-10 text-center text-[13px] text-gray">검색 결과가 없습니다</div>}
        {groups.map(([org, items]) => {
          // 묶음 제목 옆 수는 살아 있는 게시판만 센다. 폐쇄된 줄은 분모에도 분자에도 없다.
          const live = items.filter(isLive);
          return (
            <div key={org}>
              <div className="flex border-y border-line-2 bg-cream px-4 pb-2 pt-2.5 text-[13px] font-bold first:border-t-0">
                {org}
                {live.length > 0 && (
                  <small className="ml-auto font-normal text-gray-2">
                    구독 {live.filter((s) => subs.has(s.id)).length} / {live.length}
                  </small>
                )}
              </div>
              {items.map((s) => {
                const on = subs.has(s.id);
                const state = stateLine(s);
                return (
                  <div key={s.id} className="grid grid-cols-[18px_1fr_auto] items-center gap-2.5 border-b border-line-2 px-4 py-[11px]">
                    <MediumIcon code={s.medium.code} width={14} height={14} className="text-gray-2" />
                    <div className="min-w-0 text-[13.5px]">
                      <div className="truncate">{s.name}</div>
                      <small className={`block text-xs ${state.tone}`}>{state.text}</small>
                    </div>
                    <button
                      onClick={() => toggleSource(s.id)}
                      aria-label={`${s.name} ${on ? "구독 해제" : "구독"}`}
                      aria-pressed={on}
                      // 알약은 20px 이라 손가락에 좁다. before: 로 누르는 자리만 44px 로 넓힌다.
                      className={`relative h-5 w-9 rounded-full transition-colors before:absolute before:-inset-x-1 before:top-1/2 before:h-11 before:-translate-y-1/2 ${on ? "bg-red-fill" : "bg-switch-off"}`}
                    >
                      <span className={`absolute top-0.5 h-4 w-4 rounded-full bg-white shadow transition-all ${on ? "left-[18px]" : "left-0.5"}`} />
                    </button>
                  </div>
                );
              })}
            </div>
          );
        })}
      </Box>
    </Shell>
  );
}
