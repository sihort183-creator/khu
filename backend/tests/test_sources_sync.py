"""등록부 동기화 검사(3절).

조직 식별자는 이름 경로의 해시다. 상위를 바꾸면 식별자가 바뀌므로, 장치가 없으면
공지가 달린 행을 버리고 빈 행을 새로 만든다. 그 함정을 막는지 여기서 확인한다.
모의 실행·상태 맞추기·조직 이관·별칭 표시도 함께 본다.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.domain import ids
from app.ingestion import registry as registry_module
from app.ingestion.registry import UNIVERSITY_CODE, UNIVERSITY_NAME, RegistryError, load_registry
from app.ops import cli as ops
from app.storage import models as m

CAMPUSES = [{"code": "seoul", "name": "서울캠퍼스"}, {"code": "global", "name": "국제캠퍼스"}]


def _org(key: str, name: str, org_type: str = "department", **extra) -> dict:
    row = {"key": key, "name": name, "type": org_type, "parent": None, "campus_codes": ["seoul"]}
    row.update(extra)
    return row


def _source(key: str, org: str, *, status: str = "active", menu: str = "1000") -> dict:
    return {
        "key": key,
        "name": f"{key} 공지사항",
        "organization": org,
        "adapter": "khu_board",
        "status": status,
        "config": {
            "base_url": "https://example.khu.ac.kr",
            "prefix": "example",
            "board_code": "BMSR00040",
            "menu_no": menu,
        },
        "audiences": [{"type": "organization", "organization": org}],
    }


def _write(directory: Path, organizations: list[dict], sources: list[dict]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "organizations.json").write_text(
        json.dumps({"meta": {}, "campuses": CAMPUSES, "organizations": organizations}, ensure_ascii=False),
        encoding="utf-8",
    )
    (directory / "sources.json").write_text(
        json.dumps({"meta": {}, "sources": sources}, ensure_ascii=False), encoding="utf-8"
    )
    return directory


@pytest.fixture
def registry_dir(tmp_path, monkeypatch):
    """등록부를 임시 폴더에서 읽게 한다."""
    base = tmp_path / "bootstrap"

    def _use(organizations: list[dict], sources: list[dict]) -> Path:
        _write(base, organizations, sources)
        monkeypatch.setattr(registry_module, "BOOTSTRAP_DIR", base)
        return base

    return _use


def _args(**flags) -> argparse.Namespace:
    base = {
        "update_config": False,
        "retire_missing": False,
        "dry_run": False,
        "apply_status": False,
        "move_organization": False,
    }
    base.update(flags)
    return argparse.Namespace(**base)


def _org_count(session_factory) -> int:
    with session_factory() as session:
        return session.execute(select(func.count(m.Organization.id))).scalar()


# ------------------------------------------------------------------ 모의 실행


def test_dry_run_writes_nothing(session_factory, registry_dir, capsys):
    registry_dir([_org("o-college", "공과대학", "college")], [_source("s-1", "o-college")])

    assert ops.sources_sync(_args(dry_run=True)) == 0

    out = capsys.readouterr().out
    assert "조직 추가 1" in out
    assert "출처 추가 1" in out
    assert "반영 후 예상 1" in out
    with session_factory() as session:
        assert session.execute(select(func.count(m.Organization.id))).scalar() == 0
        assert session.execute(select(func.count(m.Source.id))).scalar() == 0


def test_dry_run_counts_changes_without_applying_them(session_factory, registry_dir, capsys):
    orgs = [_org("o-college", "공과대학", "college"), _org("o-dept", "건축공학과", parent="o-college")]
    registry_dir(orgs, [_source("s-1", "o-dept", status="active")])
    ops.sources_sync(_args())

    with session_factory() as session:
        source = session.execute(select(m.Source)).scalar_one()
        source.status = "pending"
        session.commit()

    # 등록부에서 학과를 다른 상위로 옮긴다(별칭 부모를 끼워 넣는 상황과 같다).
    orgs = [
        _org("o-college", "공과대학", "college"),
        _org("o-alias", "건축공학", parent="o-college"),
        _org("o-dept", "건축공학과", parent="o-alias", alias_of="o-alias"),
    ]
    registry_dir(orgs, [_source("s-1", "o-dept", status="active")])

    ops.sources_sync(_args(dry_run=True, apply_status=True, move_organization=True))
    out = capsys.readouterr().out
    assert "조직 추가 1" in out  # 별칭 부모만 새로 생긴다
    assert "상태 변경 1" in out
    assert "식별자 충돌 없음" in out
    assert "반영 후 예상 3" in out

    with session_factory() as session:
        assert session.execute(select(m.Source)).scalar_one().status == "pending"
        assert session.execute(select(func.count(m.Organization.id))).scalar() == 2


# ------------------------------------------------------------------ 상태 맞추기


def test_apply_status_lifts_only_pending(session_factory, registry_dir):
    orgs = [_org("o-college", "공과대학", "college")]
    sources = [
        _source("s-pending", "o-college", menu="1"),
        _source("s-paused", "o-college", menu="2"),
        _source("s-retired", "o-college", menu="3"),
    ]
    registry_dir(orgs, sources)
    ops.sources_sync(_args())

    with session_factory() as session:
        rows = {r.name: r for r in session.execute(select(m.Source)).scalars()}
        rows["s-pending 공지사항"].status = "pending"
        rows["s-paused 공지사항"].status = "paused"
        rows["s-retired 공지사항"].status = "retired"
        session.commit()

    ops.sources_sync(_args(apply_status=True))

    with session_factory() as session:
        rows = {r.name: r.status for r in session.execute(select(m.Source)).scalars()}
        assert rows["s-pending 공지사항"] == "active"
        assert rows["s-paused 공지사항"] == "paused"
        assert rows["s-retired 공지사항"] == "retired"
        log = session.execute(
            select(m.AuditLog).where(m.AuditLog.action == "source.active")
        ).scalar_one()
        assert "--apply-status" in log.reason


def test_status_is_left_alone_without_the_flag(session_factory, registry_dir):
    registry_dir([_org("o-college", "공과대학", "college")], [_source("s-1", "o-college")])
    ops.sources_sync(_args())
    with session_factory() as session:
        session.execute(select(m.Source)).scalar_one().status = "pending"
        session.commit()

    ops.sources_sync(_args())

    with session_factory() as session:
        assert session.execute(select(m.Source)).scalar_one().status == "pending"


# ------------------------------------------------------------------ 조직 이관


def test_move_organization_moves_source_and_its_audiences(session_factory, registry_dir):
    orgs = [_org("o-old", "[확인 필요] ce.khu.ac.kr", "office"), _org("o-new", "컴퓨터공학부")]
    registry_dir(orgs, [_source("s-1", "o-old")])
    ops.sources_sync(_args())

    with session_factory() as session:
        old_id = session.execute(select(m.Source)).scalar_one().organization_id

    registry_dir(orgs, [_source("s-1", "o-new")])
    ops.sources_sync(_args(move_organization=True))

    with session_factory() as session:
        source = session.execute(select(m.Source)).scalar_one()
        new_id = ids.organization_id(UNIVERSITY_CODE, (UNIVERSITY_NAME, "컴퓨터공학부"))
        assert source.organization_id == new_id != old_id
        audience = session.execute(select(m.SourceAudience)).scalar_one()
        assert audience.organization_id == new_id
        log = session.execute(
            select(m.AuditLog).where(m.AuditLog.action == "source.move_organization")
        ).scalar_one()
        assert log.before["organization_id"] == old_id


def test_organization_is_left_alone_without_the_flag(session_factory, registry_dir):
    orgs = [_org("o-old", "옛 조직", "office"), _org("o-new", "새 조직")]
    registry_dir(orgs, [_source("s-1", "o-old")])
    ops.sources_sync(_args())
    with session_factory() as session:
        before = session.execute(select(m.Source)).scalar_one().organization_id

    registry_dir(orgs, [_source("s-1", "o-new")])
    ops.sources_sync(_args())

    with session_factory() as session:
        assert session.execute(select(m.Source)).scalar_one().organization_id == before


# ------------------------------------------------------------------ 식별자 안전장치


def test_reparenting_keeps_the_existing_row(session_factory, registry_dir):
    """이미 상위가 있던 조직의 상위를 바꿔도 행이 늘지 않는다."""
    first = [_org("o-college", "공과대학", "college"), _org("o-dept", "건축공학과", parent="o-college")]
    registry_dir(first, [_source("s-1", "o-dept")])
    ops.sources_sync(_args())

    with session_factory() as session:
        dept = session.execute(
            select(m.Organization).where(m.Organization.name == "건축공학과")
        ).scalar_one()
        before_id = dept.id
    assert _org_count(session_factory) == 2

    second = [
        _org("o-college", "공과대학", "college"),
        _org("o-alias", "건축공학", parent="o-college"),
        _org("o-dept", "건축공학과", parent="o-alias", alias_of="o-alias"),
    ]
    registry_dir(second, [_source("s-1", "o-dept")])
    ops.sources_sync(_args())

    assert _org_count(session_factory) == 3  # 별칭 부모 하나만 늘었다
    with session_factory() as session:
        dept = session.execute(
            select(m.Organization).where(m.Organization.name == "건축공학과")
        ).scalar_one()
        assert dept.id == before_id  # 식별자가 그대로다 = 공지가 고아가 되지 않는다
        assert dept.registry_key == "o-dept"
        alias_parent = session.execute(
            select(m.Organization).where(m.Organization.name == "건축공학")
        ).scalar_one()
        assert dept.parent_id == alias_parent.id
        source = session.execute(select(m.Source)).scalar_one()
        assert source.organization_id == before_id


def test_registry_key_survives_a_rename_of_the_parent(session_factory, registry_dir):
    """등록부 열쇠가 있으면 상위 이름이 바뀌어도 같은 행을 찾는다."""
    registry_dir(
        [_org("o-college", "공과대학", "college"), _org("o-dept", "건축공학과", parent="o-college")],
        [],
    )
    ops.sources_sync(_args())
    with session_factory() as session:
        before = {
            row.registry_key: row.id for row in session.execute(select(m.Organization)).scalars()
        }

    registry_dir(
        [_org("o-college", "공과대학 (국문)", "college"), _org("o-dept", "건축공학과", parent="o-college")],
        [],
    )
    ops.sources_sync(_args())

    assert _org_count(session_factory) == 2
    with session_factory() as session:
        after = {
            row.registry_key: row.id for row in session.execute(select(m.Organization)).scalars()
        }
    assert after == before


def test_same_name_row_is_never_duplicated(session_factory, registry_dir):
    """같은 이름의 조직 행을 새로 만들지 않는다."""
    registry_dir([_org("o-dept", "지리학과")], [])
    ops.sources_sync(_args())

    # 열쇠까지 바뀐 경우. 경로 해시도 flat 해시도 등록부 열쇠도 맞지 않는다.
    registry_dir(
        [_org("o-parent", "이과대학 지리학과"), _org("o-renamed", "지리학과", parent="o-parent", alias_of="o-parent")],
        [],
    )
    ops.sources_sync(_args())

    assert _org_count(session_factory) == 2
    with session_factory() as session:
        names = sorted(r.name for r in session.execute(select(m.Organization)).scalars())
    assert names == ["이과대학 지리학과", "지리학과"]


# ------------------------------------------------------------------ 별칭


def test_alias_flag_is_written_and_cleared(session_factory, registry_dir):
    orgs = [_org("o-parent", "건축공학"), _org("o-child", "건축공학과", parent="o-parent", alias_of="o-parent")]
    registry_dir(orgs, [])
    ops.sources_sync(_args())

    with session_factory() as session:
        rows = {r.name: r for r in session.execute(select(m.Organization)).scalars()}
        assert rows["건축공학과"].is_alias is True
        assert rows["건축공학"].is_alias is False
        assert rows["건축공학과"].parent_id == rows["건축공학"].id

    registry_dir([_org("o-parent", "건축공학"), _org("o-child", "건축공학과", parent="o-parent")], [])
    ops.sources_sync(_args())

    with session_factory() as session:
        rows = {r.name: r for r in session.execute(select(m.Organization)).scalars()}
        assert rows["건축공학과"].is_alias is False


def test_alias_child_stays_in_the_parent_subtree(session_factory, registry_dir):
    """별칭 자식의 공지가 부모를 골랐을 때 합산되도록 조상 관계가 만들어진다."""
    orgs = [
        _org("o-college", "공과대학", "college"),
        _org("o-parent", "건축공학", parent="o-college"),
        _org("o-child", "건축공학과", parent="o-parent", alias_of="o-parent"),
    ]
    registry_dir(orgs, [])
    ops.sources_sync(_args())

    with session_factory() as session:
        rows = {r.name: r.id for r in session.execute(select(m.Organization)).scalars()}
        ancestors = {
            r.ancestor_id
            for r in session.execute(
                select(m.OrganizationClosure).where(
                    m.OrganizationClosure.descendant_id == rows["건축공학과"]
                )
            ).scalars()
        }
    assert rows["공과대학"] in ancestors
    assert rows["건축공학"] in ancestors


def test_alias_of_requires_the_same_parent(tmp_path):
    base = _write(
        tmp_path / "bad",
        [
            _org("o-college", "공과대학", "college"),
            _org("o-parent", "건축공학"),
            _org("o-child", "건축공학과", parent="o-college", alias_of="o-parent"),
        ],
        [],
    )
    with pytest.raises(RegistryError):
        load_registry(base)


def test_alias_of_must_point_to_a_known_organization(tmp_path):
    base = _write(
        tmp_path / "bad2", [_org("o-child", "건축공학과", parent="o-ghost", alias_of="o-ghost")], []
    )
    with pytest.raises(RegistryError):
        load_registry(base)


def test_alias_parent_cannot_be_an_alias(tmp_path):
    base = _write(
        tmp_path / "bad3",
        [
            _org("o-top", "건축"),
            _org("o-mid", "건축공학", parent="o-top", alias_of="o-top"),
            _org("o-child", "건축공학과", parent="o-mid", alias_of="o-mid"),
        ],
        [],
    )
    with pytest.raises(RegistryError):
        load_registry(base)


# ------------------------------------------------------------------ 유형 갱신


def test_org_type_is_corrected_on_existing_rows(session_factory, registry_dir):
    registry_dir([_org("o-college", "문과대학", "office")], [])
    ops.sources_sync(_args())

    registry_dir([_org("o-college", "문과대학", "college")], [])
    ops.sources_sync(_args())

    assert _org_count(session_factory) == 1
    with session_factory() as session:
        row = session.execute(select(m.Organization)).scalar_one()
        assert row.org_type == "college"
        log = session.execute(
            select(m.AuditLog).where(m.AuditLog.action == "organization.retype")
        ).scalar_one()
        assert log.after["org_type"] == "college"


# ------------------------------------------------------------------ 이름 갱신


def test_name_is_updated_on_existing_rows(session_factory, registry_dir):
    """등록부에서 이름을 고치면 기존 행의 이름도 따라 바뀐다(행은 그대로)."""
    registry_dir([_org("o-dept", "전자공학")], [_source("s-1", "o-dept")])
    ops.sources_sync(_args())
    with session_factory() as session:
        before = session.execute(select(m.Organization)).scalar_one().id

    registry_dir([_org("o-dept", "전자공학부")], [_source("s-1", "o-dept")])
    ops.sources_sync(_args())

    assert _org_count(session_factory) == 1
    with session_factory() as session:
        row = session.execute(select(m.Organization)).scalar_one()
        assert row.id == before
        assert row.name == "전자공학부"
        log = session.execute(
            select(m.AuditLog).where(m.AuditLog.action == "organization.rename")
        ).scalar_one()
        assert log.before["name"] == "전자공학"
        assert log.after["name"] == "전자공학부"


def test_dry_run_lists_renames_without_applying_them(session_factory, registry_dir, capsys):
    registry_dir([_org("o-dept", "러시아어학")], [])
    ops.sources_sync(_args())
    capsys.readouterr()

    registry_dir([_org("o-dept", "러시아어학과")], [])
    ops.sources_sync(_args(dry_run=True))

    out = capsys.readouterr().out
    assert "이름 변경 1" in out
    assert "이름 변경: o-dept: 러시아어학 -> 러시아어학과" in out
    with session_factory() as session:
        assert session.execute(select(m.Organization)).scalar_one().name == "러시아어학"


def test_rename_leaves_contacts_attached(session_factory, registry_dir):
    """연락처는 조직 식별자로 붙어 있어 이름을 바꿔도 그대로 남는다."""
    registry_dir([_org("o-dept", "건축공학")], [])
    ops.sources_sync(_args())
    with session_factory() as session:
        oid = session.execute(select(m.Organization)).scalar_one().id
        session.add(
            m.ContactEntry(id="con-1", organization_id=oid, service_name="학과 사무실")
        )
        session.commit()

    registry_dir([_org("o-dept", "건축공학과")], [])
    ops.sources_sync(_args())

    with session_factory() as session:
        row = session.execute(select(m.ContactEntry)).scalar_one()
        assert row.organization_id == oid
        assert session.get(m.Organization, oid).name == "건축공학과"


def test_legacy_id_takes_over_an_old_row_instead_of_making_a_new_one(session_factory, registry_dir):
    """등록부에 없던 옛 행을 legacy_id 로 이어받는다. 새 행을 만들지 않는다."""
    registry_dir([_org("o-old", "[확인 필요] cs.khu.ac.kr", "institute")], [])
    ops.sources_sync(_args())
    with session_factory() as session:
        old_id = session.execute(select(m.Organization)).scalar_one().id
        session.execute(
            m.Organization.__table__.update().values(registry_key=None)
        )
        session.commit()

    registry_dir(
        [_org("o-new", "고객지원", "institute", legacy_id=old_id)],
        [],
    )
    ops.sources_sync(_args(dry_run=True))
    ops.sources_sync(_args())

    assert _org_count(session_factory) == 1
    with session_factory() as session:
        row = session.execute(select(m.Organization)).scalar_one()
        assert row.id == old_id
        assert row.name == "고객지원"
        assert row.registry_key == "o-new"


def test_old_name_is_kept_as_an_alias(session_factory, registry_dir):
    """이름을 고쳐도 옛 이름을 aliases 에 더한다(연락처 명부가 옛 이름으로 찾는다)."""
    registry_dir([_org("o-dept", "전자공학")], [])
    ops.sources_sync(_args())

    registry_dir([_org("o-dept", "전자공학부", aliases=["전자공학"])], [])
    ops.sources_sync(_args())

    with session_factory() as session:
        row = session.execute(select(m.Organization)).scalar_one()
        assert row.name == "전자공학부"
        assert "전자공학" in row.aliases


def test_existing_aliases_are_never_dropped(session_factory, registry_dir):
    registry_dir([_org("o-dept", "전자공학부", aliases=["전자공학"])], [])
    ops.sources_sync(_args())
    with session_factory() as session:
        row = session.execute(select(m.Organization)).scalar_one()
        row.aliases = list(row.aliases) + ["운영자가 손으로 넣은 별칭"]
        session.commit()

    registry_dir([_org("o-dept", "전자공학부", aliases=["전자공학"])], [])
    ops.sources_sync(_args())

    with session_factory() as session:
        row = session.execute(select(m.Organization)).scalar_one()
        assert sorted(row.aliases) == sorted(["전자공학", "운영자가 손으로 넣은 별칭"])


def test_source_name_follows_the_registry(session_factory, registry_dir, capsys):
    """출처 이름은 조직 이름 + 게시판 이름이라 등록부가 바뀌면 같이 바뀐다."""
    src = _source("s-1", "o-dept")
    src["name"] = "[확인 필요] example.khu.ac.kr 공지사항"
    registry_dir([_org("o-dept", "전자공학부")], [src])
    ops.sources_sync(_args())
    capsys.readouterr()

    src["name"] = "전자공학부 공지사항"
    registry_dir([_org("o-dept", "전자공학부")], [src])
    ops.sources_sync(_args(dry_run=True))
    out = capsys.readouterr().out
    assert "출처 이름 변경 1" in out
    with session_factory() as session:
        assert session.execute(select(m.Source)).scalar_one().name.startswith("[확인 필요]")

    ops.sources_sync(_args())
    with session_factory() as session:
        assert session.execute(select(m.Source)).scalar_one().name == "전자공학부 공지사항"
        actions = [a.action for a in session.execute(select(m.AuditLog)).scalars()]
        assert "source.rename" in actions
