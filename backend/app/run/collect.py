"""수집 실행 진입점(7.1절).

한 실행의 순서:
  1. 스키마 확인 → 실행 기록 시작
  2. 실행 시각이 된 출처 선택
  3. 출처마다 목록 → 새/바뀐 항목의 상세 → 원문 증거 보관 → 저장
  4. 분류·대상·마감 계산 → 공지 묶음 갱신
  5. 중복 후보 판정
  6. 정적 JSON 개정 생성·전환
  7. 실행 결과 기록 → 외부 감시에 완료 신호

시간 상한을 넘으면 새 출처 처리를 멈추고 4~7단계로 넘어간다. 남은 출처는 다음 실행이 먼저 본다.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.config import Settings
from app.config import settings as default_settings
from app.domain import audiences as audience_rules
from app.domain import categories as category_rules
from app.domain import dates as date_rules
from app.domain import dedupe as dedupe_rules
from app.export.static import ExportResult, export_static
from app.ingestion import get_adapter
from app.ingestion.base import FetchedDetail, ListedItem, ListPage, ParseError
from app.ingestion.http import Fetcher, FetchError
from app.storage import repository as repo
from app.storage.db import assert_schema_ready, session_scope
from app.storage.objects import ObjectStore, build_store, evidence_key

log = logging.getLogger("khu.collect")


@dataclass
class SourceOutcome:
    source_id: str
    name: str
    ok: bool
    list_items: int = 0
    new_items: int = 0
    updated_items: int = 0
    duration_ms: int = 0
    error_kind: str | None = None
    error_message: str | None = None
    detail_failures: int = 0


@dataclass
class RunOutcome:
    run_id: str
    result: str
    attempted: int = 0
    succeeded: int = 0
    skipped: int = 0
    new_items: int = 0
    updated_items: int = 0
    revision: str | None = None
    export: ExportResult | None = None
    outcomes: list[SourceOutcome] = field(default_factory=list)
    note: str | None = None


class TimeBudget:
    """실행 시간 상한. 30분이 지나면 새 출처를 시작하지 않는다."""

    def __init__(self, seconds: int) -> None:
        self.seconds = seconds
        self.started = time.monotonic()

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started

    @property
    def exhausted(self) -> bool:
        return self.elapsed >= self.seconds

    def remaining(self) -> float:
        return max(0.0, self.seconds - self.elapsed)


async def collect_source(
    session: Session,
    fetcher: Fetcher,
    store: ObjectStore,
    due: repo.DueSource,
    *,
    cfg: Settings,
    budget: TimeBudget,
    campus_map: dict[str, tuple[str, str]],
) -> SourceOutcome:
    """출처 하나를 처리한다. 여기서 난 예외는 다른 출처로 번지지 않는다."""
    started = time.monotonic()
    source = due.source
    adapter = get_adapter(source.adapter)
    outcome = SourceOutcome(source_id=source.id, name=source.name, ok=False)

    seen_ids: set[str] = set()
    known_streak = 0
    detail_complete = True

    try:
        for page_index in range(1, cfg.list_page_limit + 1):
            page: ListPage = await adapter.list_page(fetcher, due.config, page_index)
            outcome.list_items += len(page.items)

            if not page.items:
                break

            for listed in page.items:
                # 글 하나를 받는 데도 서버 예의상 간격을 지킨다. 첫 수집처럼 새 글이
                # 수십 건인 게시판에서는 이 안쪽 루프만으로 예산을 넘길 수 있다.
                # 넘기면 남은 글은 다음 실행으로 넘긴다.
                if budget.exhausted:
                    detail_complete = False
                    break

                seen_ids.add(listed.external_id)
                try:
                    changed = await _process_item(
                        session,
                        fetcher,
                        store,
                        adapter,
                        due,
                        listed,
                        cfg=cfg,
                        campus_map=campus_map,
                    )
                except ParseError as exc:
                    # 한 건의 분석 실패가 출처 전체 실패는 아니다(4절 8항).
                    outcome.detail_failures += 1
                    detail_complete = False
                    log.warning("상세 분석 실패 %s/%s: %s", source.id, listed.external_id, exc)
                    continue
                except FetchError as exc:
                    outcome.detail_failures += 1
                    detail_complete = False
                    if exc.kind in ("access_denied", "rate_limited", "blocked_target"):
                        raise
                    log.warning("상세 요청 실패 %s/%s: %s", source.id, listed.external_id, exc)
                    continue

                if changed == "new":
                    outcome.new_items += 1
                elif changed == "updated":
                    outcome.updated_items += 1
                else:
                    known_streak += 1

            if budget.exhausted:
                detail_complete = False
                break

            # 이미 아는 항목만 나오는 구간에 닿으면 멈춘다.
            # 고정 공지만 만났다고 끝내지 않는다(7.2절 2항).
            non_pinned = [i for i in page.items if not i.is_pinned]
            if non_pinned and known_streak >= len(non_pinned) and page_index >= 1:
                break
            if not page.has_next:
                break
            if budget.exhausted:
                detail_complete = False
                break

        removed = repo.mark_items_missing(session, source.id, seen_ids)
        if removed:
            log.info("%s: 연속 미발견으로 삭제 표시 %d건", source.id, removed)

        repo.mark_source_success(session, due.health, detail_complete=detail_complete)
        if source.status == "delayed":
            # 지연 상태였다가 성공하면 정상으로 되돌린다. pending 은 여기서 올리지 않는다.
            source.status = "active"
            source.status_message = None
        outcome.ok = True

    except FetchError as exc:
        repo.mark_source_failure(session, due.health, kind=exc.kind, message=str(exc))
        outcome.error_kind = exc.kind
        outcome.error_message = str(exc)
    except ParseError as exc:
        repo.mark_source_failure(session, due.health, kind="parse_error", message=str(exc))
        outcome.error_kind = "parse_error"
        outcome.error_message = str(exc)
    except Exception as exc:  # noqa: BLE001 - 한 출처의 장애를 실행 전체로 번지지 않게 한다
        repo.mark_source_failure(session, due.health, kind="unexpected", message=repr(exc))
        outcome.error_kind = "unexpected"
        outcome.error_message = repr(exc)
        log.exception("출처 처리 중 예상치 못한 오류: %s", source.id)

    outcome.duration_ms = int((time.monotonic() - started) * 1000)
    return outcome


async def _process_item(
    session: Session,
    fetcher: Fetcher,
    store: ObjectStore,
    adapter,
    due: repo.DueSource,
    listed: ListedItem,
    *,
    cfg: Settings,
    campus_map: dict[str, tuple[str, str]],
) -> str:
    """원본 1건. 'new' | 'updated' | 'known' 을 돌려준다."""
    detail: FetchedDetail = await adapter.detail(fetcher, due.config, listed)

    published = date_rules.parse_published(detail.published_raw or listed.published_raw)
    digest = dedupe_rules.content_hash(detail.title, detail.body_text)

    raw_key: str | None = None
    if not cfg.dry_run:
        raw_key = evidence_key(due.source.id, listed.external_id, digest)
        try:
            store.put_bytes(
                cfg.r2.bucket_evidence,
                raw_key,
                detail.raw_html.encode("utf-8"),
                content_type="text/html; charset=utf-8",
                compress=True,
            )
        except Exception as exc:  # noqa: BLE001 - 증거 저장 실패가 수집을 멈추지 않는다
            log.warning("원문 증거 보관 실패 %s: %s", raw_key, exc)
            raw_key = None

    written = repo.upsert_item_and_revision(
        session,
        source=due.source,
        listed=listed,
        detail=detail,
        published=published,
        raw_object_key=raw_key,
        extractor_version=str(detail.extraction_notes.get("extractor", "unknown")),
    )

    if not written.is_new_revision:
        return "known"

    revision = session.get(
        __import__("app.storage.models", fromlist=["SourceItemRevision"]).SourceItemRevision,
        written.revision_id,
    )

    category = category_rules.classify(
        detail.title, detail.body_text, board_category=detail.board_category
    )
    audience = audience_rules.decide(
        detail.title,
        detail.body_text,
        source_defaults=due.audience_defaults,
        campus_lookup=campus_map,
    )
    deadline = date_rules.guess_deadline(detail.title, detail.body_text, published=published.date)

    repo.upsert_notice_for_item(
        session,
        item_id=written.item_id,
        revision=revision,
        category=category,
        audience=audience,
        deadline=deadline,
    )
    return "new" if written.is_new_item else "updated"


def run_dedupe_pass(session: Session, *, limit: int = 300) -> int:
    """최근 항목끼리 중복 후보를 비교한다. 자동 병합은 확실할 때만 한다(9.1절)."""
    rows = repo.recent_items_for_dedupe(session, limit=limit)
    candidates: list[tuple[dedupe_rules.Candidate, object]] = []
    for item, revision, notice in rows:
        candidates.append(
            (
                dedupe_rules.Candidate(
                    item_id=item.id,
                    revision_id=revision.id,
                    source_id=item.source_id,
                    organization_id=None,
                    title=revision.title,
                    body_text=revision.body_text,
                    published_date=revision.published_date,
                ),
                notice,
            )
        )

    merged = 0
    seen_pairs: set[tuple[str, str]] = set()
    for index, (left, left_notice) in enumerate(candidates):
        for right, right_notice in candidates[index + 1 :]:
            pair = tuple(sorted((left.item_id, right.item_id)))
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)

            decision = dedupe_rules.compare(left, right)
            if decision.decision == "distinct" and decision.score < 0.5:
                continue

            repo.record_dedupe_decision(
                session,
                left_item=left.item_id,
                right_item=right.item_id,
                left_revision=left.revision_id,
                right_revision=right.revision_id,
                decision=decision.decision,
                score=decision.score,
                signals=decision.signals,
                rule_version=decision.rule_version,
            )

            if (
                decision.decision == "merge"
                and left_notice is not None
                and right_notice is not None
                and left_notice.id != right_notice.id
            ):
                keep = dedupe_rules.pick_primary([left, right])
                keep_notice = left_notice if keep.item_id == left.item_id else right_notice
                absorb_notice = right_notice if keep.item_id == left.item_id else left_notice
                repo.merge_notices(
                    session,
                    keep_notice_id=keep_notice.id,
                    absorb_notice_id=absorb_notice.id,
                    reason=decision.reason,
                )
                merged += 1
    return merged


async def run_collection(cfg: Settings | None = None, *, source_keys: list[str] | None = None) -> RunOutcome:
    cfg = cfg or default_settings
    budget = TimeBudget(cfg.run_budget_seconds)
    store = build_store(cfg)
    commit = os.environ.get("GITHUB_SHA") or os.environ.get("KHU_CODE_COMMIT")

    with session_scope(cfg) as session:
        assert_schema_ready(session)
        run = repo.start_run(session, code_commit=commit)
        run_id = run.id
        campus_map = repo.campus_lookup(session)
        due_list = repo.load_due_sources(session)
        if source_keys:
            wanted = set(source_keys)
            due_list = [d for d in due_list if d.source.id in wanted or d.source.name in wanted]

    outcome = RunOutcome(run_id=run_id, result="success")
    outcomes: list[SourceOutcome] = []

    async with Fetcher(cfg) as fetcher:
        for due_ref in due_list:
            if budget.exhausted:
                outcome.skipped += 1
                continue

            # 출처마다 짧은 트랜잭션을 쓴다. 원문 요청 중 데이터베이스를 잡고 있지 않는다.
            with session_scope(cfg) as session:
                due = _reload_due(session, due_ref)
                if due is None:
                    continue
                run = session.get(_run_model(), run_id)
                source_outcome = await collect_source(
                    session,
                    fetcher,
                    store,
                    due,
                    cfg=cfg,
                    budget=budget,
                    campus_map=campus_map,
                )
                repo.record_source_run(
                    session,
                    run,
                    due.source.id,
                    succeeded=source_outcome.ok,
                    list_items=source_outcome.list_items,
                    new_items=source_outcome.new_items,
                    updated_items=source_outcome.updated_items,
                    duration_ms=source_outcome.duration_ms,
                    error_kind=source_outcome.error_kind,
                    error_message=source_outcome.error_message,
                )

            outcomes.append(source_outcome)
            outcome.attempted += 1
            outcome.succeeded += int(source_outcome.ok)
            outcome.new_items += source_outcome.new_items
            outcome.updated_items += source_outcome.updated_items

    with session_scope(cfg) as session:
        merged = run_dedupe_pass(session)
        if merged:
            log.info("중복 자동 병합 %d건", merged)

    export_result = export_static(cfg, run_id=run_id, store=store)

    with session_scope(cfg) as session:
        run = session.get(_run_model(), run_id)
        failed = [o for o in outcomes if not o.ok]
        result = "success"
        if outcome.attempted and len(failed) == outcome.attempted:
            result = "failed"
        elif failed or outcome.skipped:
            result = "partial"
        run.sources_attempted = outcome.attempted
        run.sources_succeeded = outcome.succeeded
        run.sources_skipped = outcome.skipped
        run.items_new = outcome.new_items
        run.items_updated = outcome.updated_items
        repo.finish_run(
            session,
            run,
            result=result,
            revision=export_result.revision,
            note=(
                f"시간 {budget.elapsed:.0f}초, 요청 {fetcher.requests_made}회"
                if not failed
                else f"실패 {len(failed)}건: " + ", ".join(f"{o.name}({o.error_kind})" for o in failed[:5])
            ),
        )

    outcome.result = result
    outcome.outcomes = outcomes
    outcome.revision = export_result.revision
    outcome.export = export_result
    _send_heartbeat(cfg, outcome)
    return outcome


def _run_model():
    from app.storage.models import Run

    return Run


def _reload_due(session: Session, due: repo.DueSource) -> repo.DueSource | None:
    """새 트랜잭션에서 같은 출처를 다시 읽는다."""
    from app.storage import models as m

    source = session.get(m.Source, due.source.id)
    if source is None:
        return None
    health = session.get(m.SourceHealth, due.source.id)
    if health is None:
        health = m.SourceHealth(source_id=source.id)
        session.add(health)
        session.flush()
    return repo.DueSource(
        source=source,
        config=due.config,
        interval_minutes=due.interval_minutes,
        health=health,
        audience_defaults=due.audience_defaults,
    )


def _send_heartbeat(cfg: Settings, outcome: RunOutcome) -> None:
    """외부 감시에 완료 신호를 보낸다. 실패해도 실행 결과를 바꾸지 않는다(18절)."""
    if not cfg.heartbeat_url:
        return
    import urllib.request

    url = cfg.heartbeat_url
    if outcome.result == "failed":
        url = url.rstrip("/") + "/fail"
    try:
        request = urllib.request.Request(
            url,
            data=f"run={outcome.run_id} result={outcome.result} new={outcome.new_items}".encode(),
            headers={"User-Agent": cfg.user_agent},
        )
        urllib.request.urlopen(request, timeout=10).close()
    except Exception as exc:  # noqa: BLE001
        log.warning("완료 신호 전송 실패: %s", exc)


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="경희 공지 수집 실행")
    parser.add_argument("--sources", nargs="*", default=None, help="특정 출처만 실행")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    outcome = asyncio.run(run_collection(source_keys=args.sources))
    print(
        f"실행 {outcome.run_id}: {outcome.result} | 출처 {outcome.succeeded}/{outcome.attempted} 성공"
        f" | 새 글 {outcome.new_items} | 갱신 {outcome.updated_items} | 건너뜀 {outcome.skipped}"
        f" | 개정 {outcome.revision}"
    )
    for failed in [o for o in outcome.outcomes if not o.ok]:
        print(f"  실패: {failed.name} [{failed.error_kind}] {failed.error_message}")
    return 0 if outcome.result != "failed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
