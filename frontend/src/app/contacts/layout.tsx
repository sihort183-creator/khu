import type { Metadata } from "next";

export const metadata: Metadata = { title: "연락처" };

export default function Layout({ children }: { children: React.ReactNode }) {
  return children;
}
