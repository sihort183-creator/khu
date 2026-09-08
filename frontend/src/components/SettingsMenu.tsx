"use client";
// 헤더의 사람 아이콘. 누르면 헤더 바로 아래에 "내 설정" 패널이 붙는다.
//
// 예전에는 이 아이콘이 온보딩(전체 화면)을 곧바로 띄웠고, 화면 밝기는 옆의 달 단추가
// 맡았다. 좁은 화면에서는 오른쪽 "내 설정" 상자가 통째로 사라져 캠퍼스·조직이 지금
// 무엇인지 볼 곳이 없었고, 작은 아이콘 한 번에 전체 화면이 덮이는 것도 갑작스러웠다.
// 그래서 아이콘은 패널을 열고, 온보딩은 패널 안에서 한 번 더 눌러야 뜬다.
//
// 넓은 화면(lg 이상)에서는 아이콘이 예전처럼 온보딩을 바로 띄운다. 패널을 여기서도
// 열어 보니 오른쪽 "내 설정" 상자와 폭(300px 대 240px)·윗자리(112px)가 거의 같아 상자를
// 그대로 덮어 버린다 — 같은 글이 두 겹으로 보여 고장 난 것처럼 읽힌다. 넓은 화면에는
// 캠퍼스·조직·화면이 이미 늘 보이는 상자로 있으니 패널이 더 알려 줄 것도 없다.
// 그래서 패널은 상자가 사라지는 좁은 화면(lg 미만)에만 둔다.
import { useEffect, useRef, useState } from "react";
import { useSettings } from "@/lib/settings";
import { IconUser } from "./icons";
import { MySettingsBody } from "./MySettings";

const ICON_BUTTON =
  // 아이콘 상자는 34px 이라 손가락에 좁다. 모양은 그대로 두고 before: 로 누르는 자리만 44×44 로 넓힌다.
  "relative grid h-[34px] w-[34px] shrink-0 place-items-center rounded-lg before:absolute before:left-1/2 before:top-1/2 before:h-11 before:w-11 before:-translate-x-1/2 before:-translate-y-1/2 hover:bg-bg";

export function SettingsMenu() {
  const { update } = useSettings();
  const [open, setOpen] = useState(false);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);

  // 바깥을 누르거나 ESC 를 누르면 닫는다. 여는 클릭은 pointerdown 이 먼저라
  // 이 listener 가 붙기 전에 지나가므로 열자마자 닫히지 않는다.
  useEffect(() => {
    if (!open) return;
    const close = () => {
      setOpen(false);
      buttonRef.current?.focus();
    };
    const onPointerDown = (e: PointerEvent) => {
      const t = e.target as Node | null;
      if (!t) return;
      if (panelRef.current?.contains(t) || buttonRef.current?.contains(t)) return;
      setOpen(false); // 바깥을 누른 사람의 초점을 뺏지 않는다
    };
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
    };
    document.addEventListener("pointerdown", onPointerDown, true);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown, true);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  // 열리면 초점을 패널로 옮긴다. 키보드·읽어 주는 도구가 헤더 아래에서 헤매지 않게.
  useEffect(() => {
    if (open) panelRef.current?.focus();
  }, [open]);

  return (
    <>
      {/* 좁은 화면: 패널을 연다 */}
      <button
        ref={buttonRef}
        type="button"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls="my-settings-panel"
        title="내 설정"
        aria-label="내 설정"
        onClick={() => setOpen((v) => !v)}
        className={`${ICON_BUTTON} lg:hidden ${open ? "bg-bg text-navy" : "text-ink-2"}`}
      >
        <IconUser width={19} height={19} />
      </button>

      {/* 넓은 화면: 예전 그대로 온보딩으로 간다(설정은 오른쪽 상자에 늘 떠 있다) */}
      <button
        type="button"
        title="내 학과 설정"
        aria-label="내 학과 설정"
        onClick={() => update({ onboarded: false })}
        className={`${ICON_BUTTON} hidden text-ink-2 lg:grid`}
      >
        <IconUser width={19} height={19} />
      </button>

      {open && (
        // 헤더(sticky·backdrop-blur)가 자리잡기 기준이라 top-full 이 탭 줄 바로 아래다.
        // 바깥쪽 두 겹은 폭만 맞추는 껍데기라 클릭을 먹으면 안 된다(pointer-events-none).
        // 열어 둔 채 창을 넓히면 여는 단추가 사라지므로 패널도 함께 감춘다.
        <div className="pointer-events-none absolute inset-x-0 top-full z-30 lg:hidden">
          {/* 헤더 줄과 같은 폭·여백(mx-auto max-w-[1180px] px-4)이라 오른쪽 끝이 아이콘과 맞는다 */}
          <div className="mx-auto flex max-w-[1180px] justify-end px-4">
            <div
              ref={panelRef}
              id="my-settings-panel"
              role="dialog"
              aria-label="내 설정"
              tabIndex={-1}
              className="pointer-events-auto mt-2 w-[300px] max-w-full overflow-hidden rounded-box border border-line bg-card shadow-box outline-none"
            >
              <div className="border-b border-line-2 px-3.5 py-[11px] text-[13px] font-bold">내 설정</div>
              <MySettingsBody touch onReselect={() => setOpen(false)} />
            </div>
          </div>
        </div>
      )}
    </>
  );
}
