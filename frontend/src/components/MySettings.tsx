"use client";
// "내 설정" 알맹이: 캠퍼스 · 조직 · 화면(해|달|기기) · 소속 다시 고르기.
// 넓은 화면 오른쪽 상자(Shell)와 헤더의 사람 아이콘 패널(SettingsMenu)이 같은 것을 본다.
// 두 곳이 따로 적혀 있으면 한쪽만 고쳐져 서로 다른 말을 하게 되므로 여기 한 벌만 둔다.
import { useOrganizationSelection, useSettings } from "@/lib/settings";
import { useCatalog, useOrganizations } from "@/lib/queries";
import { campusShortLabel } from "@/lib/campus";
import { organizationScopeLabel } from "./OrganizationPicker";
import { ThemeSegments } from "./ThemeToggle";

interface Props {
  /** 손가락으로 누르는 곳(헤더 패널)에서는 '소속 다시 고르기'를 44px 로 키운다 */
  touch?: boolean;
  /** 온보딩을 띄우기 직전에 부른다(패널을 닫는 용도) */
  onReselect?: () => void;
}

export function MySettingsBody({ touch = false, onReselect }: Props) {
  const { settings, update } = useSettings();
  const catalog = useCatalog();
  const orgs = useOrganizations();
  const org = useOrganizationSelection(orgs);

  const campus = campusShortLabel(catalog?.campuses, settings.campus_id);
  const scope = organizationScopeLabel(orgs, org.selected);

  return (
    <div className="px-3.5 py-3 text-[13px]">
      <dl className="grid grid-cols-[56px_1fr] gap-y-1">
        <dt className="text-gray">캠퍼스</dt>
        <dd className="font-medium">{campus || "-"}</dd>
        <dt className="text-gray">조직</dt>
        <dd className="break-keep font-medium">{scope}</dd>
        <dt className="self-center text-gray">화면</dt>
        <dd className="flex items-center">
          <ThemeSegments />
        </dd>
      </dl>
      <button
        onClick={() => {
          onReselect?.();
          update({ onboarded: false });
        }}
        className={`mt-2.5 w-full rounded-lg border border-line bg-card py-1.5 text-[13px] text-ink-2 hover:border-gray ${touch ? "min-h-11" : ""}`}
      >
        소속 다시 고르기
      </button>
    </div>
  );
}
