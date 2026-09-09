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
import hashlib
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.config import settings as default_settings
from app.domain import audiences as audience_rules
from app.domain import categories as category_rules
from app.domain import dates as date_rules
from app.domain import dedupe as dedupe_rules
from app.domain import ids
from app.domain.images import poster_keys
from app.export.static import ExportResult, export_static, refresh_public_status
from app.ingestion import get_adapter
from app.ingestion.base import FetchedDetail, ListedItem, ParseError, RestrictedError
from app.ingestion.http import Fetcher, FetchError
from app.storage import models as m
from app.storage import repository as repo
from app.storage.db import ReadStats, assert_schema_ready, enable_read_meter, read_stats, session_scope
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
    list_complete: bool = False
    detail_complete: bool = True
    scan_stop_reason: str = "not_started"
    backfill_complete: bool = False
    missing_check_performed: bool = False
    partial: bool = False


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
    """실행 시간 상한. 남은 시간은 다음 출처·내보내기에 남겨 둔다."""

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
    session: Session, fetcher: Fetcher, store: ObjectStore, due: repo.DueSource,
    *, cfg: Settings, budget: TimeBudget, campus_map: dict[str, tuple[str, str]],
    persist_progress: bool = False,
    initial_mode: bool = False,
) -> SourceOutcome:
    """최신 확인, 실패 상세 재시도, 기준 원문을 확인한 과거 재개를 수행한다."""
    started = time.monotonic()
    source, health = due.source, due.health
    adapter = get_adapter(source.adapter)
    outcome = SourceOutcome(source_id=source.id, name=source.name, ok=False)
    window = cfg.initial_window_start
    policy_window = window or date(2026, 3, 1)
    if health.initial_window_start is not None and health.initial_window_start != policy_window:
        health.backfill_complete = False
        health.backfill_boundary_reached = False
        health.backfill_cursor_page = 1
        health.backfill_cursor_external_id = None
        health.backfill_oldest_date = None
    cursor = health.backfill_cursor_page or 1
    anchor = health.backfill_cursor_external_id
    if cursor > 1 and not anchor:
        cursor = 1
    reached = bool(health.backfill_boundary_reached)
    seen: set[str] = set()
    processed: set[str] = set()
    stop = "not_started"
    oldest = health.backfill_oldest_date
    limit = max(1, cfg.list_page_limit)
    # 날짜순을 검증한 출처는 한 쪽만 보고 조기 종료할 수 있다.
    verified_order = due.config.get("date_ordered") is True
    order_evidence = due.config.get("date_order_evidence") or {}
    try:
        verified_order &= (
            order_evidence.get("method") == "full_listing_monotonic"
            and datetime.fromisoformat(order_evidence["valid_until"]) > datetime.now(UTC)
        )
    except (KeyError, ValueError, TypeError):
        verified_order = False
    # 낙관 모드는 사전 감사 없이 최신순을 가정한다. 감사가 게시판을 끝까지 읽어야 하므로
    # 아끼려는 비용을 그대로 내기 때문이다. 잘못 멈춰 놓치는 글은 수집 범위보다 오래된
    # 글이라 어차피 공개하지 않고, 정렬이 실제로 깨진 게시판은 아래 invalidate_order 가
    # 회차 안에서 잡아 되돌린다. 한 번 역전이 확인된 출처는 다시 가정하지 않는다.
    assumed_order = bool(cfg.optimistic_date_boundary and not due.config.get("date_order_invalidated"))
    ordered = verified_order or assumed_order
    # 가정에 기댄 출처는 글 하나가 잘못 꽂힌 것에 속지 않도록 연속 두 쪽을 요구한다.
    boundary_pages = 1 if verified_order else 2
    full_scan = False

    def invalidate_order(reason: str) -> None:
        """원문이 검증 결과와 다르면 새 설정 버전으로 종료 가정을 폐기한다."""
        nonlocal ordered, reached
        ordered = reached = False
        health.backfill_boundary_reached = False
        health.backfill_complete = False
        current = session.scalar(select(m.SourceConfigVersion).where(
            m.SourceConfigVersion.source_id == source.id, m.SourceConfigVersion.is_active.is_(True),
        ))
        if current and (current.config.get("date_ordered") is True or assumed_order):
            replacement = dict(current.config)
            replacement["date_ordered"] = False
            replacement["date_order_invalidated"] = {"reason": reason, "at": datetime.now(UTC).isoformat()}
            current.is_active = False
            session.flush()
            session.add(m.SourceConfigVersion(
                id=f"cfg-{uuid4().hex}", source_id=source.id, version=current.version + 1,
                config=replacement, interval_minutes=current.interval_minutes, is_active=True,
            ))

    async def process(listed: ListedItem) -> bool:
        """일반 글의 기존 여부를 반환한다. 실패·고정 글로 종료를 유발하지 않는다."""
        if budget.exhausted:
            return False
        existing = repo.item_for_listing(session, source.id, listed.external_id)
        known = existing is not None and existing.current_revision_id is not None and not existing.last_detail_error
        if listed.external_id in processed:
            return bool(known and not listed.is_pinned)
        processed.add(listed.external_id)
        published = date_rules.parse_published(listed.published_raw).date
        if window and published and published < window and not listed.is_pinned:
            return bool(known)
        try:
            if repo.detail_is_due(
                session, source_id=source.id, listed=listed, recheck_days=cfg.recheck_days,
                defer_ordinary_rechecks=initial_mode and not health.backfill_complete,
            ):
                if persist_progress:
                    session.commit()
                changed = await _process_item(
                    session, fetcher, store, adapter, due, listed, cfg=cfg, campus_map=campus_map,
                )
                outcome.new_items += int(changed == "new")
                outcome.updated_items += int(changed == "updated")
                seen.add(listed.external_id)
            else:
                repo.touch_item_from_listing(session, source_id=source.id, listed=listed)
        except RestrictedError:
            # 로그인해야 볼 수 있는 글이다. 우리가 고칠 수 없는 실패이고, 없는 것처럼
            # 두면 게시판이 통째로 사라진다. 목록에서 얻은 것만으로 남긴다.
            repo.ensure_item_stub(session, source=source, listed=listed)
            changed = record_listing_only(session, due, listed, campus_map=campus_map)
            outcome.new_items += int(changed == "new")
            outcome.updated_items += int(changed == "updated")
            seen.add(listed.external_id)
            return False
        except (ParseError, FetchError) as exc:
            repo.ensure_item_stub(session, source=source, listed=listed)
            repo.mark_detail_failure(session, source_id=source.id, external_id=listed.external_id, message=str(exc))
            outcome.detail_failures += 1
            outcome.partial = True
            if isinstance(exc, FetchError) and exc.kind in {"access_denied", "rate_limited", "blocked_target"}:
                raise
            return False
        return bool(known and not listed.is_pinned)

    async def scan(start: int, *, history: bool, expected_anchor: str | None = None) -> str:
        nonlocal cursor, anchor, oldest, reached, full_scan
        anchor_found = expected_anchor is None
        previous_normal_date = None
        below_window = 0
        # 겹침 확인에 사용한 두 쪽은 신규 구간 예산을 잠식하지 않는다.
        for page_no in range(start, start + limit + (2 if expected_anchor else 0)):
            if budget.exhausted:
                return "time_budget"
            if persist_progress:
                session.commit()
            page = await adapter.list_page(fetcher, due.config, page_no)
            outcome.list_items += len(page.items)
            if expected_anchor and any(i.external_id == expected_anchor for i in page.items):
                anchor_found = True
            if not page.items:
                if history and not anchor_found:
                    cursor, anchor = 1, None
                    return "anchor_missing"
                if history:
                    reached = True
                full_scan = start == 1
                return "end_of_board"
            normal = [i for i in page.items if not i.is_pinned]
            # 강한 참조를 페이지 처리 동안 보존하여 글마다 같은 DB 조회를 반복하지 않는다.
            page_records = repo.preload_listing_items(session, source.id, page.items)
            known_normal = 0
            for listed in page.items:
                if budget.exhausted:
                    # 현재 페이지를 완료하지 않았으므로 이전 확정 위치를 유지한다.
                    return "time_budget"
                seen.add(listed.external_id)
                published = date_rules.parse_published(listed.published_raw).date
                if published and not listed.is_pinned:
                    oldest = min(oldest, published) if oldest else published
                known_normal += int(await process(listed))
            del page_records
            dates = [date_rules.parse_published(i.published_raw).date for i in normal]
            if ordered and dates:
                if any(d is None for d in dates):
                    invalidate_order("unknown_date")
                elif any(left < right for left, right in zip(dates, dates[1:], strict=False)) or (
                    previous_normal_date is not None and previous_normal_date < dates[0]
                ):
                    invalidate_order("date_inversion")
                previous_normal_date = dates[-1]
            if history and anchor_found:
                cursor = page_no
                anchor = normal[-1].external_id if normal else page.items[-1].external_id
                repo.record_scan_progress(
                    session, health, initial_window_start=policy_window, cursor_page=cursor,
                    cursor_external_id=anchor, oldest_date=oldest, stop_reason="in_progress", complete=False,
                )
                if persist_progress:
                    session.commit()
            page_below = bool(window and ordered and dates and all(d is not None and d < window for d in dates))
            below_window = below_window + 1 if page_below else 0
            # 안전망: 날짜순 판정이 해제된 출처라도 한 쪽의 일반 글이 전부 수집 범위보다
            # 오래되면 그 자리에서 멈춘다. 2026-09-07 역전을 만나 가정을 잃은 출처 9곳이
            # 2011~2018년까지 게시판 끝을 향해 내려가고 있었다. 글 하나가 아니라 "쪽 전체"를
            # 기준으로 삼아 고정 공지·역전 한 건에 속지 않게 하고, 날짜를 모르는 글은
            # 판단에서 뺀다(쪽에 날짜 있는 글이 하나도 없으면 멈추지 않는다).
            known_dates = [d for d in dates if d is not None]
            page_all_old = bool(window and known_dates and all(d < window for d in known_dates))
            boundary = below_window >= boundary_pages or page_all_old
            if not page.has_next or boundary:
                if history and not anchor_found:
                    cursor, anchor = 1, None
                    return "anchor_missing"
                if history:
                    reached = True
                full_scan = not page.has_next and start == 1
                return "date_boundary" if boundary else "end_of_board"
            if not history and normal and known_normal == len(normal):
                return "known_streak"
        if history and not anchor_found:
            cursor, anchor = 1, None
            return "anchor_missing"
        return "page_limit"

    try:
        # 첫 순회는 최신 확인과 과거 탐색이 같은 연속 구간이다.
        if cursor <= 1 and not anchor and not reached:
            stop = await scan(1, history=True)
        else:
            latest_stop = await scan(1, history=False)
            stop = latest_stop
            if latest_stop == "page_limit" and reached:
                # 완료한 출처에서 새 구간이 한도를 넘으면 초기 범위를 다시 연다.
                # 진행 중인 출처는 아래 과거 재개의 원문 기준점을 검증한다.
                # 기간 밖 글은 저장하지 않으므로 최신 구간의 known 부재만으로
                # 진행 위치를 지우면 오래된 게시판이 같은 첫 페이지들에 갇힌다.
                reached = False
                cursor, anchor = 1, None
            if latest_stop != "time_budget" and not reached:
                stop = await scan(max(1, cursor - 1), history=True, expected_anchor=anchor)

        # 목록 위치에 의존하지 않는 재시도. 이미 이번에 처리한 글은 중복 요청하지 않는다.
        for listed in repo.pending_detail_listings(session, source.id):
            if budget.exhausted:
                outcome.partial = True
                break
            await process(listed)

        health.backfill_boundary_reached = reached
        repo.record_scan_progress(
            session, health, initial_window_start=policy_window, cursor_page=cursor,
            cursor_external_id=anchor, oldest_date=oldest, stop_reason=stop, complete=full_scan,
        )
        outcome.backfill_complete = bool(health.backfill_complete)
        outcome.detail_complete = not bool(session.scalar(
            select(func.count()).select_from(m.SourceItem)
            .where(
                m.SourceItem.source_id == source.id,
                m.SourceItem.last_detail_error.isnot(None),
            )
        ))
        outcome.list_complete = full_scan
        if full_scan:
            repo.mark_items_missing(session, source.id, seen)
            outcome.missing_check_performed = True
        repo.mark_source_success(session, health, detail_complete=outcome.detail_complete and stop != "time_budget")
        outcome.ok = True
        outcome.partial |= not outcome.detail_complete or stop in {"page_limit", "anchor_missing", "time_budget"}
        if source.status == "delayed":
            source.status, source.status_message = "active", None
    except (FetchError, ParseError) as exc:
        kind = exc.kind if isinstance(exc, FetchError) else "parse_error"
        repo.mark_source_failure(session, health, kind=kind, message=str(exc))
        outcome.error_kind, outcome.error_message = kind, str(exc)
        stop = "blocked" if kind in {"access_denied", "rate_limited", "blocked_target"} else "list_error"
        health.backfill_boundary_reached = reached
        repo.record_scan_progress(
            session, health, initial_window_start=policy_window, cursor_page=cursor,
            cursor_external_id=anchor, oldest_date=oldest, stop_reason=stop, complete=False,
        )
        outcome.partial = True
        outcome.detail_complete = False
    outcome.scan_stop_reason = stop
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
    digest = dedupe_rules.content_hash(detail.title, detail.body_text, detail.attachments)

    # 상세를 열기 전에는 목록 메타데이터로 재요청 여부를 판단한다. 여기까지
    # 도달한 항목은 이미 재확인 대상이므로, 같은 내용의 원문은 다시 올리지 않는다.
    existing = repo.item_for_listing(session, due.source.id, listed.external_id)
    current = (
        session.get(
            __import__("app.storage.models", fromlist=["SourceItemRevision"]).SourceItemRevision,
            existing.current_revision_id,
        )
        if existing is not None and existing.current_revision_id
        else None
    )

    raw_key: str | None = current.raw_object_key if current else None
    should_store_evidence = current is None or not raw_key or current.content_hash != digest or current.published_raw != published.raw
    if not cfg.dry_run and should_store_evidence:
        raw_key = evidence_key(due.source.id, listed.external_id, hashlib.sha256(detail.raw_html.encode("utf-8")).hexdigest())
        try:
            await asyncio.to_thread(store.put_bytes,
                cfg.r2.bucket_evidence,
                raw_key,
                detail.raw_html.encode("utf-8"),
                content_type="text/html; charset=utf-8",
                compress=True,
            )
        except Exception as exc:  # noqa: BLE001 - 증거 저장 실패가 수집을 멈추지 않는다
            raise ParseError("원문 증거 보관 실패: 재시도 전까지 공개를 보류합니다") from exc

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


