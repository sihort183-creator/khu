// 캠퍼스 선택지. 헤더 토글·온보딩·'내 설정'이 같은 목록을 보도록 한곳에서 만든다.
import type { Campus } from "./types";

/**
 * '공통'의 저장값은 null 이다.
 *
 * 새 문자열을 만들지 않은 이유: 조회 쪽은 이미 캠퍼스가 비면 캠퍼스로 거르지 않는다
 * (api.ts 의 audienceMatchesCampus 는 campusIds 가 비면 무조건 통과). 그래서 null 하나로
 * 서울·국제 공지는 물론 캠퍼스가 안 밝혀진 조직(484개 중 346개)의 공지까지 남는다.
 * 저장 형식이 그대로라 예전 값(campus-seoul / campus-global)도 손대지 않고 이어진다.
 */
export const CAMPUS_ALL = null;

export interface CampusOption {
  /** null 이면 공통 */
  id: string | null;
  /** 온보딩처럼 넓은 곳에서 쓰는 이름 */
  name: string;
  /** 헤더 토글처럼 좁은 곳에서 쓰는 짧은 이름 */
  short: string;
  /** 무엇이 보이는지 한 줄 설명 */
  hint: string;
}

export function campusOptions(campuses: Campus[] | undefined): CampusOption[] {
  return [
    { id: CAMPUS_ALL, name: "공통", short: "공통", hint: "서울·국제 모두" },
    ...(campuses ?? []).map((c) => ({
      id: c.id,
      name: c.name,
      short: c.name.replace("캠퍼스", ""),
      hint: "",
    })),
  ];
}

/** '내 설정' 같은 곳에 쓸 짧은 이름. 목록을 아직 못 받았어도 공통은 이름이 나와야 한다. */
export function campusShortLabel(campuses: Campus[] | undefined, id: string | null): string {
  return campusOptions(campuses).find((o) => o.id === id)?.short ?? "";
}

/** 목록 머리글용. 공통은 "공통 전체"가 어색해서 무엇이 섞이는지 그대로 적는다. */
export function campusScopeLabel(campuses: Campus[] | undefined, id: string | null): string {
  if (id === CAMPUS_ALL) return "서울·국제 전체";
  const name = campuses?.find((c) => c.id === id)?.name ?? "";
  return name ? `${name} 전체` : "전체";
}
