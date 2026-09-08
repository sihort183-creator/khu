"use client";
// 내 공지: 비회원 맞춤 조회(POST /v1/feeds/preview)
// 주소는 '/mine'. 2026-09-08 전에는 '/'였는데 전체 공지가 첫 화면이 되면서 자리를 내줬다.
// 조직 범위 규칙은 전체 공지와 같다(lib/api.ts 의 organizationScope). 다른 점은
// 구독한 게시판 공지가 여기에만 더해진다는 것 하나다.
import { useMemo } from "react";
import { useOrganizationSelection, useSettings } from "@/lib/settings";
import { useCatalog, useOrganizations } from "@/lib/queries";
import { campusScopeLabel } from "@/lib/campus";
import { useNoticeFeed } from "@/lib/useNoticeFeed";
import { useCategorySelection } from "@/lib/useCategorySelection";
import { Shell } from "@/components/Shell";
import { CategoryChips } from "@/components/CategoryChips";
import { NoticeList } from "@/components/NoticeList";
import { organizationScopeLabel } from "@/components/OrganizationPicker";

export default function MyFeedPage() {
  const { settings, ready } = useSettings();
  const catalog = useCatalog();
  const orgs = useOrganizations();
  const categoryCodes = useMemo(() => (catalog?.categories ?? []).map((c) => c.code), [catalog]);
  const cats = useCategorySelection(categoryCodes);
  const org = useOrganizationSelection();

  // 예전 저장값(단과대·학과)만 있고 옮겨 담기 전인 브라우저도 있으므로 함께 합친다.
  const orgIds = [...new Set([...org.ids, settings.college_id, settings.department_id].filter((x): x is string => !!x))];
  const feed = useNoticeFeed(
    ready
      ? {
          kind: "preview",
          body: {
            campus_id: settings.campus_id,
            organization_ids: orgIds,
            subscribed_source_ids: settings.subscribed_source_ids,
            filters: { category_code: cats.codes },
          },
        }
      : null,
  );

  const scope = orgIds.length ? organizationScopeLabel(orgs, new Set(orgIds)) : campusScopeLabel(catalog?.campuses, settings.campus_id);
  const label = `${scope}${settings.subscribed_source_ids.length ? ` + 구독 ${settings.subscribed_source_ids.length}` : ""}`;

  return (
    <Shell categories={{ selected: cats.selected, toggle: cats.toggle }} orgPicker>
      {catalog && <CategoryChips categories={catalog.categories} selected={cats.selected} onToggle={cats.choose} />}
      <NoticeList notices={feed.items} label={label} loading={!ready || feed.loading} error={feed.error} hasNext={feed.hasNext} onMore={feed.more} onRetry={feed.retry} />
    </Shell>
  );
}
