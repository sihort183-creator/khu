"use client";
// 이어보기(cursor) 공지 목록. SWR Infinite 위에 규격 2·8절의 페이지 규칙을 얹는다.
// FEED_CHANGED(409)가 오면 이어보기 위치를 버리고 첫 페이지부터 다시 읽는다.
import { useCallback, useEffect, useRef } from "react";
import useSWRInfinite from "swr/infinite";
import { listNotices, previewFeed } from "./api";
import type { ApiError, FeedPreviewBody, ListResponse, Notice, NoticeQuery } from "./types";

export type FeedRequest = { kind: "list"; query: NoticeQuery } | { kind: "preview"; body: FeedPreviewBody };

type Key = ["notice-feed", string, string | null];

async function fetchPage([, reqJson, cursor]: Key): Promise<ListResponse<Notice>> {
  const req = JSON.parse(reqJson) as FeedRequest;
  if (req.kind === "list") return listNotices({ ...req.query, cursor });
  return previewFeed({ ...req.body, cursor });
}

export function useNoticeFeed(request: FeedRequest | null) {
  const reqJson = request ? JSON.stringify(request) : null;

  const getKey = (index: number, prev: ListResponse<Notice> | null): Key | null => {
    if (!reqJson) return null;
    if (index === 0) return ["notice-feed", reqJson, null];
    if (!prev?.page.has_next || !prev.page.next_cursor) return null;
    return ["notice-feed", reqJson, prev.page.next_cursor];
  };

  const { data, error, size, setSize, isLoading, isValidating, mutate } = useSWRInfinite<ListResponse<Notice>, ApiError>(getKey, fetchPage, {
    // 첫 페이지를 주기적으로 다시 읽어 latest 포인터가 바뀌었는지 확인한다.
    // 페이지 개정이 바뀌면 아래 효과가 size를 1로 줄여 오래된 페이지와 섞이지 않게 한다.
    revalidateFirstPage: true,
    revalidateOnFocus: true,
    refreshInterval: 60_000,
  });

  const pages = data ?? [];
  const firstRevision = pages[0]?.page.dataset_revision;
  const sameRevision = !firstRevision || pages.every((page) => page.page.dataset_revision === firstRevision);
  const visiblePages = sameRevision ? pages : pages.slice(0, 1);
  const items = visiblePages.flatMap((p) => p.data);
  const last = visiblePages[visiblePages.length - 1];
  const hasNext = !!last?.page.has_next;
  const loadingMore = size > pages.length && isValidating;

  const previousRevision = useRef<string | undefined>(firstRevision);
  const resetting = useRef(false);
  const resetToFirst = useCallback(async () => {
    if (resetting.current) return;
    resetting.current = true;
    try {
      await setSize(1);
      await mutate(undefined, { revalidate: true });
    } finally {
      resetting.current = false;
    }
  }, [mutate, setSize]);

  useEffect(() => {
    if (firstRevision && previousRevision.current && firstRevision !== previousRevision.current) void resetToFirst();
    previousRevision.current = firstRevision;
  }, [firstRevision, resetToFirst]);

  useEffect(() => {
    if (error?.error?.code === "FEED_CHANGED") void resetToFirst();
  }, [error, resetToFirst]);

  const more = useCallback(() => void setSize((s) => s + 1), [setSize]);
  const retry = useCallback(() => void resetToFirst(), [resetToFirst]);

  return {
    items,
    hasNext,
    loading: isLoading || loadingMore,
    error: error ? error.error?.message ?? "네트워크 오류가 발생했습니다." : null,
    more,
    retry,
  };
}
