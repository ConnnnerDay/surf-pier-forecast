"""Extended tests for storage/reg_scraper.py covering previously missed branches.

Missing lines from full-suite report:
  59-83   _fetch_page() successful fetch and size-exceeded path
  126     _most_common() empty-list → return ""
  399     _get_ga_html() cache-hit path
  487     _parse_ga_page() dt without dd sibling → continue
  541     _get_nc_html() cache-hit path
  576     _parse_nc_page() empty tr → continue
  591     _parse_nc_page() row with 1-2 tds matching species → else: continue
  647     _get_ny_html() cache-hit path
  738     _get_al_html() cache-hit path
  815     _get_ri_html() cache-hit path
  846     _parse_ri_page() row with < 3 tds → continue
  976-977 _parse_tx_page() max-only (no min) → size = "max …"
  987     _parse_tx_page() found Daily Bag label but no captured value → return None
  1010-12 _scrape_tx() parse exception → return None
  1045    _get_ms_html() cache-hit path
  1062-63 _get_ms_html() response exceeds _MAX_RESPONSE_BYTES → return None
  1097    _parse_ms_page() row with < 3 tds → continue
  1128    _parse_ms_page() species in map but absent from tables → return None
  1205    _parse_reg_table() row with < 2 tds → continue
  1258    _parse_reg_labels() species not in names_map → return None
  1268    _parse_reg_labels() species text absent from page → return None
  1280    _parse_reg_labels() species present but no size/bag labels → return None
  1302    _make_table_scraper._get_html() second call → cache hit
  1481-82 _cache_get() exception in DB call → return None
  1495-96 _cache_set() exception in DB call → log warning (no crash)
  1526    get_regulation_stale() empty species → return (None, False)
  1542-43 get_regulation_stale() DB exception → return (None, False)
  1549-50 get_regulation_stale() corrupt JSON in DB → return (None, False)
  1573    scrape_regulation() empty species → return None
  1610-11 invalidate_cache() DB exception → return 0
"""

from __future__ import annotations


import pytest

import storage.reg_scraper as rs
from storage.sqlite import init_db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_page_caches():
    """Per-state module-level HTML caches must be clean between tests."""
    rs._ga_page_cache = None
    rs._nc_page_cache = None
    rs._ny_page_cache = None
    rs._al_page_cache = None
    rs._ri_page_cache = None
    rs._ms_page_cache = None
    yield
    rs._ga_page_cache = None
    rs._nc_page_cache = None
    rs._ny_page_cache = None
    rs._al_page_cache = None
    rs._ri_page_cache = None
    rs._ms_page_cache = None


@pytest.fixture
def db(tmp_path, monkeypatch):
    """Isolated SQLite DB with the reg_scrape_cache table."""
    monkeypatch.setattr("storage.sqlite.DB_PATH", str(tmp_path / "test.db"))
    init_db()


# ---------------------------------------------------------------------------
# Lines 59-83: _fetch_page() successful fetch and size-exceeded path
# ---------------------------------------------------------------------------

_FAKE_HTML = "<html><body>Hello</body></html>"


class _FakeResponse:
    """Minimal mock for requests.Response with streaming iter_content."""

    def __init__(self, chunks, encoding="utf-8", status_code=200):
        self._chunks = chunks
        self.encoding = encoding
        self.status_code = status_code

    def raise_for_status(self):
        pass

    def iter_content(self, chunk_size):
        yield from self._chunks