def record_listing_only(
    session: Session,
    due: repo.DueSource,
    listed: ListedItem,
    *,
    campus_map: dict[str, tuple[str, str]],
) -> str:
    """본문을 못 여는 글을 목록 정보만으로 남긴다.

    로그인해야 볼 수 있는 글이 그렇다. 본문이 없다고 빼 버리면 미래인재센터 채용
    게시판처럼 게시판 하나가 통째로 없는 것처럼 보인다. 제목·발행일·주소는 목록에서
    이미 얻었으므로 그것만으로 공지를 만들고, 원문 상태를 "로그인 필요"로 표시해
    화면이 왜 본문이 없는지 말해 줄 수 있게 한다. 증거는 보관할 원문이 없어 남기지 않는다.
    """
    detail = FetchedDetail(
        external_id=listed.external_id,
        url=listed.url,
        title=listed.title,
        body_text="",
        body_html="",
        raw_html="",
        published_raw=listed.published_raw,
        extraction_notes={"extractor": "listing_only"},
    )
    published = date_rules.parse_published(listed.published_raw)
    written = repo.upsert_item_and_revision(
        session,
        source=due.source,
        listed=listed,
        detail=detail,
        published=published,
        raw_object_key=None,
        extractor_version="listing_only",
    )
    item = session.get(m.SourceItem, written.item_id)
    if item is not None:
        item.original_status = "restricted"
    if not written.is_new_revision:
        return "known"
    revision = session.get(m.SourceItemRevision, written.revision_id)
    repo.upsert_notice_for_item(
        session,
        item_id=written.item_id,
        revision=revision,
        category=category_rules.classify(listed.title, None, board_category=None),
        audience=audience_rules.decide(
            listed.title, None, source_defaults=due.audience_defaults, campus_lookup=campus_map,
        ),
        deadline=date_rules.guess_deadline(listed.title, None, published=published.date),
    )
    return "new" if written.is_new_item else "updated"


