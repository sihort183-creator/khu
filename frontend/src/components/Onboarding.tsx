"use client";
// 캠퍼스 → 단과대 → 학과 3단계. 로그인 없음, localStorage 저장.
// 공개 파일에 조직 계층(parent_id)이 없어 "이 단과대의 학과"를 좁힐 수 없다.
// 그래서 종류별 전체 목록 + 검색으로 고르게 하고, 건너뛰어도 화면이 비지 않도록
// 이후 조직 선택은 목록 쪽 체크(OrganizationPicker)에 맡긴다.
import { useEffect, useMemo, useState } from "react";
import type { Organization, OrgType } from "@/lib/types";
import { useSettings } from "@/lib/settings";
import { useCatalog, useOrganizations } from "@/lib/queries";
import { campusOptions } from "@/lib/campus";
import { IconSearch } from "./icons";

export function Onboarding() {
  const { settings, ready } = useSettings();
  const open = ready && !settings.onboarded;
  if (!open) return null;
  // 열릴 때마다 새로 마운트되어 초기 상태를 설정에서 읽는다
  return <OnboardingDialog />;
}

/** 게시판이 붙은 곳을 위로. 아무 공지도 오지 않는 이름만 늘어놓으면 고를 수가 없다. */
function byUsefulness(list: Organization[], type: OrgType) {
  return list
    .filter((o) => o.type.code === type)
    .sort((a, b) => (b.source_count ?? 0) - (a.source_count ?? 0) || a.name.localeCompare(b.name, "ko"));
}

function OnboardingDialog() {
  const { settings, update } = useSettings();
  const catalog = useCatalog();
  const orgs = useOrganizations();
  const [step, setStep] = useState(0);
  const [campus, setCampus] = useState<string | null>(settings.campus_id);
  const [college, setCollege] = useState<string | null>(settings.college_id);
  const [q, setQ] = useState("");

  useEffect(() => {
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = "";
    };
  }, []);

  const colleges = useMemo(() => byUsefulness(orgs, "college"), [orgs]);
  const departments = useMemo(() => byUsefulness(orgs, "department"), [orgs]);
  const loaded = !!catalog && orgs.length > 0;

  const keyword = q.trim().toLowerCase();
  const visible = (list: Organization[]) => (keyword ? list.filter((o) => o.name.toLowerCase().includes(keyword)) : list);

  // campus 는 '공통'이 null 이라 ?? 로 예전 값을 덧대면 공통을 고를 수가 없다. 고른 값을 그대로 쓴다.
  const finish = (dept: string | null) =>
    update({
      campus_id: campus,
      college_id: college,
      department_id: dept,
      organization_ids: [college, dept].filter((id): id is string => !!id),
      onboarded: true,
    });
  // 캠퍼스만 고르고 다음 단계에서 건너뛰면 고른 캠퍼스가 저장되지 않았다.
  // '공통'을 고른 사람이 그대로 예전 캠퍼스에 머무르게 되므로 고른 값은 남긴다.
  const skip = () => update({ campus_id: campus, onboarded: true });
  const titles = ["캠퍼스를 선택하세요", "단과대·대학원을 선택하세요", "학과·전공을 선택하세요"];

  return (
    <div className="fixed inset-0 z-50 overflow-auto border-t-4 border-red bg-white" role="dialog" aria-modal="true" aria-labelledby="onb-title">
      <div className="mx-auto max-w-[440px] px-6 py-10">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src="https://www.khu.ac.kr/upload/cross/images/000001/imgSub0401.jpg" alt="경희대학교" className="mb-6 h-11" />
        <div className="text-xs font-semibold text-red">{step + 1} / 3</div>
        <h1 id="onb-title" className="mb-1.5 mt-1 text-[22px] font-bold leading-[1.4]">
          {titles[step]}
        </h1>
        <p className="mb-[18px] text-[13px] text-gray">
          선택한 곳의 공지가 &lsquo;내 공지&rsquo;에 모입니다. 건너뛰어도 목록에서 언제든 체크해 볼 수 있습니다. 로그인 없이 이 브라우저에만 저장됩니다.
        </p>

        {!loaded && <p className="text-[13px] text-gray">불러오는 중…</p>}

        {/* 캠퍼스를 모르는 사람이 여기서 멈추지 않도록 빠져나갈 길을 먼저 알려 준다 */}
        {loaded && step === 0 && (
          <p className="mb-[18px] -mt-2 text-[13px] text-gray">
            잘 모르겠거나 둘 다 보고 싶으면 <b className="font-semibold text-ink-2">공통</b>을 고르세요. 화면 위쪽에서 언제든 바꿀 수 있습니다.
          </p>
        )}

        {loaded &&
          step === 0 &&
          campusOptions(catalog.campuses).map((c) => (
            <Opt
              key={c.id ?? "all"}
              sub={c.hint}
              onClick={() => {
                setCampus(c.id);
                setCollege(null);
                setQ("");
                setStep(1);
              }}
            >
              {c.name}
            </Opt>
          ))}

        {loaded && step > 0 && (
          <div className="mb-2.5 flex items-center gap-2 rounded-[10px] border border-line bg-white px-[13px] py-[11px] focus-within:border-navy">
            <IconSearch className="shrink-0 text-gray" width={15} height={15} />
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="이름으로 찾기" aria-label="조직 검색" className="w-full min-w-0 bg-transparent text-sm outline-none" />
          </div>
        )}

        {loaded &&
          step === 1 &&
          visible(colleges).map((c) => (
            <Opt
              key={c.id}
              sub={c.source_count ? `게시판 ${c.source_count}` : ""}
              onClick={() => {
                setCollege(c.id);
                setQ("");
                setStep(2);
              }}
            >
              {c.name}
            </Opt>
          ))}

        {loaded &&
          step === 2 &&
          visible(departments).map((d) => (
            <Opt key={d.id} sub={d.source_count ? `게시판 ${d.source_count}` : ""} onClick={() => finish(d.id)}>
              {d.name}
            </Opt>
          ))}

        {loaded && step > 0 && visible(step === 1 ? colleges : departments).length === 0 && <p className="mb-2 text-[13px] text-gray">검색 결과가 없습니다.</p>}

        <div className="mt-5 flex justify-between text-[13px] text-gray">
          <button
            onClick={() => {
              setQ("");
              setStep((s) => Math.max(0, s - 1));
            }}
            className={`underline underline-offset-[3px] ${step === 0 ? "invisible" : ""}`}
          >
            이전
          </button>
          <button onClick={step === 2 ? () => finish(null) : skip} className="underline underline-offset-[3px]">
            {step === 2 ? "학과 없이 완료" : "건너뛰기"}
          </button>
        </div>
      </div>
    </div>
  );
}

function Opt({ children, sub, onClick }: { children: React.ReactNode; sub?: string; onClick: () => void }) {
  return (
    <button onClick={onClick} className="mb-2 flex w-full items-center gap-2 rounded-[10px] border border-line bg-white px-4 py-[13px] text-left text-[15px] transition-colors hover:border-red hover:bg-[#FFFBFB]">
      <span className="min-w-0 flex-1 break-keep">{children}</span>
      {sub && <small className="shrink-0 text-xs text-gray-2">{sub}</small>}
    </button>
  );
}
