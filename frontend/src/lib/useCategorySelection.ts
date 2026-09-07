"use client";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

/**
 * 주제 다중 선택. 비어 있으면 전체.
 *
 * "전체" 상태에서는 모든 칸이 체크로 보인다. 그때 하나를 누르면 예전에는 그 하나만
 * 남고 나머지가 다 풀렸다(사용자 지적 2026-09-08). 눌린 칸만 풀리고 나머지는 그대로
 * 체크된 채여야 한다. 그래서 전체 상태에서 하나를 끄면 "나머지 전부"를 고른 것으로
 * 바꾼다. 반대로 하나씩 켜다가 전부 켜지면 다시 "전체"(빈 집합)로 돌아간다.
 */
export function useCategorySelection(allCodes: readonly string[] = []) {
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const all = useRef<readonly string[]>(allCodes);
  useEffect(() => {
    all.current = allCodes;
  }, [allCodes]);
  const toggle = useCallback((code: string | null) => {
    setSelected((prev) => {
      if (code === null) return new Set();
      const everything = all.current;
      if (prev.size === 0 && everything.length > 0) {
        return new Set(everything.filter((c) => c !== code));
      }
      const next = new Set(prev);
      if (next.has(code)) next.delete(code);
      else next.add(code);
      if (everything.length > 0 && everything.every((c) => next.has(c))) return new Set();
      return next;
    });
  }, []);
  const codes = useMemo(() => [...selected].sort(), [selected]);
  return { selected, toggle, codes };
}
