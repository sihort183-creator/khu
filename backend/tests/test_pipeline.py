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
from app.domain.dates import KST
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

    원문에 시각이 없으면 파서는 시각을 지어내지 않고 날짜만 남긴다. 날짜를 아는 글은
    그 날짜로 판정한다. 정말로 발행일을 모르는 글만 공개하지 않는다.
    """
    session, source = seeded
    asyncio.run(_collect(settings, session, source, store, _transport(board_fixture)))
    session.commit()

    notices = session.execute(select(m.Notice).order_by(m.Notice.id)).scalars().all()
    assert len(notices) >= 5
    old_one, future_one, edge_one, undated_one, dated_one = notices[:5]
    old_one.published_at = datetime(2025, 12, 31, 3, 0, tzinfo=UTC)
    future_one.published_at = datetime(2099, 12, 31, 0, 30, tzinfo=UTC)
    # 2026-02-28T15:00Z 는 한국시간으로 2026-03-01 00:00 이다. 경계 안쪽이다.
    edge_one.published_at = datetime(2026, 2, 28, 15, 0, tzinfo=UTC)
    # 날짜도 시각도 모르면 범위 안이라고 볼 근거가 없다. 공개하지 않는다.
    undated_one.published_at = None
    undated_one.published_date = None
    # 시각만 모르고 날짜는 아는 글이다. 범위 안이므로 공개한다.
    dated_one.published_at = None
    dated_one.published_date = date(2026, 4, 15)
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
    assert undated_one.id not in listed and undated_one.id not in indexed
    assert edge_one.id in listed and edge_one.id in indexed
    assert dated_one.id in listed and dated_one.id in indexed
    # 표본의 나머지 글은 날짜가 제각각이다. 규칙대로 셈한 값과 맞는지 본다.
    today = datetime.now(UTC).astimezone(KST).date()

    def counted(n: m.Notice) -> bool:
        if n.published_at is not None:
            floor = datetime(2026, 2, 28, 15, 0, tzinfo=UTC)
            return floor <= n.published_at.replace(tzinfo=UTC) <= datetime.now(UTC)
        return n.published_date is not None and date(2026, 3, 1) <= n.published_date <= today

    expected = sum(1 for n in notices if counted(n))
    assert result.notices == expected

    # 상세 파일도 함께 사라진다. 목록에만 없고 주소로는 열리는 상태를 만들지 않는다.
    for hidden in (old_one.id, future_one.id, undated_one.id):
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


def test_list_is_newest_by_publish_date_not_by_when_we_crawled_it(seeded, settings, store, board_fixture):
    """화면의 "최신순"은 발행일 기준이다.

    백필은 과거 페이지를 나중에 긁는다. 우리가 처음 본 시각으로 세우면 오래된 공지가
    맨 위로 올라와 목록이 뒤죽박죽으로 보인다. 2026-09-07 사용자가 이 상태를 지적했다.
    """
    session, source = seeded
    asyncio.run(_collect(settings, session, source, store, _transport(board_fixture)))
    session.commit()

    notices = session.execute(select(m.Notice).order_by(m.Notice.id)).scalars().all()
    assert len(notices) >= 3
    old, middle, newest = notices[0], notices[1], notices[2]
    for notice in notices[3:]:
        notice.status = "hidden"
    # 처음 본 순서를 발행 순서와 정반대로 둔다. 발행일이 이겨야 한다.
    for index, (notice, day) in enumerate(
        ((old, date(2026, 3, 10)), (middle, date(2026, 6, 15)), (newest, date(2026, 9, 1)))
    ):
        notice.published_date = day
        notice.published_at = datetime(day.year, day.month, day.day, 1, 0, tzinfo=UTC)
        notice.first_visible_at = datetime(2026, 9, 7, 1 + index, 0, tzinfo=UTC)
    old.first_visible_at = datetime(2026, 9, 7, 9, 0, tzinfo=UTC)
    session.commit()

    windowed = replace(settings, initial_window_start=date(2026, 3, 1))
    export_static(windowed, run_id="run-order01", store=store)
    pointer = json.loads(_read_export(store, settings.r2.bucket_public, "v1/latest.json"))
    page = json.loads(_read_export(store, settings.r2.bucket_public, f"{pointer['base_path']}/notices/page/1.json"))

    listed = [n["id"] for n in page["data"]]
    assert listed == [newest.id, middle.id, old.id]


def _seed_reposts(session, *, bodies: list[str], titles: list[str]) -> None:
    """여러 학과 게시판이 같은 본부 공지를 각자 올린 상태를 만든다."""
    session.add(m.University(id="u-rep", code="khu2", name="경희대학교"))
    session.flush()
    session.add(m.Organization(id="o-rep", university_id="u-rep", org_type="office", name="본부"))
    session.flush()
    for index, (body, title) in enumerate(zip(bodies, titles, strict=True)):
        session.add(
            m.Source(
                id=f"src-rep-{index}",
                organization_id="o-rep",
                name=f"학과 게시판 {index}",
                adapter="khu_board",
                list_url=f"https://rep{index}.khu.ac.kr/list.do",
                status="active",
            )
        )
        session.flush()
        session.add(
            m.SourceItem(
                id=f"item-rep-{index}",
                source_id=f"src-rep-{index}",
                external_id=str(index),
                canonical_url=f"https://rep{index}.khu.ac.kr/view.do?id={index}",
                original_status="available",
            )
        )
        session.flush()
        session.add(
            m.SourceItemRevision(
                id=f"rev-rep-{index}",
                source_item_id=f"item-rep-{index}",
                content_hash=f"hash-rep-{index}",
                title=title,
                body_text=body,
                published_date=date(2026, 3, 20),
                extractor_version="test",
            )
        )
        session.flush()
        session.get(m.SourceItem, f"item-rep-{index}").current_revision_id = f"rev-rep-{index}"
        session.add(
            m.Notice(
                id=f"ntc-rep-{index}",
                primary_source_item_id=f"item-rep-{index}",
                status="visible",
                title=title,
                published_date=date(2026, 3, 20),
            )
        )
        session.flush()
        session.add(
            m.NoticeSource(
                id=f"ns-rep-{index}",
                notice_id=f"ntc-rep-{index}",
                source_item_id=f"item-rep-{index}",
                is_active=True,
                is_primary=True,
            )
        )
    session.flush()


BODY_SHARED = "2026학년도 1학기 우수연구자 추천장학 선발을 아래와 같이 안내합니다.\n" * 8


def _active_links(session, notice_id: str) -> int:
    return len(
        session.execute(
            select(m.NoticeSource).where(
                m.NoticeSource.notice_id == notice_id, m.NoticeSource.is_active.is_(True)
            )
        )
        .scalars()
        .all()
    )


def test_dedupe_never_merges_into_an_already_hidden_notice(session_factory):
    """이미 흡수된 공지를 대표로 삼으면 원본이 화면에서 사라진다.

    A→B 로 병합한 뒤 같은 회차가 (A, C) 를 다시 보면 A 는 이미 숨겨져 있다. 거기에
    C 를 넣으면 숨은 공지가 원본 세 개를 쥐고 목록에는 아무것도 남지 않는다.
    """
    with session_factory() as session:
        # 가운데 글만 두 쪽과 제목이 통하게 해서 전이 병합이 일어나게 둔다.
        _seed_reposts(
            session,
            bodies=[BODY_SHARED] * 3,
            titles=[
                "2026학년도 1학기 우수연구자 추천장학 선발 안내",
                "우수연구자 추천장학 선발 안내",
                "[대학원] 우수연구자 추천장학 선발 안내 (학과 공지)",
            ],
        )
        session.commit()

        run_dedupe_pass(session)
        session.commit()

        notices = session.execute(select(m.Notice).order_by(m.Notice.id)).scalars().all()
        visible = [n for n in notices if n.status == "visible"]
        assert len(visible) == 1, "같은 본문이면 하나로 묶여야 한다"
        assert _active_links(session, visible[0].id) == 3, "원본 세 개가 모두 대표에 붙어야 한다"
        for notice in notices:
            if notice.status != "visible":
                assert _active_links(session, notice.id) == 0, (
                    "숨은 공지가 활성 원본을 쥐고 있으면 그 원본은 어디에도 보이지 않는다"
                )


def test_dedupe_backfill_merges_beyond_the_recent_window(session_factory):
    """유지 수집의 최근 창 밖에 쌓인 글도 일괄 병합이 처리한다."""
    from app.run.collect import run_dedupe_backfill

    with session_factory() as session:
        _seed_reposts(
            session,
            bodies=[BODY_SHARED, BODY_SHARED, "전혀 다른 공지입니다. " * 20],
            titles=[
                "2026학년도 1학기 우수연구자 추천장학 선발 안내",
                "2026학년도 1학기 우수연구자 추천장학 선발 안내",
                "2026학년도 1학기 우수연구자 추천장학 선발 안내",
            ],
        )
        session.commit()

        # 최근 창을 두 건으로 좁혀도 창 밖의 글은 판정을 못 받는다.
        assert run_dedupe_pass(session, limit=1) == 0
        session.commit()

        stats = run_dedupe_backfill(session)
        session.commit()

        assert stats["merged"] == 1
        assert stats["scanned"] == 3
        assert stats["blocks"] == 1
        visible = session.execute(
            select(m.Notice).where(m.Notice.status == "visible")
        ).scalars().all()
        # 본문이 같은 두 건은 하나로, 제목만 같은 세 번째는 그대로 남는다.
        assert len(visible) == 2
        merged_notice = next(n for n in visible if _active_links(session, n.id) == 2)
        assert merged_notice is not None


def test_dedupe_backfill_keeps_same_title_different_body_apart(session_factory):
    """제목만 같고 본문이 다르면 합치지 않는다. 잘못 합치는 값이 더 크다(9절 1차 원칙)."""
    from app.run.collect import run_dedupe_backfill

    with session_factory() as session:
        _seed_reposts(
            session,
            bodies=[
                "학과별 안내입니다. 신청은 학과 사무실로 하십시오. " * 8,
                "본부 안내입니다. 신청은 포털에서 하십시오. " * 8,
            ],
            titles=["2026학년도 2학기 복학 신청 안내"] * 2,
        )
        session.commit()

        stats = run_dedupe_backfill(session)
        session.commit()

        assert stats["merged"] == 0
        assert stats["blocks"] == 0
        visible = session.execute(
            select(m.Notice).where(m.Notice.status == "visible")
        ).scalars().all()
        assert len(visible) == 2


def test_dedupe_backfill_skips_oversized_fingerprint_blocks(session_factory):
    """한 지문 묶음이 지나치게 크면 자동으로 다루지 않는다."""
    from app.run.collect import run_dedupe_backfill

    with session_factory() as session:
        _seed_reposts(session, bodies=[BODY_SHARED] * 3, titles=["같은 공지 안내"] * 3)
        session.commit()

        stats = run_dedupe_backfill(session, max_block=2)
        session.commit()

        assert stats["merged"] == 0
        assert stats["oversized_blocks"] == 1
        visible = session.execute(
            select(m.Notice).where(m.Notice.status == "visible")
        ).scalars().all()
        assert len(visible) == 3
