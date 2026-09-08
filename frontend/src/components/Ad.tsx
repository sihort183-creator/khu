// 광고 슬롯. 초기에는 자체 배너 1개만 두고 네트워크 연동은 뒤로 미룬다.
import { FEATURES } from "@/lib/features";

export function Ad({ vertical = false }: { vertical?: boolean }) {
  // 광고 자리는 스위치가 꺼져 있으면 아무것도 그리지 않는다(코드는 남긴다).
  if (!FEATURES.ads) return null;
  return (
    <div
      className={`flex gap-3 border-line-2 bg-cream px-4 py-2.5 text-[13px] ${
        vertical ? "flex-col rounded-box border bg-card" : "items-center border-y"
      }`}
    >
      <span className="flex-none self-start rounded border border-line px-[5px] text-[10px] leading-[15px] text-gray-2">광고</span>
      <div className={`flex-none rounded-lg ${vertical ? "h-[110px] w-full" : "h-11 w-11"}`} style={{ background: "linear-gradient(135deg,var(--ad-1),var(--ad-2))" }} />
      <div>
        토익 900+ 4주 특강
        <small className="block text-xs text-gray">경희대 학생 20% 할인 · 9월 개강반</small>
      </div>
    </div>
  );
}
