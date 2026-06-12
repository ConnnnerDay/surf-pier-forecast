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
        for state in ("SC", "NJ", "MD", "MA", "LA", "CA", "CT", "DE"):
            assert state in rs._SCRAPERS
            assert callable(rs._SCRAPERS[state])

    def test_original_states_still_present(self):
        for state in ("FL", "VA", "GA", "NC", "NY", "AL", "RI", "TX", "MS"):
            assert state in rs._SCRAPERS
