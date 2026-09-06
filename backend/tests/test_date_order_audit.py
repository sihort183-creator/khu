import asyncio
import json
from types import SimpleNamespace

from lxml import html
from test_collection_progress import item

from app.ingestion.base import ListPage
from app.ingestion.gnuboard import GnuboardAdapter
from app.ingestion.khu_board import KhuBoardAdapter
from app.ops import date_order


class FakeAdapter:
    def __init__(self, pages):
        self.pages = pages

    def list_url(self, config, number):
        return str(number)

    def allowed_hosts(self, config):
        return set()

    def parse_list(self, text, config, number):
        return ListPage(tuple(self.pages[number]), number, number < len(self.pages))


def inspect(tmp_path, monkeypatch, pages, max_pages=20):
    adapter = FakeAdapter(pages)
    monkeypatch.setattr(date_order, "get_adapter", lambda name: adapter)

    class Fetch:
        async def get(self, *args, **kwargs):
            return SimpleNamespace(text='<select name="userDisplayCount"><option value="50">50</option></select>')

    return asyncio.run(date_order.inspect_source(Fetch(), {
        "source_id": "test", "adapter": "khu_board", "config": {},
    }, max_pages=max_pages, evidence_dir=tmp_path))


def test_full_list_proof_retains_old_pinned_and_recommends_official_page_size(tmp_path, monkeypatch):
    result = inspect(tmp_path, monkeypatch, {
        1: [item(0, "2005-01-01", pinned=True), item(1, "2026-03-01")],
        2: [item(2, "2025-01-01")],
    })
    assert result["verified"]
    assert result["proposed_config"]["date_ordered"]
    assert result["proposed_config"]["user_display_count"] == 50
    assert result["proposed_config"]["date_order_evidence"]["valid_until"]
    assert result["pages"][0]["pinned_ids"] == ["0"]
    assert (tmp_path / "test" / "page-2.html").exists()


def test_late_inversion_and_unknown_dates_cannot_receive_order_flag(tmp_path, monkeypatch):
    for last in (item(3, "2026-04-01"), item(3, None)):
        result = inspect(tmp_path, monkeypatch, {
            1: [item(1, "2026-03-01")], 2: [item(2, "2025-01-01")], 3: [last],
        })
        assert not result["verified"]
        assert "proposed_config" not in result


def test_page_cap_is_not_full_order_proof(tmp_path, monkeypatch):
    result = inspect(tmp_path, monkeypatch, {1: [item(1)], 2: [item(2)]}, max_pages=1)
    assert not result["verified"]
    assert result["reason"] == "page_cap"


def test_explicit_sort_and_page_count_are_in_real_request_urls():
    assert "sst=wr_datetime&sod=desc" in GnuboardAdapter().list_url({
        "base_url": "https://law.khu.ac.kr", "bo_table": "notice", "date_sort": "wr_datetime_desc",
    })
    assert "userDisplayCount=50" in KhuBoardAdapter().list_url({
        "base_url": "https://abeek.khu.ac.kr", "prefix": "abeek", "board_code": "BMSR00040", "menu_no": "1",
        "user_display_count": 50,
    })


def test_cms_pagination_does_not_stop_at_tenth_page_block():
    doc = html.fromstring('<div class="paging"><a href="javascript:fnSubmitForm(9)">9</a>'
                          '<a href="javascript:fnSubmitForm(11)">다음</a></div>')
    assert KhuBoardAdapter()._has_next(doc, 10)


def test_apply_rejects_expired_evidence(tmp_path, monkeypatch, session_factory):
    result = inspect(tmp_path, monkeypatch, {1: [item(1)]})
    result["proposed_config"]["date_order_evidence"]["valid_until"] = "2000-01-01T00:00:00+00:00"
    path = tmp_path / "proposal.json"
    path.write_text(json.dumps([result]), encoding="utf-8")
    import pytest
    with pytest.raises(ValueError, match="유효하지"):
        date_order.apply_plan(path)


def test_old_cms_page_block_completion_is_reopened_even_without_verified_proposal(tmp_path, session_factory):
    from test_pipeline import _seed

    from app.storage import models as m
    with session_factory() as session:
        source = _seed(session)
        health = session.get(m.SourceHealth, source.id)
        health.backfill_complete = True
        health.backfill_boundary_reached = True
        health.backfill_cursor_page = 10
        health.backfill_cursor_external_id = "100"
        health.backfill_last_stop_reason = "end_of_board"
        session.commit()
    path = tmp_path / "proposal.json"
    path.write_text("[]", encoding="utf-8")
    assert date_order.apply_plan(path) == 0
    with session_factory() as session:
        health = session.get(m.SourceHealth, source.id)
        assert not health.backfill_complete and not health.backfill_boundary_reached
        assert health.backfill_cursor_page == 1


def test_comma_formatted_row_number_is_not_pinned():
    row = html.fromstring('<tr><td class="td_num">1,234</td><td>글</td></tr>')
    assert not KhuBoardAdapter._is_pinned(row, row.xpath('./td'))
    assert not GnuboardAdapter._is_pinned(row)
