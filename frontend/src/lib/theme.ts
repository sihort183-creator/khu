"use client";
// 밝게 / 어둡게 / 기기 설정. 이 브라우저의 localStorage 에만 저장한다(설정과 같은 규칙).
//
// 기본은 "기기 설정"이다. 그때는 <html> 에 아무 것도 붙이지 않고 CSS 의
// prefers-color-scheme 가 판을 고른다. 사람이 밝게·어둡게를 고르면 그때만
// <html data-theme="light|dark"> 가 붙어 기기 설정을 이긴다.
//
// 첫 그리기에서 깜빡이지 않으려면 React 가 붙기 전에 글자가 붙어 있어야 한다. 그래서
// 아래 SCRIPT 를 layout 의 <head> 에 그대로 넣는다(Next 16 문서
// node_modules/next/dist/docs/01-app/02-guides/preventing-flash-before-hydration.md
// "Themes" 절이 권하는 방법이다. 문서는 클래스가 아니라 data-theme 속성을 쓴다).
import { useCallback, useSyncExternalStore } from "react";

export type Theme = "light" | "dark" | "system";

export const THEME_KEY = "khu-notice.theme";

/** <head> 에서 동기로 도는 스크립트. 저장값이 있을 때만 속성을 붙인다. */
export const THEME_SCRIPT = `(function(){try{var t=localStorage.getItem("${THEME_KEY}");if(t==="light"||t==="dark")document.documentElement.setAttribute("data-theme",t)}catch(e){}})()`;

function read(): Theme {
  try {
    const v = localStorage.getItem(THEME_KEY);
    return v === "light" || v === "dark" ? v : "system";
  } catch {
    return "system";
  }
}

const listeners = new Set<() => void>();

function subscribe(l: () => void) {
  listeners.add(l);
  const onStorage = (e: StorageEvent) => {
    if (e.key === THEME_KEY) {
      apply(read());
      l();
    }
  };
  window.addEventListener("storage", onStorage);
  return () => {
    listeners.delete(l);
    window.removeEventListener("storage", onStorage);
  };
}

/** <html> 에 지금 고른 값을 반영한다. "기기 설정"이면 속성을 떼어 CSS 에 맡긴다. */
export function apply(theme: Theme) {
  const el = document.documentElement;
  if (theme === "system") el.removeAttribute("data-theme");
  else el.setAttribute("data-theme", theme);
}

export function useTheme() {
  const theme = useSyncExternalStore(subscribe, read, () => "system" as Theme);

  const set = useCallback((next: Theme) => {
    try {
      if (next === "system") localStorage.removeItem(THEME_KEY);
      else localStorage.setItem(THEME_KEY, next);
    } catch {}
    apply(next);
    listeners.forEach((l) => l());
  }, []);

  return { theme, set };
}