class _MergeRouter:
    """병합 대상을 살아 있는 대표 공지로 돌린다.

    한 회차 안에서 A→B 를 병합하면 A 는 숨겨지고 원본 연결은 B 로 옮겨진다. 그 뒤
    같은 회차가 (A, C) 를 다시 병합하려 할 때 A 를 대표로 고르면 C 의 원본이 이미
    숨겨진 A 에 매달려 화면에서 통째로 사라진다. 실측 자료로 전체 판정을 돌려 보면
    이런 짝이 479번 생기고 공지 55건이 원본을 쥔 채 사라진다. 대표를 항상 현재
    살아 있는 공지로 다시 풀어서 그 구멍을 막는다.
    """

    def __init__(self) -> None:
        self._alias: dict[str, str] = {}

    def resolve(self, notice_id: str) -> str:
        seen = notice_id
        while self._alias.get(seen, seen) != seen:
            seen = self._alias[seen]
        if seen != notice_id:
            self._alias[notice_id] = seen
        return seen

    def merge(
        self,
        session: Session,
        *,
        keep_notice_id: str,
        absorb_notice_id: str,
        reason: str,
    ) -> bool:
        keep_id = self.resolve(keep_notice_id)
        absorb_id = self.resolve(absorb_notice_id)
        if keep_id == absorb_id:
            return False
        keep = session.get(m.Notice, keep_id)
        absorb = session.get(m.Notice, absorb_id)
        if keep is None or absorb is None:
            return False
        if keep.status != "visible" and absorb.status == "visible":
            keep, absorb = absorb, keep
        if keep.status != "visible":
            # 둘 다 이미 공개 대상이 아니다. 건드리면 원본만 옮겨 다닌다.
            return False
        repo.merge_notices(
            session, keep_notice_id=keep.id, absorb_notice_id=absorb.id, reason=reason
        )
        self._alias[absorb.id] = keep.id
        return True


