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
    // 칩이 31px 이라 엄지에 좁았다(2026-09-08 모바일 점검). 가로 스크롤 상자는 세로도
    // 잘리므로 before: 만으로는 넓힐 수 없다 — 상자에 위아래 여백을 조금 주고 칩 자체를
    // 키워 누르는 자리를 44px 가깝게 만든다.
    <div className="no-scrollbar flex gap-1.5 overflow-x-auto pb-2 pt-1">
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
  const onCls = accent ? "bg-red-fill border-red-fill text-white" : "bg-navy-fill border-navy-fill text-white";
  return (
    <button
      onClick={onClick}
      className={`relative flex-none rounded-full border px-[13px] py-2 text-[13px] transition-colors before:absolute before:inset-x-0 before:top-1/2 before:h-11 before:-translate-y-1/2 ${on ? onCls : "border-line bg-card text-ink-2 hover:border-gray-2"}`}
    >
      {children}
    </button>
  );
}
