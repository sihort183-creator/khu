"use client";
// 연락처: GET /v1/contacts. 공지와 별개의 정보 분류.
import { Suspense, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
import type { Channel, Contact } from "@/lib/types";
import { useSettings } from "@/lib/settings";
import { useContacts } from "@/lib/queries";
import { relativeTime } from "@/lib/format";
import { Shell } from "@/components/Shell";
import { IconSearch, IconPhone, IconExternal } from "@/components/icons";

export default function ContactsPage() {
  return (
    <Suspense>
      <Contacts />
    </Suspense>
  );
}

function Contacts() {
  const { settings } = useSettings();
  const focus = useSearchParams().get("focus");
  const { contacts, loading } = useContacts(settings.campus_id);
  const [q, setQ] = useState("");

  useEffect(() => {
    if (focus && !loading) document.getElementById(`contact-${focus}`)?.scrollIntoView({ block: "center" });
  }, [focus, loading]);

  const list = useMemo(() => {
    const qq = q.trim().toLowerCase();
    const filtered = contacts.filter(
      (ct) =>
        !qq ||
        `${ct.organization.name} ${ct.organization.path.join(" ")} ${ct.service_name} ${ct.location ?? ""} ${ct.channels.map((c) => `${c.display_value} ${c.value ?? ""}`).join(" ")}`.toLowerCase().includes(qq),
    );
    // 내 학과·단과대를 먼저
    const mine = new Set([settings.department_id, settings.college_id].filter(Boolean));
    return [...filtered].sort((a, b) => Number(mine.has(b.organization.id)) - Number(mine.has(a.organization.id)));
  }, [contacts, q, settings.department_id, settings.college_id]);

  return (
    <Shell>
      <div className="mb-2.5 flex items-center gap-2 rounded-[10px] border border-line bg-card px-[13px] py-[9px] focus-within:border-navy">
        <IconSearch className="text-gray" width={15} height={15} />
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="부서·학과·업무로 검색 (장학, 휴학, 기숙사 …)" className="flex-1 bg-transparent text-sm outline-none" maxLength={100} aria-label="연락처 검색" />
      </div>
      {loading && <div className="py-10 text-center text-[13px] text-gray">불러오는 중…</div>}
      {!loading && list.length === 0 && <div className="rounded-box border border-line bg-card px-4 py-10 text-center text-[13px] text-gray">검색 결과가 없습니다</div>}
      <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2">
        {list.map((ct) => (
          <ContactCard key={ct.id} contact={ct} highlight={ct.id === focus} />
        ))}
      </div>
    </Shell>
  );
}

/**
 * 전화는 걸 수 있는 번호를 보여준다.
 *
 * 원문 전화번호부는 내선만 적는다("0920"). 그대로 보여주면 걸 수 없는 숫자가 뜬다.
 * 수집할 때 캠퍼스 대표번호를 붙여 온전한 번호를 만들어 두었으므로 그것을 쓴다.
 * 원문이 "3921~9" 처럼 범위로 적은 경우 그 꼬리를 살려 붙인다. 안내판에 적힌 대로 읽힌다.
 * 팩스도 같은 방식으로 적혀 있으므로 같이 쓴다.
 */
function dialLabel(c: Channel): string {
  if (!c.value) return c.display_value;
  if (c.extension && c.display_value.startsWith(c.extension) && c.display_value !== c.extension) {
    return c.value + c.display_value.slice(c.extension.length);
  }
  return c.value;
}

function ContactCard({ contact: ct, highlight }: { contact: Contact; highlight: boolean }) {
  const phones = ct.channels.filter((c) => c.kind.code === "phone");
  const others = ct.channels.filter((c) => c.kind.code !== "phone");
  const ver = ct.verification;
  const verColor = ver.code === "verified" ? "text-gray-2" : ver.code === "stale" ? "text-warn-ink" : "text-red";
  const primaryPhone = phones.find((p) => p.action_url);
  const evidenceUrl = ct.evidence[0]?.url;

  return (
    <div id={`contact-${ct.id}`} className={`rounded-box border bg-card px-4 py-3.5 shadow-box ${highlight ? "border-navy" : "border-line"}`}>
      <div className="text-[11.5px] font-semibold text-gold">{ct.organization.type.label}</div>
      <div className="mb-0.5 mt-px text-[15px] font-bold">{ct.organization.name}</div>
      <div className="mb-2 text-[13px] text-ink-2">{ct.service_name}</div>
      <dl className="grid grid-cols-[52px_1fr] gap-y-[3px] text-[13px]">
        {/* 번호가 여러 개인 곳이 68 곳 있다. 줄마다 "전화"를 되풀이하면 읽기 어려우므로 첫 줄에만 붙인다. */}
        {phones.map((p, index) => (
          <Row key={p.id} label={index === 0 ? "전화" : ""}>
            {p.action_url ? (
              <a href={p.action_url} className="text-navy">
                {dialLabel(p)}
              </a>
            ) : (
              <span>{dialLabel(p)}</span>
            )}
          </Row>
        ))}
        {others.map((c) => (
          <Row key={c.id} label={c.kind.label}>
            {c.action_url ? (
              <a href={c.action_url} target={c.kind.code === "website" ? "_blank" : undefined} rel="noreferrer" className="text-navy">
                {c.kind.code === "fax" ? dialLabel(c) : c.display_value}
              </a>
            ) : (
              <span>{c.kind.code === "fax" ? dialLabel(c) : c.display_value}</span>
            )}
          </Row>
        ))}
        <Row label="위치">{ct.location ?? <Empty />}</Row>
        <Row label="운영">{ct.office_hours ?? <Empty />}</Row>
      </dl>
      <div className="mt-2.5 flex flex-wrap gap-1.5">
        {primaryPhone && (
          <a href={primaryPhone.action_url!} className="inline-flex items-center gap-1 rounded-full bg-tint-red px-[11px] py-[5px] text-xs font-semibold text-red">
            <IconPhone width={12} height={12} /> 전화
          </a>
        )}
        {ct.official_url && (
          <a href={ct.official_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 rounded-full border border-line px-[11px] py-[5px] text-xs text-ink-2 hover:border-gray">
            홈페이지 <IconExternal width={11} height={11} />
          </a>
        )}
      </div>
      <div className={`mt-2.5 text-[11px] ${verColor}`} title={ver.message}>
        {ver.label}
        {ver.verified_at && ` · ${relativeTime(ver.verified_at)}`}
        {evidenceUrl && (
          <>
            {" · "}
            <a href={evidenceUrl} target="_blank" rel="noreferrer" className="underline underline-offset-2">
              근거
            </a>
          </>
        )}
      </div>
    </div>
  );
}

const Row = ({ label, children }: { label: string; children: React.ReactNode }) => (
  <>
    <dt className="text-gray">{label}</dt>
    <dd className="text-ink-2">{children}</dd>
  </>
);
const Empty = () => <span className="text-gray-2">-</span>;