def _to_candidate(item: m.SourceItem, revision: m.SourceItemRevision) -> dedupe_rules.Candidate:
    return dedupe_rules.Candidate(
        item_id=item.id,
        revision_id=revision.id,
        source_id=item.source_id,
        organization_id=None,
        title=revision.title,
        body_text=revision.body_text,
        published_date=revision.published_date,
        poster_keys=poster_keys(revision.body_html, base_url=item.canonical_url),
    )


def _judge_pairs(
    session: Session,
    pairs,
    *,
    router: _MergeRouter,
) -> tuple[int, int]:
    """짝마다 판정을 기록하고 확실한 것만 병합한다. (기록 수, 병합 수)를 준다."""
    recorded = merged = 0
    for left, left_notice, right, right_notice in pairs:
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
        recorded += 1
        if decision.decision != "merge" or left_notice is None or right_notice is None:
            continue
        keep = dedupe_rules.pick_primary([left, right])
        keep_notice = left_notice if keep.item_id == left.item_id else right_notice
        absorb_notice = right_notice if keep.item_id == left.item_id else left_notice
        merged += int(
            router.merge(
                session,
                keep_notice_id=keep_notice.id,
                absorb_notice_id=absorb_notice.id,
                reason=decision.reason,
            )
        )
    return recorded, merged


def run_dedupe_pass(session: Session, *, limit: int = 300) -> int:
    """최근 항목끼리 중복 후보를 비교한다. 자동 병합은 확실할 때만 한다(9.1절)."""
    rows = repo.recent_items_for_dedupe(session, limit=limit)
    candidates = [(_to_candidate(item, revision), notice) for item, revision, notice in rows]

    def pairs():
        seen: set[tuple[str, str]] = set()
        for index, (left, left_notice) in enumerate(candidates):
            for right, right_notice in candidates[index + 1 :]:
                key = tuple(sorted((left.item_id, right.item_id)))
                if key in seen:
                    continue
                seen.add(key)
                yield left, left_notice, right, right_notice

    _, merged = _judge_pairs(session, pairs(), router=_MergeRouter())
    return merged


def run_dedupe_backfill(
    session: Session, *, chunk: int = 1000, max_block: int = 400
) -> dict[str, int]:
    """쌓인 전체 공지에 중복 판정을 한 번 적용한다(9.1절).

    유지 수집의 run_dedupe_pass 는 가장 최근에 본 몇백 건만 비교한다. 초기 수집이
    과거 글 수천 건을 한 회차에 몰아 넣으면 대부분은 그 창에 한 번도 들어오지 못하고,
    그대로 두면 유지 수집이 아무리 돌아도 영원히 판정을 받지 못한다. 이 작업이 그 몫이다.

    전체 짝짓기는 13,700건 기준 9,400만 번 비교라 쓸 수 없다. 자동 병합의 필요 조건은
    본문 지문 일치(compare 의 body_equal)이므로 지문으로 먼저 묶고 같은 지문 안에서만
    비교한다. 지문이 다른 짝은 compare 가 절대 merge 를 내지 않으므로 병합 결과는
    전체 비교와 같다.

    지문이 없는 글(본문이 없거나 서식뿐인 글)은 자동 병합 대상이 아니지만 그렇다고
    아무 판정도 못 받으면 곤란하다. 공개 공지 13,707건 중 절반 가까이가 포스터 그림
    한 장뿐이라 지문 묶음에 아예 들어오지 못한다. 그래서 포스터 묶음(images.poster_keys)
    으로도 한 번 더 묶어 비교한다. 이 묶음에서 나오는 판정은 review 가 최대다.
    compare 가 merge 를 내려면 여전히 본문 지문이 같아야 하고, 그런 짝은 이미 지문
    묶음에 들어 있으므로 **이 추가 묶음이 병합을 새로 만들지는 않는다.**
    """
    blocks: dict[str, list[str]] = {}
    poster_blocks: dict[str, list[str]] = {}
    scanned = 0
    for item_id, body_text, body_html, canonical_url in repo.iter_item_bodies_for_dedupe(
        session, chunk=chunk
    ):
        scanned += 1
        fingerprint = dedupe_rules.body_fingerprint(body_text)
        if fingerprint is not None:
            blocks.setdefault(fingerprint, []).append(item_id)
        for key in poster_keys(body_html, base_url=canonical_url):
            poster_blocks.setdefault(key, []).append(item_id)

    router = _MergeRouter()
    recorded = merged = compared = blocked = 0
    # 지문 묶음을 먼저 돌린다. 병합은 여기서만 나오고, 포스터 묶음은 그 결과 위에서
    # 남은 짝만 검토로 올린다. 순서를 바꾸면 이미 합쳐진 짝을 다시 검토로 쌓는다.
    seen_pairs: set[tuple[str, str]] = set()
    for block_key, item_ids in list(blocks.items()) + list(poster_blocks.items()):
        if len(item_ids) < 2:
            continue
        if len(item_ids) > max_block:
            # 같은 서식을 그대로 쓰는 게시판이 있으면 한 묶음이 지나치게 커질 수 있다.
            # 그런 묶음은 자동으로 다루지 않고 운영이 따로 본다.
            blocked += 1
            log.warning("중복 묶음이 너무 큽니다(%d건) 열쇠=%s", len(item_ids), block_key[:24])
            continue
        rows = repo.items_for_dedupe(session, item_ids)
        candidates = [(_to_candidate(item, revision), notice) for item, revision, notice in rows]

        def pairs(candidates=candidates):
            # 한 원본이 지문 묶음과 포스터 묶음 여러 곳에 동시에 들어간다. 같은 짝을
            # 두 번 판정하면 결과는 같지만 헛일이므로 한 번만 본다.
            nonlocal compared
            for index, (left, left_notice) in enumerate(candidates):
                for right, right_notice in candidates[index + 1 :]:
                    key = (left.item_id, right.item_id) if left.item_id < right.item_id else (
                        right.item_id, left.item_id
                    )
                    if key in seen_pairs:
                        continue
                    seen_pairs.add(key)
                    compared += 1
                    yield left, left_notice, right, right_notice

        block_recorded, block_merged = _judge_pairs(session, pairs(), router=router)
        recorded += block_recorded
        merged += block_merged
    return {
        "scanned": scanned,
        "blocks": sum(1 for ids_ in blocks.values() if len(ids_) > 1),
        "poster_blocks": sum(1 for ids_ in poster_blocks.values() if len(ids_) > 1),
        "oversized_blocks": blocked,
        "compared": compared,
        "recorded": recorded,
        "merged": merged,
    }


