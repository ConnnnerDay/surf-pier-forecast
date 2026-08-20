"""Tests for the generic table-based regulation scraper (new saltwater states)."""

import storage.reg_scraper as rs


_SAMPLE_TABLE = """
<html><body>
<table>
  <tr><th>Species</th><th>Minimum Size</th><th>Daily Limit</th><th>Season</th></tr>
  <tr><td>Red Drum (Spottail Bass)</td><td>15-23 in</td><td>3 per day</td><td>Open year-round</td></tr>
  <tr><td>Spotted Seatrout</td><td>14 in</td><td>10 per day</td><td>Closed Jan</td></tr>
  <tr><td>Cobia (Ling)</td><td>36 in</td><td>1 per day</td><td></td></tr>
</table>
</body></html>
"""

_NAMES = rs._COMMON_NAMES


class TestParseRegTable:
    def test_extracts_size_bag_season(self):
        out = rs._parse_reg_table(
            _SAMPLE_TABLE, "Red drum (puppy drum)", _NAMES, "src", "note"
        )
        assert out is not None
        assert out["min_size"] == "15-23 in"
        assert out["bag_limit"] == "3 per day"
        assert out["season"] == "Open year-round"
        assert out["scraped_source"] == "src"
        assert out["notes"] == "note"

    def test_regional_alias_matches_column_text(self):
        # The table says "Spottail Bass"; the red_drum alias list covers it via
        # "red drum", and the row text also contains "red drum".
        out = rs._parse_reg_table(_SAMPLE_TABLE, "Spotted seatrout", _NAMES, "s", "n")
        assert out is not None
        assert out["min_size"] == "14 in"
        assert out["bag_limit"] == "10 per day"

    def test_missing_season_cell_ok(self):
        out = rs._parse_reg_table(_SAMPLE_TABLE, "Cobia", _NAMES, "s", "n")
        assert out["min_size"] == "36 in"
        assert out["season"] == ""

    def test_unknown_species_returns_none(self):
        assert rs._parse_reg_table(_SAMPLE_TABLE, "Tilapia", _NAMES, "s", "n") is None

    def test_species_not_in_table_returns_none(self):
        # Known species key, but no matching row in this table.
        assert rs._parse_reg_table(_SAMPLE_TABLE, "Tarpon", _NAMES, "s", "n") is None


class TestMakeTableScraper:
    def test_end_to_end_with_mocked_fetch(self, monkeypatch):
        monkeypatch.setattr(rs, "_fetch_page", lambda url: _SAMPLE_TABLE)
        scrape = rs._make_table_scraper(
            "http://example.test/regs", _NAMES, "example.test", "Verify rules."
        )
        out = scrape("Red drum (puppy drum)")
        assert out["bag_limit"] == "3 per day"
        assert out["scraped_source"] == "example.test"

    def test_fetch_failure_returns_none(self, monkeypatch):
        monkeypatch.setattr(rs, "_fetch_page", lambda url: None)
        scrape = rs._make_table_scraper("http://x.test", _NAMES, "x", "n")
        assert scrape("Red drum") is None


class TestNewStatesRegistered:
    def test_new_saltwater_states_have_scrapers(self):
        for state in (
            "SC",
            "NJ",
            "MD",
            "MA",
            "LA",
            "CA",
            "CT",
            "DE",
            "ME",
            "NH",
            "WA",
            "OR",
            "AK",
        ):
            assert state in rs._SCRAPERS
            assert callable(rs._SCRAPERS[state])

    def test_every_scraper_state_has_official_source(self):
        from regulations import _STATE_REGULATION_SOURCES

        for state in rs._SCRAPERS:
            assert state in _STATE_REGULATION_SOURCES, (
                f"{state} missing official source"
            )

    def test_original_states_still_present(self):
        for state in ("FL", "VA", "GA", "NC", "NY", "AL", "RI", "TX", "MS"):
            assert state in rs._SCRAPERS


_REORDERED_TABLE = """
<html><body>
<table>
  <tr><th>Species</th><th>Possession Limit</th><th>Minimum Length</th><th>Open Season</th></tr>
  <tr><td>Striped Bass</td><td>1 per day</td><td>28-31 in</td><td>May 1 - Dec 31</td></tr>
</table>
</body></html>
"""

_ALT_HEADERS_TABLE = """
<table>
  <tr><th>Fish</th><th>Creel</th><th>Legal Size</th></tr>
  <tr><td>Lingcod</td><td>2 fish</td><td>22 inches</td></tr>
</table>
"""

_NO_HEADER_TABLE = """
<table>
  <tr><td>Bluefish</td><td>12 in</td><td>3 per day</td><td>Year-round</td></tr>
</table>
"""

_LABEL_PAGE = """
<html><body>
<h2>Cobia (Ling)</h2>
<p>Minimum size: 36 inches fork length. Bag limit: 1 per person per day.
Open season: year-round.</p>
<h2>Other species</h2>
</body></html>
"""


class TestColumnAdaptivity:
    def test_reordered_columns_by_header(self):
        out = rs._parse_reg_table(_REORDERED_TABLE, "Striped bass", _NAMES, "s", "n")
        assert out["min_size"] == "28-31 in"
        assert out["bag_limit"] == "1 per day"
        assert out["season"] == "May 1 - Dec 31"

    def test_alternate_header_names(self):
        out = rs._parse_reg_table(_ALT_HEADERS_TABLE, "Lingcod", _NAMES, "s", "n")
        assert out["min_size"] == "22 inches"
        assert out["bag_limit"] == "2 fish"

    def test_positional_fallback_without_header(self):
        out = rs._parse_reg_table(_NO_HEADER_TABLE, "Bluefish", _NAMES, "s", "n")
        assert out["min_size"] == "12 in"
        assert out["bag_limit"] == "3 per day"
        assert out["season"] == "Year-round"

    def test_detect_columns_size_beats_limit(self):
        cols = rs._detect_reg_columns(["Species", "Minimum Size Limit", "Bag Limit"])
        assert cols["size"] == 1
        assert cols["bag"] == 2


class TestLabelFallback:
    def test_label_value_block(self):
        out = rs._parse_reg_labels(_LABEL_PAGE, "Cobia", _NAMES, "src", "note")
        assert out is not None
        assert "36 inches" in out["min_size"]
        assert "1 per person" in out["bag_limit"]

    def test_scraper_falls_back_to_labels(self, monkeypatch):
        # A page with no table — table parse returns None, labels succeed.
        monkeypatch.setattr(rs, "_fetch_page", lambda url: _LABEL_PAGE)
        scrape = rs._make_table_scraper("http://x.test", _NAMES, "x.test", "note")
        out = scrape("Cobia")
        assert out is not None and "36 inches" in out["min_size"]
