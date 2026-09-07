// 옛 주소. 전체 공지가 '/'로 옮겨 갔다(2026-09-08 사용자 지시: 전체 공지를 기본으로,
// 가장 좌측으로). 이미 퍼진 링크·북마크가 깨지지 않도록 여기서 넘긴다.
// ?focus=q 같은 물음표 뒤 값도 그대로 들고 간다 — 헤더의 돋보기 단추가 쓰던 주소다.
//
// 영구 이동(308)이 아니라 임시(307)다. 308은 브라우저가 영원히 기억해서, 나중에 탭
// 배치를 되돌리면 옛 주소를 눌러 본 사람만 서버에 닿지도 못하고 계속 '/'로 튄다.
// 되돌릴 수 있는 쪽을 고른다.
import { redirect } from "next/navigation";

export default async function LegacyAllPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const params = await searchParams;
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (Array.isArray(value)) value.forEach((one) => query.append(key, one));
    else if (value !== undefined) query.append(key, value);
  }
  const search = query.toString();
  redirect(search ? `/?${search}` : "/");
}
