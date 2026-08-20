"""Tests for storage/reg_scraper.py state-specific parsers (FL, VA, GA, NC, NY,
AL, RI, TX, MS) plus the SQLite-backed cache layer and public API
(get_regulation_stale, scrape_regulation, invalidate_cache).

tests/test_reg_scraper.py already covers the generic table scraper used by
the newer states (SC, NJ, MD, ...); this file fills in the original
state-specific parsers that previously had zero coverage.
"""

from __future__ import annotations

import pytest

import storage.reg_scraper as rs
from storage.sqlite import init_db


@pytest.fixture(autouse=True)
def _reset_page_caches():
    """Each per-state module keeps a process-lifetime HTML cache; reset between tests."""
    rs._va_page_cache = None
    rs._ga_page_cache = None
    rs._nc_page_cache = None
    rs._ny_page_cache = None
    rs._al_page_cache = None
    rs._ri_page_cache = None
    rs._ms_page_cache = None
    yield
    rs._va_page_cache = None
    rs._ga_page_cache = None
    rs._nc_page_cache = None
    rs._ny_page_cache = None
    rs._al_page_cache = None
    rs._ri_page_cache = None
    rs._ms_page_cache = None


@pytest.fixture
def db(tmp_path, monkeypatch):
    """Isolated SQLite DB with the reg_scrape_cache table initialized."""
    monkeypatch.setattr("storage.sqlite.DB_PATH", str(tmp_path / "test.db"))
    init_db()


# ──────────────────────────────────────────────────────────────────
# Florida
# ──────────────────────────────────────────────────────────────────

_FL_HTML = """
<html><body><div>
Region: Southeast
Season : Open year-round
Minimum Size Limit: 18 inches total length
Bag Limit: 1 per person per day
Region: Northeast
Season : Open year-round
Minimum Size Limit: 18 inches total length
Bag Limit: 1 per person per day
Expand/Collapse
</div></body></html>
"""


class TestParseFlPage:
    def test_extracts_season_size_bag(self):
        out = rs._parse_fl_page(_FL_HTML)
        assert out is not None
        assert out["season"] == "Open year-round"
        assert out["min_size"] == "18 inches total length"
        assert out["bag_limit"] == "1 per person per day"
        assert out["scraped_source"] == "myfwc.com"

    def test_returns_none_when_no_bag_or_size(self):
        assert (
            rs._parse_fl_page("<html><body>No regulations here.</body></html>") is None
        )


class TestScrapeFl:
    def test_unknown_species_returns_none_without_fetch(self, monkeypatch):
        called = []
        monkeypatch.setattr(
            rs, "_fetch_page", lambda url: called.append(url) or _FL_HTML
        )
        assert rs._scrape_fl("Some Random Fish") is None
        assert called == []

    def test_known_species_fetches_correct_slug(self, monkeypatch):
        urls = []
        monkeypatch.setattr(rs, "_fetch_page", lambda url: urls.append(url) or _FL_HTML)
        out = rs._scrape_fl("Red drum")
        assert out is not None
        assert urls[0] == f"{rs._FL_BASE}red-drum/"

    def test_fetch_failure_returns_none(self, monkeypatch):
        monkeypatch.setattr(rs, "_fetch_page", lambda url: None)
        assert rs._scrape_fl("Red drum") is None


# ──────────────────────────────────────────────────────────────────
# Virginia
# ──────────────────────────────────────────────────────────────────

_VA_HTML = """
<html><body><div>
RED DRUM
Minimum Size Limit: 18 inches
Possession Limit: 1 per person
Season: Year-round
</div></body></html>
"""


class TestParseVaPage:
    def test_extracts_size_bag_season(self):
        out = rs._parse_va_page(_VA_HTML, "Red drum")
        assert out is not None
        assert out["min_size"] == "18 inches"
        assert out["bag_limit"] == "1 per person"
        assert out["season"] == "Year-round"
        assert out["scraped_source"] == "mrc.virginia.gov"

    def test_unknown_species_returns_none(self):
        assert rs._parse_va_page(_VA_HTML, "Tilapia") is None

    def test_known_species_not_present_in_page_returns_none(self):
        assert rs._parse_va_page(_VA_HTML, "Striped bass") is None


