"use client";
// 비회원 설정. 이 브라우저의 localStorage에만 저장한다(규격 7절).
// 외부 저장소 + useSyncExternalStore: 서버 렌더에서는 기본값, 클라이언트에서는 저장값.
import { useCallback, useMemo, useSyncExternalStore } from "react";

export interface Settings {
  campus_id: string | null;
  college_id: string | null;
  department_id: string | null;
  /**
   * 보고 있는 조직. 비어 있으면 "경희대학교 전체"다.
   * college_id·department_id는 온보딩이 고른 한 쌍만 담을 수 있어 다른 학과를
   * 같이 볼 수 없었다. 여기에 여러 개를 담아 화면 어디서든 체크로 넓힌다.
   */
  organization_ids: string[];
  subscribed_source_ids: string[];
  onboarded: boolean;
}

const KEY = "khu-notice.settings.v1";
const DEFAULT: Settings = {
  campus_id: "campus-seoul",
  college_id: null,
  department_id: null,
  organization_ids: [],
  subscribed_source_ids: [],
  onboarded: false,
};

let cache: Settings | null = null;
const listeners = new Set<() => void>();

function read(): Settings {
  if (cache) return cache;
  try {
    const raw = typeof localStorage !== "undefined" ? localStorage.getItem(KEY) : null;
    const stored = raw ? ({ ...DEFAULT, ...JSON.parse(raw) } as Settings) : DEFAULT;
    // v1 저장값에는 organization_ids가 없다. 이미 온보딩을 마친 사람의 화면이
    // "전체"로 되돌아가지 않도록 예전 단과대·학과 선택을 그대로 옮겨 담는다.
    cache =
      raw && stored.organization_ids.length === 0
        ? { ...stored, organization_ids: [stored.college_id, stored.department_id].filter((id): id is string => !!id) }
        : stored;
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

/**
 * 조직 다중 선택. 비어 있으면 전체(= 경희대학교 전체)라는 뜻이라 주제 칩과 규칙이 같다.
 * 저장소에 두는 이유: 온보딩을 건너뛴 사람도 목록에서 체크만 하면 '내 공지'와
 * '전체 공지'가 함께 그 조직으로 좁혀져야 하기 때문이다.
 */
export function useOrganizationSelection() {
  const { settings, update } = useSettings();
  const ids = settings.organization_ids;
  const selected = useMemo(() => new Set(ids), [ids]);

  const toggle = useCallback((id: string) => {
    const cur = read();
    const set = new Set(cur.organization_ids);
    if (set.has(id)) set.delete(id);
    else set.add(id);
    write({ ...cur, organization_ids: [...set] });
  }, []);

  const clear = useCallback(() => update({ organization_ids: [] }), [update]);

  return { ids, selected, toggle, clear };
}
