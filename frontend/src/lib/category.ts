// 주제 코드 → 배지 색. 코드 목록 자체는 /v1/catalog에서 온다(프론트가 따로 관리하지 않음).
// 알 수 없는 코드는 기타 색으로 표시한다.
const STYLES: Record<string, { bg: string; fg: string }> = {
  academic: { bg: "#eef1f7", fg: "#253a73" },
  graduation: { bg: "#efeaf5", fg: "#4b3a8c" },
  scholarship: { bg: "#f8f1dc", fg: "#7f6417" },
  career: { bg: "#e2f2f2", fg: "#0f6b70" },
  startup: { bg: "#e6f2e9", fg: "#1f6b3a" },
  program: { bg: "#fdecdd", fg: "#a3501a" },
  international: { bg: "#e5eef7", fg: "#1c5b8e" },
  event: { bg: "#f9e5e6", fg: "#990e17" },
  student_council: { bg: "#f3e7ea", fg: "#7a2140" },
  campus_life: { bg: "#eeeff1", fg: "#4a5560" },
  other: { bg: "#f0f0f0", fg: "#737373" },
};

export function categoryStyle(code: string) {
  return STYLES[code] ?? STYLES.other;
}