class TestFetchPage:
    def test_successful_fetch_returns_text(self, monkeypatch):
        """Lines 59-80: happy path — chunks assembled and decoded."""
        resp = _FakeResponse([_FAKE_HTML.encode("utf-8")])
        monkeypatch.setattr(rs.requests, "get", lambda *a, **k: resp)
        result = rs._fetch_page("http://example.test/")
        assert result == _FAKE_HTML

    def test_encoding_fallback_to_utf8(self, monkeypatch):
        """Line 79: when resp.encoding is None, falls back to 'utf-8'."""

        class _NoEncResp(_FakeResponse):
            encoding = None

        resp = _NoEncResp([_FAKE_HTML.encode("utf-8")])
        monkeypatch.setattr(rs.requests, "get", lambda *a, **k: resp)
        assert rs._fetch_page("http://example.test/") == _FAKE_HTML

    def test_response_exceeds_max_bytes_returns_none(self, monkeypatch):
        """Lines 71-77: if total bytes > _MAX_RESPONSE_BYTES the fetch aborts."""
        big_chunk = b"x" * (rs._MAX_RESPONSE_BYTES + 1)
        resp = _FakeResponse([big_chunk])
        monkeypatch.setattr(rs.requests, "get", lambda *a, **k: resp)
        assert rs._fetch_page("http://example.test/") is None

    def test_network_exception_returns_none(self, monkeypatch):
        """Lines 81-83: any exception from requests.get → return None."""

        def _raise(*a, **k):
            raise ConnectionError("timeout")

        monkeypatch.setattr(rs.requests, "get", _raise)
        assert rs._fetch_page("http://example.test/") is None


# ---------------------------------------------------------------------------
# Line 126: _most_common() with empty list
# ---------------------------------------------------------------------------


class TestMostCommon:
    def test_empty_list_returns_empty_string(self):
        """Line 126: cleaned list is empty → return ''."""
        assert rs._most_common([]) == ""

    def test_whitespace_only_list_returns_empty_string(self):
        """Line 126: all blank values → return ''."""
        assert rs._most_common(["  ", "\t", ""]) == ""


# ---------------------------------------------------------------------------
# Line 399: _get_ga_html() cache-hit path
# ---------------------------------------------------------------------------


class TestGetGaHtmlCacheHit:
    def test_second_call_returns_from_cache(self, monkeypatch):
        """Line 399: when _ga_page_cache is populated, skip _fetch_page."""
        calls = []
        monkeypatch.setattr(
            rs, "_fetch_page", lambda url: calls.append(url) or _FAKE_HTML
        )
        # First call populates the cache.
        rs._get_ga_html()
        # Second call must not touch _fetch_page.
        result = rs._get_ga_html()
        assert len(calls) == 1
        assert result == _FAKE_HTML


# ---------------------------------------------------------------------------
# Line 487: _parse_ga_page() dt present but no following dd → continue
# ---------------------------------------------------------------------------

_GA_NO_DD = """
<html><body>
<dl>
  <dt>Red Drum</dt>
</dl>
</body></html>
"""


class TestParseGaPageNoDd:
    def test_dt_without_dd_is_skipped(self):
        """Line 487: when the last dt has no following dd, continue fires."""
        # "Red drum" is in _GA_NAMES; its dt exists but has no <dd> sibling.
        result = rs._parse_ga_page(_GA_NO_DD, "Red drum")
        assert result is None


# ---------------------------------------------------------------------------
# Line 541: _get_nc_html() cache-hit path
# ---------------------------------------------------------------------------


class TestGetNcHtmlCacheHit:
    def test_second_call_returns_from_cache(self, monkeypatch):
        """Line 541: _nc_page_cache populated → skip fetch on second call."""
        calls = []
        monkeypatch.setattr(
            rs, "_fetch_page", lambda url: calls.append(url) or _FAKE_HTML
        )
        rs._get_nc_html()
        rs._get_nc_html()
        assert len(calls) == 1


# ---------------------------------------------------------------------------
# Line 576: _parse_nc_page() empty <tr> row → continue
# ---------------------------------------------------------------------------

_NC_EMPTY_ROW = """
<table>
<tr></tr>
<tr><td>Red Drum</td><td>18 in</td><td>1 per day</td><td>n/a</td></tr>
</table>
"""


