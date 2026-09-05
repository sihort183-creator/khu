"use client";
// 캠퍼스 → 단과대 → 학과 3단계. 로그인 없음, localStorage 저장.
import { useEffect, useState } from "react";
import { useSettings } from "@/lib/settings";
import { useCatalog, useOrganizations } from "@/lib/queries";

export function Onboarding() {
  const { settings, ready } = useSettings();
  const open = ready && !settings.onboarded;
  if (!open) return null;
  // 열릴 때마다 새로 마운트되어 초기 상태를 설정에서 읽는다
  return <OnboardingDialog />;
}

function OnboardingDialog() {
  const { settings, update } = useSettings();
  const catalog = useCatalog();
  const orgs = useOrganizations();
  const [step, setStep] = useState(0);
  const [campus, setCampus] = useState<string | null>(settings.campus_id);
  const [college, setCollege] = useState<string | null>(settings.college_id);

  useEffect(() => {
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = "";
    };
  }, []);

  const campusOrg = orgs.find((o) => o.type.code === "campus" && o.campus_id === campus);
  const colleges = orgs.filter((o) => o.type.code === "college" && o.parent_id === campusOrg?.id);
  const departments = orgs.filter((o) => o.type.code === "department" && o.parent_id === college);
  const loaded = !!catalog && orgs.length > 0;

  const finish = (dept: string | null) => update({ campus_id: campus ?? settings.campus_id, college_id: college, department_id: dept, onboarded: true });
  const skip = () => update({ onboarded: true });
  const titles = ["캠퍼스를 선택하세요", "단과대를 선택하세요", "학과를 선택하세요"];

  return (
    <div className="fixed inset-0 z-50 overflow-auto border-t-4 border-red bg-white" role="dialog" aria-modal="true" aria-labelledby="onb-title">
      <div className="mx-auto max-w-[440px] px-6 py-10">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src="https://www.khu.ac.kr/upload/cross/images/000001/imgSub0401.jpg" alt="경희대학교" className="mb-6 h-11" />
        <div className="text-xs font-semibold text-red">{step + 1} / 3</div>
        <h1 id="onb-title" className="mb-1.5 mt-1 text-[22px] font-bold leading-[1.4]">
          {titles[step]}
        </h1>
        <p className="mb-[18px] text-[13px] text-gray">선택한 캠퍼스·단과대·학과의 공지가 &lsquo;내 공지&rsquo;에 모입니다. 로그인 없이 이 브라우저에만 저장됩니다.</p>

        {!loaded && <p className="text-[13px] text-gray">불러오는 중…</p>}

        {loaded &&
          step === 0 &&
          catalog.campuses.map((c) => (
            <Opt
              key={c.id}
              onClick={() => {
                setCampus(c.id);
                setCollege(null);
                setStep(1);
              }}
            >
              {c.name}
            </Opt>
          ))}

        {loaded && step === 1 && colleges.length === 0 && (
          <p className="mb-2 text-[13px] text-gray">이 캠퍼스의 단과대 정보가 아직 없습니다. 건너뛰면 캠퍼스 공지만 모아 봅니다.</p>
        )}
        {loaded &&
          step === 1 &&
          colleges.map((c) => {
            const n = orgs.filter((o) => o.parent_id === c.id && o.type.code === "department").length;
            return (
              <Opt
                key={c.id}
                sub={n ? `${n}개 학과` : ""}
                onClick={() => {
                  setCollege(c.id);
                  setStep(2);
                }}
              >
                {c.name}
              </Opt>
            );
          })}

        {loaded && step === 2 && departments.map((d) => <Opt key={d.id} onClick={() => finish(d.id)}>{d.name}</Opt>)}
        {loaded && step === 2 && departments.length === 0 && (
          <Opt onClick={() => finish(null)}>학과 없이 {orgs.find((o) => o.id === college)?.name} 공지만 보기</Opt>
        )}

        <div className="mt-5 flex justify-between text-[13px] text-gray">
          <button onClick={() => setStep((s) => Math.max(0, s - 1))} className={`underline underline-offset-[3px] ${step === 0 ? "invisible" : ""}`}>
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
    <button onClick={onClick} className="mb-2 flex w-full items-center rounded-[10px] border border-line bg-white px-4 py-[13px] text-left text-[15px] transition-colors hover:border-red hover:bg-[#FFFBFB]">
      {children}
      {sub && <small className="ml-auto text-xs text-gray-2">{sub}</small>}
    </button>
  );
}