class TestScrapeVa:
    def test_uses_cached_html_across_calls(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            rs, "_fetch_page", lambda url: calls.append(url) or _VA_HTML
        )
        rs._scrape_va("Red drum")
        rs._scrape_va("Red drum")
        assert len(calls) == 1

    def test_fetch_failure_returns_none(self, monkeypatch):
        monkeypatch.setattr(rs, "_fetch_page", lambda url: None)
        assert rs._scrape_va("Red drum") is None


# ──────────────────────────────────────────────────────────────────
# Georgia
# ──────────────────────────────────────────────────────────────────

_GA_HTML = """
<html><body>
<dl>
<dt>Red Drum</dt>
<dd>Season: All year  Limit: 5  Minimum size: 14" TL (Maximum 23" TL)</dd>
<dt>No Data Species</dt>
</dl>
</body></html>
"""


class TestParseGaDd:
    def test_extracts_season_limit_size(self):
        out = rs._parse_ga_dd(
            'Season: All year  Limit: 5  Minimum size: 14" TL (Maximum 23" TL)'
        )
        assert out["season"] == "All year"
        assert out["bag_limit"] == "5"
        assert out["min_size"] == '14" TL (Maximum 23" TL)'


class TestParseGaPage:
    def test_matches_dt_dd_pair(self):
        out = rs._parse_ga_page(_GA_HTML, "Red drum")
        assert out is not None
        assert out["bag_limit"] == "5"
        assert out["scraped_source"] == "coastalgadnr.org"

    def test_unknown_species_returns_none(self):
        assert rs._parse_ga_page(_GA_HTML, "Tilapia") is None

    def test_dt_without_sibling_dd_is_skipped(self):
        # "No Data Species" isn't even in the names map, but verify a dt with
        # no matching dd doesn't crash for a species that IS in the map.
        assert rs._parse_ga_page(_GA_HTML, "Striped bass") is None


class TestScrapeGa:
    def test_end_to_end(self, monkeypatch):
        monkeypatch.setattr(rs, "_fetch_page", lambda url: _GA_HTML)
        out = rs._scrape_ga("Red drum")
        assert out["bag_limit"] == "5"

    def test_fetch_failure_returns_none(self, monkeypatch):
        monkeypatch.setattr(rs, "_fetch_page", lambda url: None)
        assert rs._scrape_ga("Red drum") is None


# ──────────────────────────────────────────────────────────────────
# North Carolina
# ──────────────────────────────────────────────────────────────────

_NC_HTML_4COL = """
<table>
<tr><th>Species</th><th>Minimum Length</th><th>Bag Limits</th><th>Federal</th></tr>
<tr><td>Red Drum</td><td>18 in - 27 in slot</td><td>1 per day</td><td>n/a</td></tr>
</table>
"""

_NC_HTML_3COL = """
<table>
<tr><th>Species</th><th>Info</th><th></th></tr>
<tr><td>Bluefish</td><td>5 fish per day</td><td></td></tr>
</table>
"""

_NC_HTML_SKIP = """
<table>
<tr><th>Species</th><th>Minimum Length</th><th>Bag Limits</th><th>Federal</th></tr>
<tr><td>Tautog</td><td>see the most recent proclamation</td><td>varies</td><td>n/a</td></tr>
</table>
"""


class TestParseNcPage:
    def test_four_column_row(self):
        out = rs._parse_nc_page(_NC_HTML_4COL, "Red drum")
        assert out["min_size"] == "18 in - 27 in slot"
        assert out["bag_limit"] == "1 per day"
        assert out["scraped_source"] == "deq.nc.gov"

    def test_three_column_row_uses_combined_cell(self):
        out = rs._parse_nc_page(_NC_HTML_3COL, "Bluefish")
        assert out["min_size"] == "5 fish per day"
        assert out["bag_limit"] == "5 fish per day"

    def test_skip_phrase_rows_yield_none(self):
        assert rs._parse_nc_page(_NC_HTML_SKIP, "Tautog") is None

    def test_no_tables_returns_none(self):
        assert (
            rs._parse_nc_page("<html><body>nothing</body></html>", "Red drum") is None
        )

    def test_unknown_species_returns_none(self):
        assert rs._parse_nc_page(_NC_HTML_4COL, "Tilapia") is None