class TestParseNcPageEmptyRow:
    def test_empty_tr_is_skipped(self):
        """Line 576: rows with no tds are skipped without error."""
        out = rs._parse_nc_page(_NC_EMPTY_ROW, "Red drum")
        assert out is not None
        assert out["min_size"] == "18 in"


# ---------------------------------------------------------------------------
# Line 591: _parse_nc_page() row matches species but has < 3 tds → else: continue
# ---------------------------------------------------------------------------

_NC_2COL_ROW = """
<table>
<tr><td>Red Drum</td><td>18 in</td></tr>
</table>
"""


class TestParseNcPageTwoColRow:
    def test_two_col_matching_row_skipped(self):
        """Line 591: row with species match but only 2 cells → else: continue."""
        result = rs._parse_nc_page(_NC_2COL_ROW, "Red drum")
        assert result is None


# ---------------------------------------------------------------------------
# Lines 647, 738, 815: cache-hit paths for NY, AL, RI
# ---------------------------------------------------------------------------


class TestGetNyHtmlCacheHit:
    def test_second_call_returns_from_cache(self, monkeypatch):
        """Line 647: _ny_page_cache populated → skip fetch."""
        calls = []
        monkeypatch.setattr(
            rs, "_fetch_page", lambda url: calls.append(url) or _FAKE_HTML
        )
        rs._get_ny_html()
        rs._get_ny_html()
        assert len(calls) == 1


class TestGetAlHtmlCacheHit:
    def test_second_call_returns_from_cache(self, monkeypatch):
        """Line 738: _al_page_cache populated → skip fetch."""
        calls = []
        monkeypatch.setattr(
            rs, "_fetch_page", lambda url: calls.append(url) or _FAKE_HTML
        )
        rs._get_al_html()
        rs._get_al_html()
        assert len(calls) == 1


class TestGetRiHtmlCacheHit:
    def test_second_call_returns_from_cache(self, monkeypatch):
        """Line 815: _ri_page_cache populated → skip fetch."""
        calls = []
        monkeypatch.setattr(
            rs, "_fetch_page", lambda url: calls.append(url) or _FAKE_HTML
        )
        rs._get_ri_html()
        rs._get_ri_html()
        assert len(calls) == 1


# ---------------------------------------------------------------------------
# Line 846: _parse_ri_page() row with < 3 tds → continue
# ---------------------------------------------------------------------------

_RI_SHORT_ROW = """
<table><tr><td>Commercial placeholder</td></tr></table>
<table>
<tr><td>Striped Bass</td><td>28 in</td></tr>
<tr><td>Striped Bass</td><td>28 in</td><td>Apr 1 - Dec 31</td><td>1 fish</td></tr>
</table>
"""


class TestParseRiPageShortRow:
    def test_row_with_two_cols_skipped(self):
        """Line 846: row with < 3 tds is skipped; later matching row returns data."""
        out = rs._parse_ri_page(_RI_SHORT_ROW, "Striped bass")
        assert out is not None
        assert out["bag_limit"] == "1 fish"


# ---------------------------------------------------------------------------
# Lines 976-977: _parse_tx_page() max Length present but no min → "max …"
# ---------------------------------------------------------------------------

_TX_MAX_ONLY = """
Tarpon
Daily Bag:
1
Max Length:
86 inches
"""


class TestParseTxPageMaxOnly:
    def test_max_length_without_min_prefixed(self):
        """Lines 976-977: max_val but no size → size = 'max <val>'."""
        out = rs._parse_tx_page(_TX_MAX_ONLY, "tarpon")
        assert out is not None
        assert out["min_size"] == "max 86 inches"
        assert out["bag_limit"] == "1"


# ---------------------------------------------------------------------------
# Line 987: _parse_tx_page() "Daily Bag:" found but regex captures nothing
# ---------------------------------------------------------------------------

_TX_BAG_NO_VALUE = "Daily Bag:\n\n"


