"use client";
// 단건·목록 조회용 SWR 훅. 실제 서버가 붙어도 훅 시그니처는 그대로 둔다.
import useSWR from "swr";
import { getCatalog, getNotice, listContacts, listOrganizations, listSources } from "./api";
import type { ApiError } from "./types";

const opts = { revalidateOnFocus: false } as const;

export function useCatalog() {
  const { data } = useSWR("catalog", () => getCatalog().then((r) => r.data), { ...opts, revalidateIfStale: false });
  return data ?? null;
}

export function useOrganizations() {
  const { data } = useSWR("organizations", () => listOrganizations().then((r) => r.data), { ...opts, revalidateIfStale: false });
  return data ?? [];
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
