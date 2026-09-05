"use client";
// 내 공지: 비회원 맞춤 조회(POST /v1/feeds/preview)
import { useSettings } from "@/lib/settings";
import { useCatalog, useOrganizations } from "@/lib/queries";
import { useNoticeFeed } from "@/lib/useNoticeFeed";
import { useCategorySelection } from "@/lib/useCategorySelection";
import { Shell } from "@/components/Shell";
import { CategoryChips } from "@/components/CategoryChips";
import { NoticeList } from "@/components/NoticeList";

export default function MyFeedPage() {
  const { settings, ready } = useSettings();
  const catalog = useCatalog();
  const orgs = useOrganizations();
  const cats = useCategorySelection();

  const orgIds = [settings.college_id, settings.department_id].filter((x): x is string => !!x);
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

  const name = (id: string | null) => orgs.find((o) => o.id === id)?.name;
  const campusName = catalog?.campuses.find((c) => c.id === settings.campus_id)?.name.replace("캠퍼스", "");
  const scope = [campusName, name(settings.college_id), name(settings.department_id)].filter(Boolean).join(" · ");
  const label = `${scope || "설정 없음"}${settings.subscribed_source_ids.length ? ` + 구독 ${settings.subscribed_source_ids.length}` : ""}`;

  return (
    <Shell categories={{ selected: cats.selected, toggle: cats.toggle }}>
      {catalog && <CategoryChips categories={catalog.categories} selected={cats.selected} onToggle={cats.toggle} />}
      <NoticeList notices={feed.items} label={label} loading={!ready || feed.loading} error={feed.error} hasNext={feed.hasNext} onMore={feed.more} onRetry={feed.retry} />
    </Shell>
  );
}
