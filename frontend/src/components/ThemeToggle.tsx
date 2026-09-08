"use client";
// 바닥글의 밝게 / 어둡게 / 기기 설정 조절. 기본은 "기기 설정"이다.
import { useLayoutEffect, useSyncExternalStore } from "react";
import { IconMoon, IconSun } from "./icons";
import { useTheme, type Theme } from "@/lib/theme";

const OPTIONS: { value: Theme; label: string }[] = [
  { value: "light", label: "밝게" },
  { value: "dark", label: "어둡게" },
  { value: "system", label: "기기 설정" },
];

export function ThemeToggle() {
  const { theme, set } = useTheme();

  // 개발 모드에서 React 가 다시 붙을 때 <html> 의 속성을 지운다(Next 16 문서
  // preventing-flash-before-hydration.md "Re-applying attributes in development").
  // 배포본에서는 아무 일도 하지 않는다.
  useLayoutEffect(() => {
    if (theme !== "system") document.documentElement.setAttribute("data-theme", theme);
  }, [theme]);

  return (
    <div className="inline-flex items-center gap-1.5">
      <span className="text-xs text-gray-2">화면</span>
      <div className="flex rounded-full border border-line bg-card p-0.5" role="group" aria-label="화면 밝기">
        {OPTIONS.map((o) => {
          const on = theme === o.value;
          return (
            <button
              key={o.value}
              type="button"
              onClick={() => set(o.value)}
              aria-pressed={on}
              // 알약 자체는 24px 이라 손가락에 좁다. before: 로 모양은 그대로 두고
              // 누르는 자리만 위아래 44px 로 넓힌다.
              className={`relative whitespace-nowrap rounded-full px-2.5 py-1 text-[11.5px] before:absolute before:inset-x-0 before:top-1/2 before:h-11 before:-translate-y-1/2 ${
                on ? "bg-navy-fill text-white" : "text-gray"
              }`}
            >
              {o.label}
            </button>
          );
        })}
      </div>
    </div>
  );
}

const DARK_QUERY = "(prefers-color-scheme: dark)";

function subscribeDark(l: () => void) {
  const mq = window.matchMedia(DARK_QUERY);
  mq.addEventListener("change", l);
  return () => mq.removeEventListener("change", l);
}

/**
 * 헤더의 한 칸짜리 전환 단추. 지금 화면이 어두우면 해, 밝으면 달을 보여 주고 누르면
 * 반대로 바꾼다. "기기 설정"인 채여도 지금 실제로 보이는 판을 기준으로 뒤집는다.
 * 바닥글의 세 칸 조절(밝게/어둡게/기기 설정)은 그대로 둔다 — 기기 설정으로 되돌리는
 * 길은 거기에만 있다.
 */
export function ThemeButton() {
  const { theme, set } = useTheme();
  useLayoutEffect(() => {
    if (theme !== "system") document.documentElement.setAttribute("data-theme", theme);
  }, [theme]);
  const deviceDark = useSyncExternalStore(subscribeDark, () => window.matchMedia(DARK_QUERY).matches, () => false);
  const dark = theme === "dark" || (theme === "system" && deviceDark);
  return (
    <button
      type="button"
      onClick={() => set(dark ? "light" : "dark")}
      title={dark ? "밝게 보기" : "어둡게 보기"}
      aria-label={dark ? "밝게 보기" : "어둡게 보기"}
      className="relative grid h-[34px] w-[34px] shrink-0 place-items-center rounded-lg text-ink-2 before:absolute before:left-1/2 before:top-1/2 before:h-11 before:w-11 before:-translate-x-1/2 before:-translate-y-1/2 hover:bg-bg"
    >
      {dark ? <IconSun width={19} height={19} /> : <IconMoon width={19} height={19} />}
    </button>
  );
}
