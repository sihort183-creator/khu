"""초기 범위를 연속으로 채우고 완료 출처는 유지 순회로 넘기는 실행기."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import time
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
    last_publish = time.monotonic()
    last_run_id = None
    rounds = 0
    new_items = 0
    failures = 0
    inventory = initial_inventory(cfg)
    if inventory["total"] == 0:
        raise RuntimeError("초기 수집 대상이 없습니다. 완료로 처리하지 않습니다")
    if inventory["remaining"] == 0:
        outcome = await run_collection(cfg)
        return {"mode": "maintenance", "continue_initial": False, "result": outcome.result}
    while datetime.now(UTC) < work_end and inventory["remaining"]:
        remaining = (work_end - datetime.now(UTC)).total_seconds()
        if remaining <= 0:
            break
        round_cfg = replace(cfg, run_budget_seconds=max(1, int(min(round_seconds, remaining))))
        outcome = await run_collection(round_cfg, initial_mode=True, publish=False, dedupe=False)
        last_run_id = outcome.run_id
        rounds += 1
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
        if outcome.attempted == 0 and inventory["remaining"]:
            wait = min(30, max(0, (work_end - datetime.now(UTC)).total_seconds()))
            await asyncio.sleep(wait)

    revision = await asyncio.to_thread(publish, cfg, last_run_id)
    inventory = initial_inventory(cfg)
    return {
        "mode": "initial", **inventory, "rounds": rounds, "new_items": new_items,
        "revision": revision,
        "continue_initial": bool(inventory["remaining"] and datetime.now(UTC) < deadline - timedelta(seconds=reserve_seconds)),
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
