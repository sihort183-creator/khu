"use client";
// 비회원 설정. 이 브라우저의 localStorage에만 저장한다(규격 7절).
// 외부 저장소 + useSyncExternalStore: 서버 렌더에서는 기본값, 클라이언트에서는 저장값.
import { useCallback, useSyncExternalStore } from "react";

export interface Settings {
  campus_id: string | null;
  college_id: string | null;
  department_id: string | null;
  subscribed_source_ids: string[];
  onboarded: boolean;
}

const KEY = "khu-notice.settings.v1";
const DEFAULT: Settings = {
  campus_id: "campus-seoul",
  college_id: null,
  department_id: null,
  subscribed_source_ids: [],
  onboarded: false,
};

let cache: Settings | null = null;
const listeners = new Set<() => void>();

function read(): Settings {
  if (cache) return cache;
  try {
    const raw = typeof localStorage !== "undefined" ? localStorage.getItem(KEY) : null;
    cache = raw ? { ...DEFAULT, ...JSON.parse(raw) } : DEFAULT;
  } catch {
    cache = DEFAULT;
  }
  return cache!;
}

function write(next: Settings) {
  cache = next;
  try {
    localStorage.setItem(KEY, JSON.stringify(next));
  } catch {}
  listeners.forEach((l) => l());
}

function subscribe(l: () => void) {
  listeners.add(l);
  const onStorage = (e: StorageEvent) => {
    if (e.key === KEY) {
      cache = null;
      l();
    }
  };
  window.addEventListener("storage", onStorage);
  return () => {
    listeners.delete(l);
    window.removeEventListener("storage", onStorage);
  };
}

const getServerSnapshot = () => DEFAULT;
const subscribeNoop = () => () => {};

export function useSettings() {
  const settings = useSyncExternalStore(subscribe, read, getServerSnapshot);
  // 하이드레이션 전에는 false → 저장값이 아직 반영되지 않았다는 뜻
  const ready = useSyncExternalStore(subscribeNoop, () => true, () => false);

  const update = useCallback((patch: Partial<Settings>) => write({ ...read(), ...patch }), []);
  const toggleSource = useCallback((id: string) => {
    const cur = read();
    const set = new Set(cur.subscribed_source_ids);
    if (set.has(id)) set.delete(id);
    else set.add(id);
    write({ ...cur, subscribed_source_ids: [...set] });
  }, []);

  return { settings, ready, update, toggleSource };
}
