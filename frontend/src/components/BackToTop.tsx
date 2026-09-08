"use client";
// 상단으로 가기. 목록이 한 화면 넘게 내려갔을 때만 오른쪽 아래에 뜬다.
//
// 자리: 오른쪽 아래 12px, 아래 16px(+ 홈 인디케이터 여백). 44×44 라 엄지에 닿고,
// 목록 상자(좌우 16px 여백) 안의 광고 줄·"더 보기" 글자는 모두 가운데 또는 왼쪽에
// 있어 이 원과 겹치지 않는다.
import { useEffect, useState } from "react";

export function BackToTop() {
  const [show, setShow] = useState(false);

  useEffect(() => {
    const onScroll = () => setShow(window.scrollY > window.innerHeight);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);
    return () => {
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
    };
  }, []);

  if (!show) return null;

  return (
    <button
      type="button"
      aria-label="맨 위로"
      title="맨 위로"
      onClick={() => {
        // 움직임을 줄여 달라고 설정한 사람에게는 한 번에 올려 준다.
        const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
        window.scrollTo({ top: 0, behavior: reduce ? "auto" : "smooth" });
      }}
      className="fixed bottom-4 right-3 z-30 grid h-11 w-11 place-items-center rounded-full border border-line bg-card text-ink-2 shadow-box hover:border-gray-2 hover:text-ink"
      style={{ bottom: "max(16px, env(safe-area-inset-bottom))" }}
    >
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" width={18} height={18} aria-hidden>
        <path d="M12 19V6M5 13l7-7 7 7" />
      </svg>
    </button>
  );
}
