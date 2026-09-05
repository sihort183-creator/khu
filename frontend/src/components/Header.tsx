"use client";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useSettings } from "@/lib/settings";
import { useCatalog } from "@/lib/queries";
import { IconSearch, IconUser } from "./icons";

const TABS = [
  { href: "/", label: "내 공지" },
  { href: "/all", label: "전체 공지" },
  { href: "/sources", label: "출처" },
  { href: "/contacts", label: "연락처" },
];

export function Header() {
  const pathname = usePathname();
  const router = useRouter();
  const { settings, update } = useSettings();
  const campuses = useCatalog()?.campuses ?? [];

  const active = (href: string) => (href === "/" ? pathname === "/" || pathname.startsWith("/notices") : pathname.startsWith(href));

  return (
    <header className="sticky top-0 z-20 border-b border-line bg-white/95 backdrop-blur">
      <div className="h-1" style={{ background: "linear-gradient(90deg,var(--gold) 0 16%,var(--red) 16% 84%,var(--navy) 84%)" }} />
      <div className="mx-auto flex h-14 max-w-[1180px] items-center gap-3 px-4 max-[480px]:h-[50px] max-[480px]:gap-2">
        <Link href="/" className="flex items-center gap-2">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src="https://www.khu.ac.kr/upload/cross/images/000001/imgSub0401.jpg" alt="" className="h-[30px] w-auto max-[480px]:h-[26px]" />
          <b className="text-[19px] font-bold tracking-tight text-navy max-[480px]:text-[17px]">경희공지</b>
          <span className="ml-0.5 text-xs text-gray max-[480px]:hidden">경희대학교 통합 공지</span>
        </Link>

        <div className="ml-auto flex rounded-full border border-line bg-bg p-0.5">
          {campuses.map((c) => {
            const on = settings.campus_id === c.id;
            return (
              <button
                key={c.id}
                onClick={() => update({ campus_id: c.id })}
                className={`whitespace-nowrap rounded-full px-3 py-1 text-[13px] ${on ? "bg-navy text-white" : "text-gray"}`}
              >
                {c.name.replace("캠퍼스", "")}
              </button>
            );
          })}
        </div>
        <button className="grid h-[34px] w-[34px] place-items-center rounded-lg text-ink-2 hover:bg-bg" title="검색" onClick={() => router.push("/all?focus=q")}>
          <IconSearch width={19} height={19} />
        </button>
        <button className="grid h-[34px] w-[34px] place-items-center rounded-lg text-ink-2 hover:bg-bg" title="내 학과 설정" onClick={() => update({ onboarded: false })}>
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