class TestParseTxPageNoValue:
    def test_returns_none_when_bag_regex_has_no_capture(self):
        """Line 987: 'Daily Bag:' present but no value captured → return None."""
        result = rs._parse_tx_page(_TX_BAG_NO_VALUE, "red drum")
        assert result is None


# ---------------------------------------------------------------------------
# Lines 1010-1012: _scrape_tx() parse raises exception → return None
# ---------------------------------------------------------------------------


class TestScrapeTxParseException:
    def test_parse_exception_returns_none(self, monkeypatch):
        """Lines 1010-1012: if the parse helper raises, _scrape_tx returns None."""
        monkeypatch.setattr(rs, "_fetch_page", lambda url: _FAKE_HTML)

        def _bad_parse(text, target):
            raise RuntimeError("parser exploded")

        monkeypatch.setattr(rs, "_parse_tx_page", _bad_parse)
        assert rs._scrape_tx("Red drum") is None


# ---------------------------------------------------------------------------
# Line 1045: _get_ms_html() cache-hit path
# ---------------------------------------------------------------------------


class TestGetMsHtmlCacheHit:
    def test_second_call_returns_from_cache(self, monkeypatch):
        """Line 1045: _ms_page_cache populated → return immediately."""

        class _Resp:
            encoding = "utf-8"

            def raise_for_status(self):
                pass

            def iter_content(self, chunk_size):
                yield _FAKE_HTML.encode("utf-8")

        call_count = [0]

        def _fake_get(*a, **k):
            call_count[0] += 1
            return _Resp()

        monkeypatch.setattr(rs.requests, "get", _fake_get)
        rs._get_ms_html()
        rs._get_ms_html()
        assert call_count[0] == 1


# ---------------------------------------------------------------------------
# Lines 1062-1063: _get_ms_html() response exceeds _MAX_RESPONSE_BYTES
# ---------------------------------------------------------------------------


class TestGetMsHtmlSizeExceeded:
    def test_oversized_response_returns_none(self, monkeypatch):
        """Lines 1062-1063: total > _MAX_RESPONSE_BYTES → return None."""

        class _BigResp:
            encoding = "utf-8"

            def raise_for_status(self):
                pass

            def iter_content(self, chunk_size):
                yield b"x" * (rs._MAX_RESPONSE_BYTES + 1)

        monkeypatch.setattr(rs.requests, "get", lambda *a, **k: _BigResp())
        result = rs._get_ms_html()
        assert result is None


# ---------------------------------------------------------------------------
# Line 1097: _parse_ms_page() row with < 3 tds → continue
# ---------------------------------------------------------------------------

_MS_SHORT_ROW = """
<table><tr><td>offshore placeholder</td></tr></table>
<table>
<tr><td>Red Drum</td></tr>
<tr><td>Inshore</td><td>Red Drum</td><td>18 in</td><td>3</td></tr>
</table>
"""


class TestParseMsPageShortRow:
    def test_row_with_one_col_skipped(self):
        """Line 1097: row with < 3 tds is skipped; later 4-col row wins."""
        out = rs._parse_ms_page(_MS_SHORT_ROW, "Red drum")
        assert out is not None
        assert out["bag_limit"] == "3"


# ---------------------------------------------------------------------------
# Line 1128: _parse_ms_page() species in map but absent from any table row
# ---------------------------------------------------------------------------

_MS_HTML_NO_TRIPLETAIL = """
<table><tr><td>offshore placeholder</td></tr></table>
<table>
<tr><th>Species</th><th>Min Size</th><th>Bag</th></tr>
<tr><td>Red Drum</td><td>18 in</td><td>3</td></tr>
</table>
"""


class TestParseMsPageSpeciesAbsent:
    def test_species_in_map_but_not_in_table_returns_none(self):
        """Line 1128: tripletail is in _MS_NAMES but not in any table row."""
        result = rs._parse_ms_page(_MS_HTML_NO_TRIPLETAIL, "Tripletail")
        assert result is None


