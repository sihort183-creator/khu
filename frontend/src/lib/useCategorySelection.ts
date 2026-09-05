"use client";
import { useCallback, useMemo, useState } from "react";

/** 주제 다중 선택. 비어 있으면 전체. */
export function useCategorySelection() {
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const toggle = useCallback((code: string | null) => {
    setSelected((prev) => {
      if (code === null) return new Set();
      const next = new Set(prev);
      if (next.has(code)) next.delete(code);
      else next.add(code);
      return next;
    });
  }, []);
  const codes = useMemo(() => [...selected].sort(), [selected]);
  return { selected, toggle, codes };
}
