import type { Metadata } from "next";
import { Noto_Sans_KR } from "next/font/google";
import "./globals.css";
import { Header } from "@/components/Header";
import { Onboarding } from "@/components/Onboarding";

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
    <html lang="ko" className={`${noto.variable} h-full antialiased`}>
      <body className="min-h-full flex flex-col">
        <Header />
        <div className="flex-1">{children}</div>
        <p className="mx-auto w-full max-w-[1180px] px-4 pb-6 text-xs text-gray-2">
            경희공지는 학생이 만든 비공식 서비스입니다. 공지가 올라오기까지 최대 3시간 지연되거나 누락될 수 있으니 중요한 공지는 원문에서 확인하세요. 공지 원문과 저작권은 각 게시 기관에 있으며, 원문 링크를 항상 함께 제공합니다.
        </p>
        <Onboarding />
      </body>
    </html>
  );
}
