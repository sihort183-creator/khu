"""연락처 가져오기 검사(10절).

값이 같다는 이유로 다른 캠퍼스의 동명 행정실을 합치지 않아야 한다.
"""

from __future__ import annotations

import argparse
import json

from sqlalchemy import func, select

from app.domain import ids
from app.ops import cli as ops
from app.storage import models as m


def _doc(rows: list[dict]) -> dict:
    return {"summary": {}, "data": rows}


def _row(contact_id: str, org: str, service: str, campus: str, phone: str) -> dict:
    return {
        "id": contact_id,
        "kind": "contact",
        "organization": {"id": "x", "name": org, "type": {"code": "office", "label": "행정부서"}},
        "campuses": [{"id": f"campus-{campus}", "name": campus}],
        "service_name": service,
        "channels": [
            {
                "id": f"{contact_id}-p0",
                "kind": {"code": "phone", "label": "전화"},
                "display_value": phone,
                "value": phone,
            }
        ],
        "verification": {"code": "verified", "label": "원문 확인됨", "verified_at": "2026-09-05T16:59:09Z"},
        "evidence": [
            {"field": "channels", "url": "https://www.khu.ac.kr/x", "source_name": "공식 안내",
             "observed_at": "2026-09-05T16:59:09Z"}
        ],
    }


def test_same_service_on_two_campuses_stays_separate(session_factory, tmp_path):
    """10절: 서울·국제의 동명 행정실을 하나로 합치지 않는다."""
    path = tmp_path / "contacts.json"
    path.write_text(
        json.dumps(
            _doc(
                [
                    _row("kc-seoul01", "교무처", "교직팀", "서울캠퍼스", "02-961-0000"),
                    _row("kc-global01", "교무처", "교직팀", "국제캠퍼스", "031-201-0000"),
                ]
            ),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    ops.contacts_import(argparse.Namespace(path=str(path)))

    with session_factory() as session:
        assert session.execute(select(func.count(m.ContactEntry.id))).scalar() == 2
        assert session.get(m.ContactEntry, "kc-seoul01") is not None
        assert session.get(m.ContactEntry, "kc-global01") is not None


def test_reimport_updates_instead_of_duplicating(session_factory, tmp_path):
    path = tmp_path / "contacts.json"
    path.write_text(
        json.dumps(_doc([_row("kc-a", "교무처", "학사지원팀", "서울캠퍼스", "02-961-0000")]), ensure_ascii=False),
        encoding="utf-8",
    )
    ops.contacts_import(argparse.Namespace(path=str(path)))

    path.write_text(
        json.dumps(_doc([_row("kc-a", "교무처", "학사지원팀", "서울캠퍼스", "02-961-1111")]), ensure_ascii=False),
        encoding="utf-8",
    )
    ops.contacts_import(argparse.Namespace(path=str(path)))

    with session_factory() as session:
        assert session.execute(select(func.count(m.ContactEntry.id))).scalar() == 1
        channels = session.execute(select(m.ContactChannel)).scalars().all()
        assert len(channels) == 1
        assert channels[0].value == "02-961-1111"


def test_evidence_is_linked_to_each_contact(session_factory, tmp_path):
    path = tmp_path / "contacts.json"
    path.write_text(
        json.dumps(_doc([_row("kc-a", "교무처", "처장실", "서울캠퍼스", "02-961-0000")]), ensure_ascii=False),
        encoding="utf-8",
    )
    ops.contacts_import(argparse.Namespace(path=str(path)))

    with session_factory() as session:
        observation = session.execute(select(m.ContactObservation)).scalar_one()
        assert observation.url == "https://www.khu.ac.kr/x"
        evidence = session.execute(select(m.ContactFieldEvidence)).scalar_one()
        assert evidence.contact_id == "kc-a"


def test_contact_id_helper_separates_campuses():
    seoul = ids.contact_id("교무처", "교직팀", "서울캠퍼스")
    global_ = ids.contact_id("교무처", "교직팀", "국제캠퍼스")
    assert seoul != global_


def test_import_is_audited(session_factory, tmp_path):
    path = tmp_path / "contacts.json"
    path.write_text(json.dumps(_doc([])), encoding="utf-8")
    ops.contacts_import(argparse.Namespace(path=str(path)))

    with session_factory() as session:
        log = session.execute(
            select(m.AuditLog).where(m.AuditLog.action == "contacts.import")
        ).scalar_one()
        assert log.reason
