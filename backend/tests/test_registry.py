"""출처 등록부 검사(5절).

실제 registry/bootstrap 파일이 규칙을 지키는지 확인한다.
잘못된 참조·중복·순환은 데이터베이스에 들어가기 전에 막는다.
"""

from __future__ import annotations

import json

import pytest

from app.ingestion import adapter_names
from app.ingestion.registry import BOOTSTRAP_DIR, RegistryError, load_registry


@pytest.fixture(scope="module")
def registry():
    return load_registry()


def test_bootstrap_files_load(registry):
    assert registry.campuses
    assert registry.organizations
    assert registry.sources


def test_campuses_cover_both_khu_campuses(registry):
    codes = {c.code for c in registry.campuses}
    assert codes == {"seoul", "global"}


def test_every_source_points_to_a_known_organization(registry):
    keys = {o.key for o in registry.organizations}
    for source in registry.sources:
        assert source.organization_key in keys


def test_every_source_uses_a_registered_adapter(registry):
    known = set(adapter_names())
    for source in registry.sources:
        assert source.adapter in known


def test_source_keys_and_urls_are_unique(registry):
    keys = [s.key for s in registry.sources]
    urls = [s.list_url for s in registry.sources]
    assert len(keys) == len(set(keys))
    assert len(urls) == len(set(urls))


def test_all_sources_are_khu_domains(registry):
    for source in registry.sources:
        assert ".khu.ac.kr" in source.config["base_url"]
        assert source.config["base_url"].startswith("https://")


def test_organization_paths_have_no_cycles(registry):
    for org in registry.organizations:
        path = registry.org_path(org.key)
        assert path[0] == "경희대학교"
        assert len(path) == len(set(path))


def test_sources_start_as_pending_not_active(registry):
    """5.2절: 검증 전 출처를 수집 중으로 표시하지 않는다."""
    assert all(s.status in ("pending", "active") for s in registry.sources)
    assert any(s.status == "pending" for s in registry.sources)


def test_audience_defaults_do_not_claim_university_wide(registry):
    """4절 4항: 본부 게시판이라고 전교생 대상으로 단정하지 않는다."""
    for source in registry.sources:
        for audience in source.audiences:
            assert audience.get("type") in ("organization", "campus", "undetermined")


def test_every_source_records_official_evidence(registry):
    for source in registry.sources:
        assert source.official_evidence_url
        assert source.official_evidence_url.startswith("https://")


def test_organizations_needing_review_are_marked(registry):
    """이름을 찾지 못한 조직은 지어내지 않고 표시만 남긴다."""
    doc = json.loads((BOOTSTRAP_DIR / "organizations.json").read_text(encoding="utf-8"))
    flagged = {o["key"] for o in doc["organizations"] if o.get("needs_review")}
    for org in doc["organizations"]:
        if org["key"] in flagged:
            assert org["name"].startswith("[확인 필요]")
        else:
            assert not org["name"].startswith("[확인 필요]")


def test_missing_organization_reference_is_rejected(tmp_path):
    (tmp_path / "organizations.json").write_text(
        json.dumps({"campuses": [{"code": "seoul", "name": "서울캠퍼스"}], "organizations": []}),
        encoding="utf-8",
    )
    (tmp_path / "sources.json").write_text(
        json.dumps(
            {
                "sources": [
                    {"key": "x", "name": "없는 조직", "organization": "org-none", "config": {}}
                ]
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(RegistryError):
        load_registry(tmp_path)


def test_duplicate_organization_key_is_rejected(tmp_path):
    (tmp_path / "organizations.json").write_text(
        json.dumps(
            {
                "campuses": [],
                "organizations": [
                    {"key": "a", "name": "A", "type": "office"},
                    {"key": "a", "name": "A2", "type": "office"},
                ],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "sources.json").write_text(json.dumps({"sources": []}), encoding="utf-8")
    with pytest.raises(RegistryError):
        load_registry(tmp_path)


def test_organization_cycle_is_rejected(tmp_path):
    (tmp_path / "organizations.json").write_text(
        json.dumps(
            {
                "campuses": [],
                "organizations": [
                    {"key": "a", "name": "A", "type": "office", "parent": "b"},
                    {"key": "b", "name": "B", "type": "office", "parent": "a"},
                ],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "sources.json").write_text(json.dumps({"sources": []}), encoding="utf-8")
    with pytest.raises(RegistryError):
        load_registry(tmp_path)
