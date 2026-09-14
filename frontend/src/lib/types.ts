// KHU_API_CONTRACT_DRAFT.md(2026-09-06) 기준 타입.
// 실제 OpenAPI가 확정되면 이 파일을 생성 코드로 교체한다.

export interface Coded {
  code: string;
  label: string;
}

export interface Meta {
  request_id: string;
  generated_at: string;
}

export interface Page {
  next_cursor: string | null;
  has_next: boolean;
  snapshot_at: string;
  dataset_revision: string;
}

export interface ListResponse<T> {
  data: T[];
  page: Page;
  meta: Meta;
}

export interface ItemResponse<T> {
  data: T;
  meta: Meta;
}

export interface ApiError {
  error: {
    code: string;
    message: string;
    retryable: boolean;
    details?: { field: string; message: string }[];
    request_id: string;
  };
}

/* ---------- catalog ---------- */
export interface Campus {
  id: string;
  name: string;
}

export interface Catalog {
  campuses: Campus[];
  organization_types: Coded[];
  categories: Coded[];
  media: Coded[];
  features: { accounts: boolean; instagram: boolean; deadlines: boolean };
  initial_window_start?: string | null;
  /**
   * 바로가기 카드(설계문서 2절). 화면이 어차피 받는 파일에 얹혀 오므로 학생당 추가
   * 요청이 0건이다. 예전 개정에는 없으니 없을 수 있다.
   */
  shortcut_cards?: ShortcutCard[];
}

/* ---------- 바로가기 카드 ---------- */

/**
 * 카드 한 줄. 라벨과 값, 그리고 **조건**이다.
 *
 * `cond` 는 적용 대상 제한(편입생·외국인·학번·트랙)이다. 값에서 떼면 거짓말이 된다 —
 * 편입생이 SW 6학점을 들어야 하는 줄 알고 한국 학생이 TOPIK 을 준비한다(설계문서 5.8).
 * rows 64줄 중 34줄에 붙어 있다. 그리지 못할 바에는 줄을 통째로 빼는 것이 규칙이다
 * (lib/cards.ts 의 keepRows).
 */
export interface ShortcutCardRow {
  label: string;
  value: string;
  cond?: string;
  /** 값 아래에 한 단 작게 붙는 덧붙임. 조건이 아니다. */
  sub?: string;
  strong?: boolean;
}

/** 카드 아래 띠 한 칸. 글만 있거나(text), 링크이거나(link+url) 둘 중 하나다. */
export interface ShortcutCardFoot {
  text?: string;
  link?: string;
  url?: string;
}

export interface ShortcutCard {
  id: string;
  title: string;
  source: { name: string; url: string };
  /** 이 말이 검색어에 들어 있을 때만 카드가 뜬다. */
  match: string[];
  rows: ShortcutCardRow[];
  more?: { summary: string; items: string[] };
  /** 이 말이 든 공지가 최근에 올라왔으면 "최근 안내가 올라왔습니다" 줄을 붙인다(5.6). */
  watch?: string[];
  foot?: ShortcutCardFoot[];
  /** 있으면 2형(값 표시) 카드다. 학과별 값을 따로 받아 카드 안에서 보여 준다. */
  dept?: { file: string; key: string; count?: number; fallback?: string };
}

/* ---------- 학과별 졸업요건 (별도 파일) ---------- */

/** 시행세칙 조문 하나. 원문 text 를 그대로 들고, 대상 제한은 따로 표시한다. */
export interface GraduationArticle {
  article?: string;
  text: string;
  /** 적용 대상 제한. 비어 있지 않으면 조건으로 반드시 함께 그린다(5.8). */
  applies_to?: string[];
  conditions?: string[];
  /** 전교 공통 조문과 값이 다르다는 표시. 학과가 더 엄할 수 있다. */
  differs_from_common?: boolean;
  note?: string;
}

export interface GraduationTrack {
  basic?: number;
  required?: number;
  total?: number;
}

export interface GraduationDepartment {
  department: string;
  college: string;
  campus: string;
  /** 이 값을 뽑은 시행세칙의 학년도. 학생의 학번이 아니다. */
  year: number | null;
  source: { url: string; kind?: string; doc?: string; page?: number };
  graduation_credits?: { value: number; article?: string; text?: string };
  major_credits?: {
    tracks?: { single?: GraduationTrack; multi?: GraduationTrack; minor?: GraduationTrack };
    article?: string;
    text?: string;
    minor_article?: string;
    minor_text?: string;
  };
  thesis?: GraduationArticle;
  language_cert?: GraduationArticle[];
  transfer_students?: GraduationArticle[];
  area_requirements?: GraduationArticle;
  shared_article_overrides?: Record<string, GraduationArticle>;
  /** 원문에서 못 뽑은 칸과 그 사유. 값을 지어내지 않았다는 기록이다. */
  not_extracted?: Record<string, string>;
  shared_document_note?: string;
}

export interface GraduationRequirements {
  departments: GraduationDepartment[];
}

/* ---------- organization ---------- */
export type OrgType = "university" | "campus" | "college" | "department" | "office" | "council" | "institute";

