"use client";
// 캠퍼스 → 단과대 → 학과 3단계. 로그인 없음, localStorage 저장.
// 공개 파일에 조직 계층(parent_id)이 없어 "이 단과대의 학과"를 좁힐 수 없다.
// 그래서 종류별 전체 목록 + 검색으로 고르게 하고, 건너뛰어도 화면이 비지 않도록
// 이후 조직 선택은 목록 쪽 체크(OrganizationPicker)에 맡긴다.
import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import type { Organization, OrgType } from "@/lib/types";
import { useSettings } from "@/lib/settings";
import { useCatalog, useOrganizations } from "@/lib/queries";
import { compareOrgNames, drawnOrgIds, normalizeOrganizationSelection } from "@/lib/orgTree";
import { campusOptions } from "@/lib/campus";
import { IconSearch } from "./icons";

export function Onboarding() {
  const { settings, ready } = useSettings();
  const open = ready && !settings.onboarded;
  if (!open) return null;
  // 열릴 때마다 새로 마운트되어 초기 상태를 설정에서 읽는다
  return <OnboardingDialog />;
}

/** 폐쇄·대기를 뺀 게시판 수. 폐쇄된 게시판을 "게시판 4"로 보여 주면 거짓말이 된다. */
function liveBoards(o: Organization) {
  return o.active_source_count ?? o.source_count ?? 0;
}

/**
 * 조직별로 화면에 적을 게시판 수. 별칭 조직(같은 곳의 다른 이름)의 것은 본체에 합친다.
 * 목록에는 본체 한 줄만 나오므로, 합치지 않으면 게시판이 있는 학과가 "0"으로 보인다.
 */
function boardsByOrg(orgs: Organization[]): Map<string, number> {
  const drawn = drawnOrgIds(orgs, new Map(orgs.map((o) => [o.id, o])));
  const out = new Map<string, number>();
  for (const org of orgs) {
    const id = drawn.get(org.id) ?? org.id;
    out.set(id, (out.get(id) ?? 0) + liveBoards(org));
  }
  return out;
}

/**
 * 고른 캠퍼스에 있는 곳인가. 캠퍼스가 하나도 안 붙은 조직은 "그 캠퍼스가 아니다"가 아니라
 * "아직 모른다"는 뜻이므로 감추지 않는다. 캠퍼스를 안 골랐으면(공통) 전부 보인다.
 */
function inCampus(org: Organization, campusId: string | null) {
  if (!campusId) return true;
  const list = org.campuses ?? (org.campus_id ? [{ id: org.campus_id, name: "" }] : []);
  return list.length === 0 || list.some((c) => c.id === campusId);
}

/**
 * ㄱㄴㄷ순. 조직 선택기(lib/orgTree.ts)와 같은 정렬 규칙을 쓴다 — 두 화면에서 같은 이름이
 * 다른 자리에 있으면 찾을 수가 없다. 별칭 조직은 빼서 같은 학과가 두 번 나오지 않게 한다.
 */
function byName(list: Organization[], type: OrgType, campusId: string | null) {
  return list.filter((o) => o.type.code === type && !o.is_alias && inCampus(o, campusId)).sort((a, b) => compareOrgNames(a.name, b.name));
}

function OnboardingDialog() {
  const { settings, update } = useSettings();
  const router = useRouter();
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

  const boards = useMemo(() => boardsByOrg(orgs), [orgs]);
  const colleges = useMemo(() => byName(orgs, "college", campus), [orgs, campus]);
  const departments = useMemo(() => byName(orgs, "department", campus), [orgs, campus]);
  const loaded = !!catalog && orgs.length > 0;

  const keyword = q.trim().toLowerCase();
  const visible = (list: Organization[]) => (keyword ? list.filter((o) => o.name.toLowerCase().includes(keyword)) : list);

  // campus 는 '공통'이 null 이라 ?? 로 예전 값을 덧대면 공통을 고를 수가 없다. 고른 값을 그대로 쓴다.
  //
  // 소속을 고른 사람은 '내 공지'로 데려간다. 위 문구가 "선택한 곳의 공지가 '내 공지'에
  // 모입니다"라고 약속했는데, 2026-09-08 첫 화면이 '전체 공지'로 바뀌면서 그대로 두면
  // 방금 고른 결과를 아무 데서도 보지 못한 채 창만 닫히기 때문이다. 건너뛴 사람은
  // 고른 것이 없으니 보고 있던 화면에 그대로 둔다.
  const finish = (dept: string | null) => {
    // 목록 쪽 체크와 같은 규칙을 먹인다(lib/orgTree.ts). 고른 학과가 그 단과대 밑이면
    // 단과대는 빠지고 학과만 남는다 — 안 그러면 온보딩을 마치자마자 단과대 전체가 보인다.
    const organization_ids = normalizeOrganizationSelection([college, dept].filter((id): id is string => !!id), orgs);
    update({ campus_id: campus, college_id: college, department_id: dept, organization_ids, onboarded: true });
    if (organization_ids.length) router.push("/mine");
  };
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
              sub={boards.get(c.id) ? `게시판 ${boards.get(c.id)}` : ""}
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
            <Opt key={d.id} sub={boards.get(d.id) ? `게시판 ${boards.get(d.id)}` : ""} onClick={() => finish(d.id)}>
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
