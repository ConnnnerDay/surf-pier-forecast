"""Tests for services/ndbc.py — NDBC buoy observations and barometric pressure."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from services.ndbc import (
    _deg_to_compass,
    _try_ndbc_station,
    fetch_barometric_pressure,
)


# ---------------------------------------------------------------------------
# _deg_to_compass
# ---------------------------------------------------------------------------


class TestDegToCompass:
    def test_north(self):
        assert _deg_to_compass(0) == "N"
        assert _deg_to_compass(360) == "N"

    def test_east(self):
        assert _deg_to_compass(90) == "E"

    def test_south(self):
        assert _deg_to_compass(180) == "S"

    def test_west(self):
        assert _deg_to_compass(270) == "W"

    def test_northeast(self):
        assert _deg_to_compass(45) == "NE"

    def test_southwest(self):
        assert _deg_to_compass(225) == "SW"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

_HEADER = "#YY  MM DD hh mm WDIR WSPD GST  WVHT  DPD APD MWD   PRES  ATMP  WTMP  DEWP  VIS PTDY  TIDE"
_UNITS = "#yr  mo dy hr mn degT m/s  m/s     m  sec  sec degT   hPa  degC  degC  degC   mi  hPa    ft"


def _make_resp(lines: list[str]) -> MagicMock:
    m = MagicMock()
    m.raise_for_status.return_value = None
    m.text = "\n".join(lines)
    return m


def _buoy_resp(wdir="180", wspd="5.0", gst="7.0", wvht="1.5") -> MagicMock:
    data_line = f"2026 06 26 18 00 {wdir}   {wspd}  {gst}   {wvht}   8.0  5.0 180 1015.0  22.0  24.0  18.0   MM  MM   MM"
    return _make_resp([_HEADER, _UNITS, data_line])


# ---------------------------------------------------------------------------
# _try_ndbc_station
# ---------------------------------------------------------------------------


class TestTryNdbcStation:
    def test_parses_wind_and_wave(self):
        with patch("services.ndbc.http_get", return_value=_buoy_resp()):
            wind, wave, direction = _try_ndbc_station("41110")
        assert wind is not None
        assert wave is not None
        assert direction is not None

    def test_skips_short_lines(self):
        """Short data lines (fewer fields than header) should be skipped."""
        data_short = "2026 06 26 18 00"  # far fewer fields than header
        data_full = "2026 06 26 17 00 090   8.0  10.0   2.0   8.0  5.0 090 1013.0  20.0  23.0  15.0   MM  MM   MM"
        resp = _make_resp([_HEADER, _UNITS, data_short, data_full])
        with patch("services.ndbc.http_get", return_value=resp):
            wind, wave, direction = _try_ndbc_station("41110")
        assert wind is not None

    def test_missing_wind_values_return_none(self):
        data = "2026 06 26 18 00 MM   MM   MM    MM   8.0  5.0 180 1015.0  22.0  24.0  18.0   MM  MM   MM"
        resp = _make_resp([_HEADER, _UNITS, data])
        with patch("services.ndbc.http_get", return_value=resp):
            wind, wave, direction = _try_ndbc_station("41110")
        assert wind is None
        assert wave is None
        assert direction is None

    def test_too_few_lines_returns_nones(self):
        resp = _make_resp([_HEADER])  # only 1 line
        with patch("services.ndbc.http_get", return_value=resp):
            wind, wave, direction = _try_ndbc_station("41110")
        assert wind is None

    def test_propagates_http_error(self):
        import requests

        m = MagicMock()
        m.raise_for_status.side_effect = requests.HTTPError("503")
        with patch("services.ndbc.http_get", return_value=m):
            with pytest.raises(requests.HTTPError):
                _try_ndbc_station("41110")

    def test_uses_gust_for_high_end_wind_range(self):
        with patch(
            "services.ndbc.http_get", return_value=_buoy_resp(wspd="5.0", gst="10.0")
        ):
            wind, _wave, _dir = _try_ndbc_station("41110")
        # high end should be gust converted to knots
        assert wind is not None
        assert wind[1] > wind[0]


# ---------------------------------------------------------------------------
# fetch_barometric_pressure
# ---------------------------------------------------------------------------

_PRES_HEADER = "#YY  MM DD hh mm WDIR WSPD GST  WVHT  DPD APD MWD   PRES  ATMP  WTMP  DEWP  VIS PTDY  TIDE"
_PRES_UNITS = "#yr  mo dy hr mn degT m/s  m/s     m  sec  sec degT   hPa  degC  degC  degC   mi  hPa    ft"


def _pres_line(pres: str = "1015.0") -> str:
    return f"2026 06 26 18 00 180   5.0   7.0   1.5   8.0  5.0 180 {pres}  22.0  24.0  18.0   MM  MM   MM"


def _pres_resp(pressures: list[str]) -> MagicMock:
    lines = [_PRES_HEADER, _PRES_UNITS] + [_pres_line(p) for p in pressures]
    return _make_resp(lines)


class TestFetchBarometricPressure:
    def test_high_pressure_stable(self):
        resp = _pres_resp(["1021.0", "1021.0", "1021.0"])
        with patch("services.ndbc.http_get", return_value=resp):
            result = fetch_barometric_pressure()
        assert result is not None
        assert result["pressure_mb"] == pytest.approx(1021.0, abs=0.1)
        assert result["trend"] == "Steady"
        assert "stable" in result["fishing_impact"].lower()

    def test_high_pressure_rising(self):
        # diff = first - last > 1.0  → "Rising"  (no, wait: pressures[0] - pressures[-1] > 1 means RISING)
        # So first reading HIGHER than last = rising pressure over time? No...
        # Actually the code reads most-recent-first from the buoy (lines[2:12]).
        # pressures[0] is most recent; pressures[-1] is oldest.
        # diff > 1 → Rising (recent > old = pressure went up)
        resp = _pres_resp(["1022.0", "1020.5", "1019.0"])  # rising from 1019→1022
        with patch("services.ndbc.http_get", return_value=resp):
            result = fetch_barometric_pressure()
        assert result is not None
        assert result["trend"] == "Rising"

    def test_high_pressure_falling(self):
        # current (most-recent) must stay ≥1020; older readings must be higher
        resp = _pres_resp(["1020.5", "1022.0", "1023.5"])  # fell from 1023.5→1020.5
        with patch("services.ndbc.http_get", return_value=resp):
            result = fetch_barometric_pressure()
        assert result is not None
        assert result["trend"] == "Falling"
        assert (
            "dropping" in result["fishing_impact"].lower()
            or "feed" in result["fishing_impact"].lower()
        )

    def test_medium_pressure_falling(self):
        # current ≥1010 but <1020; falling trend
        resp = _pres_resp(["1012.0", "1014.0", "1015.5"])
        with patch("services.ndbc.http_get", return_value=resp):
            result = fetch_barometric_pressure()
        assert result is not None
        assert result["trend"] == "Falling"
        assert (
            "frenzy" in result["fishing_impact"].lower()
            or "dropping" in result["fishing_impact"].lower()
        )

    def test_medium_pressure_rising(self):
        resp = _pres_resp(["1013.0", "1011.5", "1010.0"])
        with patch("services.ndbc.http_get", return_value=resp):
            result = fetch_barometric_pressure()
        assert result is not None
        assert result["trend"] == "Rising"
        assert "rising" in result["fishing_impact"].lower()

    def test_medium_pressure_steady(self):
        resp = _pres_resp(["1013.0", "1013.0", "1013.0"])
        with patch("services.ndbc.http_get", return_value=resp):
            result = fetch_barometric_pressure()
        assert result is not None
        assert result["trend"] == "Steady"
        assert (
            "average" in result["fishing_impact"].lower()
            or "normal" in result["fishing_impact"].lower()
        )

    def test_low_pressure_rising(self):
        resp = _pres_resp(["1005.0", "1003.5", "1002.0"])
        with patch("services.ndbc.http_get", return_value=resp):
            result = fetch_barometric_pressure()
        assert result is not None
        assert result["trend"] == "Rising"
        assert (
            "improving" in result["fishing_impact"].lower()
            or "recovering" in result["fishing_impact"].lower()
        )

    def test_low_pressure_steady(self):
        resp = _pres_resp(["1005.0", "1005.0", "1005.0"])
        with patch("services.ndbc.http_get", return_value=resp):
            result = fetch_barometric_pressure()
        assert result is not None
        assert result["trend"] == "Steady"
        assert (
            "below average" in result["fishing_impact"].lower()
            or "low pressure" in result["fishing_impact"].lower()
        )

    def test_too_few_lines_skipped(self):
        """A station response with < 3 lines triggers continue → tries next station."""
        short_resp = _make_resp([_PRES_HEADER])  # only 1 line
        good_resp = _pres_resp(["1015.0"])
        with patch("services.ndbc.http_get", side_effect=[short_resp, good_resp]):
            result = fetch_barometric_pressure({"ndbc_stations": ["41110", "41037"]})
        assert result is not None

    def test_no_pres_column_skipped(self):
        """A station without a PRES column triggers continue."""
        no_pres = "#YY  MM DD hh mm WDIR WSPD GST\n#yr  mo dy hr mn degT m/s  m/s\n2026 06 26 18 00 180 5.0 7.0"
        resp_no_pres = MagicMock()
        resp_no_pres.raise_for_status.return_value = None
        resp_no_pres.text = no_pres
        good_resp = _pres_resp(["1015.0"])
        with patch("services.ndbc.http_get", side_effect=[resp_no_pres, good_resp]):
            result = fetch_barometric_pressure({"ndbc_stations": ["41110", "41037"]})
        assert result is not None

    def test_all_missing_pressure_values_skipped(self):
        """If every PRES reading is MM, pressures list stays empty → continue."""
        resp_mm = _pres_resp(["MM", "MM"])
        good_resp = _pres_resp(["1015.0"])
        with patch("services.ndbc.http_get", side_effect=[resp_mm, good_resp]):
            result = fetch_barometric_pressure({"ndbc_stations": ["41110", "41037"]})
        assert result is not None

    def test_short_pressure_data_line_skipped(self):
        """A data line with fewer fields than header columns triggers continue."""
        resp = _make_resp([_PRES_HEADER, _PRES_UNITS, "2026 06 26 18 00"])
        good_resp = _pres_resp(["1015.0"])
        with patch("services.ndbc.http_get", side_effect=[resp, good_resp]):
            result = fetch_barometric_pressure({"ndbc_stations": ["41110", "41037"]})
        assert result is not None

    def test_all_stations_fail_returns_none(self):
        with patch("services.ndbc.http_get", side_effect=Exception("timeout")):
            result = fetch_barometric_pressure({"ndbc_stations": ["41110"]})
        assert result is None

    def test_returns_inhg_conversion(self):
        resp = _pres_resp(["1013.0"])
        with patch("services.ndbc.http_get", return_value=resp):
            result = fetch_barometric_pressure()
        assert result is not None
        assert result["pressure_inhg"] == pytest.approx(1013.0 * 0.02953, abs=0.01)
