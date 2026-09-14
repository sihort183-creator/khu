"use client";
// 단건·목록 조회용 SWR 훅. 실제 서버가 붙어도 훅 시그니처는 그대로 둔다.
import useSWR from "swr";
import {
  findShortcutWatchNotice,
  getCatalog,
  getGraduationRequirements,
  getNotice,
  getShortcutCards,
  listContacts,
  listOrganizationNoticeCounts,
  listOrganizations,
  listShortcutDepartmentNotices,
  listSources,
} from "./api";
import type { ApiError } from "./types";
import type { NodeCounts } from "./orgTree";

const opts = {
  revalidateOnFocus: true,
  revalidateIfStale: true,
  refreshInterval: 60_000,
} as const;

export function useCatalog() {
  const { data } = useSWR("catalog", () => getCatalog().then((r) => r.data), opts);
  return data ?? null;
}

export function useOrganizations() {
  const { data } = useSWR("organizations", () => listOrganizations().then((r) => r.data), { ...opts, revalidateIfStale: false });
  return data ?? [];
}

/**
 * 조직 트리에 붙일 공지 건수. 색인 전체(약 600KB)를 읽어야 하므로 선택기가 실제로
 * 눈에 보일 때만(enabled) 부르고, 한 번 읽으면 다시 읽지 않는다.
 */
export function useOrganizationNoticeCounts(enabled: boolean): NodeCounts | null {
  const { data } = useSWR(enabled ? "organization-notice-counts" : null, listOrganizationNoticeCounts, {
    revalidateOnFocus: false,
    revalidateIfStale: false,
    revalidateOnReconnect: false,
    refreshInterval: 0,
  });
  return data ?? null;
}

export function useNotice(id: string) {
  const { data, error, isLoading, mutate } = useSWR<Awaited<ReturnType<typeof getNotice>>["data"], ApiError>(["notice", id], () => getNotice(id).then((r) => r.data), opts);
  return { notice: data ?? null, error: error ? error.error?.message ?? "불러오지 못했습니다." : null, loading: isLoading, retry: () => void mutate() };
}

export function useSources(campusId: string | null) {
  const { data, isLoading } = useSWR(["sources", campusId], () => listSources({ campus_id: campusId ?? undefined }).then((r) => r.data), opts);
  return { sources: data ?? [], loading: isLoading };
}

export function useContacts(campusId: string | null) {
  const { data, isLoading } = useSWR(["contacts", campusId], () => listContacts({ campus_id: campusId ?? undefined }).then((r) => r.data), opts);
  return { contacts: data ?? [], loading: isLoading };
}

/* ---------- 바로가기 카드 ---------- */

/**
 * 카드 13장. catalog.json 에 얹혀 오므로 추가 요청이 없다 — 위 useCatalog 와 같은 파일을
 * 본다. 60초마다 다시 확인하지 않는다(개정이 바뀌기 전에는 같은 값이다).
 */
export function useShortcutCards() {
  const { data } = useSWR("shortcut-cards", getShortcutCards, {
    revalidateOnFocus: false,
    revalidateIfStale: false,
    refreshInterval: 0,
  });
  return data ?? [];
}

/**
 * 학과별 졸업요건(gzip 41KB). `enabled` 가 참일 때만 받는다.
 *
 * 졸업요건 카드가 실제로 떴을 때만 켜진다. 공지만 보는 학생의 화면에서는 이 훅이 키를
 * null 로 두므로 요청이 나가지 않는다.
 */
export function useGraduationRequirements(enabled: boolean) {
  const { data } = useSWR(enabled ? "graduation-requirements" : null, getGraduationRequirements, {
    revalidateOnFocus: false,
    revalidateIfStale: false,
    revalidateOnReconnect: false,
    refreshInterval: 0,
  });
  return data ?? null;
}

/** watch 말이 든 최근 공지 한 건. 없으면 null 이고 카드에 줄이 붙지 않는다. */
export function useShortcutWatch(terms: readonly string[] | undefined, since: string) {
  const key = terms?.length ? ["shortcut-watch", terms.join("|"), since] : null;
  const { data } = useSWR(key, () => findShortcutWatchNotice(terms ?? [], since), {
    revalidateOnFocus: false,
    revalidateIfStale: false,
    refreshInterval: 0,
  });
  return data ?? null;
}

/**
 * 고른 학과의 졸업 공지. 아직 안 받아 왔으면 undefined, 학과를 공지 조직에 못 맞췄으면
 * null, 맞췄으면 목록이다(빈 목록이면 "아직 올라오지 않았습니다"라고 말할 자리다).
 */
export function useDepartmentGraduationNotices(department: string | null) {
  const { data } = useSWR(department ? ["shortcut-dept-notices", department] : null, () => listShortcutDepartmentNotices(department!), {
    revalidateOnFocus: false,
    revalidateIfStale: false,
    refreshInterval: 0,
  });
  return data;
}
