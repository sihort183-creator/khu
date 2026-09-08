"use client";
// 공지 상세: GET /v1/notices/{id}
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useNotice } from "@/lib/queries";
import { fileSize, fullDate, publishedLabel, relativeTime } from "@/lib/format";
import { Shell, Box } from "@/components/Shell";
import { CategoryBadge } from "@/components/CategoryBadge";
import { IconBack, IconExternal, IconFile, MediumIcon } from "@/components/icons";
import { imageUrl } from "@/lib/api";

export default function NoticePage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const { notice: n, error, retry } = useNotice(id);
  // 본문 글자도 정리된 HTML 도 없을 때 대신 보여줄 그림이다.
  const posters = (n?.images ?? []).map(imageUrl).filter((src): src is string => src !== null);

  return (
    <Shell>
      <button onClick={() => (window.history.length > 1 ? router.back() : router.push("/"))} className="mb-2.5 inline-flex items-center gap-1 text-[13px] text-gray hover:text-ink">
        <IconBack width={14} height={14} /> 목록
      </button>

      {error && (
        <Box>
          <div className="px-4 py-12 text-center text-[13px] text-gray">
            <b className="mb-1 block text-[15px] text-ink">{error}</b>
            <div className="mt-3 flex justify-center gap-2">
              <button onClick={retry} className="rounded-lg border border-line px-4 py-2 text-ink-2 hover:bg-bg">
                다시 시도
              </button>
              <Link href="/" className="rounded-lg border border-line px-4 py-2 text-ink-2 hover:bg-bg">
                전체 공지로 이동
              </Link>
            </div>
          </div>
        </Box>
      )}

      {!n && !error && (
        <Box>
          <div className="animate-pulse px-[18px] py-4">
            <div className="h-5 w-4/5 rounded bg-line-2" />
            <div className="mt-3 h-3 w-1/3 rounded bg-line-2" />
            <div className="mt-6 h-24 rounded bg-line-2" />
          </div>
        </Box>
      )}

      {n && (
        <Box>
          {/* 원문 보기는 본문 아래에도 있지만, 본문이 있는 공지는 거기까지 1,000px 넘게
              내려야 한다(2026-09-08 모바일 실측 977~1205px). 그래서 이 줄 오른쪽에 하나 더
              둔다. 주소는 아래 큰 단추와 같은 n.original_url — 대표 출처의 원문이다.
              줄 높이를 min-h-11 로 잡아 링크의 누르는 자리 44px 이 잘리지 않게 한다. */}
          <div className="flex min-h-11 items-center border-b border-line-2 px-3.5 py-2 text-[13px] text-gray">
            <span className="inline-flex min-w-0 items-center gap-1.5">
              <MediumIcon code={n.primary_source.medium.code} width={13} height={13} />
              <span className="truncate">{n.primary_source.name}</span>
            </span>
            <span className="ml-auto inline-flex flex-none items-center gap-1 text-xs">
              <span className={`h-1.5 w-1.5 rounded-full ${n.freshness.code === "fresh" ? "bg-ok" : "bg-warn"}`} />
              {/* 375px 에서는 이 줄에 게시판 이름·확인 상태·원문 링크가 함께 선다. 셋을 다
                  적으면 게시판 이름이 "건축공학과 학…"으로 잘린다. 상태 글자("최근 확인됨")는
                  왼쪽 점 색이 이미 말해 주므로 좁은 화면에서만 접고 시각은 남긴다. */}
              <span className="hidden sm:inline">{n.freshness.label} · </span>
              {relativeTime(n.freshness.last_checked_at)}
            </span>
            <a
              href={n.original_url}
              target="_blank"
              rel="noreferrer"
              className="relative ml-2.5 inline-flex flex-none items-center gap-1 text-xs font-medium text-navy before:absolute before:inset-x-[-8px] before:top-1/2 before:h-11 before:-translate-y-1/2 hover:underline"
            >
              원문 보기
              <IconExternal width={11} height={11} />
            </a>
          </div>

          <article className="px-[18px] py-4">
            <div className="flex flex-wrap gap-1">
              <CategoryBadge category={n.primary_category} />
              {n.secondary_categories.map((c) => (
                <CategoryBadge key={c.code} category={c} className="opacity-70" />
              ))}
            </div>
            <h1 className="mb-2 mt-1.5 text-lg font-bold leading-[1.4]">{n.title}</h1>
            <div className="flex flex-wrap gap-x-1.5 text-[12.5px] text-gray">
              <span>
                {n.published_precision === "unknown"
                  ? "원문 등록일 미확정"
                  : `${fullDate(n.published_date)}${n.published_precision === "datetime" ? " " + publishedLabel(n).split(" ")[1] : ""}`}
              </span>
              <Sep />
              <span>대상: {n.audiences.map((a) => a.name).join(", ")}</span>
              {n.source_count > 1 && (
                <>
                  <Sep />
                  <span className="text-navy">동일 공지 {n.source_count}곳</span>
                </>
              )}
            </div>
            {n.audience_note && <p className="mt-2 rounded-lg bg-cream px-3 py-2 text-xs text-ink-2">{n.audience_note}</p>}

            {/* 로그인해야 볼 수 있는 글은 본문을 아예 못 가져온다. 저장된 내용을 보여준다고
                하면 거짓말이 되므로 왜 없는지 그대로 말한다. */}
            {n.original_status.code === "restricted" ? (
              <p className="mt-3 rounded-lg border border-warn-line bg-warn-bg px-3 py-2 text-xs text-ink-2">
                원문 {n.original_status.label}: 학교 계정으로 로그인해야 볼 수 있는 글입니다. 제목과 날짜만 모았습니다. 내용은 원문에서 확인하세요.
              </p>
            ) : n.original_status.code !== "available" ? (
              <p className="mt-3 rounded-lg border border-warn-line bg-warn-bg px-3 py-2 text-xs text-ink-2">
                원문 {n.original_status.label}: 원문 사이트에 접근할 수 없어 저장된 내용을 보여줍니다. 최신 내용은 원문에서 확인하세요.
              </p>
            ) : null}

            <div className="my-4 border-y border-line-2 py-3.5 text-sm leading-[1.7] text-ink-2">
              {/* body_html 은 서버에서 허용 목록으로 다시 지은 것이다. 그림 주소도 중계 주소로
                  바뀌어 있다. 원문 HTML 을 그대로 넣지 않는다. */}
              {n.body_html ? (
                <div className="notice-body" dangerouslySetInnerHTML={{ __html: n.body_html }} />
              ) : n.body_text ? (
                <div className="whitespace-pre-line">{n.body_text}</div>
              ) : posters.length > 0 ? (
                // 글자 없이 포스터만 올라온 공지다. 그림이 곧 내용이다.
                <div className="flex flex-col gap-2">
                  {posters.map((src) => (
                    // next/image 는 쓰지 않는다. 위 목록과 같은 이유다.
                    // eslint-disable-next-line @next/next/no-img-element
                    <img
                      key={src}
                      src={src}
                      alt=""
                      loading="lazy"
                      className="w-full rounded-lg border border-line-2"
                      onError={(e) => { e.currentTarget.style.display = "none"; }}
                    />
                  ))}
                </div>
              ) : (
                <div className="text-gray">본문 텍스트가 없습니다. {n.attachments.length ? "첨부파일을 확인하세요." : "원문에서 확인하세요."}</div>
              )}
            </div>

            {n.attachments.length > 0 && (
              <>
                <H5>첨부파일</H5>
                <div className="flex flex-wrap gap-1">
                  {n.attachments.map((a) =>
                    a.url ? (
                      <a key={a.id} href={a.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1.5 rounded-md border border-line px-[9px] py-[3px] text-[12.5px] text-ink-2 hover:border-navy">
                        <IconFile width={13} height={13} className="text-gray" />
                        {a.filename}
                        {a.size_bytes != null && <span className="text-gray-2">{fileSize(a.size_bytes)}</span>}
                      </a>
                    ) : (
                      <span key={a.id} className="inline-flex items-center gap-1.5 rounded-md border border-dashed border-line px-[9px] py-[3px] text-[12.5px] text-gray-2" title={a.status.label}>
                        <IconFile width={13} height={13} />
                        {a.filename} · {a.status.label}
                      </span>
                    ),
                  )}
                </div>
              </>
            )}

            {n.contact_mentions.length > 0 && (
              <>
                <H5>이 공지의 문의처</H5>
                <ul className="text-[13px] text-ink-2">
                  {n.contact_mentions.map((m, i) => (
                    <li key={i} className="py-0.5">
                      {m.text}
                      {m.channel?.action_url && (
                        <a href={m.channel.action_url} className="ml-2 text-navy underline underline-offset-2">
                          {m.channel.kind.code === "phone" ? "전화" : "메일"}
                        </a>
                      )}
                      {m.contact_id && (
                        <Link href={`/contacts?focus=${m.contact_id}`} className="ml-2 text-navy underline underline-offset-2">
                          연락처 보기
                        </Link>
                      )}
                    </li>
                  ))}
                </ul>
              </>
            )}

            <H5>출처 {n.sources.length}곳</H5>
            <div>
              {n.sources.map((s) => (
                <a key={s.id} href={s.url} target="_blank" rel="noreferrer" className={`mb-1.5 flex items-center gap-2 rounded-lg border px-3 py-[9px] text-[13px] hover:border-navy ${s.is_primary ? "border-line" : "border-line-2"}`}>
                  <MediumIcon code={s.medium.code} width={13} height={13} className="flex-none text-gray-2" />
                  <span className="min-w-0 truncate">{s.source_name}</span>
                  {s.published_date && <span className="flex-none text-xs text-gray-2">{fullDate(s.published_date)}</span>}
                  {s.original_status.code !== "available" && <span className="flex-none text-xs text-gray-2">· {s.original_status.label}</span>}
                  <span className="ml-auto flex-none text-xs text-navy">원문 보기</span>
                </a>
              ))}
            </div>

            {n.related_notices.length > 0 && (
              <>
                <H5>관련 공지</H5>
                {n.related_notices.map((r) => (
                  <Link key={r.id} href={`/notices/${r.id}`} className="block py-1 text-[13px] text-navy underline underline-offset-2">
                    {r.title} <span className="text-gray-2">({r.relation.label})</span>
                  </Link>
                ))}
              </>
            )}

            <a href={n.original_url} target="_blank" rel="noreferrer" className="mt-4 flex items-center justify-center gap-1.5 rounded-[10px] bg-red-fill py-3 text-sm font-bold text-white hover:bg-red-fill-2">
              {n.primary_source.medium.code === "instagram" ? "Instagram에서 원문 보기" : "원문 보기"}
              <IconExternal width={14} height={14} />
            </a>
          </article>
        </Box>
      )}
    </Shell>
  );
}

const Sep = () => <span className="text-line">|</span>;
const H5 = ({ children }: { children: React.ReactNode }) => <h5 className="mb-1.5 mt-3.5 text-xs font-medium text-gray">{children}</h5>;