@dataclass(frozen=True)
class AudienceChange:
    """대상이 달라지는 공지 한 건. 표본 확인과 되돌리기에 필요한 값만 담는다."""

    notice_id: str
    title: str
    source_id: str
    before: tuple[str, ...]
    after: tuple[str, ...]
    transition: str


def _audience_shape(keys: tuple[str, ...]) -> str:
    """대상 열쇠 묶음을 종류 표기로 줄인다. 'campus+organization' 같은 모양."""
    kinds = set()
    for key in keys:
        if key.startswith("campus:"):
            kinds.add("campus")
        elif key.startswith("org:"):
            kinds.add("organization")
        else:
            kinds.add(key)
    return "+".join(sorted(kinds)) or "none"


def run_audience_backfill(
    session: Session,
    *,
    chunk: int = 1000,
    apply: bool = False,
    batch_id: str | None = None,
    reason: str | None = None,
    actor: str = "ops",
    sample_limit: int = 0,
    now: datetime | None = None,
) -> dict:
    """쌓인 공지의 대상 범위를 저장된 값으로 다시 계산한다(8.2절).

    대상 판정 audiences.decide 의 입력은 제목·본문·출처 기본 대상 셋뿐이고, 셋 다
    이미 데이터베이스에 있다. 그래서 원문을 다시 받지 않고 다시 계산할 수 있다.
    2026-09-07 게시물이 캠퍼스를 언급하면 출처 조직을 통째로 버리던 규칙을 고쳤는데
    (조직은 지키고 캠퍼스를 더한다), 이미 쌓인 공지는 상세 재확인이 돌아올 때까지
    최대 14일 동안 잘못된 채로 남는다. 이 작업이 그 기다림을 없앤다.

    apply=False 면 세션을 전혀 건드리지 않고 세기만 한다. 실제 적용은 공지마다
    audit_logs 에 이전·이후 대상을 남기므로 batch_id 하나로 되돌릴 수 있다.
    """
    now = now or datetime.now(UTC)
    campus_map = repo.campus_lookup(session)
    defaults_map = repo.all_source_audiences(session)

    scanned = changed = unchanged = 0
    transitions: dict[str, int] = {}
    samples: list[AudienceChange] = []

    for rows in repo.iter_notices_for_audience_recompute(session, chunk=chunk):
        current = repo.notice_audience_keys(session, [row[0] for row in rows])
        for notice_id, status, source_id, title, body_text in rows:
            scanned += 1
            decision = audience_rules.decide(
                title,
                body_text,
                source_defaults=defaults_map.get(source_id, ()),
                campus_lookup=campus_map,
            )
            after = tuple(sorted({target.key() for target in decision.targets}))
            before = tuple(sorted(current.get(notice_id, set())))
            if before == after:
                unchanged += 1
                continue
            changed += 1
            transition = f"{_audience_shape(before)} -> {_audience_shape(after)}"
            transitions[transition] = transitions.get(transition, 0) + 1
            if len(samples) < sample_limit:
                samples.append(
                    AudienceChange(
                        notice_id=notice_id,
                        title=title,
                        source_id=source_id,
                        before=before,
                        after=after,
                        transition=transition,
                    )
                )
            if not apply:
                continue
            repo.replace_notice_audiences(session, notice_id, decision)
            notice = session.get(m.Notice, notice_id)
            if notice is not None:
                notice.audience_note = decision.note
                notice.updated_at = now
            session.add(
                m.AuditLog(
                    id=ids._digest("reaudience", notice_id, batch_id or now.isoformat()),
                    actor=actor,
                    action="notice.reaudience",
                    target_kind="notice",
                    target_id=notice_id,
                    before={"audiences": list(before)},
                    after={"audiences": list(after), "status": status},
                    reason=reason,
                    run_id=batch_id,
                )
            )

    return {
        "scanned": scanned,
        "changed": changed,
        "unchanged": unchanged,
        "transitions": dict(sorted(transitions.items(), key=lambda kv: -kv[1])),
        "applied": bool(apply),
        "batch": batch_id,
        "samples": [
            {
                "notice_id": s.notice_id,
                "title": s.title[:80],
                "before": list(s.before),
                "after": list(s.after),
                "transition": s.transition,
            }
            for s in samples
        ],
    }