class TestScrapeNc:
    def test_end_to_end(self, monkeypatch):
        monkeypatch.setattr(rs, "_fetch_page", lambda url: _NC_HTML_4COL)
        out = rs._scrape_nc("Red drum")
        assert out["bag_limit"] == "1 per day"

    def test_fetch_failure_returns_none(self, monkeypatch):
        monkeypatch.setattr(rs, "_fetch_page", lambda url: None)
        assert rs._scrape_nc("Red drum") is None


# ──────────────────────────────────────────────────────────────────
# New York
# ──────────────────────────────────────────────────────────────────

_NY_HTML = """
<table>
<tr><th>Species</th><th>Min Size</th><th>Bag</th><th>Season</th></tr>
<tr><td>Bluefish</td><td>varies (2)</td><td>3 fish (5)</td><td>Open year-round</td></tr>
<tr><td>Short Row</td><td>x</td></tr>
</table>
"""


class TestParseNyPage:
    def test_strips_footnote_references(self):
        out = rs._parse_ny_page(_NY_HTML, "Bluefish")
        assert out["min_size"] == "varies"
        assert out["bag_limit"] == "3 fish"
        assert out["season"] == "Open year-round"
        assert out["scraped_source"] == "dec.ny.gov"

    def test_unknown_species_returns_none(self):
        assert rs._parse_ny_page(_NY_HTML, "Tilapia") is None

    def test_no_tables_returns_none(self):
        assert rs._parse_ny_page("<html></html>", "Bluefish") is None

    def test_species_not_in_table_returns_none(self):
        assert rs._parse_ny_page(_NY_HTML, "Cobia") is None


class TestScrapeNy:
    def test_end_to_end(self, monkeypatch):
        monkeypatch.setattr(rs, "_fetch_page", lambda url: _NY_HTML)
        out = rs._scrape_ny("Bluefish")
        assert out["bag_limit"] == "3 fish"

    def test_fetch_failure_returns_none(self, monkeypatch):
        monkeypatch.setattr(rs, "_fetch_page", lambda url: None)
        assert rs._scrape_ny("Bluefish") is None


# ──────────────────────────────────────────────────────────────────
# Alabama
# ──────────────────────────────────────────────────────────────────

_AL_HTML = """
<html><body>
<div class="table-row">
  <div class="row-column">Red Drum</div>
  <div class="row-column">18-27 in slot</div>
  <div class="row-column">3 per day</div>
</div>
<div class="table-row">
  <div class="row-column">Bluefish</div>
  <div class="row-column">x</div>
</div>
</body></html>
"""


class TestParseAlPage:
    def test_extracts_size_and_bag(self):
        out = rs._parse_al_page(_AL_HTML, "Red drum")
        assert out["min_size"] == "18-27 in slot"
        assert out["bag_limit"] == "3 per day"
        assert out["season"] == ""
        assert out["scraped_source"] == "outdooralabama.com"

    def test_row_with_too_few_columns_is_skipped(self):
        # "Bluefish" is a known species key, but its only row has 2 columns
        # (< 3), so the parser must skip it instead of raising an IndexError.
        assert rs._parse_al_page(_AL_HTML, "Bluefish") is None

    def test_unknown_species_returns_none(self):
        assert rs._parse_al_page(_AL_HTML, "Tilapia") is None


class TestScrapeAl:
    def test_end_to_end(self, monkeypatch):
        monkeypatch.setattr(rs, "_fetch_page", lambda url: _AL_HTML)
        out = rs._scrape_al("Red drum")
        assert out["bag_limit"] == "3 per day"

    def test_fetch_failure_returns_none(self, monkeypatch):
        monkeypatch.setattr(rs, "_fetch_page", lambda url: None)
        assert rs._scrape_al("Red drum") is None


