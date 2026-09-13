"""회차가 만든 WAL·임시 파일 계측의 검사(docs/디스크IO_줄이기_2026-09-13.md).

2026-09-13 Supabase 가 "Disk IO Budget 고갈" 을 알려 왔다. 그날 실측으로 디스크
입출력의 거의 전부가 임시 파일이었다(회차당 약 9.3 GB, WAL 은 약 12 MB). 읽은 양은
앱이 셀 수 있지만 이 둘은 데이터베이스만 안다. 그래서 회차 앞뒤로 계수기를 한 번씩
읽어 차이를 회차 기록에 남긴다.

검사용 sqlite 에는 그 계수기가 없다. **없으면 없다고 적고 넘어가는 것**이 여기서
지켜야 할 동작이다. 계측 때문에 회차가 멈추면 안 된다.
"""

from __future__ import annotations

from app.run.collect import _writes_note
from app.storage.db import WriteStats, write_stats


def test_sqlite_has_no_counters_and_says_so(engine):
    stats = write_stats(engine)
    assert stats.available is False
    assert stats.reason and "sqlite" in stats.reason


def test_delta_reports_what_the_run_made():
    before = WriteStats(available=True, wal_bytes=1_000, temp_files=2, temp_bytes=3_000)
    after = WriteStats(available=True, wal_bytes=3_048, temp_files=5, temp_bytes=1_051_576)
    made = after.delta(before)
    assert made.available is True
    assert made.wal_bytes == 2_048
    assert made.temp_files == 3
    assert made.temp_bytes == 1_048_576


def test_delta_gives_up_when_either_end_is_missing():
    before = WriteStats(reason="권한 없음")
    after = WriteStats(available=True, wal_bytes=10, temp_files=0, temp_bytes=0)
    assert after.delta(before).available is False
    assert "권한 없음" in after.delta(before).reason


def test_delta_gives_up_when_the_counters_were_reset():
    """계수기가 회차 도중 초기화되면 차가 음수가 된다. 그때는 숫자를 지어내지 않는다."""
    before = WriteStats(available=True, wal_bytes=5_000, temp_files=9, temp_bytes=9_000)
    after = WriteStats(available=True, wal_bytes=10, temp_files=0, temp_bytes=0)
    made = after.delta(before)
    assert made.available is False
    assert "초기화" in made.reason


def test_note_puts_temp_files_first():
    run = WriteStats(available=True, wal_bytes=12 * 1024 * 1024, temp_files=125,
                     temp_bytes=9_300 * 1024 * 1024)
    export = WriteStats(available=True, wal_bytes=1024, temp_files=3,
                        temp_bytes=70 * 1024 * 1024)
    note = _writes_note(run, export)
    assert "임시파일 9,300MB(125개)" in note
    assert "WAL 12MB" in note
    assert "공개 임시 70MB" in note


def test_note_says_when_it_could_not_measure():
    note = _writes_note(WriteStats(reason="권한이 없다"), WriteStats())
    assert "못 잼" in note and "권한이 없다" in note
