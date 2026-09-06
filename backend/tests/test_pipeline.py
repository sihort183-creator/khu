"""수집 → 저장 → 정적 파일까지의 종단 검사.

네트워크 없이 실제 원문 표본을 가짜 전송 계층으로 돌려준다(15.3절 자동 검사).
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import UTC, date, datetime

import httpx
import pytest
from sqlalchemy import select

from app.config import Settings
from app.contracts import models as api
from app.export.static import export_static
from app.ingestion.http import Fetcher
from app.run.collect import TimeBudget, collect_source, run_dedupe_pass
from app.storage import models as m
from app.storage import repository as repo

CONFIG = {
    "base_url": "https://www.khu.ac.kr",
    "prefix": "kor",
    "board_code": "BMSR00040",
    "menu_no": "200072",
}


def _read_export(store, bucket, key):
    """Worker의 개정별 합성 계약을 Python 계약 검사에서도 확인한다."""
    if not key.startswith("v1/r/"):
        return store.get_bytes(bucket, key)
    prefix, path = key.split("/", 3)[:3], key.split("/", 3)[3]
    manifest = json.loads(store.get_bytes(bucket, "/".join(prefix) + "/manifest.json"))
    if path not in manifest["entries"]:
        raise FileNotFoundError(key)
    payload = json.loads(store.get_bytes(bucket, manifest["entries"][path]))
    if "meta" in payload:
        payload["meta"] = {"request_id": "static-" + manifest["revision"], "generated_at": manifest["generated_at"]}
    if "page" in payload:
        payload["page"].update(snapshot_at=manifest["generated_at"], dataset_revision=manifest["revision"])
    for field in ("revision", "generated_at"):
        if field in payload:
            payload[field] = manifest[field]

    def restore(value):
        if isinstance(value, dict):
            if "freshness" in value and "primary_source" in value:
                value["freshness"] = manifest["freshness"][value["primary_source"]["id"]]
            for child in value.values():
                restore(child)
        elif isinstance(value, list):
            for child in value:
                restore(child)

    restore(payload)
    return json.dumps(payload).encode()


def _seed(session) -> m.Source:
    session.add(m.University(id="univ-khu", code="khu", name="경희대학교"))
    session.flush()
    session.add(m.Campus(id="campus-seoul", university_id="univ-khu", code="seoul", name="서울캠퍼스"))
    session.add(
        m.Organization(id="org-hq", university_id="univ-khu", org_type="office", name="대학본부")
    )
    session.flush()
    source = m.Source(
        id="src-hq",
        organization_id="org-hq",
        name="대학본부 예결산공고",
        adapter="khu_board",
        list_url="https://www.khu.ac.kr/kor/user/bbs/BMSR00040/list.do?menuNo=200072",
        # 운영자가 promote 한 상태를 흉내낸다. pending 은 자동 수집 대상이 아니다(5.2절).
        status="active",
    )
    session.add(source)
    session.flush()
    session.add(
        m.SourceConfigVersion(
            id="cfg-hq", source_id="src-hq", version=1, config=CONFIG, interval_minutes=60, is_active=True
        )
    )
    session.add(m.SourceHealth(source_id="src-hq"))
    session.add(
        m.SourceAudience(
            id="aud-hq", source_id="src-hq", audience_type="organization", organization_id="org-hq"
        )
    )
    session.flush()
    return source


def _transport(board_fixture, *, list_name="list_main_notice.html", detail_name="detail_main_notice.html"):
    list_html = board_fixture(list_name)
    detail_html = board_fixture(detail_name)

    def handler(request: httpx.Request) -> httpx.Response:
        headers = {"content-type": "text/html; charset=utf-8"}
        if request.url.path.endswith("/list.do"):
            return httpx.Response(200, text=list_html, headers=headers)
        if request.url.path.endswith("/view.do"):
            return httpx.Response(200, text=detail_html, headers=headers)
        return httpx.Response(404, text="not found", headers=headers)

    return httpx.MockTransport(handler)


async def _collect(settings, session, source, store, transport, budget=None) -> object:
    due = repo.DueSource(
        source=source,
        config=CONFIG,
        interval_minutes=60,
        health=session.get(m.SourceHealth, source.id),
        audience_defaults=repo._source_audiences(session, source.id),
    )
    async with Fetcher(settings, transport=transport) as fetcher:
        return await collect_source(
            session,
            fetcher,
            store,
            due,
            cfg=settings,
            budget=budget or TimeBudget(settings.run_budget_seconds),
            campus_map=repo.campus_lookup(session),
        )


@pytest.fixture
def seeded(session_factory, settings: Settings):
    with session_factory() as session:
        source = _seed(session)
        session.commit()
        yield session, source


def test_collect_creates_items_notices_and_evidence(seeded, settings, store, board_fixture):
    session, source = seeded
    outcome = asyncio.run(_collect(settings, session, source, store, _transport(board_fixture)))
    session.commit()

    assert outcome.ok is True
    assert outcome.new_items == 6
    assert session.execute(select(m.SourceItem)).scalars().all()
    notices = session.execute(select(m.Notice)).scalars().all()
    assert len(notices) == 6
    assert all(n.status == "visible" for n in notices)

    # 원문 증거가 비공개 버킷에 남는다.
    keys = store.list_keys(settings.r2.bucket_evidence, "raw/")
    assert len(keys) == 6

    # 성공해도 상태는 그대로 active 다.
    assert session.get(m.Source, source.id).status == "active"
    health = session.get(m.SourceHealth, source.id)
    assert health.consecutive_failures == 0
    assert health.last_list_success_at is not None


def test_second_run_adds_nothing_new(seeded, settings, store, board_fixture):
    """4절 13항: 같은 입력은 같은 유효 레코드로 수렴한다."""
    session, source = seeded
    asyncio.run(_collect(settings, session, source, store, _transport(board_fixture)))
    session.commit()
    second = asyncio.run(_collect(settings, session, source, store, _transport(board_fixture)))
    session.commit()

    assert second.new_items == 0
    assert second.updated_items == 0
    assert len(session.execute(select(m.Notice)).scalars().all()) == 6
    assert len(session.execute(select(m.SourceItemRevision)).scalars().all()) == 6


def test_access_denied_marks_source_blocked(seeded, settings, store):
    session, source = seeded

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="forbidden")

    outcome = asyncio.run(_collect(settings, session, source, store, httpx.MockTransport(handler)))
    session.commit()

    assert outcome.ok is False
    assert outcome.error_kind == "access_denied"
    assert session.get(m.Source, source.id).status == "blocked"


def test_server_error_does_not_delete_existing_notices(seeded, settings, store, board_fixture):
    """4절 9항: 원문 실패로 기존 공지를 지우지 않는다."""
    session, source = seeded
    asyncio.run(_collect(settings, session, source, store, _transport(board_fixture)))
    session.commit()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="server error")

    asyncio.run(_collect(settings, session, source, store, httpx.MockTransport(handler)))
    session.commit()

    visible = session.execute(select(m.Notice).where(m.Notice.status == "visible")).scalars().all()
    assert len(visible) == 6


def test_empty_board_is_success_not_failure(seeded, settings, store):
    session, source = seeded
    empty = '<html><body><div class="bbs-list"><div class="bbs-total">전체 0 건</div></div></body></html>'

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=empty, headers={"content-type": "text/html; charset=utf-8"})

    outcome = asyncio.run(_collect(settings, session, source, store, httpx.MockTransport(handler)))
    session.commit()

    assert outcome.ok is True
    assert outcome.new_items == 0
    assert session.get(m.SourceHealth, source.id).consecutive_failures == 0


def test_export_produces_contract_valid_files(seeded, settings, store, board_fixture):
    session, source = seeded
    asyncio.run(_collect(settings, session, source, store, _transport(board_fixture)))
    session.commit()
    run_dedupe_pass(session)
    session.commit()

    result = export_static(settings, run_id="run-test1234", store=store)

    assert result.notices == 6
    assert result.files_written >= 10

    def read(key: str) -> dict:
        return json.loads(_read_export(store, settings.r2.bucket_public, key).decode("utf-8"))

    pointer = api.LatestPointer.model_validate(read("v1/latest.json"))
    assert pointer.revision == result.revision
    assert pointer.notices_total == 6

    base = pointer.base_path
    page = api.NoticePageFile.model_validate(read(f"{base}/notices/page/1.json"))
    assert len(page.data) == 6
    assert page.page.dataset_revision == result.revision

    first = page.data[0]
    assert first.kind == "notice"
    assert first.original_url.startswith("https://")
    # 날짜만 있는 원문에 시각을 지어내지 않는다.
    assert first.published_precision == "date"
    assert first.published_at is None

    detail = api.ItemResponse[api.NoticeDetail].model_validate(read(f"{base}/notices/{first.id}.json"))
    assert detail.data.body_text
    assert len(detail.data.sources) >= 1

    catalog = api.ItemResponse[api.Catalog].model_validate(read(f"{base}/catalog.json"))
    assert any(c.code == "scholarship" for c in catalog.data.categories)

    sources_file = api.ListResponse[api.Source].model_validate(read(f"{base}/sources.json"))
    assert sources_file.data[0].status.code == "active"

    index = api.NoticeIndexFile.model_validate(read(f"{base}/notices/index.json"))
    assert index.count == 6
    assert all(entry.a for entry in index.entries)

    status = api.RunStatus.model_validate(read("v1/status.json"))
    assert status.sources_total == 1
    assert status.notices_total == 6


def test_hidden_notice_disappears_from_static_files(seeded, settings, store, board_fixture):
    """4절 14항: 숨긴 내용은 목록·상세·색인에서 함께 사라진다."""
    session, source = seeded
    asyncio.run(_collect(settings, session, source, store, _transport(board_fixture)))
    session.commit()

    notice = session.execute(select(m.Notice)).scalars().first()
    hidden_id = notice.id
    notice.status = "hidden"
    session.commit()

    result = export_static(settings, run_id="run-test5678", store=store)
    pointer = json.loads(_read_export(store, settings.r2.bucket_public, "v1/latest.json"))
    base = pointer["base_path"]
    page = json.loads(_read_export(store, settings.r2.bucket_public, f"{base}/notices/page/1.json"))
    index = json.loads(_read_export(store, settings.r2.bucket_public, f"{base}/notices/index.json"))

    assert result.notices == 5
    assert hidden_id not in [n["id"] for n in page["data"]]
    assert hidden_id not in [e["id"] for e in index["entries"]]
    with pytest.raises(FileNotFoundError):
        _read_export(store, settings.r2.bucket_public, f"{base}/notices/{hidden_id}.json")


def test_publish_window_hides_old_and_future_notices(seeded, settings, store, board_fixture):
    """수집 범위 시작일보다 오래된 글과 발행일이 미래인 글은 공개하지 않는다.

    저장은 그대로 두고 공개만 막는다. 경계는 표시 기준인 Asia/Seoul 날짜로 판정하므로
    3월 1일 오전 한국시간(= 2월 28일 UTC) 글은 남아야 한다.
    """
    session, source = seeded
    asyncio.run(_collect(settings, session, source, store, _transport(board_fixture)))
    session.commit()

    notices = session.execute(select(m.Notice).order_by(m.Notice.id)).scalars().all()
    assert len(notices) >= 3
    old_one, future_one, edge_one = notices[0], notices[1], notices[2]
    old_one.published_at = datetime(2025, 12, 31, 3, 0, tzinfo=UTC)
    future_one.published_at = datetime(2099, 12, 31, 0, 30, tzinfo=UTC)
    # 2026-02-28T15:00Z 는 한국시간으로 2026-03-01 00:00 이다. 경계 안쪽이다.
    edge_one.published_at = datetime(2026, 2, 28, 15, 0, tzinfo=UTC)
    session.commit()

    windowed = replace(settings, initial_window_start=date(2026, 3, 1))
    result = export_static(windowed, run_id="run-window01", store=store)
    pointer = json.loads(_read_export(store, settings.r2.bucket_public, "v1/latest.json"))
    base = pointer["base_path"]
    page = json.loads(_read_export(store, settings.r2.bucket_public, f"{base}/notices/page/1.json"))
    index = json.loads(_read_export(store, settings.r2.bucket_public, f"{base}/notices/index.json"))

    listed = {n["id"] for n in page["data"]}
    indexed = {e["id"] for e in index["entries"]}
    assert old_one.id not in listed and old_one.id not in indexed
    assert future_one.id not in listed and future_one.id not in indexed
    assert edge_one.id in listed and edge_one.id in indexed
    assert result.notices == len(notices) - 2

    # 상세 파일도 함께 사라진다. 목록에만 없고 주소로는 열리는 상태를 만들지 않는다.
    for hidden in (old_one.id, future_one.id):
        with pytest.raises(FileNotFoundError):
            _read_export(store, settings.r2.bucket_public, f"{base}/notices/{hidden}.json")

    # 원문은 지우지 않는다. 범위가 바뀌면 다시 공개할 수 있어야 한다.
    assert session.get(m.Notice, old_one.id) is not None


def test_revision_pointer_switches_only_after_files_exist(seeded, settings, store, board_fixture):
    """12절: 새 개정 파일을 모두 올린 뒤에 포인터를 바꾼다."""
    session, source = seeded
    asyncio.run(_collect(settings, session, source, store, _transport(board_fixture)))
    session.commit()

    result = export_static(settings, run_id="run-order", store=store)
    pointer = json.loads(_read_export(store, settings.r2.bucket_public, "v1/latest.json"))

    # 포인터가 가리키는 개정의 파일이 실제로 존재해야 한다.
    keys = store.list_keys(settings.r2.bucket_public, pointer["base_path"])
    assert keys
    assert pointer["revision"] == result.revision


def test_run_records_outcome(seeded, settings, store, board_fixture):
    session, source = seeded
    run = repo.start_run(session, code_commit="abc123")
    session.commit()

    outcome = asyncio.run(_collect(settings, session, source, store, _transport(board_fixture)))
    repo.record_source_run(
        session, run, source.id, succeeded=outcome.ok, list_items=outcome.list_items,
        new_items=outcome.new_items,
    )
    repo.finish_run(session, run, result="success", revision="r1")
    session.commit()

    stored = session.get(m.Run, run.id)
    assert stored.result == "success"
    assert stored.finished_at is not None
    assert stored.code_commit == "abc123"
    source_run = session.execute(select(m.SourceRun)).scalar_one()
    assert source_run.succeeded is True
    assert source_run.new_items == 6


def test_time_budget_stops_starting_new_work(settings):
    budget = TimeBudget(0)
    assert budget.exhausted is True
    assert budget.remaining() == 0.0


def test_outbox_event_written_with_notice(seeded, settings, store, board_fixture):
    session, source = seeded
    asyncio.run(_collect(settings, session, source, store, _transport(board_fixture)))
    session.commit()

    events = session.execute(select(m.OutboxEvent)).scalars().all()
    assert events
    assert all(e.event_type == "notice.changed" for e in events)
    assert all(e.processed_at is None for e in events)


def test_dedupe_pass_records_decisions_without_wrong_merges(seeded, settings, store, board_fixture):
    session, source = seeded
    asyncio.run(_collect(settings, session, source, store, _transport(board_fixture)))
    session.commit()

    merged = run_dedupe_pass(session)
    session.commit()

    # 같은 출처의 서로 다른 게시물은 자동 병합하지 않는다.
    assert merged == 0
    visible = session.execute(select(m.Notice).where(m.Notice.status == "visible")).scalars().all()
    assert len(visible) == 6


def test_generated_at_is_timezone_aware(seeded, settings, store, board_fixture):
    session, source = seeded
    asyncio.run(_collect(settings, session, source, store, _transport(board_fixture)))
    session.commit()
    export_static(settings, run_id="run-tz", store=store)

    payload = json.loads(_read_export(store, settings.r2.bucket_public, "v1/status.json"))
    parsed = datetime.fromisoformat(payload["generated_at"].replace("Z", "+00:00"))
    assert parsed.tzinfo is not None
    assert parsed.astimezone(UTC) <= datetime.now(UTC)


def test_search_shards_preserve_every_public_notice(seeded, settings, store, board_fixture, monkeypatch):
    from app.export import static

    session, source = seeded
    asyncio.run(_collect(settings, session, source, store, _transport(board_fixture)))
    session.commit()
    monkeypatch.setattr(static, "INDEX_SHARD_SIZE", 2)
    result = export_static(settings, run_id="run-shards", store=store)
    prefix = f"v1/r/{result.revision}"
    manifest = json.loads(_read_export(store, settings.r2.bucket_public, f"{prefix}/notices/index.json"))
    assert manifest["count"] == 6
    assert manifest["entries"] == []
    ids = []
    for shard in manifest["shards"]:
        payload = json.loads(_read_export(store, settings.r2.bucket_public, f"{prefix}/{shard['path']}"))
        assert payload["revision"] == result.revision
        assert payload["count"] == shard["count"]
        ids.extend(entry["id"] for entry in payload["entries"])
    expected = set(session.scalars(select(m.Notice.id).where(m.Notice.status == "visible")))
    assert set(ids) == expected
    assert len(ids) == len(expected)


def test_budget_cut_run_does_not_mark_items_missing(seeded, settings, store, board_fixture):
    """시간이 모자라 목록을 끝까지 못 본 회차는 없어진 글을 세지 않는다.

    안 본 글과 사라진 글을 구분할 수 없기 때문이다. 그대로 세면 멀쩡한 공지가
    연속 미발견으로 쌓여 삭제 표시된다(4절 9항).
    """
    db, source = seeded
    transport = _transport(board_fixture)

    # 먼저 정상으로 한 번 모아 둔다.
    asyncio.run(_collect(settings, db, source, store, transport))
    db.flush()
    stored = db.execute(select(m.SourceItem).where(m.SourceItem.source_id == source.id)).scalars().all()
    assert stored, "선행 수집이 항목을 남겨야 한다"
    before = {item.id: item.missing_streak for item in stored}

    # 예산이 0인 회차는 글을 하나도 보지 못한다.
    asyncio.run(_collect(settings, db, source, store, transport, budget=TimeBudget(0)))
    db.flush()

    after = db.execute(select(m.SourceItem).where(m.SourceItem.source_id == source.id)).scalars().all()
    for item in after:
        assert item.missing_streak == before[item.id], (
            "목록을 못 본 회차가 멀쩡한 항목을 없어진 것으로 세면 안 된다"
        )
        assert item.original_status == "available"


def test_unchanged_export_reuses_all_objects_and_refreshes_metadata(seeded, settings, store, board_fixture):
    session, source = seeded
    asyncio.run(_collect(settings, session, source, store, _transport(board_fixture)))
    session.commit()
    first = export_static(settings, store=store, revision="r100")
    old = json.loads(store.get_bytes(settings.r2.bucket_public, "v1/r/r100/manifest.json"))
    health = session.get(m.SourceHealth, source.id)
    health.last_list_success_at = datetime.now(UTC)
    session.commit()
    second = export_static(settings, store=store, revision="r101")
    new = json.loads(store.get_bytes(settings.r2.bucket_public, "v1/r/r101/manifest.json"))
    # 출처 상태 파일만 실제 데이터 변경. 상세/페이지의 신선도는 개정 명세에서 합성한다.
    for path, target in old["entries"].items():
        if path != "sources.json":
            assert new["entries"][path] == target
    assert second.files_reused >= first.notices
    assert second.files_written == 4  # 출처 객체, 명세, 상태, 최신 포인터
    third = export_static(settings, store=store, revision="r102")
    assert third.files_written == 3
    assert third.files_reused == len(new["entries"])
    page = json.loads(_read_export(store, settings.r2.bucket_public, "v1/r/r102/notices/page/1.json"))
    assert page["page"]["dataset_revision"] == "r102"
    assert page["data"][0]["freshness"]["last_checked_at"] != old["freshness"][source.id]["last_checked_at"]


def test_hidden_reference_removed_and_pruning_preserves_shared_objects(seeded, settings, store, board_fixture):
    from app.export.static import prune_old_revisions

    session, source = seeded
    asyncio.run(_collect(settings, session, source, store, _transport(board_fixture)))
    session.commit()
    export_static(settings, store=store, revision="r100")
    notice = session.scalars(select(m.Notice)).first()
    hidden_path = f"notices/{notice.id}.json"
    old = json.loads(store.get_bytes(settings.r2.bucket_public, "v1/r/r100/manifest.json"))
    notice.status = "hidden"
    session.commit()
    export_static(settings, store=store, revision="r101")
    new = json.loads(store.get_bytes(settings.r2.bucket_public, "v1/r/r101/manifest.json"))
    assert hidden_path not in new["entries"]
    with pytest.raises(FileNotFoundError):
        _read_export(store, settings.r2.bucket_public, "v1/r/r101/" + hidden_path)
    prune_old_revisions(settings, keep=1, store=store)
    for target in new["entries"].values():
        assert store.get_bytes(settings.r2.bucket_public, target)
    # 비공개 객체는 동시 내보내기의 재사용 보호를 위해 보존하고 공개 명세만 제거한다.
    assert store.get_bytes(settings.r2.bucket_public, old["entries"][hidden_path])
    with pytest.raises(FileNotFoundError):
        store.get_bytes(settings.r2.bucket_public, "v1/r/r100/manifest.json")


@pytest.mark.parametrize("fail_manifest", [False, True])
def test_failed_object_upload_does_not_switch_latest(seeded, settings, store, board_fixture, monkeypatch, fail_manifest):
    session, source = seeded
    asyncio.run(_collect(settings, session, source, store, _transport(board_fixture)))
    session.commit()
    export_static(settings, store=store, revision="r100")
    session.scalars(select(m.Notice)).first().title = "변경된 공지 제목"
    session.commit()
    original_put = store.put_bytes

    def failing_put(bucket, key, data, **kwargs):
        if key.endswith("/manifest.json") if fail_manifest else key.startswith("v1/objects/"):
            raise OSError("업로드 실패 재현")
        return original_put(bucket, key, data, **kwargs)

    monkeypatch.setattr(store, "put_bytes", failing_put)
    with pytest.raises(OSError):
        export_static(settings, store=store, revision="r101")
    assert json.loads(store.get_bytes(settings.r2.bucket_public, "v1/latest.json"))["revision"] == "r100"
    assert json.loads(store.get_bytes(settings.r2.bucket_public, "v1/status.json"))["revision"] == "r100"


def test_pruning_pins_latest_despite_newer_unpublished_revision(seeded, settings, store):
    from app.export.static import prune_old_revisions

    export_static(settings, store=store, revision="r100")
    store.put_bytes(settings.r2.bucket_public, "v1/r/r999/manifest.json", b'{"entries":{}}', content_type="application/json")
    prune_old_revisions(settings, keep=1, store=store)
    assert store.get_bytes(settings.r2.bucket_public, "v1/r/r100/manifest.json")