def revert_audience_backfill(
    session: Session, *, batch_id: str, actor: str = "ops", apply: bool = False
) -> dict:
    """대상 다시 계산 한 묶음을 audit_logs 의 이전 값으로 되돌린다.

    되돌리기는 규칙을 다시 돌리지 않는다. 남겨 둔 이전 대상 열쇠를 그대로 다시 쓴다.
    규칙으로 되돌리면 그 사이에 규칙이 또 바뀐 경우 원래 값으로 돌아가지 않는다.
    """
    rows = list(
        session.execute(
            select(m.AuditLog).where(
                m.AuditLog.action == "notice.reaudience", m.AuditLog.run_id == batch_id
            )
        ).scalars()
    )
    restored = missing = 0
    for entry in rows:
        keys = tuple((entry.before or {}).get("audiences") or ())
        if entry.target_id is None or not keys:
            missing += 1
            continue
        targets = [_target_from_key(key) for key in keys]
        if any(target is None for target in targets):
            missing += 1
            continue
        restored += 1
        if not apply:
            continue
        repo.replace_notice_audiences(
            session,
            entry.target_id,
            audience_rules.AudienceDecision(
                targets=tuple(t for t in targets if t is not None), note=None
            ),
        )
    return {"batch": batch_id, "entries": len(rows), "restored": restored, "unusable": missing}


def _target_from_key(key: str) -> audience_rules.AudienceTarget | None:
    if key == "university":
        return audience_rules.AudienceTarget("university", None, "대학 전체")
    if key == "undetermined":
        return audience_rules.UNDETERMINED
    if key.startswith("campus:"):
        return audience_rules.AudienceTarget("campus", key.split(":", 1)[1], "캠퍼스")
    if key.startswith("org:"):
        return audience_rules.AudienceTarget("organization", key.split(":", 1)[1], "조직")
    return None


@dataclass(frozen=True)
class CategoryChange:
    """주제가 달라지는 공지 한 건. 표본 확인과 되돌리기에 필요한 값만 담는다."""

    notice_id: str
    title: str
    source_id: str
    before: str
    after: str
    rule_name: str
    evidence: str | None


def run_category_backfill(
    session: Session,
    *,
    chunk: int = 500,
    apply: bool = False,
    batch_id: str | None = None,
    reason: str | None = None,
    actor: str = "ops",
    sample_limit: int = 0,
    only_codes: frozenset[str] | None = None,
    now: datetime | None = None,
) -> dict:
    """쌓인 공지의 주제를 저장된 제목·본문·게시판 분류로 다시 계산한다(8.2절).

    분류는 새 리비전이 생길 때 한 번만 계산되어 저장되므로 규칙을 고쳐도 이미 쌓인
    공지는 그대로다. 2026-09-09 장학 규칙을 고치면서(categories/2) 이 명령을 만들었다.
    입력 셋이 모두 리비전에 있으므로 원문을 다시 받지 않는다.

    only_codes 를 주면 "이전 대표 주제 또는 새 대표 주제가 그 안에 드는" 공지만 바꾼다.
    장학만 먼저 고칠 때처럼 한 탭의 드나듦만 손대고 나머지는 그대로 두려는 용도다.
    보조 주제만 달라지는 공지는 세지도 바꾸지도 않는다. 탭은 대표 주제로만 거른다.

    apply=False 면 세션을 건드리지 않고 세기만 한다. 실제 적용은 공지마다 audit_logs 에
    이전·이후 주제를 남기고 갱신 시각을 올린다. 증분 내보내기는 갱신 시각으로 바뀐
    공지를 고르므로, 이걸 올리지 않으면 사이트에 반영되지 않는다.
    """
    now = now or datetime.now(UTC)
    scanned = changed = unchanged = skipped = 0
    transitions: dict[str, int] = {}
    samples: list[CategoryChange] = []

    # 덩어리마다 한 번만 flush 한다. 공지마다 autoflush 가 돌면 GitHub 러너(미국)에서 서울
    # DB 로 공지 하나에 서너 번씩 왕복해 6천 건에 90분이 넘게 걸렸다(2026-09-09).
    for rows in repo.iter_notices_for_category_recompute(session, chunk=chunk):
        current = repo.notice_category_codes(session, [row[0] for row in rows])
        notices = (
            {n.id: n for n in session.execute(select(m.Notice).where(m.Notice.id.in_([r[0] for r in rows]))).scalars()}
            if apply
            else {}
        )
        session.autoflush = False
        for notice_id, status, source_id, title, body_text, board_category in rows:
            scanned += 1
            decision = category_rules.classify(title, body_text, board_category=board_category)
            before, _ = current.get(notice_id, ("other", ()))
            after = decision.primary
            if before == after:
                unchanged += 1
                continue
            if only_codes is not None and before not in only_codes and after not in only_codes:
                skipped += 1
                continue
            changed += 1
            transition = f"{before} -> {after}"
            transitions[transition] = transitions.get(transition, 0) + 1
            if len(samples) < sample_limit:
                samples.append(
                    CategoryChange(
                        notice_id=notice_id,
                        title=title,
                        source_id=source_id,
                        before=before,
                        after=after,
                        rule_name=decision.rule_name,
                        evidence=decision.evidence,
                    )
                )
            if not apply:
                continue
            repo.replace_notice_categories(session, notice_id, decision)
            notice = notices.get(notice_id)
            if notice is not None:
                notice.updated_at = now
                audience_version = (notice.derived_version or "|").split("|", 1)[-1]
                notice.derived_version = f"{decision.rule_version}|{audience_version}"
            session.add(
                m.AuditLog(
                    id=ids._digest("reclassify", notice_id, batch_id or now.isoformat()),
                    actor=actor,
                    action="notice.reclassify",
                    target_kind="notice",
                    target_id=notice_id,
                    before={"primary": before},
                    after={
                        "primary": after,
                        "secondary": list(decision.secondary),
                        "rule": decision.rule_name,
                        "evidence": decision.evidence,
                        "status": status,
                    },
                    reason=reason,
                    run_id=batch_id,
                )
            )
        session.autoflush = True
        if apply:
            session.flush()

    return {
        "scanned": scanned,
        "changed": changed,
        "unchanged": unchanged,
        "skipped_outside_scope": skipped,
        "transitions": dict(sorted(transitions.items(), key=lambda kv: -kv[1])),
        "applied": bool(apply),
        "batch": batch_id,
        "samples": [
            {
                "notice_id": s.notice_id,
                "title": s.title[:80],
                "before": s.before,
                "after": s.after,
                "rule": s.rule_name,
                "evidence": s.evidence,
            }
            for s in samples
        ],
    }


