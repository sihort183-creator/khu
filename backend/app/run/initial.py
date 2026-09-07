"""초기 범위를 연속으로 채우고 완료 출처는 유지 순회로 넘기는 실행기."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import time
from contextlib import suppress
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select

from app.config import Settings
from app.config import settings as default_settings
from app.export.static import export_static, refresh_public_status
from app.run.collect import run_collection, run_dedupe_pass
from app.storage import models as m
from app.storage.db import session_scope
from app.storage.objects import build_store

log = logging.getLogger("khu.initial")


def parse_deadline(value: str) -> datetime | None:
    if not value.strip():
        return None
    deadline = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if deadline.tzinfo is None:
        raise ValueError("종료 시각에는 시간대가 필요합니다")
    return deadline.astimezone(UTC)


def initial_inventory(cfg: Settings) -> dict[str, int]:
    with session_scope(cfg) as session:
        rows = session.execute(
            select(m.Source, m.SourceHealth)
            .outerjoin(m.SourceHealth, m.SourceHealth.source_id == m.Source.id)
            .where(m.Source.status.in_(("active", "delayed", "blocked")))
        ).all()
        completed = sum(
            bool(h and h.backfill_complete and h.initial_window_start == cfg.initial_window_start)
            for _, h in rows
        )
        return {
            "total": len(rows), "complete": completed, "remaining": len(rows) - completed,
            "blocked": sum(s.status == "blocked" for s, _ in rows),
        }


def should_continue(inventory: dict[str, int], deadline: datetime | None, now: datetime,
                    reserve_seconds: int) -> bool:
    """다음 회차를 이어받을지. 남은 백필과 마감, 둘 다 있어야 이어받는다.

    두 조건 중 하나라도 무너지면 멈춘다. 끝난 백필을 계속 띄우면 헛돌고,
    마감을 넘긴 뒤 띄우면 유지 수집 한 번으로 끝나 회차만 버린다.
    마감이 뒤로 늘어나 다시 이어받아야 하는 경우는 실행 중인 잡이 알 수 없다.
    저장소 변수를 새로 읽는 감시자(app.ops.watchdog)가 그 자리를 맡는다.
    """
    if not inventory["remaining"] or deadline is None:
        return False
    return now < deadline - timedelta(seconds=reserve_seconds)


def publish(cfg: Settings, run_id: str | None) -> str:
    # 수집 작업 사이에만 실행하여 동시 공개와 동일 세션 공유를 피한다.
    with session_scope(cfg) as session:
        run_dedupe_pass(session)
    store = build_store(cfg)
    exported = export_static(cfg, run_id=run_id, store=store)
    if run_id:
        with session_scope(cfg) as session:
            run = session.get(m.Run, run_id)
            if run:
                run.revision = exported.revision
    refresh_public_status(cfg, revision=exported.revision, store=store)
    return exported.revision


async def supervise(
    cfg: Settings,
    *, deadline: datetime | None, max_seconds: int = 16200,
    round_seconds: int = 300, publish_seconds: int = 3600,
    reserve_seconds: int = 900,
) -> dict:
    now = datetime.now(UTC)
    if deadline is None or now >= deadline:
        outcome = await run_collection(cfg)
        return {"mode": "maintenance", "continue_initial": False, "result": outcome.result}

    work_end = min(deadline - timedelta(seconds=reserve_seconds), now + timedelta(seconds=max_seconds))
    # 한 주기가 지나야 처음 공개하면, 잡이 그 전에 끝나는 동안 화면은 몇 시간 낡은 것만
    # 보여준다. 2026-09-07 실제로 3시간 51분 동안 공개가 한 번도 나가지 않았다.
    # 이미 한 주기가 지난 것처럼 두어 첫 회차 직후에 내보낸다.
    last_publish = time.monotonic() - publish_seconds
    last_run_id = None
    rounds = 0
    new_items = 0
    failures = 0
    inventory = initial_inventory(cfg)
    if inventory["total"] == 0:
        raise RuntimeError("초기 수집 대상이 없습니다. 완료로 처리하지 않습니다")
    if inventory["remaining"] == 0:
        outcome = await run_collection(cfg)
        return {"mode": "maintenance", **inventory, "continue_initial": False, "result": outcome.result}
    # 마지막 공개 뒤로 새로 모은 회차 수. 취소될 때 내보낼 것이 있는지 이것으로 안다.
    unpublished_rounds = 0
    try:
        while datetime.now(UTC) < work_end and inventory["remaining"]:
            remaining = (work_end - datetime.now(UTC)).total_seconds()
            if remaining <= 0:
                break
            round_cfg = replace(cfg, run_budget_seconds=max(1, int(min(round_seconds, remaining))))
            outcome = await run_collection(round_cfg, initial_mode=True, publish=False, dedupe=False)
            last_run_id = outcome.run_id
            rounds += 1
            unpublished_rounds += 1
            new_items += outcome.new_items
            failures = failures + 1 if outcome.result == "failed" else 0
            inventory = initial_inventory(cfg)
            log.info("초기 진행 %s", json.dumps({**inventory, "rounds": rounds, "new_items": new_items}, ensure_ascii=False))
            if failures >= 3:
                raise RuntimeError("초기 수집이 연속 3회 전체 실패했습니다")
            if time.monotonic() - last_publish >= publish_seconds:
                revision = await asyncio.to_thread(publish, cfg, last_run_id)
                log.info("중간 공개 완료 %s", revision)
                last_publish = time.monotonic()
                unpublished_rounds = 0
            if outcome.attempted == 0 and inventory["remaining"]:
                wait = min(30, max(0, (work_end - datetime.now(UTC)).total_seconds()))
                await asyncio.sleep(wait)
    except BaseException:
        # 잡이 취소되거나 터져도 그때까지 모은 것은 내보낸다. 그러지 않으면 회차를
        # 스무 번 돌고도 화면에 아무것도 반영되지 않은 채 끝난다.
        # 파일을 다 올린 뒤에야 포인터가 바뀌므로 도중에 죽어도 개정이 섞이지 않는다.
        if unpublished_rounds:
            with suppress(Exception):
                await asyncio.to_thread(publish, cfg, last_run_id)
        raise

    revision = await asyncio.to_thread(publish, cfg, last_run_id)
    inventory = initial_inventory(cfg)
    return {
        "mode": "initial", **inventory, "rounds": rounds, "new_items": new_items,
        "revision": revision,
        "continue_initial": should_continue(inventory, deadline, datetime.now(UTC), reserve_seconds),
        "result": "complete" if inventory["remaining"] == 0 else "partial",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="초기 연속 수집과 유지 수집 자동 전환")
    parser.add_argument("--until", default=os.getenv("KHU_INITIAL_UNTIL", ""))
    parser.add_argument("--max-seconds", type=int, default=16200)
    parser.add_argument("--round-seconds", type=int, default=300)
    # 초기 수집 동안에는 DB 가 시간당 수천 건씩 자라므로 한 시간에 한 번 공개하면
    # 화면이 계속 낡은 것만 보여준다. 변수로 두어 상황에 맞게 줄인다.
    parser.add_argument("--publish-seconds", type=int,
        default=int(os.getenv("KHU_PUBLISH_SECONDS", "1200")))
    parser.add_argument("--result-file", default=".localstore/initial-result.json")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    result = asyncio.run(supervise(default_settings, deadline=parse_deadline(args.until),
        max_seconds=args.max_seconds, round_seconds=args.round_seconds, publish_seconds=args.publish_seconds))
    target = Path(args.result_file)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 1 if result.get("result") == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
