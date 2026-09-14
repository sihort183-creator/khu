"use client";
// 전체 공지: GET /v1/notices + 조직·출처 유형 필터 + 검색
// 조직은 Shell의 선택기(좌측 열 / 모바일 접힘 상자)가 저장한 값을 그대로 쓴다.
//
// 이 화면이 첫 화면('/')이다. 2026-09-08 사용자 지시로 '/all' 에서 옮겨 왔고, 옛 주소는
// src/app/all/page.tsx 가 물음표 뒤 값까지 들고 여기로 넘긴다.
// 조직 범위는 '내 공지'와 같은 함수(lib/api.ts 의 organizationScope)를 쓴다.
//
// 2026-09-08 모바일 점검: 375px 에서 첫 공지 줄이 352px(화면의 43%)까지 밀려 있었다.
// 검색 상자와 '출처 유형'은 좁은 화면에서 접어 두고(아래 주석 참고) 넓은 화면에서는
// 지금 자리를 그대로 지킨다.
import { Suspense, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import { useOrganizationSelection, useSettings } from "@/lib/settings";
import { useCatalog, useOrganizations } from "@/lib/queries";
import { campusScopeLabel } from "@/lib/campus";
import { useNoticeFeed } from "@/lib/useNoticeFeed";
import { useCategorySelection } from "@/lib/useCategorySelection";
import { Shell } from "@/components/Shell";
import { CategoryChips } from "@/components/CategoryChips";
import { NoticeList, type NoticeSort } from "@/components/NoticeList";
import { organizationScopeLabel } from "@/components/OrganizationPicker";
import { IconChevron, IconSearch } from "@/components/icons";

export default function AllPage() {
  return (
    <Suspense>
      <AllFeed />
    </Suspense>
  );
}

function AllFeed() {
  const { settings } = useSettings();
  const params = useSearchParams();
  const catalog = useCatalog();
  const orgs = useOrganizations();
  const categoryCodes = useMemo(() => (catalog?.categories ?? []).map((c) => c.code), [catalog]);
  const cats = useCategorySelection(categoryCodes);
  const org = useOrganizationSelection(orgs);
  const [medium, setMedium] = useState("");
  // 검색어는 주소에만 산다(점검 문서 "상" 5번의 절반). 상태로도 들고 있으면 뒤로 가기로
  // 주소가 '/' 로 돌아왔는데 목록은 여전히 걸러진 채인 어긋남이 생긴다. 입력하면
  // replaceState 로 같은 자리를 고쳐 쓴다 — 새 기록을 쌓지 않으므로 뒤로 가기는 검색을
  // 시작하기 전 화면으로 한 번에 간다.
  const q = (params.get("q") ?? "").trim().slice(0, 100);
  const qRef = useRef<HTMLInputElement>(null);
  /**
   * 정렬도 검색어와 같이 주소에만 산다. 상태로도 들고 있으면 뒤로 가기로 주소가 돌아왔는데
   * 목록만 그대로인 어긋남이 생긴다.
   *
   * 검색어가 있을 때의 기본은 연관도순이라 주소에는 **최신순일 때만** 적는다. 기본값을
   * 굳이 적어 두면 주소가 길어지기만 하고 뜻이 늘지 않는다. 검색어가 없으면 연관도 자체가
   * 뜻이 없으므로 값을 읽지 않는다 — 그때는 단추도 나오지 않는다.
   */
  const sort: NoticeSort = q && params.get("sort") !== "recent" ? "relevance" : "recent";

  // 검색 상자는 늘 보인다(2026-09-08 사용자 결정: 헤더 돋보기를 빼고 상자는 그대로).
  // ?focus=q 는 옛 링크 호환으로 초점만 준다.

  useEffect(() => {
    if (params.get("focus") === "q") qRef.current?.focus();
  }, [params]);

  /**
   * 검색어를 주소에 적는다. 기록을 쌓지 않도록 replaceState 만 쓴다(Next 가 이 호출을
   * useSearchParams 와 이어 주므로 위 q 도 함께 바뀐다).
   */
  const replaceParams = (edit: (sp: URLSearchParams) => void) => {
    const sp = new URLSearchParams(window.location.search);
    edit(sp);
    // 돋보기가 붙인 한 번짜리 표시다. 검색이 끝났으면 남길 이유가 없다.
    sp.delete("focus");
    const s = sp.toString();
    window.history.replaceState(null, "", s ? `?${s}` : window.location.pathname);
  };

  const search = (next: string) => replaceParams((sp) => {
    if (next) sp.set("q", next);
    // 검색어를 지우면 정렬도 함께 지운다. 연관도순은 검색어가 있어야 뜻이 있는 값이라,
    // 남겨 두면 주소에는 sort=recent 가 있는데 화면에는 고른 자국이 없는 꼴이 된다.
    else {
      sp.delete("q");
      sp.delete("sort");
    }
  });

  const chooseSort = (next: NoticeSort) => replaceParams((sp) => {
    if (next === "recent") sp.set("sort", "recent");
    else sp.delete("sort");
  });

  const feed = useNoticeFeed({
    kind: "list",
    query: {
      campus_id: settings.campus_id ? [settings.campus_id] : [],
      organization_id: org.ids,
      include_descendants: true,
      category_code: cats.codes,
      medium: medium ? [medium] : [],
      q: q || undefined,
      sort,
    },
  });

  const scope = org.ids.length ? organizationScopeLabel(orgs, org.selected) : campusScopeLabel(catalog?.campuses, settings.campus_id);
  const label = q ? `‘${q}’ 검색 결과` : scope;
  // 검색은 캠퍼스 설정을 넘어간다(사용자 결정 2026-09-14, lib/api.ts 의 staticNoticeMatches).
  // 서울캠퍼스로 두고 '스페인어학과'를 찾으면 국제캠퍼스 공지가 섞여 나온다. 왜 그런지
  // 모르면 그것대로 이상하므로 라벨 옆에 한 줄만 덧붙인다 - 배너나 경고 상자는 두지 않는다.
  // 검색어가 없거나 캠퍼스를 안 골랐으면 넘을 범위 자체가 없어 아무것도 적지 않는다.
  const note = q && settings.campus_id ? "캠퍼스 구분 없이 찾았습니다" : undefined;

  return (
    <Shell categories={{ selected: cats.selected, toggle: cats.toggle }} orgPicker>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          search((qRef.current?.value ?? "").trim().slice(0, 100));
        }}
        className="mb-2.5 flex items-center gap-2 rounded-[10px] border border-line bg-card px-[13px] py-[9px] focus-within:border-navy"
      >
        <IconSearch className="text-gray" width={15} height={15} />
        {/* 값을 React 상태로 붙들지 않는다. key 를 주소의 검색어로 두어, 뒤로 가기로 주소가
            바뀌면 칸의 글자도 따라 바뀌게 한다(상태로 들고 있으면 목록만 되돌아간다). */}
        {/* 안내 문구는 실제로 찾아지는 것만 적는다. 예전에는 '제목·본문·기관명'이었는데
            본문은 공지 색인에 아예 없어 지금도 앞으로도 찾아지지 않는다(lib/api.ts 의
            noticeSearchText 주석 참고). 기관 이름은 2026-09-14 부터 찾아진다. */}
        <input key={q} ref={qRef} defaultValue={q} placeholder="제목·기관명 검색" className="min-w-0 flex-1 bg-transparent text-sm outline-none" maxLength={100} aria-label="공지 검색" />
        {q && (
          <button
            type="button"
            onClick={() => search("")}
            className="shrink-0 text-xs text-gray"
          >
            지우기
          </button>
        )}
      </form>

      {/* 이 줄은 모바일에서 고를 것이 실제로 있을 때만 자리를 차지한다. */}
      <div className={`no-scrollbar mb-0.5 gap-1.5 overflow-x-auto pb-2 ${medium || org.ids.length > 0 ? "flex" : "hidden lg:flex"}`}>
        {/* 출처 유형: 지금 인스타그램·기관 제공 자료는 게시판이 0개라 웹 하나만 고를 수 있다.
            고를 것이 없는 드롭다운에 모바일 첫 화면 41px 을 내줄 수 없어 넓은 화면에만 둔다.
            이미 골라 둔 값이 있으면 좁은 화면에서도 보여야 한다 — 안 그러면 왜 적게 나오는지
            알 수도, 되돌릴 수도 없다. */}
        <div className={`flex-none ${medium ? "" : "hidden lg:block"}`}>
          <Select value={medium} onChange={setMedium} placeholder="출처 유형" options={(catalog?.media ?? []).map((m) => [m.code, m.label])} />
        </div>
        {org.ids.length > 0 && (
          <button type="button" onClick={org.clear} className="flex-none rounded-lg border border-line bg-card px-[11px] py-1.5 text-[13px] text-ink-2 hover:border-gray-2">
            조직 {org.ids.length}곳 해제
          </button>
        )}
      </div>

      {catalog && <CategoryChips categories={catalog.categories} selected={cats.selected} onToggle={cats.choose} />}
      {/* 정렬 단추는 검색어가 있을 때만 준다. 연관도는 검색어가 있어야 뜻이 있는 값이다. */}
      <NoticeList
        notices={feed.items}
        label={label}
        note={note}
        sort={q ? sort : undefined}
        onSort={q ? chooseSort : undefined}
        loading={feed.loading}
        error={feed.error}
        hasNext={feed.hasNext}
        onMore={feed.more}
        onRetry={feed.retry}
      />
    </Shell>
  );
}

function Select({ value, onChange, placeholder, options, disabled }: { value: string; onChange: (v: string) => void; placeholder: string; options: [string, string][]; disabled?: boolean }) {
  const on = !!value;
  return (
    <label className={`relative inline-flex flex-none items-center rounded-lg border bg-card text-[13px] ${on ? "border-navy font-medium text-navy" : "border-line text-ink-2"} ${disabled ? "opacity-50" : ""}`}>
      <select value={value} onChange={(e) => onChange(e.target.value)} disabled={disabled} aria-label={placeholder} className="appearance-none bg-transparent py-1.5 pl-[11px] pr-7 outline-none">
        <option value="">{placeholder}</option>
        {options.map(([v, l]) => (
          <option key={v} value={v}>
            {l}
          </option>
        ))}
      </select>
      <IconChevron className="pointer-events-none absolute right-2.5 opacity-60" width={10} height={10} strokeWidth={3} />
    </label>
  );
}