export interface Organization {
  id: string;
  name: string;
  type: Coded & { code: OrgType };
  parent_id: string | null;
  /** 실제 정적 계약은 campuses 배열이다. campus_id는 기존 화면 호환용 파생 값이다. */
  campuses?: Campus[];
  campus_id?: string | null;
  has_children: boolean;
  /** 이 조직에 붙은 공개 게시판 수. 폐쇄·대기까지 센 값이라 "볼 공지가 있다"는 뜻은 아니다. */
  source_count?: number;
  /**
   * 지금 실제로 공지가 들어올 수 있는 게시판 수(폐쇄·대기·운영 중지 제외).
   * 화면에서 조직을 감출지 판정할 때는 source_count 가 아니라 이 값을 본다.
   * 예전 공개 파일에는 없으므로 없으면 source_count 로 갈음한다.
   */
  active_source_count?: number;
  /**
   * true면 이 조직은 parent_id 조직의 다른 이름이다(예: 미디어학과 = 언론정보학과).
   * 트리에는 그리지 않고, 게시판 수·공지 수를 부모 줄에 합쳐 보여 준다.
   */
  is_alias?: boolean;
}

/* ---------- notice ---------- */
export type Audience = { type: "university" | "campus" | "organization" | "undetermined" | "unknown"; id: string | null; name: string };

export interface Deadline {
  date: string;
  at: string | null;
  precision: "date" | "datetime";
  evidence: string;
}

export interface Notice {
  id: string;
  kind: "notice";
  title: string;
  excerpt: string | null;
  primary_category: Coded;
  secondary_categories: Coded[];
  audiences: Audience[];
  audience_note: string | null;
  primary_source: { id: string; name: string; medium: Coded };
  source_count: number;
  published_date: string | null;
  published_at: string | null;
  published_precision: "date" | "datetime" | "unknown";
  first_visible_at: string;
  updated_at: string;
  deadline: Deadline | null;
  original_url: string;
  original_status: Coded;
  freshness: Coded & { last_checked_at: string | null };
  /** 포스터 한 장뿐인 공지의 대표 그림. 조회 서버 기준 상대 경로다. imageUrl()로 붙인다. */
  poster_image?: string | null;
}

export interface NoticeSource {
  id: string;
  source_id: string;
  source_name: string;
  medium: Coded;
  url: string;
  published_date: string | null;
  original_status: Coded;
  is_primary: boolean;
}

export interface Attachment {
  id: string;
  filename: string;
  type: string;
  size_bytes: number | null;
  url: string | null;
  status: Coded;
}

export interface ContactMention {
  text: string;
  channel: { kind: Coded; value: string; action_url: string | null } | null;
  contact_id: string | null;
}

export interface NoticeDetail extends Notice {
  body_text: string | null;
  body_html: string | null;
  /** 본문에 박힌 그림. 원문 주소가 http 라 조회 서버가 중계한다. imageUrl()로 붙인다. */
  images?: string[];
  sources: NoticeSource[];
  attachments: Attachment[];
  contact_mentions: ContactMention[];
  related_notices: { id: string; title: string; relation: Coded }[];
  resolved_from_id: string | null;
}

/* ---------- contact ---------- */
export interface Channel {
  id: string;
  kind: Coded & { code: "phone" | "email" | "fax" | "website" };
  display_value: string;
  value: string | null;
  action_url: string | null;
  extension: string | null;
}

export interface Contact {
  id: string;
  kind: "contact";
  organization: { id: string; name: string; path: string[]; type: Coded };
  campuses: Campus[];
  service_name: string;
  channels: Channel[];
  location: string | null;
  office_hours: string | null;
  official_url: string | null;
  verification: Coded & { verified_at: string | null; message: string };
  evidence: { field: string; url: string; source_name: string; observed_at: string }[];
}

/* ---------- source ---------- */
export interface Source {
  id: string;
  name: string;
  organization: { id: string; name: string; campuses?: Campus[] };
  /** 실제 정적 계약에는 없고 organization.campuses에서 파생한다. */
  campus_id?: string | null;
  medium: Coded;
  content_kind: Coded; // notice | contact
  url: string | null;
  status: Coded; // active | delayed | blocked | pending | paused | retired
  status_message: string | null;
  /** 이 게시판을 마지막으로 성공적으로 확인한 시각. 출처 화면이 "어제 14:20 확인"으로 적는다. */
  last_success_at: string | null;
  /**
   * 아래 초기 범위·백필 값은 운영용이다. 공개 파일에는 그대로 있지만 화면에는 쓰지 않는다
   * (2026-09-08 결정: 학생은 "초기 수집 전"·"전체 확인"이 무슨 말인지 알 수 없다).
   */
  initial_window_days: number | null;
  initial_window_start?: string | null;
  backfill_status?: string | null;
  backfill_complete?: boolean;
  backfill_oldest_date?: string | null;
  last_scan_stop_reason?: string | null;
}

/* ---------- query ---------- */
export interface NoticeQuery {
  q?: string;
  campus_id?: string[];
  organization_id?: string[];
  include_descendants?: boolean;
  category_code?: string[];
  source_id?: string[];
  medium?: string[];
  sort?: "recent" | "published" | "relevance";
  limit?: number;
  cursor?: string | null;
}

export interface FeedPreviewBody {
  campus_id: string | null;
  organization_ids: string[];
  subscribed_source_ids: string[];
  filters?: Pick<NoticeQuery, "category_code" | "q" | "medium" | "sort">;
  cursor?: string | null;
  limit?: number;
}