# ---------------------------------------------------------------------------
# Line 1205: _parse_reg_table() row with < 2 tds → continue
# ---------------------------------------------------------------------------

_TABLE_SHORT_ROW = """
<table>
<tr><th>Species</th><th>Min Size</th><th>Bag</th><th>Season</th></tr>
<tr><td>only one</td></tr>
<tr><td>Red Drum</td><td>15 in</td><td>3 per day</td><td>Open</td></tr>
</table>
"""


class TestParseRegTableShortRow:
    def test_row_with_one_td_skipped(self):
        """Line 1205: row with < 2 tds is skipped; matching row below still found."""
        out = rs._parse_reg_table(
            _TABLE_SHORT_ROW, "Red drum", rs._COMMON_NAMES, "src", "n"
        )
        assert out is not None
        assert out["min_size"] == "15 in"


# ---------------------------------------------------------------------------
# Lines 1258, 1268, 1280: _parse_reg_labels() return-None paths
# ---------------------------------------------------------------------------

_LABEL_HTML = """
<html><body>
Red Drum. Minimum size: 18 inches. Bag limit: 3 per day.
</body></html>
"""

_LABEL_HTML_NO_LABELS = """
<html><body>
Red Drum
Some unrelated text with no size or bag labels.
</body></html>
"""


class TestParseRegLabels:
    _NAMES = {"red_drum": ["red drum"]}

    def test_unknown_species_returns_none(self):
        """Line 1258: species key not in names_map → return None."""
        result = rs._parse_reg_labels(
            _LABEL_HTML, "Tilapia", self._NAMES, "src", "note"
        )
        assert result is None

    def test_species_absent_from_text_returns_none(self):
        """Line 1268: names_map hit but species text not in page → return None."""
        result = rs._parse_reg_labels(
            "<html><body>Nothing here.</body></html>",
            "Red drum",
            self._NAMES,
            "src",
            "note",
        )
        assert result is None

    def test_no_size_or_bag_labels_returns_none(self):
        """Line 1280: species found but no size/bag patterns → return None."""
        result = rs._parse_reg_labels(
            _LABEL_HTML_NO_LABELS, "Red drum", self._NAMES, "src", "note"
        )
        assert result is None

    def test_labels_found_returns_data(self):
        """Sanity: when labels are present, the dict is returned."""
        result = rs._parse_reg_labels(
            _LABEL_HTML, "Red drum", self._NAMES, "src", "note"
        )
        assert result is not None
        assert result["min_size"] == "18 inches"
        assert result["bag_limit"] == "3 per day"


# ---------------------------------------------------------------------------
# Line 1302: _make_table_scraper._get_html() second call → cache hit
# ---------------------------------------------------------------------------


class TestMakeTableScraperCacheHit:
    def test_second_scrape_uses_cached_html(self, monkeypatch):
        """Line 1302: the closure's internal cache is reused on the second call."""
        calls = []
        monkeypatch.setattr(
            rs, "_fetch_page", lambda url: calls.append(url) or _TABLE_SHORT_ROW
        )
        scrape = rs._make_table_scraper(
            "http://example.test/regs", rs._COMMON_NAMES, "example.test", "Verify."
        )
        scrape("Red drum")  # first call — fetches
        scrape("Red drum")  # second call — cache hit (line 1302)
        assert len(calls) == 1


# ---------------------------------------------------------------------------
# Lines 1481-1482: _cache_get() DB exception → return None
# ---------------------------------------------------------------------------


class TestCacheGetException:
    def test_db_exception_returns_none(self, monkeypatch):
        """Lines 1481-1482: when get_db() raises, _cache_get returns None."""

        def _raise():
            raise RuntimeError("db unavailable")

        monkeypatch.setattr(rs, "get_db", _raise)
        result = rs._cache_get("red_drum", "FL")
        assert result is None


# ---------------------------------------------------------------------------
# Lines 1495-1496: _cache_set() DB exception → log warning, no crash
# ---------------------------------------------------------------------------