# ──────────────────────────────────────────────────────────────────
# Rhode Island
# ──────────────────────────────────────────────────────────────────

_RI_HTML = """
<table><tr><td>Commercial table placeholder</td></tr></table>
<table>
<tr><th>Species</th><th>Min Size</th><th>Season</th><th>Possession</th></tr>
<tr><td>Striped Bass</td><td>28 in</td><td>Apr 1 - Dec 31</td><td>1 fish</td></tr>
</table>
"""

_RI_HTML_ONE_TABLE = "<table><tr><td>only one</td></tr></table>"


class TestParseRiPage:
    def test_extracts_from_second_table(self):
        out = rs._parse_ri_page(_RI_HTML, "Striped bass")
        assert out["min_size"] == "28 in"
        assert out["season"] == "Apr 1 - Dec 31"
        assert out["bag_limit"] == "1 fish"
        assert out["scraped_source"] == "dem.ri.gov"

    def test_fewer_than_two_tables_returns_none(self):
        assert rs._parse_ri_page(_RI_HTML_ONE_TABLE, "Striped bass") is None

    def test_unknown_species_returns_none(self):
        assert rs._parse_ri_page(_RI_HTML, "Tilapia") is None

    def test_species_not_in_table_returns_none(self):
        assert rs._parse_ri_page(_RI_HTML, "Cobia") is None


class TestScrapeRi:
    def test_end_to_end(self, monkeypatch):
        monkeypatch.setattr(rs, "_fetch_page", lambda url: _RI_HTML)
        out = rs._scrape_ri("Striped bass")
        assert out["bag_limit"] == "1 fish"

    def test_fetch_failure_returns_none(self, monkeypatch):
        monkeypatch.setattr(rs, "_fetch_page", lambda url: None)
        assert rs._scrape_ri("Striped bass") is None


# ──────────────────────────────────────────────────────────────────
# Texas
# ──────────────────────────────────────────────────────────────────

_TX_TEXT_TARGETED = """
Red Drum
Daily Bag:
3
Min Length:
20 inches
Max Length:
28 inches
"""

_TX_TEXT_NO_TARGET_MATCH = """
Spotted Seatrout Limits
Daily Bag:
5
Min Length:
15 inches
"""

_TX_TEXT_NO_BAG = "No regulation data on this page at all."


class TestParseTxPage:
    def test_extracts_bag_and_size_range(self):
        out = rs._parse_tx_page(_TX_TEXT_TARGETED, "red drum")
        assert out["bag_limit"] == "3"
        assert out["min_size"] == "20 inches (max 28 inches)"
        assert out["scraped_source"] == "tpwd.texas.gov"

    def test_falls_back_to_first_bag_when_target_not_preceding(self):
        out = rs._parse_tx_page(_TX_TEXT_NO_TARGET_MATCH, "cobia")
        assert out is not None
        assert out["bag_limit"] == "5"
        assert out["min_size"] == "15 inches"

    def test_returns_none_when_no_bag_label(self):
        assert rs._parse_tx_page(_TX_TEXT_NO_BAG, "red drum") is None


class TestScrapeTx:
    def test_unknown_species_returns_none_without_fetch(self, monkeypatch):
        called = []
        monkeypatch.setattr(rs, "_fetch_page", lambda url: called.append(url))
        assert rs._scrape_tx("Some Random Fish") is None
        assert called == []

    def test_known_species_end_to_end(self, monkeypatch):
        html = f"<html><body><p>{_TX_TEXT_TARGETED}</p></body></html>"
        monkeypatch.setattr(rs, "_fetch_page", lambda url: html)
        out = rs._scrape_tx("Red drum")
        assert out is not None
        assert out["bag_limit"] == "3"

    def test_fetch_failure_returns_none(self, monkeypatch):
        monkeypatch.setattr(rs, "_fetch_page", lambda url: None)
        assert rs._scrape_tx("Red drum") is None


