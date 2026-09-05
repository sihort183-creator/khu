"use client";
// 이어보기(cursor) 공지 목록. SWR Infinite 위에 규격 2·8절의 페이지 규칙을 얹는다.
// FEED_CHANGED(409)가 오면 이어보기 위치를 버리고 첫 페이지부터 다시 읽는다.
import { useCallback } from "react";
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
    revalidateFirstPage: false,
    revalidateOnFocus: false,
    onError: (e) => {
      if (e?.error?.code === "FEED_CHANGED") void mutate(undefined, { revalidate: true });
    },
  });

  const pages = data ?? [];
  const items = pages.flatMap((p) => p.data);
  const last = pages[pages.length - 1];
  const hasNext = !!last?.page.has_next;
  const loadingMore = size > pages.length && isValidating;

  const more = useCallback(() => void setSize((s) => s + 1), [setSize]);
  const retry = useCallback(() => void mutate(undefined, { revalidate: true }), [mutate]);

  return {
    items,
    hasNext,
    loading: isLoading || loadingMore,
    error: error ? error.error?.message ?? "네트워크 오류가 발생했습니다." : null,
    more,
    retry,
  };
}
