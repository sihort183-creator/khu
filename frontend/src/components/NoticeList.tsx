"use client";
import Link from "next/link";
import type { Notice } from "@/lib/types";
import { isNew, publishedLabel } from "@/lib/format";
import { imageUrl } from "@/lib/api";
import { CategoryBadge } from "./CategoryBadge";
import { MediumIcon } from "./icons";
import { Ad } from "./Ad";
import { Box } from "./Shell";

interface Props {
  notices: Notice[];
  label: string;
  loading?: boolean;
  error?: string | null;
  hasNext?: boolean;
  onMore?: () => void;
  onRetry?: () => void;
  adAfter?: number;
}

export function NoticeList({ notices, label, loading, error, hasNext, onMore, onRetry, adAfter = 4 }: Props) {
  return (
    <Box>
      <div className="flex bg-cream px-4 py-2.5 text-xs text-gray">
        <span>
          {label}
          {!loading && !error && <span className="ml-1">{notices.length}건{hasNext ? "+" : ""}</span>}
        </span>
        <span className="ml-auto flex gap-2.5">
          <b className="font-medium text-ink">최신순</b>
        </span>
      </div>

      {error && (
        <div className="px-4 py-12 text-center text-[13px] text-gray">
          <b className="mb-1 block text-[15px] text-ink">불러오지 못했습니다</b>
          {error}
          {onRetry && (
            <button onClick={onRetry} className="mt-3 block w-full rounded-lg border border-line py-2 text-ink-2 hover:bg-bg">
              다시 시도
            </button>
          )}
        </div>
      )}

      {!error && loading && notices.length === 0 && (
        <div className="divide-y divide-line-2">
          {[0, 1, 2, 3, 4].map((i) => (
            <div key={i} className="animate-pulse px-4 py-3.5">
              <div className="h-4 w-3/4 rounded bg-line-2" />
              <div className="mt-2 h-3 w-1/3 rounded bg-line-2" />
            </div>
          ))}
        </div>
      )}

      {!error && !loading && notices.length === 0 && (
        <div className="px-4 py-12 text-center text-[13px] text-gray">
          <b className="mb-1 block text-[15px] text-ink">표시할 공지가 없습니다</b>
          선택한 주제·조직에 새 공지가 올라오면 여기에 모입니다.
        </div>
      )}

      {notices.map((n, i) => (
        <div key={n.id}>
          <NoticeRow notice={n} />
          {i === adAfter && <Ad />}
        </div>
      ))}

      {hasNext && onMore && (
        <button onClick={onMore} disabled={loading} className="block w-full border-t border-line-2 bg-cream py-3 text-[13px] text-ink-2 hover:bg-bg disabled:opacity-60">
          {loading ? "불러오는 중…" : "더 보기"}
        </button>
      )}
    </Box>
  );
}

export function NoticeRow({ notice: n }: { notice: Notice }) {
  const ig = n.primary_source.medium.code === "instagram";
  // 포스터 한 장뿐인 공지는 글자가 없다. 그림이라도 보여야 무슨 공지인지 알 수 있다.
  const poster = imageUrl(n.poster_image);
  return (
    <Link href={`/notices/${n.id}`} className="block border-b border-line-2 px-4 py-3.5 transition-colors last:border-b-0 hover:bg-cream">
      <div className="flex items-start gap-2.5">
        {poster ? (
          // 원문 그림이 사라졌을 수 있다. 못 불러오면 자리를 비운다.
          // next/image 는 쓰지 않는다. 최적화가 배포처 과금으로 이어지고, 그림은 이미
          // 조회 서버가 오래 캐시한다. 0원 구성을 지킨다.
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={poster}
            alt=""
            loading="lazy"
            className="h-[52px] w-[52px] flex-none rounded-lg border border-line-2 object-cover"
            style={{ background: "linear-gradient(135deg,#F3EFE8,#E9E4DA)" }}
            onError={(e) => { e.currentTarget.style.display = "none"; }}
          />
        ) : ig ? (
          <div className="grid h-[52px] w-[52px] flex-none place-items-center rounded-lg text-gray" style={{ background: "linear-gradient(135deg,#F3EFE8,#E9E4DA)" }}>
            <MediumIcon code="instagram" width={18} height={18} />
          </div>
        ) : null}
        <div className="min-w-0 flex-1">
          <div className="line-clamp-2 text-[14.5px] font-medium leading-[1.45]">
            <CategoryBadge category={n.primary_category} className="mr-1.5" />
            {n.title}
            {isNew(n) && <span className="ml-1.5 inline-block rounded px-[5px] align-[2px] text-[10px] font-bold leading-[15px] text-red" style={{ background: "#FBEAEB" }}>N</span>}
          </div>
          <div className="mt-1.5 flex flex-wrap items-center gap-x-1.5 text-xs text-gray">
            <span className="text-ink-2">{n.primary_source.name}</span>
            <Sep />
            <span className={n.published_precision === "unknown" ? "text-gray-2" : ""}>{publishedLabel(n)}</span>
            <Sep />
            <span className="inline-flex items-center gap-1">
              <MediumIcon code={n.primary_source.medium.code} width={12} height={12} />
              {n.primary_source.medium.label}
            </span>
            {n.source_count > 1 && (
              <>
                <Sep />
                <span className="text-navy">동일 공지 {n.source_count}곳</span>
              </>
            )}
            {n.original_status.code !== "available" && (
              <>
                <Sep />
                <span className="text-gray-2">원문 {n.original_status.label}</span>
              </>
            )}
          </div>
        </div>
      </div>
    </Link>
  );
}

const Sep = () => <span className="text-line">|</span>;
