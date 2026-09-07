"""출처 등록부.

registry/bootstrap/*.json 은 최초 가져오기용 파일이다(3절).
운영 중 설정의 정본은 데이터베이스이며, 이 파일이 매 실행 운영값을 덮어쓰지 않는다.
`khu.ops sources sync` 를 실행할 때만 새 출처를 추가하고, 기존 출처는 설정 버전을 올려서 바꾼다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.config import REPO_ROOT

BOOTSTRAP_DIR = REPO_ROOT / "registry" / "bootstrap"

UNIVERSITY_CODE = "khu"
UNIVERSITY_NAME = "경희대학교"


class RegistryError(ValueError):
    pass


@dataclass(frozen=True)
class CampusSpec:
    code: str
    name: str


@dataclass(frozen=True)
class OrganizationSpec:
    key: str
    name: str
    org_type: str
    parent_key: str | None = None
    campus_codes: tuple[str, ...] = ()
    short_name: str | None = None
    homepage_url: str | None = None
    aliases: tuple[str, ...] = ()
    alias_of: str | None = None
    """같은 조직이 두 번 등록된 경우, 이 조직이 어느 조직의 별칭인지.

    별칭 조직은 행을 지우지 않고 남긴다(공지와 출처가 달려 있다). 화면 트리에는
    그리지 않고, 공지·연락처만 부모 쪽으로 합쳐 보인다. 별칭이면 parent_key 는
    반드시 alias_of 와 같아야 한다(트리에서 부모 아래에 놓여 합산에 들어간다).
    """


@dataclass(frozen=True)
class SourceSpec:
    key: str
    name: str
    organization_key: str
    adapter: str
    config: dict[str, Any]
    content_kind: str = "notice"
    medium: str = "web"
    interval_minutes: int = 60
    status: str = "pending"
    audiences: tuple[dict[str, str], ...] = ()
    official_evidence_url: str | None = None
    notes: str | None = None

    @property
    def list_url(self) -> str:
        base = str(self.config.get("base_url", "")).rstrip("/")
        prefix = str(self.config.get("prefix", "")).strip("/")
        board = self.config.get("board_code", "")
        menu = self.config.get("menu_no", "")
        if self.adapter == "khu_board":
            return f"{base}/{prefix}/user/bbs/{board}/list.do?menuNo={menu}"
        if self.adapter == "gnuboard":
            return f"{base}/bbs/board.php?bo_table={self.config.get('bo_table', '')}"
        return str(self.config.get("list_url") or base)


@dataclass(frozen=True)
class Registry:
    campuses: tuple[CampusSpec, ...] = ()
    organizations: tuple[OrganizationSpec, ...] = ()
    sources: tuple[SourceSpec, ...] = ()
    meta: dict[str, Any] = field(default_factory=dict)

    def organization(self, key: str) -> OrganizationSpec:
        for org in self.organizations:
            if org.key == key:
                return org
        raise RegistryError(f"등록부에 없는 조직 키: {key}")

    def org_path(self, key: str) -> tuple[str, ...]:
        """조직 경로. 조직 식별자를 만들 때 쓴다."""
        chain: list[str] = []
        seen: set[str] = set()
        current: str | None = key
        while current:
            if current in seen:
                raise RegistryError(f"조직 계층에 순환이 있습니다: {key}")
            seen.add(current)
            org = self.organization(current)
            chain.append(org.name)
            current = org.parent_key
        chain.append(UNIVERSITY_NAME)
        return tuple(reversed(chain))


def _read_json(path: Path) -> Any:
    if not path.exists():
        raise RegistryError(f"등록부 파일이 없습니다: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def load_registry(directory: Path | None = None) -> Registry:
    base = Path(directory or BOOTSTRAP_DIR)

    orgs_doc = _read_json(base / "organizations.json")
    sources_doc = _read_json(base / "sources.json")

    campuses = tuple(
        CampusSpec(code=str(c["code"]), name=str(c["name"])) for c in orgs_doc.get("campuses", [])
    )
    known_campus = {c.code for c in campuses}

    organizations: list[OrganizationSpec] = []
    seen_keys: set[str] = set()
    for row in orgs_doc.get("organizations", []):
        key = str(row["key"])
        if key in seen_keys:
            raise RegistryError(f"조직 키가 중복입니다: {key}")
        seen_keys.add(key)
        campus_codes = tuple(str(c) for c in row.get("campus_codes", []))
        for code in campus_codes:
            if code not in known_campus:
                raise RegistryError(f"{key}: 등록되지 않은 캠퍼스 코드 {code}")
        organizations.append(
            OrganizationSpec(
                key=key,
                name=str(row["name"]),
                org_type=str(row["type"]),
                parent_key=(str(row["parent"]) if row.get("parent") else None),
                campus_codes=campus_codes,
                short_name=row.get("short_name"),
                homepage_url=row.get("homepage_url"),
                aliases=tuple(str(a) for a in row.get("aliases", [])),
                alias_of=(str(row["alias_of"]) if row.get("alias_of") else None),
            )
        )

    by_key = {org.key: org for org in organizations}
    for org in organizations:
        if org.parent_key and org.parent_key not in seen_keys:
            raise RegistryError(f"{org.key}: 없는 상위 조직 {org.parent_key}")
        if org.alias_of is None:
            continue
        if org.alias_of not in seen_keys:
            raise RegistryError(f"{org.key}: 없는 별칭 부모 {org.alias_of}")
        if org.alias_of == org.key:
            raise RegistryError(f"{org.key}: 자기 자신의 별칭일 수 없습니다")
        if org.parent_key != org.alias_of:
            raise RegistryError(
                f"{org.key}: 별칭 조직의 parent 는 alias_of 와 같아야 합니다"
                f"(parent={org.parent_key}, alias_of={org.alias_of})"
            )
        if by_key[org.alias_of].alias_of is not None:
            raise RegistryError(f"{org.key}: 별칭 부모({org.alias_of})가 다시 별칭입니다")

    sources: list[SourceSpec] = []
    source_keys: set[str] = set()
    for row in sources_doc.get("sources", []):
        key = str(row["key"])
        if key in source_keys:
            raise RegistryError(f"출처 키가 중복입니다: {key}")
        source_keys.add(key)
        if row["organization"] not in seen_keys:
            raise RegistryError(f"{key}: 없는 조직 {row['organization']}")
        audiences = tuple(dict(a) for a in row.get("audiences", []))
        for aud in audiences:
            if aud.get("type") == "campus" and aud.get("campus") not in known_campus:
                raise RegistryError(f"{key}: 대상 캠퍼스 코드가 등록부에 없습니다 {aud.get('campus')}")
        sources.append(
            SourceSpec(
                key=key,
                name=str(row["name"]),
                organization_key=str(row["organization"]),
                adapter=str(row.get("adapter", "khu_board")),
                config=dict(row.get("config", {})),
                content_kind=str(row.get("content_kind", "notice")),
                medium=str(row.get("medium", "web")),
                interval_minutes=int(row.get("interval_minutes", 60)),
                status=str(row.get("status", "pending")),
                audiences=audiences,
                official_evidence_url=row.get("official_evidence_url"),
                notes=row.get("notes"),
            )
        )

    registry = Registry(
        campuses=campuses,
        organizations=tuple(organizations),
        sources=tuple(sources),
        meta={"organizations": orgs_doc.get("meta", {}), "sources": sources_doc.get("meta", {})},
    )

    # 순환 검사를 지금 한 번 돌려 둔다.
    for org in registry.organizations:
        registry.org_path(org.key)
    return registry
