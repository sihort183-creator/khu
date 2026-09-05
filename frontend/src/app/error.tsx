"use client";
// 렌더 중 예외의 최후 방어선. API 오류는 각 화면에서 따로 처리한다.
export default function ErrorPage({ reset }: { error: Error; reset: () => void }) {
  return (
    <div className="mx-auto max-w-[1180px] px-4 py-20 text-center text-[13px] text-gray">
      <b className="mb-1 block text-[15px] text-ink">화면을 그리는 중 문제가 생겼습니다</b>
      잠시 후 다시 시도해 주세요.
      <div className="mt-4">
        <button onClick={reset} className="rounded-lg border border-line bg-white px-4 py-2 text-ink-2 hover:bg-bg">
          다시 시도
        </button>
      </div>
    </div>
  );
}
