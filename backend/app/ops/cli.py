"""운영 명령 도구(14절).

1차에는 운영자 화면과 관리 연동 경로를 만들지 않는다. 운영 동작은 이 도구로 한다.
운영자 PC 에서 직접 실행하거나 GitHub Actions 의 수동 실행 입력으로 부른다.
모든 변경 명령은 audit_logs 에 사유·실행자를 남긴다.

    python -m app.ops.cli sources sync          등록부의 새 출처를 데이터베이스에 넣는다
        --dry-run              아무것도 쓰지 않고 무엇이 바뀌는지만 센다
        --apply-status         등록부 active · 데이터베이스 pending 인 출처만 올린다
        --move-organization    등록부가 가리키는 조직으로 출처를 옮긴다
    python -m app.ops.cli sources list          출처와 상태를 본다
    python -m app.ops.cli sources promote KEY   pending 출처를 active 로 올린다
    python -m app.ops.cli sources pause KEY --reason ...
    python -m app.ops.cli contacts import       정적 연락처 자료를 데이터베이스에 넣는다
    python -m app.ops.cli notice hide ID --reason ...
    python -m app.ops.cli notice dedupe        쌓인 전체 공지에 중복 병합을 한 번 적용한다
    python -m app.ops.cli notice reaudience    쌓인 전체 공지의 대상 범위를 저장된 값으로 다시 계산한다
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
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy import inspect as sa_inspect

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


def _organization_columns(session) -> set[str]:
    """organizations 에 실제로 있는 열 이름.

    모의 실행은 마이그레이션 전 데이터베이스에서도 돌아야 한다. 새 열(is_alias,
    registry_key)이 없으면 그 부분만 건너뛰고 나머지를 센다.
    """
    try:
        return {c["name"] for c in sa_inspect(session.get_bind()).get_columns("organizations")}
    except Exception:  # noqa: BLE001 - 열 목록을 못 읽으면 새 열이 없다고 본다
        return {"id", "name", "parent_id", "org_type"}


class _OrgResolution:
    """등록부 조직 하나가 어느 데이터베이스 행에 앉는지."""

    __slots__ = ("key", "name", "org_id", "how", "row")

    def __init__(self, key: str, name: str, org_id: str, how: str, row: dict | None) -> None:
        self.key = key
        self.name = name
        self.org_id = org_id
        self.how = how  # registry_key | path | flat | name | shared | new
        self.row = row


def _resolve_organizations(session, registry, *, columns: set[str]) -> tuple[dict[str, _OrgResolution], dict[str, dict]]:
    """등록부 조직을 기존 데이터베이스 행에 맞춘다.

    조직 식별자는 이름 경로의 해시다(`ids.organization_id`). 상위를 바꾸면 경로가
    바뀌어 식별자도 바뀌므로, 아무 장치 없이 반영하면 공지가 달린 행을 버리고 빈
    행을 새로 만든다. 그래서 네 단계로 찾는다.

      1) registry_key   — 지난 반영이 행에 적어 둔 등록부 열쇠. 가장 확실하다
      1-5) legacy_id    — 등록부에 없던 시절에 만들어진 행을 이름만 고쳐 이어받는 경우
      2) 새 경로 해시   — 계층이 그대로인 조직
      3) flat 해시      — 상위 없이 만들어졌던 예전 식별자
      4) 같은 이름 행   — 이미 상위가 있던 조직의 상위를 바꾼 경우. 이름이 등록부와
                          데이터베이스 양쪽에서 하나뿐일 때만 쓴다
      5) 같은 경로를 쓰는 등록부 조직이 둘이면(거울) 같은 행을 함께 쓴다

    넷 다 실패하면 새 행이다. 그 경우 같은 이름의 행이 이미 있으면 식별자 충돌로
    알린다(모의 실행이 목록으로 찍는다).
    """
    has_registry_key = "registry_key" in columns
    has_alias = "is_alias" in columns

    select_cols = [
        m.Organization.id,
        m.Organization.name,
        m.Organization.parent_id,
        m.Organization.org_type,
    ]
    if has_registry_key:
        select_cols.append(m.Organization.registry_key)
    if has_alias:
        select_cols.append(m.Organization.is_alias)

    rows: dict[str, dict] = {}
    for record in session.execute(select(*select_cols)).all():
        item = {
            "id": record[0],
            "name": record[1],
            "parent_id": record[2],
            "org_type": record[3],
            "registry_key": record[4] if has_registry_key else None,
            "is_alias": bool(record[-1]) if has_alias else False,
        }
        rows[item["id"]] = item

    by_registry_key = {r["registry_key"]: r for r in rows.values() if r["registry_key"]}
    by_name: dict[str, list[dict]] = {}
    for row in rows.values():
        by_name.setdefault(row["name"], []).append(row)

    registry_name_counts: dict[str, int] = {}
    for org in registry.organizations:
        registry_name_counts[org.name] = registry_name_counts.get(org.name, 0) + 1

    resolutions: dict[str, _OrgResolution] = {}
    claimed: set[str] = set()
    pending: list = []

    # 1차: 확실한 열쇠부터. 이름으로 찾는 일은 이 뒤에 한다. 순서를 섞으면
    # 경로가 맞는 다른 조직의 행을 이름이 먼저 가져가 버린다.
    for org in registry.organizations:
        row = by_registry_key.get(org.key)
        how = "registry_key"
        if row is None or row["id"] in claimed:
            path_id = ids.organization_id(UNIVERSITY_CODE, registry.org_path(org.key))
            row, how = rows.get(path_id), "path"
            if row is None or row["id"] in claimed:
                flat = ids.organization_id(UNIVERSITY_CODE, (UNIVERSITY_NAME, org.name))
                row, how = rows.get(flat), "flat"
            if (row is None or row["id"] in claimed) and org.legacy_id:
                row, how = rows.get(org.legacy_id), "legacy_id"
        if row is None or row["id"] in claimed:
            pending.append(org)
            continue
        claimed.add(row["id"])
        resolutions[org.key] = _OrgResolution(org.key, org.name, row["id"], how, row)

    # 2차: 이름으로 자리를 지킨다. 이미 상위가 있던 조직의 상위를 바꾸면 경로 해시도
    # flat 해시도 맞지 않는다. 이름이 양쪽에서 하나뿐일 때만 같은 조직으로 본다.
    for org in pending:
        candidates = [r for r in by_name.get(org.name, []) if r["id"] not in claimed]
        if len(candidates) == 1 and registry_name_counts.get(org.name, 0) == 1:
            row = candidates[0]
            claimed.add(row["id"])
            resolutions[org.key] = _OrgResolution(org.key, org.name, row["id"], "name", row)
            continue
        new_id = ids.organization_id(UNIVERSITY_CODE, registry.org_path(org.key))
        shared = rows.get(new_id)
        if shared is not None:
            # 등록부에 이름도 경로도 같은 조직이 둘 있는 경우다(거울로 들어온 사이트).
            # 식별자가 같으니 새 행이 아니라 같은 행을 함께 쓴다.
            resolutions[org.key] = _OrgResolution(org.key, org.name, new_id, "shared", shared)
            continue
        resolutions[org.key] = _OrgResolution(org.key, org.name, new_id, "new", None)

    return resolutions, rows


def sources_sync(args: argparse.Namespace) -> int:
    """등록부의 조직·출처를 데이터베이스에 반영한다.

    기본은 예전과 같다. 새 것만 넣고 기존 운영값은 덮어쓰지 않는다(3절).
    기존 행을 고치는 일은 깃발을 줄 때만 한다.

      --dry-run            아무것도 쓰지 않고 무엇이 바뀌는지 센다
      --apply-status       등록부가 active 인데 데이터베이스가 pending 인 출처만 올린다
                           (retired·paused·delayed 는 건드리지 않는다)
      --move-organization  등록부가 가리키는 조직으로 출처를 옮긴다(대상 범위도 함께)
      --update-config      기존 출처 설정을 새 버전으로 갱신
      --retire-missing     등록부에 없는 출처를 폐쇄로 돌린다
    """
    registry = load_registry()
    dry_run = bool(getattr(args, "dry_run", False))
    apply_status = bool(getattr(args, "apply_status", False))
    move_org = bool(getattr(args, "move_organization", False))

    added_org = added_src = updated_cfg = retired = 0
    linked_org = typed_org = aliased_org = keyed_org = renamed_org = 0
    status_changed = moved_src = renamed_src = 0
    source_renames: list[str] = []
    conflicts: list[str] = []
    renames: list[str] = []
    moves: list[str] = []
    status_moves: list[str] = []
    type_moves: list[str] = []

    with session_scope() as session:
        columns = _organization_columns(session)
        has_alias = "is_alias" in columns
        has_registry_key = "registry_key" in columns
        resolutions, existing_rows = _resolve_organizations(session, registry, columns=columns)
        org_ids = {key: res.org_id for key, res in resolutions.items()}
        name_index: dict[str, int] = {}
        for row in existing_rows.values():
            name_index[row["name"]] = name_index.get(row["name"], 0) + 1

        university_id = ids.university_id(UNIVERSITY_CODE)
        campus_ids = {
            campus.code: ids.campus_id(UNIVERSITY_CODE, campus.code) for campus in registry.campuses
        }

        # ---------------------------------------------------------------- 계획
        for org in registry.organizations:
            res = resolutions[org.key]
            want_parent = org_ids.get(org.parent_key) if org.parent_key else None
            want_alias = org.alias_of is not None
            if res.how == "new":
                added_org += 1
                if name_index.get(org.name):
                    conflicts.append(
                        f"{org.key} {org.name}: 같은 이름의 조직 행이 이미 있는데 새 행을 만들게 된다"
                    )
                continue
            row = res.row or {}
            if row.get("parent_id") != want_parent:
                linked_org += 1
            if row.get("name") != org.name:
                renamed_org += 1
                renames.append(f"{org.key}: {row.get('name')} -> {org.name}")
            if row.get("org_type") != org.org_type:
                typed_org += 1
                type_moves.append(f"{org.key} {org.name}: {row.get('org_type')} -> {org.org_type}")
            if has_alias and bool(row.get("is_alias")) != want_alias:
                aliased_org += 1
            if has_registry_key and row.get("registry_key") != org.key:
                keyed_org += 1

        source_rows = {row.id: row for row in session.execute(select(m.Source)).scalars()}
        for spec in registry.sources:
            sid = ids.source_id(spec.adapter, spec.list_url)
            row = source_rows.get(sid)
            want_org = org_ids[spec.organization_key]
            if row is None:
                added_src += 1
                continue
            if row.name != spec.name:
                renamed_src += 1
                source_renames.append(f"{spec.key}: {row.name} -> {spec.name}")
            if apply_status and spec.status == "active" and row.status == "pending":
                status_changed += 1
                status_moves.append(f"{spec.key} {spec.name}")
            if move_org and row.organization_id != want_org:
                moved_src += 1
                moves.append(f"{spec.key} {spec.name}: {row.organization_id} -> {want_org}")

        if args.update_config:
            active_configs = {
                row.source_id: row
                for row in session.execute(
                    select(m.SourceConfigVersion).where(m.SourceConfigVersion.is_active.is_(True))
                ).scalars()
            }
            for spec in registry.sources:
                sid = ids.source_id(spec.adapter, spec.list_url)
                active = active_configs.get(sid)
                if active is not None and dict(active.config) != spec.config:
                    updated_cfg += 1

        if args.retire_missing:
            known = {ids.source_id(spec.adapter, spec.list_url) for spec in registry.sources}
            retired = sum(
                1 for row in source_rows.values() if row.id not in known and row.status != "retired"
            )

        if dry_run:
            expected = len(existing_rows) + added_org
            print(
                f"[모의 실행] 조직 추가 {added_org} / 이름 변경 {renamed_org}"
                f" / 상위 변경 {linked_org} / 유형 변경 {typed_org}"
                f" / 별칭 표시 변경 {aliased_org} / 등록부 열쇠 기록 {keyed_org}"
            )
            print(
                f"[모의 실행] 출처 추가 {added_src} / 출처 이름 변경 {renamed_src} / 상태 변경 {status_changed}"
                f" / 조직 이관 {moved_src} / 설정 갱신 {updated_cfg} / 폐쇄 {retired}"
            )
            print(f"[모의 실행] organizations 행 수: 지금 {len(existing_rows)} -> 반영 후 예상 {expected}")
            if not has_alias or not has_registry_key:
                missing = [
                    name
                    for name, present in (("is_alias", has_alias), ("registry_key", has_registry_key))
                    if not present
                ]
                print(f"[모의 실행] 이 데이터베이스에 없는 열: {', '.join(missing)} (그 부분은 세지 않았다)")
            if conflicts:
                print(f"[모의 실행] 식별자 충돌 {len(conflicts)}건:")
                for line in conflicts:
                    print(f"  - {line}")
            else:
                print("[모의 실행] 식별자 충돌 없음")
            for line in renames:
                print(f"  이름 변경: {line}")
            for line in type_moves:
                print(f"  유형 변경: {line}")
            for line in source_renames:
                print(f"  출처 이름 변경: {line}")
            for line in status_moves:
                print(f"  상태 pending -> active: {line}")
            for line in moves:
                print(f"  조직 이관: {line}")
            session.rollback()
            print("모의 실행입니다. 아무것도 저장하지 않았습니다.")
            return 0

        # ---------------------------------------------------------------- 반영
        if session.get(m.University, university_id) is None:
            session.add(m.University(id=university_id, code=UNIVERSITY_CODE, name=UNIVERSITY_NAME))
            session.flush()

        for campus in registry.campuses:
            cid = campus_ids[campus.code]
            if session.get(m.Campus, cid) is None:
                session.add(
                    m.Campus(id=cid, university_id=university_id, code=campus.code, name=campus.name)
                )
        session.flush()

        created_ids: set[str] = set()
        for org in registry.organizations:
            res = resolutions[org.key]
            oid = res.org_id
            row = session.get(m.Organization, oid)
            if row is None:
                created_ids.add(oid)
                session.add(
                    m.Organization(
                        id=oid,
                        university_id=university_id,
                        parent_id=None,
                        org_type=org.org_type,
                        name=org.name,
                        short_name=org.short_name,
                        aliases=list(org.aliases),
                        homepage_url=org.homepage_url,
                        is_alias=org.alias_of is not None,
                        registry_key=org.key,
                    )
                )
            else:
                if row.name != org.name:
                    _audit(
                        session,
                        action="organization.rename",
                        target_kind="organization",
                        target_id=oid,
                        reason=f"등록부 동기화: {org.key}",
                        before={"name": row.name},
                        after={"name": org.name},
                    )
                    row.name = org.name
                merged = list(row.aliases or ())
                for alias in org.aliases:
                    if alias not in merged:
                        merged.append(alias)
                if merged != list(row.aliases or ()):
                    row.aliases = merged
                if org.short_name and not row.short_name:
                    row.short_name = org.short_name
                if row.org_type != org.org_type:
                    _audit(
                        session,
                        action="organization.retype",
                        target_kind="organization",
                        target_id=oid,
                        reason=f"등록부 동기화: {org.key}",
                        before={"org_type": row.org_type},
                        after={"org_type": org.org_type},
                    )
                    row.org_type = org.org_type
                if bool(row.is_alias) != (org.alias_of is not None):
                    row.is_alias = org.alias_of is not None
                if row.registry_key != org.key:
                    row.registry_key = org.key
            for code in org.campus_codes:
                link = session.get(
                    m.OrganizationCampus,
                    {"organization_id": oid, "campus_id": campus_ids[code]},
                )
                if link is None:
                    session.add(
                        m.OrganizationCampus(organization_id=oid, campus_id=campus_ids[code])
                    )
        session.flush()

        # 상위 연결은 모든 조직의 식별자를 안 뒤에 한다. 한 번에 하면 등록부에서 자식이
        # 부모보다 먼저 나온 경우 부모를 못 찾아 연결이 조용히 빠진다.
        for org in registry.organizations:
            row = session.get(m.Organization, org_ids[org.key])
            parent = org_ids.get(org.parent_key) if org.parent_key else None
            if row is not None and row.parent_id != parent:
                before = row.parent_id
                row.parent_id = parent
                if row.id in created_ids:
                    continue
                _audit(
                    session,
                    action="organization.reparent",
                    target_kind="organization",
                    target_id=row.id,
                    reason=f"등록부 동기화: {org.key}",
                    before={"parent_id": before},
                    after={"parent_id": parent},
                )
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
                _audit(
                    session,
                    action="source.create",
                    target_kind="source",
                    target_id=sid,
                    reason=f"등록부 동기화: {spec.key}",
                    after={"name": spec.name, "list_url": spec.list_url},
                )
                continue

            # 출처 이름은 "조직 이름 + 게시판 이름" 이라 조직 이름이 바뀌면 같이 바뀐다.
            # "[확인 필요] 호스트" 자리표시자가 공지의 출처 표시에 남지 않게 한다.
            if row.name != spec.name:
                _audit(
                    session,
                    action="source.rename",
                    target_kind="source",
                    target_id=sid,
                    reason=f"등록부 동기화: {spec.key}",
                    before={"name": row.name},
                    after={"name": spec.name},
                )
                row.name = spec.name

            # 등록부가 active 라고 말하는데 데이터베이스가 pending 인 경우만 올린다.
            # retired·paused·delayed 는 운영 판단이 들어간 값이라 등록부가 덮지 않는다.
            if apply_status and spec.status == "active" and row.status == "pending":
                row.status = "active"
                row.status_message = None
                _audit(
                    session,
                    action="source.active",
                    target_kind="source",
                    target_id=sid,
                    reason=f"등록부 동기화(--apply-status): {spec.key}",
                    before={"status": "pending"},
                    after={"status": "active"},
                )

            # 조직 이관. 출처의 기본 대상 범위(organization 형)도 같이 옮긴다.
            # 이미 쌓인 공지의 대상(notice_audiences)은 건드리지 않는다.
            # 그쪽은 `notice reaudience` 가 출처 기본값에서 다시 계산한다.
            want_org = org_ids[spec.organization_key]
            if move_org and row.organization_id != want_org:
                before_org = row.organization_id
                row.organization_id = want_org
                for aud in session.execute(
                    select(m.SourceAudience).where(
                        m.SourceAudience.source_id == sid,
                        m.SourceAudience.audience_type == "organization",
                        m.SourceAudience.organization_id == before_org,
                    )
                ).scalars():
                    aud.organization_id = want_org
                _audit(
                    session,
                    action="source.move_organization",
                    target_kind="source",
                    target_id=sid,
                    reason=f"등록부 동기화(--move-organization): {spec.key}",
                    before={"organization_id": before_org},
                    after={"organization_id": want_org},
                )

            if args.update_config:
                active = session.execute(
                    select(m.SourceConfigVersion).where(
                        m.SourceConfigVersion.source_id == sid,
                        m.SourceConfigVersion.is_active.is_(True),
                    )
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
            stale = (
                session.execute(
                    select(m.Source).where(
                        m.Source.id.not_in(known), m.Source.status.not_in(("retired",))
                    )
                )
                .scalars()
                .all()
            )
            for row in stale:
                before = row.status
                row.status = "retired"
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
        f"조직 추가 {added_org} / 상위 변경 {linked_org} / 유형 변경 {typed_org}"
        f" / 별칭 표시 변경 {aliased_org} / 출처 추가 {added_src} / 설정 갱신 {updated_cfg}"
        f" / 상태 변경 {status_changed} / 조직 이관 {moved_src} / 출처 이름 변경 {renamed_src} / 폐쇄 {retired}"
    )
    if conflicts:
        print(f"주의: 같은 이름의 조직 행을 새로 만들었습니다 {len(conflicts)}건")
        for line in conflicts:
            print(f"  - {line}")
    if not args.update_config:
        print("기존 출처 설정은 그대로 두었습니다. 바꾸려면 --update-config 를 쓰세요.")
    if not args.retire_missing:
        print("등록부에서 빠진 출처는 그대로 두었습니다. 정리하려면 --retire-missing 을 쓰세요.")
    if not apply_status:
        print("등록부와 어긋난 출처 상태는 그대로 두었습니다. 맞추려면 --apply-status 를 쓰세요.")
    if not move_org:
        print("출처의 소속 조직은 그대로 두었습니다. 옮기려면 --move-organization 을 쓰세요.")
    if moved_src:
        print(
            "출처를 옮겼습니다. 이미 쌓인 공지의 대상 범위는 그대로이므로 "
            "`notice reaudience` 를 이어서 실행해야 합니다."
        )
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

        # 이름으로 찾는다. 이름을 고친 조직(전화번호 명부의 "전자공학" -> "전자공학부")은
        # 옛 이름이 aliases 에 남아 있으므로 그것으로도 찾는다. 그러지 않으면 명부를
        # 다시 들일 때마다 같은 조직의 빈 행이 새로 생긴다.
        org_by_name: dict[str, str] = {}
        for o in session.execute(select(m.Organization)).scalars():
            for alias in o.aliases or ():
                org_by_name.setdefault(str(alias), o.id)
        for o in session.execute(select(m.Organization)).scalars():
            org_by_name[o.name] = o.id

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

    본문 지문 묶음과 포스터 묶음을 둘 다 돌린다. 포스터 묶음은 본문 글자가 없어
    지문이 안 나오는 공지를 검토 대상으로 올릴 뿐이고, 자동 병합은 지문 묶음에서만
    나온다. 결과의 poster_blocks 는 포스터로 묶인 묶음 수다.
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


def notice_reaudience(args: argparse.Namespace) -> int:
    """쌓인 공지의 대상 범위를 저장된 제목·본문으로 다시 계산한다.

    대상 판정의 입력은 제목·본문·출처 기본 대상뿐이고 셋 다 이미 저장되어 있다.
    그래서 원문을 다시 받지 않는다. 규칙을 고쳐도 이미 쌓인 공지는 상세 재확인이
    돌아오는 최대 14일 뒤에야 반영되는데, 이 명령이 그 기다림을 없앤다.

    모의 실행은 세션을 전혀 건드리지 않고 세기만 한다. 실제 적용은 바뀐 공지마다
    audit_logs 에 이전·이후 대상을 남기고 한 묶음 번호를 붙이므로,
    `notice reaudience --revert 묶음번호` 로 그대로 되돌릴 수 있다.
    """
    from app.run.collect import revert_audience_backfill, run_audience_backfill

    if args.revert:
        with session_scope() as session:
            # 묶음이 없으면 아무것도 하지 않고 세션을 되돌린다. 빈 되돌리기를 기록하지 않는다.
            preview = revert_audience_backfill(session, batch_id=args.revert, apply=False)
            if preview["entries"] == 0 or args.dry_run:
                session.rollback()
                print(json.dumps(preview, ensure_ascii=False))
                if preview["entries"] == 0:
                    print(f"그 묶음의 기록이 없습니다: {args.revert}", file=sys.stderr)
                    return 1
                print("모의 실행입니다. 아무것도 저장하지 않았습니다.")
                return 0
            stats = revert_audience_backfill(
                session, batch_id=args.revert, actor=_actor(), apply=True
            )
            _audit(
                session,
                action="notice.reaudience_revert",
                target_kind="notice",
                target_id="*",
                reason=args.reason,
                after=stats,
            )
        print(json.dumps(stats, ensure_ascii=False))
        print("되돌렸습니다. `ops export` 또는 다음 수집 실행 후 정적 파일에 반영됩니다.")
        return 0

    if args.dry_run:
        with session_scope() as session:
            stats = run_audience_backfill(
                session, apply=False, sample_limit=args.samples, actor=_actor()
            )
            session.rollback()
        print(json.dumps(stats, ensure_ascii=False, indent=2))
        print("모의 실행입니다. 아무것도 저장하지 않았습니다.")
        return 0

    batch_id = f"reaud-{uuid4().hex[:12]}"
    with session_scope() as session:
        stats = run_audience_backfill(
            session,
            apply=True,
            batch_id=batch_id,
            reason=args.reason,
            actor=_actor(),
            sample_limit=args.samples,
        )
        _audit(
            session,
            action="notice.reaudience_batch",
            target_kind="notice",
            target_id="*",
            reason=args.reason,
            after={k: v for k, v in stats.items() if k != "samples"},
        )
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    print(f"되돌리려면: python -m app.ops.cli notice reaudience --revert {batch_id}")
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
    sync.add_argument(
        "--dry-run",
        action="store_true",
        help="아무것도 쓰지 않고 무엇이 바뀌는지만 센다",
    )
    sync.add_argument(
        "--apply-status",
        action="store_true",
        help="등록부가 active 인데 데이터베이스가 pending 인 출처만 active 로 올린다",
    )
    sync.add_argument(
        "--move-organization",
        action="store_true",
        help="등록부가 가리키는 조직으로 출처를 옮긴다(출처 기본 대상도 함께)",
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

    dedupe = notice.add_parser("dedupe", help="쌓인 전체 공지에 중복 판정(지문 병합 + 포스터 검토)을 한 번 적용")
    dedupe.add_argument("--reason", default="초기 수집 백필 뒤 일괄 중복 병합")
    dedupe.add_argument("--dry-run", action="store_true", help="저장하지 않고 결과만 센다")
    dedupe.add_argument("--max-block", type=int, default=400, help="한 묶음(지문·포스터)의 상한")
    dedupe.set_defaults(func=notice_dedupe)

    reaud = notice.add_parser(
        "reaudience", help="쌓인 전체 공지의 대상 범위를 저장된 제목·본문으로 다시 계산"
    )
    reaud.add_argument("--reason", default="대상 판정 규칙 수정 후 일괄 재계산")
    reaud.add_argument("--dry-run", action="store_true", help="저장하지 않고 결과만 센다")
    reaud.add_argument("--samples", type=int, default=20, help="눈으로 볼 변경 표본 수")
    reaud.add_argument(
        "--revert", default=None, metavar="BATCH", help="그 묶음의 변경을 이전 대상으로 되돌린다"
    )
    reaud.set_defaults(func=notice_reaudience)

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