class TestCacheSetException:
    def test_db_exception_does_not_propagate(self, monkeypatch):
        """Lines 1495-1496: when get_db() raises, _cache_set logs and exits cleanly."""

        def _raise():
            raise RuntimeError("db unavailable")

        monkeypatch.setattr(rs, "get_db", _raise)
        # Should not raise.
        rs._cache_set("red_drum", "FL", {"min_size": "18 in"})


# ---------------------------------------------------------------------------
# Line 1526: get_regulation_stale() empty species → (None, False)
# ---------------------------------------------------------------------------


class TestGetRegulationStaleEmptySpecies:
    def test_empty_name_returns_none_false(self, db):
        """Line 1526: _name_variants('') returns [] → early return (None, False)."""
        data, fresh = rs.get_regulation_stale("", "FL")
        assert data is None
        assert fresh is False


# ---------------------------------------------------------------------------
# Lines 1542-1543: get_regulation_stale() DB exception → (None, False)
# ---------------------------------------------------------------------------


class TestGetRegulationStaleDbException:
    def test_db_exception_returns_none_false(self, monkeypatch):
        """Lines 1542-1543: get_db() raises during stale-lookup → (None, False)."""

        def _raise():
            raise RuntimeError("db unavailable")

        # _cache_get also uses get_db; patch both to ensure we hit the stale path.
        monkeypatch.setattr(rs, "_cache_get", lambda *a: None)
        monkeypatch.setattr(rs, "get_db", _raise)
        data, fresh = rs.get_regulation_stale("Red drum", "FL")
        assert data is None
        assert fresh is False


# ---------------------------------------------------------------------------
# Lines 1549-1550: get_regulation_stale() corrupt JSON in DB → (None, False)
# ---------------------------------------------------------------------------


class TestGetRegulationStaleCorruptJson:
    def test_corrupt_json_returns_none_false(self, db, monkeypatch):
        """Lines 1549-1550: JSON decode error on stale row → (None, False)."""
        from storage.sqlite import get_db as real_get_db

        # Insert corrupt JSON directly.
        conn = real_get_db()
        conn.execute(
            "INSERT OR REPLACE INTO reg_scrape_cache "
            "(species_key, state, reg_json, scraped_at) "
            "VALUES (?, ?, ?, datetime('now', '-2 days'))",
            ("red_drum", "FL", "{not valid json"),
        )
        conn.commit()
        conn.close()

        # _cache_get will return None (expired), so we reach the stale path.
        monkeypatch.setattr(rs, "_cache_get", lambda *a: None)
        data, fresh = rs.get_regulation_stale("Red drum", "FL")
        assert data is None
        assert fresh is False


# ---------------------------------------------------------------------------
# Line 1573: scrape_regulation() empty species → None
# ---------------------------------------------------------------------------


class TestScrapeRegulationEmptySpecies:
    def test_empty_name_returns_none(self, db):
        """Line 1573: _name_variants('') returns [] → early return None."""
        result = rs.scrape_regulation("", "FL")
        assert result is None


# ---------------------------------------------------------------------------
# Lines 1610-1611: invalidate_cache() DB exception → return 0
# ---------------------------------------------------------------------------


class TestInvalidateCacheException:
    def test_db_exception_returns_zero(self, monkeypatch):
        """Lines 1610-1611: when get_db() raises, invalidate_cache returns 0."""

        def _raise():
            raise RuntimeError("db unavailable")

        monkeypatch.setattr(rs, "get_db", _raise)
        count = rs.invalidate_cache("FL")
        assert count == 0

    def test_db_exception_all_states_returns_zero(self, monkeypatch):
        """Lines 1610-1611: same for invalidate_cache() with no state arg."""

        def _raise():
            raise RuntimeError("db unavailable")

        monkeypatch.setattr(rs, "get_db", _raise)
        count = rs.invalidate_cache()
        assert count == 0
