# 경희공지 프론트엔드

Next.js(App Router) + Tailwind v4. 디자인 기준은 `../docs/UI_CONCEPT.md`(v0.3), 데이터 계약은 `../KHU_API_CONTRACT_DRAFT.md`.

```bash
npm install
npm run dev      # http://localhost:3000
npm run build
```

- `NEXT_PUBLIC_API_BASE`가 비어 있으면 `src/mocks/data.ts`의 가상 데이터로 동작한다. 실제 서버가 생기면 `.env.local`에 주소만 넣는다(`src/lib/api.ts`의 mock 분기 제거).
- 비회원 설정(캠퍼스·단과대·학과·구독 출처)은 localStorage `khu-notice.settings.v1`에만 저장된다. 로그인 없음.
- 주제 코드 목록은 `/v1/catalog`에서 온다. 프론트는 코드 → 색만 `src/lib/category.ts`에 가진다.

## 구조

```
src/app/            페이지: / (내 공지), /all, /sources, /contacts, /notices/[id]
src/components/     Header, Shell(3열), NoticeList, CategoryChips, Onboarding, Ad
src/lib/            api.ts(클라이언트), types.ts(계약 타입), settings.tsx, format.ts, category.ts
src/mocks/data.ts   가상 데이터(규격 9절 시나리오 포함)
```
