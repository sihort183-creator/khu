// 주제 코드 → 배지 색. 코드 목록 자체는 /v1/catalog에서 온다(프론트가 따로 관리하지 않음).
// 알 수 없는 코드는 기타 색으로 표시한다.
//
// 값은 색이 아니라 토큰 이름이다. 밝은 판·어두운 판 두 벌이 app/globals.css 에 있고
// 여기서는 어느 쪽인지 알 필요가 없다.
const VARS: Record<string, string> = {
  academic: "academic",
  graduation: "graduation",
  scholarship: "scholarship",
  career: "career",
  startup: "startup",
  program: "program",
  international: "international",
  event: "event",
  student_council: "council",
  campus_life: "life",
  other: "other",
};

export function categoryStyle(code: string) {
  const name = VARS[code] ?? VARS.other;
  return { bg: `var(--cat-${name}-bg)`, fg: `var(--cat-${name}-fg)` };
}
