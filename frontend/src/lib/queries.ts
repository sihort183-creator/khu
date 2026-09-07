"use client";
// 단건·목록 조회용 SWR 훅. 실제 서버가 붙어도 훅 시그니처는 그대로 둔다.
import useSWR from "swr";
import { getCatalog, getNotice, listContacts, listOrganizationNoticeCounts, listOrganizations, listSources } from "./api";
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
