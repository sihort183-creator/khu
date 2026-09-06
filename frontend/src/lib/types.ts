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
  /** 이 조직에 붙은 수집 게시판 수. 0이면 아직 볼 공지가 없다. */
  source_count?: number;
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
  last_success_at: string | null;
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
  filters?: Pick<NoticeQuery, "category_code" | "q" | "medium">;
  cursor?: string | null;
  limit?: number;
}
