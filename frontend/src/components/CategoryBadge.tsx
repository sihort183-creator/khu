import { categoryStyle } from "@/lib/category";
import type { Coded } from "@/lib/types";

export function CategoryBadge({ category, className = "" }: { category: Coded; className?: string }) {
  const s = categoryStyle(category.code);
  return (
    <span className={`inline-block rounded-[5px] px-[7px] align-[1px] text-[11.5px] font-semibold leading-[19px] ${className}`} style={{ background: s.bg, color: s.fg }}>
      {category.label}
    </span>
  );
}
