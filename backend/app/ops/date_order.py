"""목록 전체 원문을 검증한 출처에만 날짜 경계 설정을 제안·적용한다.

audit은 운영을 변경하지 않는다. apply는 원문 검증 결과와 현재 설정을 대조해
설정 버전을 추가하고 페이지 크기/정렬 변경으로 무효해진 진행 위치를 초기화한다.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from lxml import html
from sqlalchemy import select

from app.config import settings
from app.domain.dates import parse_published
from app.ingestion import get_adapter
from app.ingestion.http import Fetcher
from app.storage import models as m
from app.storage.db import session_scope


def digest(config: dict) -> str:
    return hashlib.sha256(json.dumps(config, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


async def inspect_source(fetcher, source: dict, *, max_pages: int, evidence_dir: Path) -> dict:
    adapter = get_adapter(source["adapter"])
    original = source["config"]
    config = dict(original)
    result = {
        "source_id": source["source_id"], "adapter": source["adapter"],
        "config_digest": digest(original), "verified_at": datetime.now(UTC).isoformat(),
        "verified": False, "pages": [], "reason": None,
    }
    root = evidence_dir / source["source_id"]
    root.mkdir(parents=True, exist_ok=True)
    try:
        first = await fetcher.get(adapter.list_url(config, 1), allowed_hosts=adapter.allowed_hosts(config))
        (root / "original-first.html").write_text(first.text, encoding="utf-8")
        if source["adapter"] == "khu_board":
            doc = html.fromstring(first.text)
            offered = [int(v) for v in doc.xpath("//select[@name='userDisplayCount']/option/@value") if v.isdigit()]
            supported = [v for v in offered if v in (10, 20, 30, 40, 50)]
            if supported:
                config["user_display_count"] = max(supported)
        elif source["adapter"] == "gnuboard":
            # 공식 gnuboard5 bbs/list.php의 허용 정렬 필드. 실제 응답은 아래 전체 검증.
            config["date_sort"] = "wr_datetime_desc"
        else:
            result["reason"] = "unsupported_adapter"
            return result

        previous = None
        normal_ids = set()
        signatures = set()
        violations = []
        ended = False
        for number in range(1, max_pages + 1):
            url = adapter.list_url(config, number)
            response = await fetcher.get(url, allowed_hosts=adapter.allowed_hosts(config))
            path = root / f"page-{number}.html"
            path.write_text(response.text, encoding="utf-8")
            page = adapter.parse_list(response.text, config, number)
            normal = [item for item in page.items if not item.is_pinned]
            signature = tuple(item.external_id for item in normal)
            if signature and signature in signatures:
                result["reason"] = "repeated_page"
                break
            signatures.add(signature)
            for item in normal:
                stamp = parse_published(item.published_raw).date
                if stamp is None:
                    violations.append({"page": number, "id": item.external_id, "kind": "unknown_date"})
                elif previous is not None and previous < stamp:
                    violations.append({"page": number, "id": item.external_id, "kind": "date_inversion"})
                if stamp is not None:
                    previous = stamp
                if item.external_id in normal_ids:
                    violations.append({"page": number, "id": item.external_id, "kind": "overlapping_normal_item"})
                normal_ids.add(item.external_id)
            result["pages"].append({
                "page": number, "url": url, "raw_sha256": hashlib.sha256(response.text.encode()).hexdigest(),
                "items": len(page.items), "normal": len(normal),
                "dates": [item.published_raw for item in normal],
                "pinned_ids": [item.external_id for item in page.items if item.is_pinned],
            })
            if not page.has_next:
                ended = True
                break
        result["violations"] = violations
        result["normal_items"] = len(normal_ids)
        result["verified"] = bool(ended and normal_ids and not violations)
        result["reason"] = result["reason"] or (
            "full_listing_monotonic" if result["verified"] else "order_not_proven" if ended else "page_cap"
        )
        # 표시개수 확대도 전체 페이지 검증에 성공한 경우에만 적용한다.
        if result["verified"]:
            config["date_ordered"] = True
            config["date_order_evidence"] = {
                "method": "full_listing_monotonic", "verified_at": result["verified_at"],
                "valid_until": (datetime.now(UTC) + timedelta(hours=12)).isoformat(),
                "pages": len(result["pages"]), "items": len(normal_ids),
                "evidence_digest": digest({"pages": result["pages"]}),
            }
            result["proposed_config"] = config
    except Exception as exc:
        result["reason"] = f"{type(exc).__name__}: {exc}"
    return result


async def audit(args) -> list[dict]:
    if args.bootstrap:
        from app.ingestion.registry import load_registry
        sources = [dict(source_id=s.key, adapter=s.adapter, config=s.config) for s in load_registry().sources]
    else:
        with session_scope(settings) as session:
            rows = session.execute(select(m.Source, m.SourceConfigVersion).join(
                m.SourceConfigVersion, (m.SourceConfigVersion.source_id == m.Source.id)
                & m.SourceConfigVersion.is_active.is_(True),
            ).where(m.Source.status.in_(("active", "delayed")))).all()
            sources = [dict(source_id=s.id, adapter=s.adapter, config=dict(c.config)) for s, c in rows]
    if args.sources:
        wanted = set(args.sources)
        sources = [s for s in sources if s["source_id"] in wanted]
    gate = asyncio.Semaphore(max(1, args.concurrency))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    evidence = output.with_suffix("")
    results = []
    async with Fetcher(settings) as fetcher:
        async def work(source):
            async with gate:
                result = await inspect_source(fetcher, source, max_pages=args.max_pages, evidence_dir=evidence)
                results.append(result)
                output.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
                print(json.dumps({k: result[k] for k in ("source_id", "verified", "reason")}, ensure_ascii=False), flush=True)
        await asyncio.gather(*(work(source) for source in sources))
    return results


def apply_plan(path: Path) -> int:
    proposals = json.loads(path.read_text(encoding="utf-8"))
    changed = 0
    with session_scope(settings) as session:
        # 구형 CMS 페이지 판독은 숫자 묶음 끝에서 다음 링크를 놓칠 수 있었다.
        # 이번 날짜검증 미통과 출처에도 과거 오완료 표시가 남지 않도록 재개한다.
        suspect_health = session.scalars(select(m.SourceHealth).join(
            m.Source, m.Source.id == m.SourceHealth.source_id,
        ).where(
            m.Source.adapter == "khu_board", m.SourceHealth.backfill_complete.is_(True),
            m.SourceHealth.backfill_last_stop_reason == "end_of_board",
            m.SourceHealth.backfill_cursor_page % 10 == 0,
        )).all()
        for health in suspect_health:
            health.backfill_complete = False
            health.backfill_boundary_reached = False
            health.backfill_cursor_page = 1
            health.backfill_cursor_external_id = None
            health.backfill_status = "in_progress"
        print(f"이전 페이지 묶음 종료 재검증 {len(suspect_health)}개")
        for proposal in proposals:
            if not proposal.get("verified"):
                continue
            evidence = proposal.get("proposed_config", {}).get("date_order_evidence", {})
            if (evidence.get("method") != "full_listing_monotonic"
                    or datetime.fromisoformat(evidence.get("valid_until", "1970-01-01T00:00:00+00:00")) <= datetime.now(UTC)
                    or not proposal.get("pages") or proposal.get("violations")
                    or evidence.get("evidence_digest") != digest({"pages": proposal["pages"]})):
                raise ValueError(f"날짜 검증 명세가 유효하지 않습니다: {proposal['source_id']}")
            row = session.scalar(select(m.SourceConfigVersion).where(
                m.SourceConfigVersion.source_id == proposal["source_id"], m.SourceConfigVersion.is_active.is_(True),
            ).with_for_update())
            if row is None or digest(row.config) != proposal["config_digest"]:
                raise ValueError(f"검증 이후 설정이 바뀌었거나 출처가 없습니다: {proposal['source_id']}")
            allowed = {"date_sort", "user_display_count", "date_ordered", "date_order_evidence"}
            proposed = proposal["proposed_config"]
            if (proposed.get("date_ordered") is not True
                    or {k: v for k, v in proposed.items() if k not in allowed}
                    != {k: v for k, v in row.config.items() if k not in allowed}):
                raise ValueError(f"날짜 검증 외 설정 변경이 포함되어 있습니다: {proposal['source_id']}")
            row.is_active = False
            session.flush()
            session.add(m.SourceConfigVersion(
                id=f"cfg-{digest({'source': row.source_id, 'config': proposal['proposed_config']})[:24]}",
                source_id=row.source_id, version=row.version + 1, is_active=True,
                interval_minutes=row.interval_minutes, config=proposal["proposed_config"],
            ))
            health = session.get(m.SourceHealth, row.source_id)
            if health:
                health.backfill_complete = False
                health.backfill_boundary_reached = False
                health.backfill_cursor_page = 1
                health.backfill_cursor_external_id = None
                health.backfill_status = "in_progress"
            changed += 1
    return changed


def main(argv=None):
    parser = argparse.ArgumentParser(description="원문 목록 전체 날짜 정렬 검증 및 설정 제안")
    parser.add_argument("mode", choices=("audit", "apply"))
    parser.add_argument("--output", default=".localstore/date-order-audit.json")
    parser.add_argument("--sources", nargs="*")
    parser.add_argument("--bootstrap", action="store_true")
    parser.add_argument("--max-pages", type=int, default=300)
    parser.add_argument("--concurrency", type=int, default=12)
    args = parser.parse_args(argv)
    if args.mode == "audit":
        asyncio.run(audit(args))
    else:
        print(f"설정 변경 {apply_plan(Path(args.output))}개")


if __name__ == "__main__":
    main()