def revert_category_backfill(
    session: Session, *, batch_id: str, actor: str = "ops", apply: bool = False
) -> dict:
    """주제 다시 계산 한 묶음을 audit_logs 의 이전 대표 주제로 되돌린다.

    이전 보조 주제는 남기지 않았으므로 대표만 돌아간다. 되돌린 행의 규칙명은
    "revert" 로 남겨 어디서 왔는지 알 수 있게 한다.
    """
    rows = list(
        session.execute(
            select(m.AuditLog).where(
                m.AuditLog.action == "notice.reclassify", m.AuditLog.run_id == batch_id
            )
        ).scalars()
    )
    restored = missing = 0
    now = datetime.now(UTC)
    for entry in rows:
        code = (entry.before or {}).get("primary")
        if entry.target_id is None or not code or code not in category_rules.CATEGORY_LABELS:
            missing += 1
            continue
        restored += 1
        if not apply:
            continue
        repo.replace_notice_categories(
            session,
            entry.target_id,
            category_rules.CategoryDecision(primary=code, rule_name="revert", all_codes=(code,)),
        )
        notice = session.get(m.Notice, entry.target_id)
        if notice is not None:
            notice.updated_at = now
    return {"batch": batch_id, "entries": len(rows), "restored": restored, "unusable": missing}


class SharedFetcherProxy:
    """출처 스레드의 요청을 단일 통신 루프로 전달한다. 호스트 제한을 공유한다.

    DB 세션과 ORM 객체는 작업 스레드 밖으로 보내지 않고, 통신 응답만 전달한다.
    """

    def __init__(self, fetcher: Fetcher, loop: asyncio.AbstractEventLoop):
        self.fetcher, self.loop = fetcher, loop

    async def get(self, *args, **kwargs):
        future = asyncio.run_coroutine_threadsafe(self.fetcher.get(*args, **kwargs), self.loop)
        return await asyncio.wrap_future(future)

    async def post_form(self, *args, **kwargs):
        future = asyncio.run_coroutine_threadsafe(self.fetcher.post_form(*args, **kwargs), self.loop)
        return await asyncio.wrap_future(future)


@dataclass(frozen=True)
class SourceJob:
    """세션과 ORM 객체 없이 스레드로 전달하는 출처 작업 명세."""

    source_id: str
    config: dict
    interval_minutes: int
    audience_defaults: tuple