# ──────────────────────────────────────────────────────────────────
# Mississippi
# ──────────────────────────────────────────────────────────────────

_MS_HTML_4COL = """
<table><tr><td>Offshore table placeholder, no species here</td></tr></table>
<table>
<tr><th>Category</th><th>Species</th><th>Min Size</th><th>Bag</th></tr>
<tr><td>Inshore</td><td>Red Drum</td><td>18 in</td><td>3</td></tr>
</table>
"""

_MS_HTML_3COL = """
<table><tr><td>placeholder</td></tr></table>
<table>
<tr><th>Species</th><th>Min Size</th><th>Bag</th></tr>
<tr><td>Sheepshead</td><td>12 in</td><td>5</td></tr>
</table>
"""

_MS_HTML_CURLY_QUOTES = """
<table><tr><td>placeholder</td></tr></table>
<table>
<tr><th>Species</th><th>Min Size</th><th>Bag</th></tr>
<tr><td>Cobia</td><td>14” TL</td><td>1</td></tr>
</table>
"""

_MS_HTML_OFFSHORE_ONLY = """
<table>
<tr><th>Species</th><th>Min Size</th><th>Bag</th></tr>
<tr><td>Red Snapper</td><td>16 in</td><td>2</td></tr>
</table>
<table>
<tr><th>Species</th><th>Min Size</th><th>Bag</th></tr>
<tr><td>Unrelated Species</td><td>0</td><td>0</td></tr>
</table>
"""


class TestParseMsPage:
    def test_four_column_row(self):
        out = rs._parse_ms_page(_MS_HTML_4COL, "Red drum")
        assert out["min_size"] == "18 in"
        assert out["bag_limit"] == "3"
        assert out["scraped_source"] == "eregulations.com/mississippi"

    def test_three_column_row(self):
        out = rs._parse_ms_page(_MS_HTML_3COL, "Sheepshead")
        assert out["min_size"] == "12 in"
        assert out["bag_limit"] == "5"

    def test_curly_quotes_normalized(self):
        out = rs._parse_ms_page(_MS_HTML_CURLY_QUOTES, "Cobia")
        assert out["min_size"] == '14" TL'

    def test_falls_back_to_first_table_when_not_in_second(self):
        out = rs._parse_ms_page(_MS_HTML_OFFSHORE_ONLY, "Red snapper")
        assert out["min_size"] == "16 in"

    def test_fewer_than_two_tables_returns_none(self):
        assert (
            rs._parse_ms_page("<table><tr><td>x</td></tr></table>", "Red drum") is None
        )

    def test_unknown_species_returns_none(self):
        assert rs._parse_ms_page(_MS_HTML_4COL, "Tilapia") is None


class TestScrapeMs:
    def test_end_to_end(self, monkeypatch):
        # _get_ms_html uses requests.get directly (not _fetch_page) so we
        # monkeypatch the requests module used inside reg_scraper.
        class _FakeResp:
            encoding = "utf-8"

            def raise_for_status(self):
                pass

            def iter_content(self, chunk_size):
                yield _MS_HTML_4COL.encode("utf-8")

        monkeypatch.setattr(rs.requests, "get", lambda *a, **k: _FakeResp())
        out = rs._scrape_ms("Red drum")
        assert out["bag_limit"] == "3"

    def test_fetch_failure_returns_none(self, monkeypatch):
        def _raise(*a, **k):
            raise rs.requests.ConnectionError("down")

        monkeypatch.setattr(rs.requests, "get", _raise)
        assert rs._scrape_ms("Red drum") is None


# ──────────────────────────────────────────────────────────────────
# SQLite cache layer + public API
# ──────────────────────────────────────────────────────────────────


