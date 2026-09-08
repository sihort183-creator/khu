/**
 * 화면 기능 스위치. 코드는 남기고 보이기만 끈다.
 *
 * 2026-09-08 사용자 결정: 출처 탭과 광고 자리는 당분간 감춘다. 코드는 지우지 않는다.
 * 다시 켤 때는 값만 true 로 바꾼다.
 */
export const FEATURES = {
  /** 상단 "출처" 탭과 /sources 화면 */
  sourcesTab: false,
  /** 목록 사이·오른쪽 열의 광고 자리 */
  ads: false,
} as const;
