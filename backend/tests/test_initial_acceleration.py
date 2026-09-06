"""초기 연속 수집과 통신 제한·세션 격리의 재현 검사."""

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import httpx
from sqlalchemy import select
from test_pipeline import _seed, _transport

from app.ingestion.http import Fetcher
from app.run import collect
from app.storage import models as m
from app.storage import repository as repo


def test_initial_ignores_interval_but_respects_backoff_and_completion(session_factory):
    now = datetime.now(UTC)
    with session_factory() as session:
        source = _seed(session)
        health = session.get(m.SourceHealth, source.id)
        health.last_attempt_at = now
        session.flush()
        assert repo.load_due_sources(session, now=now) == []
        assert len(repo.load_due_sources(session, now=now, initial_mode=True)) == 1
        health.next_attempt_after = now + timedelta(minutes=10)
        assert repo.load_due_sources(session, now=now, initial_mode=True) == []
        health.next_attempt_after = None
        health.backfill_complete = True
        health.initial_window_start = date(2026, 3, 1)
        assert repo.load_due_sources(session, now=now, initial_mode=True) == []
        assert len(repo.load_due_sources(
            session, now=now, initial_mode=True, initial_window_start=date(2026, 2, 1),
        )) == 1


def test_collection_without_publish_preserves_public_pointer(
    session_factory, settings, store, board_fixture, monkeypatch,
):
    with session_factory() as session:
        source = _seed(session)
        source_id = source.id
        session.get(m.SourceHealth, source_id).last_attempt_at = datetime.now(UTC)
        session.commit()
    store.put_bytes(settings.r2.bucket_public, "v1/latest.json", b'{"revision":"existing"}', content_type="application/json")
    monkeypatch.setattr(collect, "build_store", lambda cfg: store)
    monkeypatch.setattr(collect, "Fetcher", lambda cfg: Fetcher(cfg, transport=_transport(board_fixture)))
    monkeypatch.setattr("app.ingestion.http.check_url", lambda url, **kwargs: url)

    def forbidden(*args, **kwargs):
        raise AssertionError("수집 전용 실행은 공개와 전체 중복 비교를 호출하지 않는다")

    monkeypatch.setattr(collect, "export_static", forbidden)
    monkeypatch.setattr(collect, "refresh_public_status", forbidden)
    monkeypatch.setattr(collect, "run_dedupe_pass", forbidden)
    result = asyncio.run(collect.run_collection(
        settings, source_keys=[source_id], initial_mode=True, publish=False, dedupe=False,
    ))
    assert result.attempted == 1
    assert result.new_items == 6
    assert result.revision is None and result.export is None
    assert store.get_bytes(settings.r2.bucket_public, "v1/latest.json") == b'{"revision":"existing"}'
    with session_factory() as session:
        run = session.get(m.Run, result.run_id)
        assert run.finished_at is not None and run.items_new == 6
        assert len(list(session.scalars(select(m.Notice)))) == 6


def test_shared_fetcher_keeps_one_host_limit_across_source_threads(settings, monkeypatch):
    monkeypatch.setattr("app.ingestion.http.check_url", lambda url, **kwargs: url)
    main_thread = threading.get_ident()
    active = peak = 0
    request_threads = set()

    async def handler(request):
        nonlocal active, peak
        request_threads.add(threading.get_ident())
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.02)
        active -= 1
        return httpx.Response(200, text="ok")

    async def run():
        async with Fetcher(replace(settings, http_concurrency=4), transport=httpx.MockTransport(handler)) as fetcher:
            proxy = collect.SharedFetcherProxy(fetcher, asyncio.get_running_loop())

            def source_job():
                assert threading.get_ident() != main_thread
                return asyncio.run(proxy.get("https://www.khu.ac.kr/test"))

            responses = await asyncio.gather(*(asyncio.to_thread(source_job) for _ in range(4)))
            assert len(responses) == 4
            assert fetcher.requests_made == 4

    asyncio.run(run())
    assert peak == 1
    assert request_threads == {main_thread}


def test_source_database_session_is_created_and_used_on_worker_thread(
    session_factory, settings, store, monkeypatch,
):
    with session_factory() as session:
        _seed(session)
        session.commit()
    main_thread = threading.get_ident()
    observed = []
    original_scope = collect.session_scope

    async def fake_collect(session, fetcher, store, due, **kwargs):
        observed.append(threading.get_ident())
        assert threading.get_ident() != main_thread
        assert session.get(m.Source, due.source.id) is due.source
        return collect.SourceOutcome(source_id=due.source.id, name=due.source.name, ok=True)

    monkeypatch.setattr(collect, "collect_source", fake_collect)
    monkeypatch.setattr(collect, "build_store", lambda cfg: store)
    result = asyncio.run(collect.run_collection(settings, publish=False, dedupe=False))
    assert result.succeeded == 1 and len(observed) == 1
    assert collect.session_scope is original_scope


def test_source_pool_cannot_starve_http_default_executor(session_factory, settings, store, monkeypatch):
    with session_factory() as session:
        _seed(session)
        session.commit()

    class AsyncResolverFetcher:
        requests_made = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, *args, **kwargs):
            # HTTP 루프의 DNS처럼 기본 executor가 반드시 필요하다.
            return await asyncio.to_thread(lambda: "resolved")

    async def fake_collect(session, fetcher, store, due, **kwargs):
        assert await fetcher.get("https://www.khu.ac.kr") == "resolved"
        return collect.SourceOutcome(source_id=due.source.id, name=due.source.name, ok=True)

    monkeypatch.setattr(collect, "Fetcher", lambda cfg: AsyncResolverFetcher())
    monkeypatch.setattr(collect, "collect_source", fake_collect)
    monkeypatch.setattr(collect, "build_store", lambda cfg: store)

    async def run():
        asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(max_workers=1))
        return await asyncio.wait_for(collect.run_collection(settings, publish=False, dedupe=False), timeout=5)

    assert asyncio.run(run()).succeeded == 1
