import type { Metadata } from "next";
import { Noto_Sans_KR } from "next/font/google";
import "./globals.css";
import { Header } from "@/components/Header";
import { Onboarding } from "@/components/Onboarding";
import { BackToTop } from "@/components/BackToTop";
import { ThemeToggle } from "@/components/ThemeToggle";
import { THEME_SCRIPT } from "@/lib/theme";

const noto = Noto_Sans_KR({
  variable: "--font-noto",
  subsets: ["latin"],
  weight: ["400", "500", "700"],
  display: "swap",
});

export const metadata: Metadata = {
  title: { default: "경희공지", template: "%s · 경희공지" },
  description: "경희대학교 홈페이지·학과·기관·학생회에 흩어진 공지를 한 곳에서 봅니다.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    // data-theme 은 아래 스크립트가 첫 그리기 전에 붙인다. 서버가 그린 값과 달라도
    // 되도록 suppressHydrationWarning 을 붙인다(Next 16 문서 권고).
    <html lang="ko" className={`${noto.variable} h-full antialiased`} suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_SCRIPT }} />
      </head>
      <body className="min-h-full flex flex-col">
        <Header />
        <div className="flex-1">{children}</div>
        <footer className="mx-auto w-full max-w-[1180px] px-4 pb-6">
          <p className="text-xs text-gray-2">
            경희공지는 학생이 만든 비공식 서비스입니다. 공지가 올라오기까지 최대 3시간 지연되거나 누락될 수 있으니 중요한 공지는 원문에서 확인하세요. 공지 원문과 저작권은 각 게시 기관에 있습니다.
          </p>
          <div className="mt-2.5">
            <ThemeToggle />
          </div>
        </footer>
        <Onboarding />
        <BackToTop />
      </body>
    </html>
  );
}