async def run_collection(
    cfg: Settings | None = None, *, source_keys: list[str] | None = None,
    initial_mode: bool = False, publish: bool = True, dedupe: bool = True,
) -> RunOutcome:
    cfg = cfg or default_settings
    budget = TimeBudget(cfg.run_budget_seconds)
    store = build_store(cfg)
    commit = os.environ.get("GITHUB_SHA") or os.environ.get("KHU_CODE_COMMIT")
    # 회차가 데이터베이스에서 읽은 양을 잰다. 2026-09-09 Supabase 무료 egress 한도를
    # 넘긴 뒤로, 어느 단계가 얼마나 읽는지 모르면 줄일 수도 없다.
    # 인터페이스는 app/storage/db.py 를 본다(docs/공개파일_증분화_2026-09-09.md).
    enable_read_meter()
    reads_before = read_stats()

    with session_scope(cfg) as session:
        assert_schema_ready(session)
        run = repo.start_run(
            session,
            code_commit=commit,
            stale_after_minutes=cfg.stale_run_after_minutes,
        )
        run_id = run.id
        campus_map = repo.campus_lookup(session)
        due_list = repo.load_due_sources(
            session, initial_mode=initial_mode, initial_window_start=cfg.initial_window_start,
        )
        if source_keys:
            wanted = set(source_keys)
            due_list = [d for d in due_list if d.source.id in wanted or d.source.name in wanted]

    outcome = RunOutcome(run_id=run_id, result="success")
    outcomes: list[SourceOutcome] = []

    # 출처를 여러 개 동시에 처리한다. 예전에는 하나를 끝내야 다음으로 넘어가서
    # 속도 제한을 아무리 풀어도 요청이 항상 한 개씩만 날아갔다.
    # 원문 서버에 대한 예의는 HostLimiter 가 그대로 지킨다(7.3절).
    pending = [SourceJob(d.source.id, d.config, d.interval_minutes, d.audience_defaults) for d in due_list]
    tally = asyncio.Lock()

    def collect_job(due_ref, fetcher) -> SourceOutcome | None:
        # 페이지 진행과 항목 변경을 나누어 확정하고, 원문 대기 전에 연결을 반환한다.
        with session_scope(cfg) as session:
            due = _reload_due(session, due_ref)
            if due is None:
                return None
            run = session.get(_run_model(), run_id)
            try:
                source_outcome = asyncio.run(collect_source(
                    session, fetcher, store, due, cfg=cfg,
                    budget=TimeBudget(min(cfg.source_budget_seconds, budget.remaining())),
                    campus_map=campus_map, persist_progress=True, initial_mode=initial_mode,
                ))
            except Exception as exc:
                # DB 오류가 난 세션은 반드시 되돌린 뒤 새 실패 기록을 쓴다.
                session.rollback()
                due = _reload_due(session, due_ref)
                repo.mark_source_failure(session, due.health, kind="unexpected", message=str(exc))
                source_outcome = SourceOutcome(
                    source_id=due.source.id, name=due.source.name, ok=False,
                    error_kind="unexpected", error_message=str(exc), partial=True,
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
                detail_failures=source_outcome.detail_failures,
                scan_stop_reason=source_outcome.scan_stop_reason,
                backfill_complete=source_outcome.backfill_complete,
                missing_check_performed=source_outcome.missing_check_performed,
            )

        return source_outcome

    # 남은 예산이 이 값보다 적으면 새 출처를 시작하지 않는다. 몇 초짜리 조각으로
    # 착수하면 목록 한 번 받고 끝나 저장은 0건인데 last_attempt_at 만 갱신되고,
    # 대기열이 last_attempt_at 오름차순이므로 그 출처는 다음 회차에서도 같은 꼬리
    # 자리에 다시 놓인다. 착수하지 않으면 위치를 지켜 다음 회차가 먼저 본다.
    min_slice = max(1.0, min(float(cfg.source_min_budget_seconds), cfg.run_budget_seconds / 4))

    async def worker(fetcher) -> None:
        while True:
            async with tally:
                if not pending:
                    return
                if budget.remaining() < min_slice:
                    outcome.skipped += len(pending)
                    pending.clear()
                    return
                due_ref = pending.pop(0)

            source_outcome = await asyncio.get_running_loop().run_in_executor(
                source_pool, collect_job, due_ref, fetcher,
            )
            if source_outcome is None:
                continue
            async with tally:
                outcomes.append(source_outcome)
                outcome.attempted += 1
                outcome.succeeded += int(source_outcome.ok)
                outcome.new_items += source_outcome.new_items
                outcome.updated_items += source_outcome.updated_items

    async with Fetcher(cfg) as fetcher:
        workers = max(1, min(cfg.source_concurrency, len(pending) or 1))
        proxy = SharedFetcherProxy(fetcher, asyncio.get_running_loop())
        # 출처 스레드는 DNS 등에 쓰는 기본 executor를 점유하면 안 된다.
        # 출처가 HTTP 응답을 기다리고 DNS가 출처 종료를 기다리는 교착을 방지한다.
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="khu-source") as source_pool:
            results = await asyncio.gather(*(worker(proxy) for _ in range(workers)), return_exceptions=True)
        # 다른 스레드가 공유 HTTP 클라이언트를 쓰는 동안 먼저 닫지 않는다.
        for failure in results:
            if isinstance(failure, BaseException):
                raise failure

    if dedupe:
        with session_scope(cfg) as session:
            merged = run_dedupe_pass(session)
            if merged:
                log.info("중복 자동 병합 %d건", merged)

    export_result = export_static(cfg, run_id=run_id, store=store) if publish else None
    run_reads = read_stats().delta(reads_before)

    with session_scope(cfg) as session:
        run = session.get(_run_model(), run_id)
        failed = [o for o in outcomes if not o.ok]
        partial = [o for o in outcomes if o.partial or not o.detail_complete]
        result = "success"
        if outcome.attempted and len(failed) == outcome.attempted:
            result = "failed"
        elif failed or outcome.skipped or partial:
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
            revision=export_result.revision if export_result else None,
            note=(
                (
                    f"시간 {budget.elapsed:.0f}초, 요청 {fetcher.requests_made}회"
                    if not failed
                    else f"실패 {len(failed)}건: " + ", ".join(f"{o.name}({o.error_kind})" for o in failed[:5])
                )
                + _reads_note(run_reads, export_result)
            ),
        )

    # 내보내기 때 만들어진 상태는 실행 종료 전 스냅샷일 수 있으므로,
    # 마지막 정상 개정은 유지한 채 공개 상태만 최종 결과로 갱신한다.
    if export_result:
        refresh_public_status(cfg, revision=export_result.revision, store=store)

    outcome.result = result
    outcome.outcomes = outcomes
    outcome.revision = export_result.revision if export_result else None
    outcome.export = export_result
    _send_heartbeat(cfg, outcome)
    return outcome


def _reads_note(run_reads: ReadStats, export_result: ExportResult | None) -> str:
    """실행 기록에 붙일 읽기량 한 줄.

    회차 전체와 내보내기 단계를 나눠 적는다. 두 값의 차가 곧 수집 단계가 읽은 양이다.
    증분 전후를 견주려면 이 한 줄이면 된다.
    """
    total = run_reads.bytes / 1024 / 1024
    if export_result is None:
        return f", DB 읽기 {total:.1f}MB"
    export_mb = export_result.db_read_bytes / 1024 / 1024
    return (
        f", DB 읽기 {total:.1f}MB(공개 {export_mb:.1f}MB, "
        f"방식 {export_result.mode}, 물려받은 상세 {export_result.details_carried}개)"
    )


def _run_model():
    from app.storage.models import Run

    return Run


def _reload_due(session: Session, due: SourceJob) -> repo.DueSource | None:
    """새 트랜잭션에서 같은 출처를 다시 읽는다."""
    from app.storage import models as m

    source = session.get(m.Source, due.source_id)
    if source is None:
        return None
    health = session.get(m.SourceHealth, due.source_id)
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
    parser.add_argument("--initial", action="store_true", help="초기 미완료 출처는 정상 수집 간격 없이 재개")
    parser.add_argument("--no-publish", action="store_true", help="수집 결과만 저장하고 공개 생성 생략")
    parser.add_argument("--no-dedupe", action="store_true", help="전체 중복 비교를 별도 공개 작업으로 미룸")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    outcome = asyncio.run(run_collection(
        source_keys=args.sources, initial_mode=args.initial,
        publish=not args.no_publish, dedupe=not args.no_dedupe,
    ))
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
