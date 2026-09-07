import Link from "next/link";

export default function NotFound() {
  return (
    <div className="mx-auto max-w-[1180px] px-4 py-20 text-center text-[13px] text-gray">
      <b className="mb-1 block text-[15px] text-ink">페이지를 찾을 수 없습니다</b>
      주소가 바뀌었거나 삭제된 페이지입니다.
      <div className="mt-4">
        <Link href="/" className="inline-block rounded-lg border border-line bg-white px-4 py-2 text-ink-2 hover:bg-bg">
          전체 공지로 이동
        </Link>
      </div>
    </div>
  );
}
