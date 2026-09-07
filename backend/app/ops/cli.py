"""운영 명령 도구(14절).

1차에는 운영자 화면과 관리 연동 경로를 만들지 않는다. 운영 동작은 이 도구로 한다.
운영자 PC 에서 직접 실행하거나 GitHub Actions 의 수동 실행 입력으로 부른다.
모든 변경 명령은 audit_logs 에 사유·실행자를 남긴다.

    python -m app.ops.cli sources sync          등록부의 새 출처를 데이터베이스에 넣는다
    python -m app.ops.cli sources list          출처와 상태를 본다
    python -m app.ops.cli sources promote KEY   pending 출처를 active 로 올린다
    python -m app.ops.cli sources pause KEY --reason ...
    python -m app.ops.cli contacts import       정적 연락처 자료를 데이터베이스에 넣는다
    python -m app.ops.cli notice hide ID --reason ...
    python -m app.ops.cli notice dedupe        쌓인 전체 공지에 중복 병합을 한 번 적용한다
    python -m app.ops.cli export                정적 파일만 다시 만든다
    python -m app.ops.cli status                최근 실행과 출처 상태 요약
    python -m app.ops.cli check-sources         출처가 실행 서버에서 열리는지 확인한다
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import func, select

from app.config import REPO_ROOT, settings
from app.domain import ids
from app.export.static import export_static, prune_old_revisions
from app.ingestion import get_adapter
from app.ingestion.http import Fetcher, FetchError
from app.ingestion.registry import UNIVERSITY_CODE, UNIVERSITY_NAME, load_registry
from app.storage import models as m
from app.storage.db import session_scope

CONTACTS_JSON = REPO_ROOT / "data" / "contacts" / "contacts.json"


def _actor() -> str:
    try:
        return f"ops:{getpass.getuser()}"
    except Exception:  # noqa: BLE001
        return "ops:unknown"


def _audit(
    session,
    *,
    action: str,
    target_kind: str,
    target_id: str | None,
    reason: str,
    before=None,
    after=None,
) -> None:
    session.add(
        m.AuditLog(
            id=ids._digest(action, target_id or "", datetime.now(UTC).isoformat()),
            actor=_actor(),
            action=action,
            target_kind=target_kind,
            target_id=target_id,
            before=before,
            after=after,
            reason=reason,
        )
    )


# ------------------------------------------------------------------ 출처 동기화


def sources_sync(args: argparse.Namespace) -> int:
    """등록부의 조직·출처를 데이터베이스에 반영한다.

    새 항목만 추가하고 기존 운영 설정은 덮어쓰지 않는다(3절).
    설정을 바꾸려면 --update-config 를 명시해야 하며 그때 새 설정 버전을 만든다.
    """
    registry = load_registry()
    added_org = added_src = updated_cfg = retired = 0
    linked_org = 0

    with session_scope() as session:
        university_id = ids.university_id(UNIVERSITY_CODE)
        if session.get(m.University, university_id) is None:
            session.add(m.University(id=university_id, code=UNIVERSITY_CODE, name=UNIVERSITY_NAME))
            session.flush()

        campus_ids: dict[str, str] = {}
        for campus in registry.campuses:
            cid = ids.campus_id(UNIVERSITY_CODE, campus.code)
            campus_ids[campus.code] = cid
            if session.get(m.Campus, cid) is None:
                session.add(
                    m.Campus(id=cid, university_id=university_id, code=campus.code, name=campus.name)
                )
        session.flush()

        org_ids: dict[str, str] = {}
        for org in registry.organizations:
            path = registry.org_path(org.key)
            oid = ids.organization_id(UNIVERSITY_CODE, path)
            row = session.get(m.Organization, oid)
            if row is None and org.parent_key:
                # 조직 식별자는 경로의 해시다. 상위를 뒤늦게 채우면 경로가 길어져 식별자가
                # 바뀌고, 그대로 두면 공지가 이미 달린 행을 버리고 빈 행을 새로 만들게 된다.
                # 상위 없이 만들어졌던 예전 식별자로 그 행을 찾아 자리를 지킨 채 상위만 채운다.
                flat = ids.organization_id(UNIVERSITY_CODE, (UNIVERSITY_NAME, org.name))
                existing = session.get(m.Organization, flat)
                if existing is not None:
                    row, oid = existing, flat
            org_ids[org.key] = oid
            if row is None:
                session.add(
                    m.Organization(
                        id=oid,
                        university_id=university_id,
                        parent_id=org_ids.get(org.parent_key) if org.parent_key else None,
                        org_type=org.org_type,
                        name=org.name,
                        short_name=org.short_name,
                        aliases=list(org.aliases),
                        homepage_url=org.homepage_url,
                    )
                )
                added_org += 1
            for code in org.campus_codes:
                link = session.get(m.OrganizationCampus, {"organization_id": oid, "campus_id": campus_ids[code]})
                if link is None:
                    session.add(m.OrganizationCampus(organization_id=oid, campus_id=campus_ids[code]))
        session.flush()

        # 상위 연결은 모든 조직의 식별자를 안 뒤에 한다. 한 번에 하면 등록부에서 자식이
        # 부모보다 먼저 나온 경우 부모를 못 찾아 연결이 조용히 빠진다.
        for org in registry.organizations:
            if not org.parent_key:
                continue
            row = session.get(m.Organization, org_ids[org.key])
            parent = org_ids.get(org.parent_key)
            if row is not None and parent and row.parent_id != parent:
                row.parent_id = parent
                linked_org += 1
        session.flush()

        _rebuild_closure(session)

        for spec in registry.sources:
            sid = ids.source_id(spec.adapter, spec.list_url)
            row = session.get(m.Source, sid)
            if row is None:
                session.add(
                    m.Source(
                        id=sid,
                        organization_id=org_ids[spec.organization_key],
                        name=spec.name,
                        medium=spec.medium,
                        content_kind=spec.content_kind,
                        adapter=spec.adapter,
                        list_url=spec.list_url,
                        official_evidence_url=spec.official_evidence_url,
                        status=spec.status,
                    )
                )
                session.flush()
                session.add(
                    m.SourceConfigVersion(
                        id=ids._digest(sid, "1"),
                        source_id=sid,
                        version=1,
                        config=spec.config,
                        interval_minutes=spec.interval_minutes,
                        is_active=True,
                    )
                )
                session.add(m.SourceHealth(source_id=sid))
                for aud in spec.audiences:
                    session.add(
                        m.SourceAudience(
                            id=ids._digest(sid, json.dumps(aud, sort_keys=True)),
                            source_id=sid,
                            audience_type=aud.get("type", "undetermined"),
                            campus_id=campus_ids.get(aud.get("campus", "")),
                            organization_id=org_ids.get(aud.get("organization", "")),
                        )
                    )
                added_src += 1
                _audit(
                    session,
                    action="source.create",
                    target_kind="source",
                    target_id=sid,
                    reason=f"등록부 동기화: {spec.key}",
                    after={"name": spec.name, "list_url": spec.list_url},
                )
            elif args.update_config:
                active = session.execute(
                    select(m.SourceConfigVersion)
                    .where(m.SourceConfigVersion.source_id == sid, m.SourceConfigVersion.is_active.is_(True))
                ).scalar_one_or_none()
                if active is not None and dict(active.config) != spec.config:
                    active.is_active = False
                    session.add(
                        m.SourceConfigVersion(
                            id=ids._digest(sid, str(active.version + 1)),
                            source_id=sid,
                            version=active.version + 1,
                            config=spec.config,
                            interval_minutes=spec.interval_minutes,
                            is_active=True,
                        )
                    )
                    updated_cfg += 1
                    _audit(
                        session,
                        action="source.config_update",
                        target_kind="source",
                        target_id=sid,
                        reason="등록부 동기화(--update-config)",
                        after=spec.config,
                    )

        # 등록부에서 빠진 출처는 남겨 두면 계속 수집된다. 조사에서 공지가 아니라고
        # 판정했거나 사라진 게시판이므로 폐쇄로 돌린다. 지우지는 않는다.
        # 이미 모은 공지와 그 근거를 잃지 않아야 한다(3절).
        if args.retire_missing:
            known = {ids.source_id(spec.adapter, spec.list_url) for spec in registry.sources}
            stale = session.execute(
                select(m.Source).where(
                    m.Source.id.not_in(known), m.Source.status.not_in(("retired",))
                )
            ).scalars().all()
            for row in stale:
                before = row.status
                row.status = "retired"
                retired += 1
                _audit(
                    session,
                    action="source.retire",
                    target_kind="source",
                    target_id=row.id,
                    reason="등록부에서 빠진 출처(--retire-missing)",
                    before={"status": before},
                    after={"status": "retired"},
                )

    print(
        f"조직 추가 {added_org} / 상위 연결 {linked_org} / 출처 추가 {added_src} / 설정 갱신 {updated_cfg}"
        f" / 폐쇄 {retired}"
    )
    if not args.update_config:
        print("기존 출처 설정은 그대로 두었습니다. 바꾸려면 --update-config 를 쓰세요.")
    if not args.retire_missing:
        print("등록부에서 빠진 출처는 그대로 두었습니다. 정리하려면 --retire-missing 을 쓰세요.")
    return 0


def _rebuild_closure(session) -> None:
    """조직 조상·자손 관계를 다시 만든다."""
    session.query(m.OrganizationClosure).delete(synchronize_session=False)
    orgs = {o.id: o for o in session.execute(select(m.Organization)).scalars()}
    for org_id, _org in orgs.items():
        depth = 0
        current: str | None = org_id
        guard = 0
        while current is not None and guard < 12:
            session.add(m.OrganizationClosure(ancestor_id=current, descendant_id=org_id, depth=depth))
            node = orgs.get(current)
            current = node.parent_id if node else None
            depth += 1
            guard += 1


# ------------------------------------------------------------------ 출처 조회·상태


def sources_list(args: argparse.Namespace) -> int:
    with session_scope() as session:
        rows = session.execute(
            select(m.Source, m.SourceHealth, m.Organization)
            .outerjoin(m.SourceHealth, m.SourceHealth.source_id == m.Source.id)
            .join(m.Organization, m.Organization.id == m.Source.organization_id)
            .order_by(m.Source.status, m.Source.name)
        ).all()
        counts: dict[str, int] = {}
        for source, health, _org in rows:
            counts[source.status] = counts.get(source.status, 0) + 1
            if args.status and source.status != args.status:
                continue
            last = health.last_list_success_at if health else None
            fails = health.consecutive_failures if health else 0
            print(
                f"{source.status:8} {source.id}  {source.name[:52]:54} "
                f"성공 {last.isoformat() if last else '없음':26} 실패 {fails}"
            )
        print("\n상태별 합계:", ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    return 0


def sources_promote(args: argparse.Namespace) -> int:
    return _set_source_status(args.key, "active", args.reason or "검증 완료 후 수집 시작")


def sources_pause(args: argparse.Namespace) -> int:
    return _set_source_status(args.key, "paused", args.reason or "운영 판단으로 중지")


def _set_source_status(key: str, status: str, reason: str) -> int:
    with session_scope() as session:
        source = _find_source(session, key)
        if source is None:
            print(f"출처를 찾지 못했습니다: {key}", file=sys.stderr)
            return 1
        before = source.status
        source.status = status
        source.status_message = None if status == "active" else reason
        _audit(
            session,
            action=f"source.{status}",
            target_kind="source",
            target_id=source.id,
            reason=reason,
            after={"before": before, "after": status},
        )
        print(f"{source.name}: {before} -> {status}")
    return 0


def _find_source(session, key: str):
    source = session.get(m.Source, key)
    if source is not None:
        return source
    return session.execute(
        select(m.Source).where(m.Source.name.like(f"%{key}%")).limit(1)
    ).scalar_one_or_none()


def status_cmd(_args: argparse.Namespace) -> int:
    with session_scope() as session:
        run = session.execute(
            select(m.Run).order_by(m.Run.started_at.desc()).limit(1)
        ).scalar_one_or_none()
        if run is None:
            print("실행 기록이 없습니다.")
        else:
            print(
                f"최근 실행 {run.id}: {run.result or '진행 중'} | 시작 {run.started_at} | "
                f"출처 {run.sources_succeeded}/{run.sources_attempted} | 새 글 {run.items_new} | 개정 {run.revision}"
            )
        totals = dict(
            session.execute(select(m.Source.status, func.count(m.Source.id)).group_by(m.Source.status)).all()
        )
        notices = session.execute(
            select(func.count(m.Notice.id)).where(m.Notice.status == "visible")
        ).scalar()
        contacts = session.execute(select(func.count(m.ContactEntry.id))).scalar()
        print("출처:", ", ".join(f"{k}={v}" for k, v in sorted(totals.items())) or "없음")
        print(f"공개 공지 {notices}건 / 연락처 {contacts}건")
    return 0


# ------------------------------------------------------------------ 연락처 가져오기


def contacts_import(args: argparse.Namespace) -> int:
    """data/contacts/contacts.json 을 데이터베이스에 넣는다.

    이 파일은 2026-09-06 공식 원문에서 만든 정적 자료다. 조직은 이름으로 맞추고,
    맞는 조직이 없으면 연락처 전용 조직으로 새로 만든다.
    """
    path = Path(args.path or CONTACTS_JSON)
    if not path.exists():
        print(f"연락처 자료가 없습니다: {path}", file=sys.stderr)
        return 1
    doc = json.loads(path.read_text(encoding="utf-8"))
    entries = doc.get("data", [])

    created = updated = 0
    with session_scope() as session:
        university_id = ids.university_id(UNIVERSITY_CODE)
        if session.get(m.University, university_id) is None:
            session.add(m.University(id=university_id, code=UNIVERSITY_CODE, name=UNIVERSITY_NAME))
            session.flush()

        campus_by_name = {c.name: c.id for c in session.execute(select(m.Campus)).scalars()}
        for code, name in (("seoul", "서울캠퍼스"), ("global", "국제캠퍼스")):
            if name not in campus_by_name:
                cid = ids.campus_id(UNIVERSITY_CODE, code)
                session.add(m.Campus(id=cid, university_id=university_id, code=code, name=name))
                campus_by_name[name] = cid
        session.flush()

        org_by_name = {o.name: o.id for o in session.execute(select(m.Organization)).scalars()}

        for row in entries:
            org_name = (row.get("organization") or {}).get("name") or "미상 기관"
            org_type = ((row.get("organization") or {}).get("type") or {}).get("code") or "office"
            org_id = org_by_name.get(org_name)
            if org_id is None:
                org_id = ids.organization_id(UNIVERSITY_CODE, (UNIVERSITY_NAME, org_name))
                session.add(
                    m.Organization(
                        id=org_id,
                        university_id=university_id,
                        org_type=org_type if org_type in
                        ("university", "campus", "college", "department", "office", "council", "institute")
                        else "office",
                        name=org_name,
                    )
                )
                org_by_name[org_name] = org_id
                session.flush()

            # 자료가 준 안정 식별자를 그대로 쓴다. 조직+업무명으로 만들면
            # 서울·국제의 동명 행정실이 합쳐진다(10절: 값이 같다고 기관을 합치지 않는다).
            contact_id = str(row.get("id") or "").strip() or ids.contact_id(
                org_id,
                row.get("service_name") or "대표 안내",
                *sorted(c.get("name", "") for c in (row.get("campuses") or [])),
            )
            verification = row.get("verification") or {}
            entry = session.get(m.ContactEntry, contact_id)
            if entry is None:
                entry = m.ContactEntry(id=contact_id, organization_id=org_id, service_name=row.get("service_name") or "대표 안내")
                session.add(entry)
                created += 1
            else:
                updated += 1

            entry.location = row.get("location")
            entry.office_hours = row.get("office_hours")
            entry.official_url = row.get("official_url")
            entry.status = verification.get("code") or "verified"
            entry.verified_at = _parse_dt(verification.get("verified_at"))
            entry.note = row.get("note")
            entry.search_text = " ".join(
                filter(None, [org_name, row.get("service_name"), row.get("location")])
            )[:4000]
            session.flush()

            session.query(m.ContactChannel).filter(m.ContactChannel.contact_id == contact_id).delete(
                synchronize_session=False
            )
            for index, channel in enumerate(row.get("channels") or []):
                kind = (channel.get("kind") or {}).get("code") or "phone"
                if kind not in ("phone", "email", "fax", "website"):
                    continue
                session.add(
                    m.ContactChannel(
                        id=ids._digest(contact_id, kind, str(index)),
                        contact_id=contact_id,
                        channel_kind=kind,
                        display_value=str(channel.get("display_value") or "")[:300],
                        value=(str(channel["value"])[:300] if channel.get("value") else None),
                        extension=channel.get("extension"),
                        expanded_values=channel.get("values"),
                        priority=index,
                    )
                )

            session.query(m.ContactCampus).filter(m.ContactCampus.contact_id == contact_id).delete(
                synchronize_session=False
            )
            for campus in row.get("campuses") or []:
                cid = campus_by_name.get(campus.get("name"))
                if cid:
                    session.add(m.ContactCampus(contact_id=contact_id, campus_id=cid))

            for ev in (row.get("evidence") or [])[:20]:
                obs_id = ids._digest(contact_id, ev.get("url", ""), ev.get("field", ""))
                if session.get(m.ContactObservation, obs_id) is None:
                    session.add(
                        m.ContactObservation(
                            id=obs_id,
                            contact_id=contact_id,
                            source_name=ev.get("source_name") or "공식 안내",
                            url=ev.get("url") or "",
                            observed_at=_parse_dt(ev.get("observed_at")) or datetime.now(UTC),
                        )
                    )
                    session.flush()
                    session.add(
                        m.ContactFieldEvidence(
                            id=ids._digest(obs_id, ev.get("field", "")),
                            contact_id=contact_id,
                            observation_id=obs_id,
                            field=ev.get("field") or "unknown",
                        )
                    )

        _audit(
            session,
            action="contacts.import",
            target_kind="contact",
            target_id=None,
            reason=f"정적 연락처 자료 가져오기: {path.name}",
            after={"created": created, "updated": updated},
        )

    print(f"연락처 생성 {created} / 갱신 {updated}")
    return 0


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


# ------------------------------------------------------------------ 공지 운영


def notice_hide(args: argparse.Namespace) -> int:
    with session_scope() as session:
        notice = session.get(m.Notice, args.notice_id)
        if notice is None:
            print(f"공지를 찾지 못했습니다: {args.notice_id}", file=sys.stderr)
            return 1
        notice.status = "hidden"
        notice.updated_at = datetime.now(UTC)
        _audit(
            session,
            action="notice.hide",
            target_kind="notice",
            target_id=notice.id,
            reason=args.reason,
        )
        print(f"숨김 처리: {notice.title[:60]}")
    print("다음 수집 실행 또는 `ops export` 후 정적 파일에 반영됩니다.")
    return 0


def notice_dedupe(args: argparse.Namespace) -> int:
    """초기 수집으로 쌓인 공지에 중복 판정을 한 번에 적용한다.

    유지 수집의 중복 판정은 가장 최근에 본 몇백 건만 본다. 백필로 한 번에 들어온
    과거 공지는 그 창에 들어오지 못해 판정을 받지 못한 채 남는다. 그것을 메운다.
    """
    from app.run.collect import run_dedupe_backfill

    if args.dry_run:
        with session_scope() as session:
            stats = run_dedupe_backfill(session, max_block=args.max_block)
            print(json.dumps(stats, ensure_ascii=False))
            session.rollback()
        print("모의 실행입니다. 아무것도 저장하지 않았습니다.")
        return 0

    with session_scope() as session:
        stats = run_dedupe_backfill(session, max_block=args.max_block)
        _audit(
            session,
            action="notice.dedupe_backfill",
            target_kind="notice",
            target_id="*",
            reason=args.reason,
            after=stats,
        )
    print(json.dumps(stats, ensure_ascii=False))
    print("`ops export` 또는 다음 수집 실행 후 정적 파일에 반영됩니다.")
    return 0


# ------------------------------------------------------------------ 내보내기·점검


def export_cmd(args: argparse.Namespace) -> int:
    result = export_static()
    print(
        f"개정 {result.revision}: 파일 {result.files_written}개, "
        f"{result.bytes_written / 1024:.1f}KB, 공지 {result.notices}건, 연락처 {result.contacts}건"
    )
    if args.prune:
        removed = prune_old_revisions(keep=args.keep)
        print(f"오래된 개정 파일 {removed}개 정리")
    return 0


def check_sources(args: argparse.Namespace) -> int:
    """출처가 실행 서버에서 실제로 열리는지 확인한다(15.1절 blocked_abroad 판정)."""

    async def run() -> int:
        ok = bad = 0
        with session_scope() as session:
            rows = session.execute(
                select(m.Source, m.SourceConfigVersion)
                .join(
                    m.SourceConfigVersion,
                    (m.SourceConfigVersion.source_id == m.Source.id)
                    & (m.SourceConfigVersion.is_active.is_(True)),
                )
                .where(m.Source.status.in_(("active", "pending")))
                .order_by(m.Source.name)
            ).all()
            targets = [(s.id, s.name, s.adapter, dict(c.config)) for s, c in rows]

        async with Fetcher(settings) as fetcher:
            for source_id, name, adapter_name, config in targets[: args.limit]:
                adapter = get_adapter(adapter_name)
                try:
                    page = await adapter.list_page(fetcher, config, 1)
                    ok += 1
                    print(f"OK   {name[:50]:52} 목록 {len(page.items)}건")
                except FetchError as exc:
                    bad += 1
                    print(f"FAIL {name[:50]:52} [{exc.kind}] {exc}")
                    if exc.kind == "blocked_target":
                        with session_scope() as session:
                            health = session.get(m.SourceHealth, source_id)
                            if health is not None:
                                health.blocked_abroad = True
                except Exception as exc:  # noqa: BLE001
                    bad += 1
                    print(f"FAIL {name[:50]:52} {exc}")
        print(f"\n통과 {ok} / 실패 {bad}")
        return 0 if bad == 0 else 1

    return asyncio.run(run())


# ------------------------------------------------------------------ 진입점


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="khu-ops", description="경희 공지 운영 명령")
    sub = parser.add_subparsers(dest="command", required=True)

    sources = sub.add_parser("sources", help="출처 관리").add_subparsers(dest="sub", required=True)
    sync = sources.add_parser("sync", help="등록부를 데이터베이스에 반영")
    sync.add_argument("--update-config", action="store_true", help="기존 출처 설정도 새 버전으로 갱신")
    sync.add_argument(
        "--retire-missing",
        action="store_true",
        help="등록부에 없는 출처를 폐쇄로 돌린다(지우지 않는다)",
    )
    sync.set_defaults(func=sources_sync)

    listing = sources.add_parser("list", help="출처 목록")
    listing.add_argument("--status", default=None)
    listing.set_defaults(func=sources_list)

    promote = sources.add_parser("promote", help="출처를 active 로")
    promote.add_argument("key")
    promote.add_argument("--reason", default=None)
    promote.set_defaults(func=sources_promote)

    pause = sources.add_parser("pause", help="출처 수집 중지")
    pause.add_argument("key")
    pause.add_argument("--reason", default=None)
    pause.set_defaults(func=sources_pause)

    contacts = sub.add_parser("contacts", help="연락처 관리").add_subparsers(dest="sub", required=True)
    imp = contacts.add_parser("import", help="정적 연락처 자료 가져오기")
    imp.add_argument("--path", default=None)
    imp.set_defaults(func=contacts_import)

    notice = sub.add_parser("notice", help="공지 관리").add_subparsers(dest="sub", required=True)
    hide = notice.add_parser("hide", help="공지를 공개에서 숨김")
    hide.add_argument("notice_id")
    hide.add_argument("--reason", required=True)
    hide.set_defaults(func=notice_hide)

    dedupe = notice.add_parser("dedupe", help="쌓인 전체 공지에 중복 병합을 한 번 적용")
    dedupe.add_argument("--reason", default="초기 수집 백필 뒤 일괄 중복 병합")
    dedupe.add_argument("--dry-run", action="store_true", help="저장하지 않고 결과만 센다")
    dedupe.add_argument("--max-block", type=int, default=400, help="한 지문 묶음의 상한")
    dedupe.set_defaults(func=notice_dedupe)

    export = sub.add_parser("export", help="정적 파일 다시 만들기")
    export.add_argument("--prune", action="store_true")
    export.add_argument("--keep", type=int, default=3)
    export.set_defaults(func=export_cmd)

    check = sub.add_parser("check-sources", help="출처 접근 가능 여부 확인")
    check.add_argument("--limit", type=int, default=20)
    check.set_defaults(func=check_sources)

    st = sub.add_parser("status", help="최근 실행·출처 요약")
    st.set_defaults(func=status_cmd)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
