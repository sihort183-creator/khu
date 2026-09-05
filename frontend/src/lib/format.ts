import type { Notice } from "./types";

/** "2026-09-03" → "09.03". 날짜 미확정이면 null */
export function shortDate(date: string | null): string | null {
  if (!date) return null;
  const [, m, d] = date.split("-");
  return `${m}.${d}`;
}

export function fullDate(date: string | null): string | null {
  if (!date) return null;
  return date.replace(/-/g, ".");
}

/** 시각이 있으면 "09.05 18:12", 날짜만이면 "09.05" */
export function publishedLabel(n: Pick<Notice, "published_date" | "published_at" | "published_precision">): string {
  if (n.published_precision === "unknown" || !n.published_date) return "날짜 미확정";
  if (n.published_precision === "datetime" && n.published_at) {
    const t = new Date(n.published_at);
    const hh = String(t.getHours()).padStart(2, "0");
    const mm = String(t.getMinutes()).padStart(2, "0");
    return `${shortDate(n.published_date)} ${hh}:${mm}`;
  }
  return shortDate(n.published_date)!;
}

/** 24시간 안에 서비스에 처음 노출된 글 */
export function isNew(n: Pick<Notice, "first_visible_at">, now = Date.now()): boolean {
  return now - new Date(n.first_visible_at).getTime() < 24 * 60 * 60 * 1000;
}

export function relativeTime(iso: string | null, now = Date.now()): string {
  if (!iso) return "기록 없음";
  const diff = Math.max(0, now - new Date(iso).getTime());
  const min = Math.floor(diff / 60000);
  if (min < 1) return "방금";
  if (min < 60) return `${min}분 전`;
  const h = Math.floor(min / 60);
  if (h < 24) return `${h}시간 전`;
  const d = Math.floor(h / 24);
  return `${d}일 전`;
}

export function fileSize(bytes: number | null): string {
  if (bytes == null) return "";
  if (bytes < 1024) return `${bytes}B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)}KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)}MB`;
}