class TestCacheGetSet:
    def test_set_then_get_round_trips(self, db):
        rs._cache_set("red_drum", "FL", {"min_size": "18 in"})
        out = rs._cache_get("red_drum", "FL")
        assert out["min_size"] == "18 in"
        assert "fetched_at" in out

    def test_missing_key_returns_none(self, db):
        assert rs._cache_get("nonexistent", "FL") is None

    def test_expired_entry_returns_none(self, db, monkeypatch):
        from storage.sqlite import get_db

        rs._cache_set("red_drum", "FL", {"min_size": "18 in"})
        conn = get_db()
        conn.execute(
            "UPDATE reg_scrape_cache SET scraped_at = datetime('now', '-2 days') "
            "WHERE species_key=? AND state=?",
            ("red_drum", "FL"),
        )
        conn.commit()
        conn.close()
        assert rs._cache_get("red_drum", "FL") is None


class TestGetRegulationStale:
    def test_unsupported_state_returns_none_false(self, db):
        data, fresh = rs.get_regulation_stale("Red drum", "ZZ")
        assert data is None
        assert fresh is False

    def test_never_cached_returns_none_false(self, db):
        data, fresh = rs.get_regulation_stale("Red drum", "FL")
        assert data is None
        assert fresh is False

    def test_fresh_cache_hit(self, db):
        rs._cache_set("red_drum", "FL", {"min_size": "18 in"})
        data, fresh = rs.get_regulation_stale("Red drum", "FL")
        assert fresh is True
        assert data["min_size"] == "18 in"

    def test_stale_cache_returns_data_with_fresh_false(self, db):
        from storage.sqlite import get_db

        rs._cache_set("red_drum", "FL", {"min_size": "18 in"})
        conn = get_db()
        conn.execute(
            "UPDATE reg_scrape_cache SET scraped_at = datetime('now', '-2 days') "
            "WHERE species_key=? AND state=?",
            ("red_drum", "FL"),
        )
        conn.commit()
        conn.close()
        data, fresh = rs.get_regulation_stale("Red drum", "FL")
        assert fresh is False
        assert data["min_size"] == "18 in"
        assert data["fetched_at"] is None


class TestScrapeRegulation:
    def test_unsupported_state_returns_none(self, db):
        assert rs.scrape_regulation("Red drum", "ZZ") is None

    def test_live_scrape_is_cached(self, db, monkeypatch):
        monkeypatch.setattr(rs, "_fetch_page", lambda url: _FL_HTML)
        out1 = rs.scrape_regulation("Red drum", "FL")
        assert out1 is not None

        # Second call should hit the SQLite cache, not the live scraper.
        def _fail(name):
            raise AssertionError("should not be called")

        monkeypatch.setitem(rs._SCRAPERS, "FL", _fail)
        out2 = rs.scrape_regulation("Red drum", "FL")
        # The cached read injects a "fetched_at" timestamp; everything else
        # must match the original live-scraped result.
        assert out2["min_size"] == out1["min_size"]
        assert out2["bag_limit"] == out1["bag_limit"]
        assert "fetched_at" in out2

    def test_failed_scrape_caches_empty_sentinel(self, db, monkeypatch):
        monkeypatch.setattr(rs, "_fetch_page", lambda url: None)
        out1 = rs.scrape_regulation("Red drum", "FL")
        assert out1 is None
        calls = []
        monkeypatch.setattr(rs, "_scrape_fl", lambda name: calls.append(name) or None)
        out2 = rs.scrape_regulation("Red drum", "FL")
        assert out2 is None
        # Cached miss sentinel should prevent re-invoking the scraper.
        assert calls == []


class TestInvalidateCache:
    def test_invalidate_single_state(self, db):
        rs._cache_set("red_drum", "FL", {"min_size": "18 in"})
        rs._cache_set("red_drum", "VA", {"min_size": "20 in"})
        count = rs.invalidate_cache("FL")
        assert count == 1
        assert rs._cache_get("red_drum", "FL") is None
        assert rs._cache_get("red_drum", "VA") is not None

    def test_invalidate_all(self, db):
        rs._cache_set("red_drum", "FL", {"min_size": "18 in"})
        rs._cache_set("red_drum", "VA", {"min_size": "20 in"})
        count = rs.invalidate_cache()
        assert count == 2
        assert rs._cache_get("red_drum", "FL") is None
        assert rs._cache_get("red_drum", "VA") is None
