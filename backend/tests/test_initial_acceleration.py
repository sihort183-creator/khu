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


def _seed_extra(session, index: int) -> str:
    """_seed 가 만든 조직에 출처를 하나 더 붙인다."""
    source_id = f"src-extra-{index:02d}"
    session.add(m.Source(
        id=source_id, organization_id="org-hq", name=f"추가 출처 {index}", adapter="khu_board",
        list_url=f"https://www.khu.ac.kr/kor/user/bbs/BMSR00040/list.do?menuNo={index}",
        status="active",
    ))
    session.flush()
    session.add(m.SourceConfigVersion(
        id=f"cfg-extra-{index:02d}", source_id=source_id, version=1,
        config={"base_url": "https://www.khu.ac.kr", "prefix": "kor", "board_code": "BMSR00040",
                "menu_no": str(index)},
        interval_minutes=60, is_active=True,
    ))
    session.add(m.SourceHealth(source_id=source_id))
    session.flush()
    return source_id


def test_tail_sources_are_not_started_with_an_unusable_time_slice(
    session_factory, settings, store, monkeypatch,
):
    """남은 예산이 한 조각도 안 될 때 새 출처를 착수하면 기아가 굳는다.

    조각으로 착수하면 저장은 0건인데 last_attempt_at 만 갱신되고, 대기열이
    last_attempt_at 오름차순이라 그 출처는 다음 회차에서도 같은 꼬리 자리에 놓인다.
    착수하지 않아야 위치를 지켜 다음 회차가 먼저 본다.
    """
    settings = replace(settings, run_budget_seconds=4, source_budget_seconds=2,
                       source_min_budget_seconds=1, source_concurrency=1)
    with session_factory() as session:
        _seed(session)
        for i in range(8):
            _seed_extra(session, i)
        session.commit()

    slices: list[tuple[str, float]] = []

    async def fake_collect(session, fetcher, store, due, *, budget, **kwargs):
        slices.append((due.source.id, budget.seconds))
        await asyncio.sleep(0.9)
        return collect.SourceOutcome(source_id=due.source.id, name=due.source.name, ok=True)

    monkeypatch.setattr(collect, "collect_source", fake_collect)
    monkeypatch.setattr(collect, "build_store", lambda cfg: store)
    monkeypatch.setattr(collect, "Fetcher", lambda cfg: Fetcher(cfg, transport=httpx.MockTransport(
        lambda request: httpx.Response(200, text="ok"))))
    monkeypatch.setattr("app.ingestion.http.check_url", lambda url, **kwargs: url)
    result = asyncio.run(collect.run_collection(settings, publish=False, dedupe=False))

    assert result.skipped > 0, "예산이 모자라 남은 출처가 있어야 이 검사가 의미가 있다"
    # 착수한 출처는 모두 쓸 수 있는 크기의 예산을 받았다.
    min_slice = max(1.0, min(float(settings.source_min_budget_seconds), settings.run_budget_seconds / 4))
    assert slices and all(sec >= min_slice for _, sec in slices), slices
    started = {sid for sid, _ in slices}
    with session_factory() as session:
        for health in session.scalars(select(m.SourceHealth)):
            if health.source_id in started:
                continue
            # 착수하지 않은 출처는 시도 기록이 남지 않아 다음 회차 대기열 맨 앞을 지킨다.
            assert health.last_attempt_at is None, health.source_id
