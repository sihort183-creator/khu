"""자체 점검(docs/자동_점검_구조와_작업지시.md 4절).

수집 회차 뒤에 규칙만으로 이상을 찾는다. 판단에 사람이나 모형을 쓰지 않는다.
운영 자료를 바꾸지 않는다. 읽기와 원문 조회만 한다.

    python -m app.ops.selfcheck                    8개 항목을 모두 점검한다
    python -m app.ops.selfcheck --no-fetch         원문 요청 없이 데이터베이스만 본다
    python -m app.ops.selfcheck --probe-limit 30   조용한 0건 확인 상한(회차당)

결과는 기계 판독용 `.localstore/selfcheck.json` 과 사람 판독용 표로 함께 낸다.
표는 `$GITHUB_STEP_SUMMARY` 에 그대로 붙일 수 있게 마크다운으로 쓴다.
점검이 실패해도 수집을 실패시키지 않는다(작업 흐름에서 continue-on-error).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
from datetime import date, datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select

from app.config import Settings
from app.config import settings as default_settings
from app.domain.dates import KST, as_utc, parse_published, utcnow
from app.ingestion import get_adapter
from app.ingestion.http import Fetcher, FetchError
from app.run.initial import parse_deadline
from app.storage import models as m
from app.storage.db import session_scope

DEFAULT_OUTPUT = ".localstore/selfcheck.json"

# 상태 표기. 사람이 표에서 바로 읽는 값이므로 코드가 아니라 말로 쓴다.
OK = "정상"
WARN = "주의"
ALERT = "이상"
_RANK = {OK: 0, WARN: 1, ALERT: 2}

# 조용한 0건 판정 결과.
OUT_OF_WINDOW = "범위밖(정상)"
PROBE_FAILED = "확인실패"

ANCIENT_BEFORE = date(1990, 1, 1)
ZOMBIE_AFTER_HOURS = 6
# 백필이 남았는데 이만큼 새 회차가 없으면 정체로 본다. 회차 하나가 5분이다.
BACKFILL_IDLE_MINUTES = 30
STUCK_FAILURES = 3
PUBLISH_LAG_HOURS = 3
DETAIL_SAMPLE_LIMIT = 5000
LIST_LIMIT = 20

# 상세 실패 메시지를 성격별로 묶는다. 비공개·로그인 필요와 추출 실패를 갈라 본다.
_DETAIL_KINDS = (
    ("권한/로그인 필요", re.compile(r"권한이 없|로그인")),
    ("원문 없음", re.compile(r"찾을 수 없|삭제|404")),
    ("본문 추출 실패", re.compile(r"본문|추출|구조")),
)
_ERROR_PREFIX = re.compile(r"^\[(?P<kind>[a-z_]+)\]")


def _worst(*statuses: str) -> str:
    return max(statuses, key=lambda value: _RANK[value], default=OK)


def _iso(value: datetime | date | None) -> str | None:
    return value.isoformat() if value is not None else None


# --------------------------------------------------------------- 1 조용한 0건


def silent_zero_candidates(session) -> list[dict]:
    """목록 조회에 성공한 적이 있는데 저장된 원문이 하나도 없는 활성 출처."""
    counts = (
        select(m.SourceItem.source_id.label("source_id"))
        .group_by(m.SourceItem.source_id)
        .subquery()
    )
    rows = session.execute(
        select(m.Source, m.SourceConfigVersion)
        .join(m.SourceHealth, m.SourceHealth.source_id == m.Source.id)
        .join(
            m.SourceConfigVersion,
            (m.SourceConfigVersion.source_id == m.Source.id)
            & m.SourceConfigVersion.is_active.is_(True),
        )
        .outerjoin(counts, counts.c.source_id == m.Source.id)
        .where(
            m.Source.status == "active",
            m.SourceHealth.last_list_success_at.is_not(None),
            counts.c.source_id.is_(None),
        )
        .order_by(m.Source.id)
    ).all()
    return [
        {"source_id": s.id, "name": s.name, "adapter": s.adapter, "config": dict(c.config)}
        for s, c in rows
    ]


def rotate(candidates: list[dict], cursor: str | None, limit: int) -> list[dict]:
    """지난 회차 다음 출처부터 상한만큼 고른다. 끝에 닿으면 앞으로 돌아온다."""
    if limit <= 0 or not candidates:
        return []
    start = 0
    if cursor is not None:
        start = next(
            (i for i, c in enumerate(candidates) if c["source_id"] > cursor), 0
        )
    ordered = candidates[start:] + candidates[:start]
    return ordered[:limit]


async def probe_source(fetcher, candidate: dict, *, window_start: date | None) -> dict:
    """원문 1페이지를 실제로 받아 파서 인식 건수와 최신 글 날짜를 남긴다.

    상태 코드만으로는 '올릴 것이 없는 게시판'과 '수집이 빠뜨린 게시판'이 구분되지 않는다.
    """
    row = {
        "source_id": candidate["source_id"],
        "name": candidate["name"],
        "adapter": candidate["adapter"],
        "parsed_items": 0,
        "latest_date": None,
        "verdict": PROBE_FAILED,
        "error": None,
    }
    try:
        page = await get_adapter(candidate["adapter"]).list_page(fetcher, candidate["config"], 1)
    except FetchError as exc:
        row["error"] = str(exc)
        return row
    except Exception as exc:  # noqa: BLE001  점검이 수집을 실패시키지 않는다
        row["error"] = f"{type(exc).__name__}: {exc}"
        return row

    row["parsed_items"] = len(page.items)
    normal = [item for item in page.items if not item.is_pinned] or list(page.items)
    dates = [d for d in (parse_published(i.published_raw).date for i in normal) if d is not None]
    latest = max(dates) if dates else None
    row["latest_date"] = _iso(latest)
    if not page.items:
        row["verdict"] = ALERT
        row["error"] = "목록은 열렸는데 파서가 0건으로 읽었습니다"
    elif latest is None:
        row["verdict"] = ALERT
        row["error"] = "목록에서 날짜를 하나도 읽지 못했습니다"
    elif window_start is not None and latest < window_start:
        row["verdict"] = OUT_OF_WINDOW
    else:
        row["verdict"] = ALERT
    return row


async def probe_silent_zero(
    targets: list[dict], *, cfg: Settings, window_start: date | None, concurrency: int
) -> list[dict]:
    results: list[dict] = []
    gate = asyncio.Semaphore(max(1, concurrency))

    async def work(candidate: dict) -> None:
        async with gate:
            results.append(await probe_source(fetcher, candidate, window_start=window_start))

    async with Fetcher(cfg) as fetcher:
        await asyncio.gather(*(work(candidate) for candidate in targets))
    results.sort(key=lambda row: row["source_id"])
    return results


def check_silent_zero(candidates: list[dict], probes: list[dict], *, fetched: bool) -> dict:
    bad = [row for row in probes if row["verdict"] == ALERT]
    failed = [row for row in probes if row["verdict"] == PROBE_FAILED]
    if not fetched:
        status = WARN if candidates else OK
        summary = f"후보 {len(candidates)}곳 (원문 확인 생략)"
    elif bad:
        status = ALERT
        summary = f"후보 {len(candidates)}곳 중 {len(probes)}곳 확인, 기간 안 글이 있는데 0건인 곳 {len(bad)}곳"
    else:
        status = WARN if failed else OK
        summary = (
            f"후보 {len(candidates)}곳 중 {len(probes)}곳 확인, 전부 범위 밖"
            + (f" (확인 실패 {len(failed)}곳)" if failed else "")
        )
    return {
        "name": "조용한 0건",
        "status": status,
        "summary": summary,
        "candidates": len(candidates),
        "probed": len(probes),
        "anomalies": len(bad),
        "probe_failures": len(failed),
        "cursor": probes[-1]["source_id"] if probes else None,
        "results": probes,
        "candidate_ids": [c["source_id"] for c in candidates],
    }


# ---------------------------------------------------------------- 2 범위 미달


def check_window_shortfall(session, *, window_start: date | None, previous: dict | None) -> dict:
    if window_start is None:
        return {
            "name": "범위 미달",
            "status": OK,
            "summary": "수집 범위 하한이 없어 판정하지 않습니다",
            "count": 0,
            "sources": [],
        }
    rows = session.execute(
        select(m.Source.id, m.Source.name, m.SourceHealth.backfill_oldest_date,
               m.SourceHealth.backfill_last_stop_reason)
        .join(m.SourceHealth, m.SourceHealth.source_id == m.Source.id)
        .where(
            m.Source.status == "active",
            m.SourceHealth.backfill_oldest_date.is_not(None),
            m.SourceHealth.backfill_oldest_date > window_start,
        )
        .order_by(m.SourceHealth.backfill_oldest_date.desc())
    ).all()
    prior = (previous or {}).get("checks", {}).get("window_shortfall", {}).get("count")
    delta = None if prior is None else len(rows) - prior
    trend = "" if delta is None else f" (이전 {prior}, 변화 {delta:+d})"
    return {
        "name": "범위 미달",
        "status": OK if not rows else WARN,
        "summary": f"{window_start.isoformat()} 에 아직 못 닿은 출처 {len(rows)}곳" + trend,
        "count": len(rows),
        "previous_count": prior,
        "delta": delta,
        "sources": [
            {
                "source_id": sid,
                "name": name,
                "oldest_date": _iso(oldest),
                "stop_reason": reason,
            }
            for sid, name, oldest, reason in rows[:LIST_LIMIT]
        ],
        "truncated": max(0, len(rows) - LIST_LIMIT),
    }


# ---------------------------------------------------------------- 3 날짜 오염


def check_date_pollution(session, *, now: datetime) -> dict:
    today = now.astimezone(KST).date()
    rows = session.execute(
        select(m.Notice.id, m.Notice.title, m.Notice.published_date, m.Notice.status)
        .where(
            m.Notice.published_date.is_not(None),
            (m.Notice.published_date > today) | (m.Notice.published_date < ANCIENT_BEFORE),
        )
        .order_by(m.Notice.published_date.desc())
    ).all()
    future = sum(1 for _, _, day, _ in rows if day > today)
    return {
        "name": "날짜 오염",
        "status": OK if not rows else ALERT,
        "summary": f"발행일이 미래 {future}건 / {ANCIENT_BEFORE.isoformat()} 이전 {len(rows) - future}건",
        "count": len(rows),
        "future": future,
        "ancient": len(rows) - future,
        "notices": [
            {"notice_id": nid, "title": title[:80], "published_date": _iso(day), "status": state}
            for nid, title, day, state in rows[:LIST_LIMIT]
        ],
        "truncated": max(0, len(rows) - LIST_LIMIT),
    }


# ---------------------------------------------------------------- 4 상세 실패


def detail_error_kind(message: str) -> str:
    """오류 메시지를 성격별로 묶는다. 주소·숫자가 섞여 있어도 같은 유형으로 센다."""
    text = " ".join(message.split())
    prefix = _ERROR_PREFIX.match(text)
    if prefix:
        return prefix.group("kind")
    for label, pattern in _DETAIL_KINDS:
        if pattern.search(text):
            return label
    return text[:60]


def check_detail_failures(session) -> dict:
    total = int(
        session.execute(
            select(func.count(m.SourceItem.id)).where(m.SourceItem.last_detail_error.is_not(None))
        ).scalar()
        or 0
    )
    messages = session.execute(
        select(m.SourceItem.last_detail_error)
        .where(m.SourceItem.last_detail_error.is_not(None))
        .limit(DETAIL_SAMPLE_LIMIT)
    ).scalars().all()
    grouped: dict[str, int] = {}
    for message in messages:
        kind = detail_error_kind(message)
        grouped[kind] = grouped.get(kind, 0) + 1
    kinds = sorted(grouped.items(), key=lambda pair: -pair[1])
    top = ", ".join(f"{kind} {count}건" for kind, count in kinds[:3]) or "없음"
    return {
        "name": "상세 실패",
        "status": OK if not total else WARN,
        "summary": f"원문 {total}건 상세 실패 · {top}",
        "count": total,
        "sampled": len(messages),
        "kinds": [{"kind": kind, "count": count} for kind, count in kinds],
    }


# ---------------------------------------------------------------- 5 접근 실패


def check_access_failures(session) -> dict:
    rows = session.execute(
        select(
            m.Source.id,
            m.Source.name,
            m.SourceHealth.consecutive_failures,
            m.SourceHealth.last_error_kind,
            m.SourceHealth.last_error_message,
            m.SourceHealth.last_attempt_at,
        )
        .join(m.SourceHealth, m.SourceHealth.source_id == m.Source.id)
        .where(m.SourceHealth.consecutive_failures > 0)
        .order_by(m.SourceHealth.consecutive_failures.desc())
    ).all()
    stuck = [row for row in rows if row[2] >= STUCK_FAILURES]
    return {
        "name": "접근 실패",
        "status": ALERT if stuck else (WARN if rows else OK),
        "summary": f"연속 실패 {len(rows)}곳 (그중 {STUCK_FAILURES}회 이상 고착 {len(stuck)}곳)",
        "count": len(rows),
        "stuck": len(stuck),
        "sources": [
            {
                "source_id": sid,
                "name": name,
                "consecutive_failures": failures,
                "last_error_kind": kind,
                "last_error_message": (message or "")[:200] or None,
                "last_attempt_at": _iso(as_utc(attempt)),
                "stuck": failures >= STUCK_FAILURES,
            }
            for sid, name, failures, kind, message, attempt in rows[:LIST_LIMIT]
        ],
        "truncated": max(0, len(rows) - LIST_LIMIT),
    }


# ---------------------------------------------------------------- 6 좀비 실행


def check_zombie_runs(session, *, now: datetime, hours: int) -> dict:
    cutoff = now - timedelta(hours=hours)
    rows = session.execute(
        select(m.Run.id, m.Run.kind, m.Run.started_at)
        .where(m.Run.finished_at.is_(None), m.Run.started_at < cutoff)
        .order_by(m.Run.started_at)
    ).all()
    return {
        "name": "좀비 실행",
        "status": OK if not rows else ALERT,
        "summary": f"시작 후 {hours}시간이 지나도 끝나지 않은 실행 {len(rows)}건",
        "count": len(rows),
        "runs": [
            {
                "run_id": rid,
                "kind": kind,
                "started_at": _iso(as_utc(started)),
                "age_hours": round((now - as_utc(started)).total_seconds() / 3600, 1),
            }
            for rid, kind, started in rows[:LIST_LIMIT]
        ],
    }


# ---------------------------------------------------------------- 7 공개 지연


def check_publish_delay(session, *, now: datetime, lag_hours: int) -> dict:
    published = session.execute(
        select(m.Run.revision, m.Run.finished_at, m.Run.started_at)
        .where(m.Run.revision.is_not(None))
        .order_by(m.Run.started_at.desc())
        .limit(1)
    ).first()
    last_finished = session.execute(
        select(func.max(m.Run.finished_at)).where(m.Run.finished_at.is_not(None))
    ).scalar()
    published_at = as_utc(published[1] or published[2]) if published else None
    last_finished = as_utc(last_finished)
    if published_at is None:
        return {
            "name": "공개 지연",
            "status": ALERT,
            "summary": "공개 개정을 만든 실행 기록이 없습니다",
            "last_revision": None,
            "last_revision_at": None,
            "last_collect_finished_at": _iso(last_finished),
            "lag_hours": None,
        }
    reference = max(filter(None, (last_finished, published_at)))
    lag = round((reference - published_at).total_seconds() / 3600, 1)
    return {
        "name": "공개 지연",
        "status": OK if lag <= lag_hours else ALERT,
        "summary": (
            f"최신 개정 {published[0]} 생성 {published_at.isoformat()} · "
            f"마지막 수집 종료보다 {lag}시간 뒤처짐 (기준 {lag_hours}시간)"
        ),
        "last_revision": published[0],
        "last_revision_at": _iso(published_at),
        "last_collect_finished_at": _iso(last_finished),
        "lag_hours": lag,
        "threshold_hours": lag_hours,
    }


# ---------------------------------------------------------------- 8 백필 정체


def check_backfill_stall(
    session, *, window_start: date | None, now: datetime, deadline: datetime | None, idle_minutes: int
) -> dict:
    """초기 백필이 남았는데 아무도 그것을 진행시키지 않는 상태를 잡는다.

    2026-09-07 초기 마감이 지난 채 30곳이 남아 45분간 아무 회차도 뜨지 않았다.
    수집은 매번 성공으로 끝났으므로 다른 어떤 항목에도 걸리지 않았다.
    점검은 알려줄 뿐이고 되살리는 것은 app.ops.watchdog 이 한다.
    """
    rows = session.execute(
        select(m.SourceHealth.backfill_complete, m.SourceHealth.initial_window_start)
        .select_from(m.Source)
        .outerjoin(m.SourceHealth, m.SourceHealth.source_id == m.Source.id)
        .where(m.Source.status.in_(("active", "delayed", "blocked")))
    ).all()
    total = len(rows)
    complete = sum(bool(done and start == window_start) for done, start in rows)
    remaining = total - complete
    last_activity = session.execute(
        select(func.max(m.Run.started_at)).where(m.Run.kind == "collect")
    ).scalar()
    last_activity = as_utc(last_activity)
    idle = None if last_activity is None else round((now - last_activity).total_seconds() / 60, 1)

    # 막힌 출처는 영원히 남으므로 '남았다'는 사실만으로 이상이라 부르지 않는다.
    # 마감이 살아 있는데 아무도 수집하지 않는 상태만 이상이다.
    status, note = OK, "진행 중이거나 완료"
    if remaining and window_start is not None:
        if deadline is None:
            status, note = WARN, "초기 마감(KHU_INITIAL_UNTIL)이 비어 있어 이어받지 않습니다"
        elif now >= deadline:
            status, note = WARN, f"초기 마감 {deadline.isoformat()} 이 지나 이어받지 않습니다"
        elif idle is None or idle >= idle_minutes:
            status = ALERT
            note = (
                f"마감이 남았는데 마지막 수집 시작이 {idle if idle is not None else '기록 없음'}분 전입니다"
                f" (기준 {idle_minutes}분). 감시(app.ops.watchdog)가 되살려야 합니다"
            )
    return {
        "name": "백필 정체",
        "status": status,
        "summary": f"남은 백필 {remaining}곳 / 전체 {total}곳 · {note}",
        "remaining": remaining,
        "total": total,
        "idle_minutes": idle,
        "deadline": _iso(deadline),
        "last_collect_started_at": _iso(last_activity),
        "threshold_minutes": idle_minutes,
    }


# ------------------------------------------------------------------- 점검 실행


def _deadline(value: str) -> datetime | None:
    """마감 문자열이 망가져 있어도 나머지 일곱 항목은 점검한다."""
    try:
        return parse_deadline(value)
    except ValueError:
        return None


def load_previous(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def run_check(
    *,
    cfg: Settings | None = None,
    fetch: bool = True,
    probe_limit: int = 30,
    concurrency: int = 3,
    zombie_hours: int = ZOMBIE_AFTER_HOURS,
    lag_hours: int = PUBLISH_LAG_HOURS,
    backfill_idle_minutes: int = BACKFILL_IDLE_MINUTES,
    deadline: datetime | None = None,
    previous: dict | None = None,
    now: datetime | None = None,
) -> dict:
    cfg = cfg or default_settings
    now = now or utcnow()
    window_start = cfg.initial_window_start

    with session_scope(cfg) as session:
        candidates = silent_zero_candidates(session)
        checks = {
            "window_shortfall": check_window_shortfall(
                session, window_start=window_start, previous=previous
            ),
            "date_pollution": check_date_pollution(session, now=now),
            "detail_failures": check_detail_failures(session),
            "access_failures": check_access_failures(session),
            "zombie_runs": check_zombie_runs(session, now=now, hours=zombie_hours),
            "publish_delay": check_publish_delay(session, now=now, lag_hours=lag_hours),
            "backfill_stall": check_backfill_stall(
                session,
                window_start=window_start,
                now=now,
                deadline=deadline,
                idle_minutes=backfill_idle_minutes,
            ),
        }

    cursor = (previous or {}).get("checks", {}).get("silent_zero", {}).get("cursor")
    targets = rotate(candidates, cursor, probe_limit) if fetch else []
    probes = (
        asyncio.run(
            probe_silent_zero(
                targets, cfg=cfg, window_start=window_start, concurrency=concurrency
            )
        )
        if targets
        else []
    )
    checks = {"silent_zero": check_silent_zero(candidates, probes, fetched=fetch), **checks}

    return {
        "generated_at": now.isoformat(),
        "window_start": _iso(window_start),
        "status": _worst(*(check["status"] for check in checks.values())),
        "checks": checks,
    }


def render(report: dict) -> str:
    """사람이 읽는 요약. 마크다운이라 실행 요약에 그대로 붙는다."""
    lines = [
        f"## 자체 점검 — {report['status']}",
        "",
        f"기준 시각 {report['generated_at']} · 공개 하한 {report['window_start'] or '없음'}",
        "",
        "| 항목 | 상태 | 요약 |",
        "| --- | --- | --- |",
    ]
    for check in report["checks"].values():
        summary = check["summary"].replace("|", "／")
        lines.append(f"| {check['name']} | {check['status']} | {summary} |")

    silent = report["checks"]["silent_zero"]
    bad = [row for row in silent["results"] if row["verdict"] == ALERT]
    if bad:
        lines += [
            "",
            f"### 조용한 0건 — 이상 {len(bad)}곳",
            "",
            "| 출처 | 파서 인식 | 최신 글 | 비고 |",
            "| --- | --- | --- | --- |",
        ]
        lines += [
            f"| {row['name'][:40]} | {row['parsed_items']}건 | {row['latest_date'] or '불명'} "
            f"| {(row['error'] or '기간 안 글이 있는데 저장 0건')} |"
            for row in bad
        ]

    stuck = [row for row in report["checks"]["access_failures"]["sources"] if row["stuck"]]
    if stuck:
        lines += [
            "",
            f"### 고착된 접근 실패 {len(stuck)}곳",
            "",
            "| 출처 | 연속 실패 | 오류 |",
            "| --- | --- | --- |",
        ]
        lines += [
            f"| {row['name'][:40]} | {row['consecutive_failures']}회 | {row['last_error_kind'] or '불명'} |"
            for row in stuck
        ]

    if report["status"] == OK:
        lines += ["", "이상 없음."]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="khu-selfcheck", description="수집 결과 자체 점검(8개 항목)"
    )
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="기계 판독용 결과 파일")
    parser.add_argument("--probe-limit", type=int, default=30, help="회차당 원문 확인 상한")
    parser.add_argument("--concurrency", type=int, default=3, help="원문 확인 동시 요청 수")
    parser.add_argument("--no-fetch", action="store_true", help="원문 요청 없이 데이터베이스만 본다")
    parser.add_argument("--zombie-hours", type=int, default=ZOMBIE_AFTER_HOURS)
    parser.add_argument("--publish-lag-hours", type=int, default=PUBLISH_LAG_HOURS)
    parser.add_argument("--backfill-idle-minutes", type=int, default=BACKFILL_IDLE_MINUTES)
    parser.add_argument(
        "--initial-until",
        default=os.getenv("KHU_INITIAL_UNTIL", ""),
        help="초기 수집 마감. 백필 정체 판정에 쓴다",
    )
    parser.add_argument(
        "--fail-on-alert",
        action="store_true",
        help="이상이 있으면 종료 코드 1. 작업 흐름에서는 쓰지 않는다(수집을 막지 않는다)",
    )
    args = parser.parse_args(argv)

    output = Path(args.output)
    report = run_check(
        fetch=not args.no_fetch,
        probe_limit=args.probe_limit,
        concurrency=args.concurrency,
        zombie_hours=args.zombie_hours,
        lag_hours=args.publish_lag_hours,
        backfill_idle_minutes=args.backfill_idle_minutes,
        deadline=_deadline(args.initial_until),
        previous=load_previous(output),
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(render(report))
    return 1 if (args.fail_on_alert and report["status"] == ALERT) else 0


if __name__ == "__main__":
    raise SystemExit(main())
