import type { Metadata } from "next";

export const metadata: Metadata = { title: "공지" };

export default function Layout({ children }: { children: React.ReactNode }) {
  return children;
}
