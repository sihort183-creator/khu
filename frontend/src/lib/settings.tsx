"use client";
// 비회원 설정. 이 브라우저의 localStorage에만 저장한다(규격 7절).
// 외부 저장소 + useSyncExternalStore: 서버 렌더에서는 기본값, 클라이언트에서는 저장값.
import { useCallback, useMemo, useSyncExternalStore } from "react";
import type { Organization } from "./types";
import { toggleOrganizationSelection } from "./orgTree";

export interface Settings {
  /**
   * 보고 있는 캠퍼스. null 이면 '공통'이라 캠퍼스로 거르지 않는다(서울·국제·캠퍼스 미상 전부).
   * 값 자체는 예전 그대로(campus-seoul / campus-global)라 저장값을 옮길 필요가 없다. lib/campus.ts 참고.
   */
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
 *
 * 조직 목록을 **인자로 받는다.** 여기서 useOrganizations() 를 부르면 저장소 하나 읽자고
 * 화면마다 SWR 조회가 딸려 오고, 설정 파일이 조회 계층에 매달린다. 인자로 두면 타입이
 * 빠뜨린 화면을 잡아 준다 — 전체 공지·내 공지·온보딩이 같은 규칙을 쓰게 하려면 그게 낫다.
 * 체크를 뒤집는 규칙(좁히기·합치기)은 lib/orgTree.ts 의 toggleOrganizationSelection 하나뿐이다.
 */
export function useOrganizationSelection(orgs: Organization[]) {
  const { settings, update } = useSettings();
  const ids = settings.organization_ids;
  const selected = useMemo(() => new Set(ids), [ids]);

  // 저장값은 write 직전에 read() 로 다시 읽는다. 두 선택기(좌측 열·모바일 상자)가 같은
  // 저장소를 보므로, 렌더 때 들고 있던 값으로 덮으면 다른 쪽이 방금 한 체크가 지워진다.
  const toggle = useCallback(
    (id: string) => {
      const cur = read();
      write({ ...cur, organization_ids: toggleOrganizationSelection(cur.organization_ids, id, orgs) });
    },
    [orgs],
  );

  const clear = useCallback(() => update({ organization_ids: [] }), [update]);

  return { ids, selected, toggle, clear };
}
