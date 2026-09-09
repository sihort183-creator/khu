"""공개 파일을 증분으로 만드는 길의 검사(docs/공개파일_증분화_2026-09-09.md).

여기서 지키려는 것은 하나다. **증분으로 만든 결과는 전체로 만든 결과와 같아야 한다.**
비교 기준은 개정 명세(manifest)의 값이다. 그 값은 곧 내용 해시라, 두 명세가 같다는
것은 만들어진 파일이 바이트 단위로 같다는 뜻이다.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.config import Settings
from app.domain.dates import KST
from app.export import state as state_mod
from app.export.state import (
    ExportState,
    NoticeState,
    needs_daily_full,
    plan_export,
    state_key,
)
from app.export.static import BASE, export_static
from app.storage import models as m

BASE_TIME = datetime(2026, 6, 1, 3, 0, tzinfo=UTC)


def _seed(session, *, count: int = 8) -> None:
    session.add(m.University(id="univ-khu", code="khu", name="경희대학교"))
    session.flush()
    session.add(m.Campus(id="campus-seoul", university_id="univ-khu", code="seoul", name="서울캠퍼스"))
    session.add(
        m.Organization(id="org-hq", university_id="univ-khu", org_type="office", name="대학본부")
    )
    session.flush()
    session.add(
        m.Source(
            id="src-hq",
            organization_id="org-hq",
            name="대학본부 공지",
            adapter="khu_board",
            list_url="https://www.khu.ac.kr/list.do",
            status="active",
        )
    )
    session.add(m.SourceHealth(source_id="src-hq", last_list_success_at=BASE_TIME))
    session.flush()
    for index in range(count):
        item_id = f"itm-{index:03d}"
        revision_id = f"rev-{index:03d}"
        notice_id = f"ntc-{index:03d}"
        session.add(
            m.SourceItem(
                id=item_id,
                source_id="src-hq",
                external_id=str(index),
                canonical_url=f"https://www.khu.ac.kr/view.do?id={index}",
                current_revision_id=revision_id,
                original_status="available",
            )
        )
        session.flush()
        session.add(
            m.SourceItemRevision(
                id=revision_id,
                source_item_id=item_id,
                content_hash=f"hash{index:03d}",
                title=f"공지 {index}",
                body_text=f"본문 {index} " * 20,
                body_html=f"<p>본문 {index}</p>",
                published_date=(BASE_TIME - timedelta(days=index)).date(),
                published_precision="date",
                extractor_version="test",
            )
        )
        session.flush()
        session.add(
            m.Notice(
                id=notice_id,
                primary_source_item_id=item_id,
                status="visible",
                first_visible_at=BASE_TIME - timedelta(days=index),
                updated_at=BASE_TIME - timedelta(days=index),
                title=f"공지 {index}",
                excerpt=f"발췌 {index}",
                published_date=(BASE_TIME - timedelta(days=index)).date(),
                published_precision="date",
            )
        )
        session.add(
            m.NoticeSource(
                id=f"lnk-{index:03d}",
                notice_id=notice_id,
                source_item_id=item_id,
                is_active=True,
                is_primary=True,
            )
        )
        session.add(
            m.NoticeCategory(
                id=f"cat-{index:03d}", notice_id=notice_id, category_code="other", is_primary=True
            )
        )
        session.add(
            m.NoticeAudience(
                id=f"aud-{index:03d}",
                notice_id=notice_id,
                audience_type="organization",
                organization_id="org-hq",
            )
        )
        session.flush()
    session.commit()


@pytest.fixture
def seeded(session_factory, settings: Settings):
    with session_factory() as session:
        _seed(session)
        yield session


@pytest.fixture
def cfg(settings: Settings) -> Settings:
    # 검사용 표본은 8건이라 한 건만 바뀌어도 12.5% 다. 임계 판정은 따로 검사한다.
    return replace(settings, export_max_changed_ratio=0.95)


def _manifest(store, cfg: Settings, revision: str) -> dict:
    raw = store.get_bytes(cfg.r2.bucket_public, f"{BASE}/r/{revision}/manifest.json")
    return json.loads(raw)


def _entries(store, cfg: Settings, revision: str) -> dict[str, str]:
    return _manifest(store, cfg, revision)["entries"]


# ------------------------------------------------------------- 계획(무엇이 바뀌었나)


def _state(**over) -> ExportState:
    base = ExportState(
        revision="r1",
        generated_at="2026-06-01T03:00:00Z",
        fingerprint="fp",
        mode="full",
        last_full_at="2026-06-01T03:00:00Z",
        notices={
            "a": NoticeState("2026-06-01T00:00:00Z", "ra", {"published_adjusted": False}),
            "b": NoticeState("2026-06-01T00:00:00Z", "rb", {"published_adjusted": False}),
        },
        excluded={"c": NoticeState("2026-06-01T00:00:00Z", "rc")},
    )
    for key, value in over.items():
        setattr(base, key, value)
    return base


_MISSING = object()


def _plan(live, *, state=_MISSING, entries=_MISSING, objects=_MISSING, ratio=0.95,
          mode="incremental", now=None):
    return plan_export(
        requested_mode=mode,
        live=live,
        state=_state() if state is _MISSING else state,
        manifest_entries={
            "notices/a.json": "v1/objects/aa.json", "notices/b.json": "v1/objects/bb.json",
        } if entries is _MISSING else entries,
        existing_objects={
            "v1/objects/aa.json", "v1/objects/bb.json",
        } if objects is _MISSING else objects,
        fingerprint="fp",
        now=now or datetime(2026, 6, 1, 4, 0, tzinfo=UTC),
        kst=KST,
        max_changed_ratio=ratio,
        full_every_hours=24,
        full_hour_kst=4,
    )


def test_plan_splits_new_changed_and_dropped():
    stamp = datetime(2026, 6, 1, 0, 0, tzinfo=UTC)
    plan = _plan(
        [
            ("a", stamp, "ra"),           # 그대로
            ("b", stamp + timedelta(minutes=1), "rb"),  # 수정 시각이 달라졌다
            ("c", stamp, "rc"),           # 지난번에도 범위 밖이었다
            ("d", stamp, "rd"),           # 처음 보는 공지
        ]
    )
    assert plan.mode == "incremental"
    assert plan.reused == {"a"}
    assert plan.changed == {"b", "d"}
    assert plan.excluded_kept == {"c"}
    assert plan.added == 1
    assert plan.dropped == []


def test_plan_marks_notice_changed_when_revision_moved():
    stamp = datetime(2026, 6, 1, 0, 0, tzinfo=UTC)
    plan = _plan([("a", stamp, "ra-new"), ("b", stamp, "rb")])
    assert plan.changed == {"a"}
    assert plan.reused == {"b"}


def test_plan_reports_notices_that_disappeared():
    stamp = datetime(2026, 6, 1, 0, 0, tzinfo=UTC)
    plan = _plan([("a", stamp, "ra")])
    assert plan.dropped == ["b", "c"]


def test_plan_rebuilds_notices_whose_shown_date_moves_on_its_own():
    """원문 날짜가 미래인 공지는 시간이 지나면 보이는 날짜가 저절로 달라진다."""
    stamp = datetime(2026, 6, 1, 0, 0, tzinfo=UTC)
    state = _state()
    state.notices["a"] = NoticeState("2026-06-01T00:00:00Z", "ra", {"published_adjusted": True})
    plan = _plan([("a", stamp, "ra"), ("b", stamp, "rb")], state=state)
    assert plan.changed == {"a"}


# ------------------------------------------------------------- 자동 후퇴


def test_falls_back_to_full_without_previous_state():
    plan = _plan([("a", None, "ra")], state=None)
    assert plan.mode == "full"
    assert "이전 판" in plan.reason


def test_falls_back_to_full_when_shared_information_changed():
    state = _state(fingerprint="다른값")
    plan = _plan([("a", None, "ra")], state=state)
    assert plan.mode == "full"
    assert "공유 정보" in plan.reason


def test_falls_back_to_full_without_previous_manifest():
    plan = _plan([("a", None, "ra")], entries=None)
    assert plan.mode == "full"


def test_falls_back_to_full_when_too_many_notices_changed():
    stamp = datetime(2026, 6, 1, 0, 0, tzinfo=UTC)
    plan = _plan([("a", stamp + timedelta(minutes=1), "ra"), ("b", stamp, "rb")], ratio=0.3)
    assert plan.mode == "full"
    assert "임계" in plan.reason


def test_missing_content_object_forces_that_notice_to_be_rebuilt():
    stamp = datetime(2026, 6, 1, 0, 0, tzinfo=UTC)
    plan = _plan(
        [("a", stamp, "ra"), ("b", stamp, "rb")],
        objects={"v1/objects/bb.json"},  # a 의 내용 객체가 사라졌다
    )
    assert plan.changed == {"a"}
    assert plan.reused == {"b"}


def test_missing_manifest_entry_forces_that_notice_to_be_rebuilt():
    stamp = datetime(2026, 6, 1, 0, 0, tzinfo=UTC)
    plan = _plan([("a", stamp, "ra"), ("b", stamp, "rb")], entries={"notices/b.json": "v1/objects/bb.json"})
    assert plan.changed == {"a"}


def test_requesting_full_never_plans_incremental():
    plan = _plan([("a", None, "ra")], mode="full")
    assert plan.mode == "full"
    assert plan.reason == "전체 방식 요청"


# ------------------------------------------------------------- 하루 한 번 전체 재생성


def test_daily_full_is_due_on_the_first_run_after_the_chosen_hour():
    # 한국 시간 2026-06-02 04:10 (UTC 6-01 19:10). 직전 전체 재생성은 하루 전이다.
    now = datetime(2026, 6, 1, 19, 10, tzinfo=UTC)
    last = datetime(2026, 5, 31, 19, 10, tzinfo=UTC)
    assert needs_daily_full(now=now, last_full_at=last, hour_kst=4, every_hours=24, kst=KST)


def test_daily_full_is_not_due_before_the_chosen_hour():
    # 한국 시간 2026-06-02 02:10 — 아직 4시 전이고, 마지막 전체 재생성은 21시간 전이다.
    now = datetime(2026, 6, 1, 17, 10, tzinfo=UTC)
    last = datetime(2026, 5, 31, 20, 10, tzinfo=UTC)
    assert needs_daily_full(now=now, last_full_at=last, hour_kst=4, every_hours=24, kst=KST) is None


def test_daily_full_is_due_when_the_last_one_is_far_behind():
    now = datetime(2026, 6, 1, 17, 10, tzinfo=UTC)
    last = now - timedelta(hours=40)
    assert needs_daily_full(now=now, last_full_at=last, hour_kst=4, every_hours=24, kst=KST)


def test_daily_full_is_due_without_any_record():
    now = datetime(2026, 6, 1, 17, 10, tzinfo=UTC)
    assert needs_daily_full(now=now, last_full_at=None, hour_kst=4, every_hours=24, kst=KST)


# ------------------------------------------------------------- 종단: 같은 결과가 나오는가


def test_incremental_run_carries_details_and_matches_a_full_rebuild(seeded, cfg, store):
    first = export_static(cfg, store=store, revision="r-full-1", mode="full")
    assert first.mode == "full"
    assert first.details_carried == 0
    assert first.notices == 8

    second = export_static(cfg, store=store, revision="r-inc-1", mode="incremental")
    assert second.mode == "incremental"
    assert second.notices == 8
    assert second.notices_changed == 0
    assert second.details_carried == 8
    # 본문을 하나도 읽지 않았으니 읽기량이 전체 방식보다 확실히 적다.
    assert second.db_read_bytes < first.db_read_bytes

    third = export_static(cfg, store=store, revision="r-full-2", mode="full")
    assert third.mode == "full"

    incremental = _entries(store, cfg, "r-inc-1")
    rebuilt = _entries(store, cfg, "r-full-2")
    assert incremental == rebuilt


def test_incremental_rebuilds_only_the_notice_that_changed(seeded, cfg, store, session_factory):
    export_static(cfg, store=store, revision="r-full-1", mode="full")
    before = _entries(store, cfg, "r-full-1")

    with session_factory() as session:
        notice = session.get(m.Notice, "ntc-003")
        notice.title = "고쳐 쓴 제목"
        notice.updated_at = BASE_TIME + timedelta(hours=1)
        session.commit()

    result = export_static(cfg, store=store, revision="r-inc-1", mode="incremental")
    assert result.mode == "incremental"
    assert result.notices_changed == 1
    assert result.details_carried == 7

    after = _entries(store, cfg, "r-inc-1")
    assert after["notices/ntc-003.json"] != before["notices/ntc-003.json"]
    assert after["notices/ntc-004.json"] == before["notices/ntc-004.json"]

    # 전체로 다시 만들어도 같은 결과여야 한다.
    export_static(cfg, store=store, revision="r-full-2", mode="full")
    assert after == _entries(store, cfg, "r-full-2")


def test_incremental_picks_up_new_and_hidden_notices(seeded, cfg, store, session_factory):
    export_static(cfg, store=store, revision="r-full-1", mode="full")

    with session_factory() as session:
        session.add(
            m.SourceItem(
                id="itm-900", source_id="src-hq", external_id="900",
                canonical_url="https://www.khu.ac.kr/view.do?id=900",
                current_revision_id="rev-900", original_status="available",
            )
        )
        session.add(
            m.SourceItemRevision(
                id="rev-900", source_item_id="itm-900", content_hash="hash900", title="새 공지",
                body_text="새 본문", body_html="<p>새 본문</p>",
                published_date=BASE_TIME.date(), published_precision="date",
                extractor_version="test",
            )
        )
        session.flush()
        session.add(
            m.Notice(
                id="ntc-900", primary_source_item_id="itm-900", status="visible",
                first_visible_at=BASE_TIME, updated_at=BASE_TIME, title="새 공지",
                excerpt="새 발췌", published_date=BASE_TIME.date(), published_precision="date",
            )
        )
        session.add(
            m.NoticeCategory(id="cat-900", notice_id="ntc-900", category_code="other", is_primary=True)
        )
        session.get(m.Notice, "ntc-005").status = "hidden"
        session.commit()

    result = export_static(cfg, store=store, revision="r-inc-1", mode="incremental")
    assert result.mode == "incremental"
    assert result.notices == 8  # 하나 늘고 하나 숨었다
    assert result.notices_changed == 1
    entries = _entries(store, cfg, "r-inc-1")
    assert "notices/ntc-900.json" in entries
    assert "notices/ntc-005.json" not in entries


def test_incremental_keeps_out_of_window_notices_out_without_reading_them(
    session_factory, settings, store
):
    """공개 범위 밖 공지는 기록에 남겨 두 번 읽지 않는다."""
    with session_factory() as session:
        _seed(session, count=4)
        old = session.get(m.Notice, "ntc-003")
        old.published_date = datetime(2020, 1, 1, tzinfo=UTC).date()
        session.commit()

    windowed = replace(
        settings, initial_window_start=datetime(2026, 1, 1, tzinfo=UTC).date(),
        export_max_changed_ratio=0.95,
    )
    first = export_static(windowed, store=store, revision="r-full-1", mode="full")
    assert first.notices == 3

    state = state_mod.ExportState.from_json(
        store.get_bytes(windowed.r2.bucket_public, state_key("r-full-1"))
    )
    assert "ntc-003" in state.excluded

    second = export_static(windowed, store=store, revision="r-inc-1", mode="incremental")
    assert second.mode == "incremental"
    assert second.notices == 3
    assert second.notices_changed == 0
    assert _entries(store, windowed, "r-inc-1") == _entries(store, windowed, "r-full-1")


def test_shadow_mode_publishes_full_and_records_a_matching_comparison(seeded, cfg, store):
    export_static(cfg, store=store, revision="r-full-1", mode="full")
    result = export_static(cfg, store=store, revision="r-shadow-1", mode="shadow")

    assert result.mode == "full"  # 공개되는 것은 전체 방식의 결과다
    assert result.shadow is not None
    assert result.shadow["ran"] is True
    assert result.shadow["matched"] is True
    assert result.shadow["mismatched"] == 0
    assert result.shadow["compared"] > 0
    assert result.shadow["reused"] == 8
    # 그림자 회차는 한 바이트도 올리지 않는다: 개정 경로는 하나뿐이다.
    revisions = {
        key.split("/")[2]
        for key in store.list_keys(cfg.r2.bucket_public, f"{BASE}/r/")
    }
    assert "r-shadow-1-shadow" not in revisions


def test_shadow_mode_reports_a_mismatch_when_the_previous_edition_went_stale(
    seeded, cfg, store, session_factory, monkeypatch
):
    """수정 시각이 오르지 않은 채 내용만 바뀌면 그림자 대조가 잡아낸다.

    데이터베이스 트리거(마이그레이션 0004)가 막으려는 바로 그 상황이다.
    """
    export_static(cfg, store=store, revision="r-full-1", mode="full")

    with session_factory() as session:
        # 수정 시각은 그대로 두고 본문만 바꾼다.
        session.get(m.SourceItemRevision, "rev-002").body_html = "<p>몰래 바뀐 본문</p>"
        session.get(m.Notice, "ntc-002").title = "몰래 바뀐 제목"
        session.commit()

    result = export_static(cfg, store=store, revision="r-shadow-1", mode="shadow")
    assert result.shadow["ran"] is True
    assert result.shadow["matched"] is False
    assert result.shadow["mismatched"] >= 1
    assert any("ntc-002" in path for path in result.shadow["examples"])


def test_forced_daily_full_also_runs_the_comparison(seeded, cfg, store, monkeypatch):
    """하루 한 번 전체 재생성 회차는 그 자체가 증분·전체 대조가 된다."""
    export_static(cfg, store=store, revision="r-full-1", mode="full")

    # 마지막 전체 재생성이 이틀 전인 것처럼 기록을 고친다.
    raw = store.get_bytes(cfg.r2.bucket_public, state_key("r-full-1"))
    payload = json.loads(raw)
    payload["last_full_at"] = "2026-05-01T00:00:00Z"
    store.put_bytes(
        cfg.r2.bucket_public, state_key("r-full-1"),
        json.dumps(payload, ensure_ascii=False).encode(),
        content_type="application/json; charset=utf-8",
    )

    result = export_static(cfg, store=store, revision="r-inc-1", mode="incremental")
    assert result.mode == "full"
    assert "전체 재생성" in result.mode_reason
    assert result.shadow is not None and result.shadow["matched"] is True


def test_unknown_mode_falls_back_to_full(seeded, cfg, store):
    result = export_static(cfg, store=store, revision="r-x", mode="이상한값")
    assert result.requested_mode == "full"
    assert result.mode == "full"


def test_export_metrics_are_recorded_for_the_self_check(seeded, cfg, store):
    from app.export.static import read_export_metrics

    export_static(cfg, store=store, revision="r-full-1", mode="full")
    metrics = read_export_metrics(cfg, store=store)
    assert metrics["mode"] == "full"
    assert metrics["revision"] == "r-full-1"
    assert metrics["db_read_bytes"] > 0


def test_old_state_files_are_pruned(store, settings):
    """회차마다 4 MB 남짓을 올리므로 최근 몇 개만 남긴다."""
    from app.export.static import METRICS_KEY, prune_export_state

    bucket = settings.r2.bucket_public
    for name in ("r1", "r2", "r3", "r4", "r5"):
        store.put_bytes(
            bucket, state_key(name), b"{}", content_type="application/json; charset=utf-8"
        )
    store.put_bytes(bucket, METRICS_KEY, b"{}", content_type="application/json; charset=utf-8")

    assert prune_export_state(store, bucket, keep=3) == 2
    left = set(store.list_keys(bucket, "v1/state/"))
    assert left == {state_key("r3"), state_key("r4"), state_key("r5"), METRICS_KEY}
    # 회차 기록은 정리 대상이 아니다.
    assert prune_export_state(store, bucket, keep=3) == 0
