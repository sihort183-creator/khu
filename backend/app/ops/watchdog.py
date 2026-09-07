"""백필이 남아 있는 동안 수집이 멈춰 있지 않게 지키는 감시자.

2026-09-07 초기 백필이 45분 동안 통째로 멈췄다. 저장소 변수 `KHU_INITIAL_UNTIL` 이
10:00 KST 였고 그 시각에 시작한 회차가 `app.run.initial.supervise` 의 유지 모드로
빠지면서 `continue_initial: False` 를 돌려주었다. `collect.yml` 의 이어받기 단계는
그 값만 보고 다음 회차를 띄우므로 사슬이 끊겼다. 사람이 마감을 늘려도 초기 모드로
되돌릴 방아쇠가 없었고, 남은 것은 매시 예약뿐인데 그 예약도 40분 넘게 늦게 왔다.

이 감시자는 사슬이 끊긴 자리를 메운다. 실행 중인 잡이 보는 마감이 아니라 **지금**
저장소 변수에 들어 있는 마감을 보고, 백필이 남았는데 수집이 굴러가고 있지 않으면
수집을 한 번 띄운다. 되살리는 것이 본체이고 알림은 `app.ops.selfcheck` 가 맡는다.

    python -m app.ops.watchdog --runs-file .localstore/collect-runs.json

운영 자료를 바꾸지 않는다. 데이터베이스는 읽기만 한다. 실제 dispatch 는 판단을
출력으로 받은 작업 흐름이 한다(`.github/workflows/watchdog.yml`).

헛도는 무한 dispatch 를 막는 안전장치는 네 겹이다.
1. 백필이 끝났거나 마감이 지났으면 절대 띄우지 않는다.
2. 이미 대기·실행 중인 수집이 있으면 띄우지 않는다.
3. 최근에 시작된 수집이 있으면(냉각 시간) 띄우지 않는다.
4. 최근 실행이 연속 실패했거나 시간당 상한을 넘겼으면 띄우지 않는다.
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select

from app.config import Settings
from app.config import settings as default_settings
from app.domain.dates import as_utc
from app.run.initial import initial_inventory, parse_deadline
from app.storage import models as m
from app.storage.db import session_scope

DEFAULT_OUTPUT = ".localstore/watchdog.json"

# 회차 하나가 5분, 그 사이 공개가 몇 분 걸린다. 그보다 넉넉히 잡아야 정상 진행을
# 정체로 잘못 부르지 않는다. 판단이 아니라 보고와 자체 점검에 쓰는 값이다.
STALE_AFTER_MINUTES = 20
# 방금 시작된 수집이 있으면 기다린다. 사람이 손으로 취소한 직후에 곧바로 되살려
# 놀라게 하지 않는 여유이기도 하다.
COOLDOWN_MINUTES = 10
MAX_DISPATCHES_PER_HOUR = 4
FAILURE_LIMIT = 3
# 마감까지 이만큼도 안 남았으면 띄워봐야 유지 수집 한 번으로 끝난다.
RESERVE_SECONDS = 900

ACTIVE_STATUSES = frozenset({"queued", "in_progress", "requested", "waiting", "pending"})
FAILED_CONCLUSIONS = frozenset({"failure", "timed_out", "startup_failure"})


@dataclass(frozen=True)
class WorkflowRuns:
    """GitHub 이 아는 사실. 데이터베이스로는 알 수 없어 밖에서 받아 온다."""

    active: int = 0
    recent: int = 0
    consecutive_failures: int = 0
    last_created_at: datetime | None = None


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


def summarize_runs(rows: list[dict], *, now: datetime, window_minutes: int = 60) -> WorkflowRuns:
    """`gh run list --json databaseId,createdAt,status,conclusion` 결과를 읽는다."""
    since = now - timedelta(minutes=window_minutes)
    active = 0
    recent = 0
    last_created: datetime | None = None
    for row in rows:
        created = _parse_time(row.get("createdAt"))
        if str(row.get("status", "")) in ACTIVE_STATUSES:
            active += 1
        if created is not None:
            if created >= since:
                recent += 1
            if last_created is None or created > last_created:
                last_created = created

    # 최신순으로 실패가 몇 번 이어졌는지 센다. 취소·성공을 만나면 끊긴다.
    ordered = sorted(
        (row for row in rows if _parse_time(row.get("createdAt")) is not None),
        key=lambda row: _parse_time(row.get("createdAt")),
        reverse=True,
    )
    failures = 0
    for row in ordered:
        if str(row.get("status", "")) in ACTIVE_STATUSES:
            continue
        if str(row.get("conclusion", "")) in FAILED_CONCLUSIONS:
            failures += 1
            continue
        break
    return WorkflowRuns(
        active=active, recent=recent, consecutive_failures=failures, last_created_at=last_created
    )


def last_collection_activity(cfg: Settings) -> datetime | None:
    """마지막으로 수집이 살아 있던 시각. 회차마다 실행 기록이 하나씩 생긴다."""
    with session_scope(cfg) as session:
        started, finished = session.execute(
            select(func.max(m.Run.started_at), func.max(m.Run.finished_at)).where(
                m.Run.kind == "collect"
            )
        ).one()
    stamps = [as_utc(value) for value in (started, finished) if value is not None]
    return max(stamps) if stamps else None


@dataclass(frozen=True)
class Decision:
    dispatch: bool
    reason: str


def decide(
    *,
    inventory: dict[str, int],
    deadline: datetime | None,
    runs: WorkflowRuns,
    now: datetime,
    cooldown_minutes: int = COOLDOWN_MINUTES,
    max_per_hour: int = MAX_DISPATCHES_PER_HOUR,
    failure_limit: int = FAILURE_LIMIT,
    reserve_seconds: int = RESERVE_SECONDS,
) -> Decision:
    """수집을 한 번 띄울지 정한다. 판단만 하고 아무것도 실행하지 않는다."""
    if inventory.get("total", 0) == 0:
        # 대상이 하나도 없으면 완료가 아니라 설정 사고다. 되살려도 소용없다.
        return Decision(False, "초기 수집 대상이 없습니다")
    if inventory.get("remaining", 0) <= 0:
        return Decision(False, "백필이 끝났습니다. 유지 수집 예약에 맡깁니다")
    if deadline is None:
        return Decision(False, "초기 마감(KHU_INITIAL_UNTIL)이 비어 있습니다")
    if now >= deadline - timedelta(seconds=reserve_seconds):
        return Decision(False, f"초기 마감 {deadline.isoformat()} 이 지났습니다")
    if runs.active:
        return Decision(False, f"수집 {runs.active}건이 이미 대기·실행 중입니다")
    if runs.consecutive_failures >= failure_limit:
        return Decision(
            False, f"최근 수집이 연속 {runs.consecutive_failures}회 실패했습니다. 사람이 봐야 합니다"
        )
    if runs.last_created_at is not None and now - runs.last_created_at < timedelta(
        minutes=cooldown_minutes
    ):
        return Decision(False, f"{cooldown_minutes}분 안에 시작된 수집이 있습니다")
    if runs.recent >= max_per_hour:
        return Decision(False, f"한 시간에 {runs.recent}회를 넘겨 띄웠습니다. 상한에서 멈춥니다")
    return Decision(True, f"백필 {inventory['remaining']}곳이 남았는데 수집이 굴러가고 있지 않습니다")


def build_report(
    *,
    cfg: Settings | None = None,
    deadline: datetime | None,
    runs: WorkflowRuns,
    now: datetime | None = None,
    stale_after_minutes: int = STALE_AFTER_MINUTES,
    **limits,
) -> dict:
    cfg = cfg or default_settings
    now = now or datetime.now(UTC)
    inventory = initial_inventory(cfg)
    activity = last_collection_activity(cfg)
    idle_minutes = None if activity is None else round((now - activity).total_seconds() / 60, 1)
    decision = decide(inventory=inventory, deadline=deadline, runs=runs, now=now, **limits)
    return {
        "generated_at": now.isoformat(),
        "dispatch": decision.dispatch,
        "reason": decision.reason,
        "deadline": deadline.isoformat() if deadline else None,
        **inventory,
        "idle_minutes": idle_minutes,
        "stalled": bool(
            inventory.get("remaining", 0)
            and (idle_minutes is None or idle_minutes >= stale_after_minutes)
        ),
        "last_activity_at": activity.isoformat() if activity else None,
        "workflow_runs": {
            "active": runs.active,
            "last_hour": runs.recent,
            "consecutive_failures": runs.consecutive_failures,
            "last_created_at": runs.last_created_at.isoformat() if runs.last_created_at else None,
        },
    }


def render(report: dict) -> str:
    verdict = "수집을 띄웁니다" if report["dispatch"] else "그대로 둡니다"
    idle = report["idle_minutes"]
    return "\n".join(
        [
            f"## 수집 감시 — {verdict}",
            "",
            f"- 판단 근거: {report['reason']}",
            f"- 백필: 남음 {report['remaining']} / 전체 {report['total']}",
            f"- 마지막 수집 활동: {report['last_activity_at'] or '없음'}"
            + (f" ({idle}분 전)" if idle is not None else ""),
            f"- 초기 마감: {report['deadline'] or '없음'}",
            f"- 최근 수집 실행: 진행 {report['workflow_runs']['active']}건 · "
            f"한 시간 {report['workflow_runs']['last_hour']}건 · "
            f"연속 실패 {report['workflow_runs']['consecutive_failures']}회",
        ]
    )


def load_runs(path: str | None) -> list[dict]:
    if not path:
        return []
    try:
        rows = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # 목록을 못 읽으면 판단 근거가 없다. 빈 목록으로 두면 냉각·상한 장치가
        # 풀린 채 띄우게 되므로, 실행 중인 것이 있다고 보고 이번 회는 넘긴다.
        return [{"status": "in_progress", "conclusion": None, "createdAt": None}]
    return [row for row in rows if isinstance(row, dict)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="khu-watchdog", description="백필이 남았는데 수집이 멈춰 있으면 되살릴지 판단한다"
    )
    parser.add_argument("--until", default=os.getenv("KHU_INITIAL_UNTIL", ""))
    parser.add_argument("--runs-file", default="", help="gh run list --json 결과 파일")
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--cooldown-minutes", type=int, default=COOLDOWN_MINUTES)
    parser.add_argument("--max-per-hour", type=int, default=MAX_DISPATCHES_PER_HOUR)
    parser.add_argument("--failure-limit", type=int, default=FAILURE_LIMIT)
    args = parser.parse_args(argv)

    now = datetime.now(UTC)
    report = build_report(
        deadline=parse_deadline(args.until),
        runs=summarize_runs(load_runs(args.runs_file), now=now),
        now=now,
        cooldown_minutes=args.cooldown_minutes,
        max_per_hour=args.max_per_hour,
        failure_limit=args.failure_limit,
    )
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    print(render(report), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
