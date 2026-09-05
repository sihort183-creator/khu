"use client";
import type { Coded } from "@/lib/types";

interface Props {
  categories: Coded[];
  selected: Set<string>;
  onToggle: (code: string | null) => void;
}

export function CategoryChips({ categories, selected, onToggle }: Props) {
  const all = selected.size === 0;
  return (
    <div className="no-scrollbar flex gap-1.5 overflow-x-auto pb-2.5">
      <Chip on={all} onClick={() => onToggle(null)} accent>
        전체
      </Chip>
      {categories.map((c) => (
        <Chip key={c.code} on={selected.has(c.code)} onClick={() => onToggle(c.code)}>
          {c.label}
        </Chip>
      ))}
    </div>
  );
}

function Chip({ on, onClick, children, accent }: { on: boolean; onClick: () => void; children: React.ReactNode; accent?: boolean }) {
  const onCls = accent ? "bg-red border-red text-white" : "bg-navy border-navy text-white";
  return (
    <button
      onClick={onClick}
      className={`flex-none rounded-full border px-[13px] py-[5px] text-[13px] transition-colors ${on ? onCls : "border-line bg-white text-ink-2 hover:border-gray-2"}`}
    >
      {children}
    </button>
  );
}
