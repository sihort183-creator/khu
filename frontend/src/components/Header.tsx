"use client";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useSettings } from "@/lib/settings";
import { useCatalog } from "@/lib/queries";
import { campusOptions } from "@/lib/campus";
import { IconSearch, IconUser } from "./icons";

// 2026-09-08 사용자 지시: "전체 공지를 기본으로, 가장 좌측으로 밀고." 그래서 전체 공지가
// '/'(첫 화면)이고 맨 왼쪽이다. 공지 상세(/notices/...)는 어느 탭에서 들어가든 같은 글이라
// 기본 탭인 전체 공지에 걸어 둔다.
const TABS = [
  { href: "/", label: "전체 공지" },
  { href: "/mine", label: "내 공지" },
  { href: "/sources", label: "출처" },
  { href: "/contacts", label: "연락처" },
];

export function Header() {
  const pathname = usePathname();
  const router = useRouter();
  const { settings, update } = useSettings();
  // 목록을 받기 전에 '공통' 하나만 뜨는 어색한 순간을 막는다(예전에도 받기 전엔 토글이 없었다).
  const catalogCampuses = useCatalog()?.campuses;
  const campuses = catalogCampuses?.length ? campusOptions(catalogCampuses) : [];

  const active = (href: string) => (href === "/" ? pathname === "/" || pathname.startsWith("/notices") : pathname.startsWith(href));

  return (
    <header className="sticky top-0 z-20 border-b border-line bg-white/95 backdrop-blur">
      <div className="h-1" style={{ background: "linear-gradient(90deg,var(--gold) 0 16%,var(--red) 16% 84%,var(--navy) 84%)" }} />
      <div className="mx-auto flex h-14 max-w-[1180px] items-center gap-3 px-4 max-[480px]:h-[50px] max-[480px]:gap-1.5">
        <Link href="/" className="flex min-w-0 items-center gap-2">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src="https://www.khu.ac.kr/upload/cross/images/000001/imgSub0401.jpg" alt="" className="h-[30px] w-auto max-[480px]:h-[26px]" />
          <b className="truncate text-[19px] font-bold tracking-tight text-navy max-[480px]:text-[17px] max-[360px]:hidden">경희공지</b>
          <span className="ml-0.5 text-xs text-gray max-[480px]:hidden">경희대학교 통합 공지</span>
        </Link>

        <div className="ml-auto flex shrink-0 rounded-full border border-line bg-bg p-0.5" role="group" aria-label="캠퍼스">
          {campuses.map((c) => {
            const on = settings.campus_id === c.id;
            return (
              <button
                key={c.id ?? "all"}
                onClick={() => update({ campus_id: c.id })}
                aria-pressed={on}
                title={c.hint || c.name}
                // 알약은 26px밖에 안 돼 손가락으로 누르기 좁다. before:로 모양은 그대로 두고
                // 누르는 자리만 위아래로 44px까지 넓힌다(헤더 높이 안에 들어간다).
                className={`relative whitespace-nowrap rounded-full px-3 py-1 text-[13px] before:absolute before:inset-x-0 before:top-1/2 before:h-11 before:-translate-y-1/2 ${
                  on ? "bg-navy text-white" : "text-gray"
                }`}
              >
                {c.short}
              </button>
            );
          })}
        </div>
        <button className="grid h-[34px] w-[34px] shrink-0 place-items-center rounded-lg text-ink-2 hover:bg-bg" title="검색" onClick={() => router.push("/?focus=q")}>
          <IconSearch width={19} height={19} />
        </button>
        <button className="grid h-[34px] w-[34px] shrink-0 place-items-center rounded-lg text-ink-2 hover:bg-bg" title="내 학과 설정" onClick={() => update({ onboarded: false })}>
          <IconUser width={19} height={19} />
        </button>
      </div>
      <nav className="border-t border-line-2">
        <div className="no-scrollbar mx-auto flex max-w-[1180px] overflow-x-auto px-2">
          {TABS.map((t) => (
            <Link
              key={t.href}
              href={t.href}
              className={`-mb-px whitespace-nowrap border-b-2 px-3 pb-[9px] pt-[11px] text-[15px] transition-colors ${
                active(t.href) ? "border-red font-bold text-red" : "border-transparent text-gray hover:text-ink"
              }`}
            >
              {t.label}
            </Link>
          ))}
        </div>
      </nav>
    </header>
  );
}
