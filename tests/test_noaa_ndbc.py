"""Tests for services/noaa.py and services/ndbc.py — NOAA CO-OPS and NDBC buoy parsing."""

from __future__ import annotations

from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# services/noaa.py — fetch_latest_coops_product
# ---------------------------------------------------------------------------


class TestFetchLatestCoopsProduct:
    def _mock_resp(self, body: dict) -> MagicMock:
        m = MagicMock()
        m.raise_for_status.return_value = None
        m.json.return_value = body
        return m

    def test_returns_float_value(self):
        from services.noaa import fetch_latest_coops_product

        body = {"data": [{"v": "72.4"}]}
        with patch("services.noaa.http_get", return_value=self._mock_resp(body)):
            result = fetch_latest_coops_product("8651370", "water_temperature")
        assert result == 72.4

    def test_returns_none_for_empty_value(self):
        from services.noaa import fetch_latest_coops_product

        body = {"data": [{"v": ""}]}
        with patch("services.noaa.http_get", return_value=self._mock_resp(body)):
            result = fetch_latest_coops_product("8651370", "water_temperature")
        assert result is None

    def test_returns_none_for_null_value(self):
        from services.noaa import fetch_latest_coops_product

        body = {"data": [{"v": None}]}
        with patch("services.noaa.http_get", return_value=self._mock_resp(body)):
            result = fetch_latest_coops_product("8651370", "water_temperature")
        assert result is None

    def test_returns_none_for_empty_data_list(self):
        from services.noaa import fetch_latest_coops_product

        body = {"data": []}
        with patch("services.noaa.http_get", return_value=self._mock_resp(body)):
            result = fetch_latest_coops_product("8651370", "water_temperature")
        assert result is None

    def test_returns_none_on_exception(self):
        from services.noaa import fetch_latest_coops_product

        with patch("services.noaa.http_get", side_effect=Exception("timeout")):
            result = fetch_latest_coops_product("8651370", "water_temperature")
        assert result is None


class TestFetchCoopsEnvironmentalMetrics:
    def test_returns_dict_with_available_values(self):
        from services.noaa import fetch_coops_environmental_metrics

        def fake_product(station_id, product, units="english"):
            values = {
                "air_temperature": 72.0,
                "humidity": 65.0,
                "air_pressure": 1013.5,
            }
            return values.get(product)

        with patch(
            "services.noaa.fetch_latest_coops_product", side_effect=fake_product
        ):
            result = fetch_coops_environmental_metrics("8651370")

        assert "air_temp_f" in result
        assert result["air_temp_f"] == 72.0
        assert "humidity_pct" in result
        assert result["humidity_pct"] == 65.0
        assert "air_pressure_mb" in result
        # Products returning None are not included
        assert "salinity_psu" not in result

    def test_returns_empty_dict_when_all_fail(self):
        from services.noaa import fetch_coops_environmental_metrics

        with patch("services.noaa.fetch_latest_coops_product", return_value=None):
            result = fetch_coops_environmental_metrics("8651370")
        assert result == {}

    def test_values_are_rounded(self):
        from services.noaa import fetch_coops_environmental_metrics

        with patch(
            "services.noaa.fetch_latest_coops_product", return_value=72.12345678
        ):
            result = fetch_coops_environmental_metrics("8651370")
        for v in result.values():
            # Should be rounded to 2 decimal places
            assert round(v, 2) == v


# ---------------------------------------------------------------------------
# services/ndbc.py — _deg_to_compass (pure function)
# ---------------------------------------------------------------------------


class TestDegToCompass:
    def test_north(self):
        from services.ndbc import _deg_to_compass

        assert _deg_to_compass(0) == "N"
        assert _deg_to_compass(360) == "N"

    def test_east(self):
        from services.ndbc import _deg_to_compass

        assert _deg_to_compass(90) == "E"

    def test_south(self):
        from services.ndbc import _deg_to_compass

        assert _deg_to_compass(180) == "S"

    def test_west(self):
        from services.ndbc import _deg_to_compass

        assert _deg_to_compass(270) == "W"

    def test_northeast(self):
        from services.ndbc import _deg_to_compass

        assert _deg_to_compass(45) == "NE"

    def test_southwest(self):
        from services.ndbc import _deg_to_compass

        assert _deg_to_compass(225) == "SW"

    def test_southeast(self):
        from services.ndbc import _deg_to_compass

        assert _deg_to_compass(135) == "SE"

    def test_northwest(self):
        from services.ndbc import _deg_to_compass

        assert _deg_to_compass(315) == "NW"


# ---------------------------------------------------------------------------
# services/ndbc.py — _try_ndbc_station (mocked HTTP)
# ---------------------------------------------------------------------------

_NDBC_SAMPLE = """\
#YY  MM DD hh mm WDIR WSPD GST  WVHT   DPD   APD MWD   PRES  ATMP  WTMP  DEWP  VIS PTDY  TIDE
#yr  mo dy hr mn degT m/s  m/s     m   sec   sec degT   hPa  degC  degC  degC  nmi  hPa    ft
2024 04 15 18 00  220  7.2  9.1  1.5  10.0   6.5 200 1013.4  20.1  22.3  15.0   MM   MM    MM
2024 04 15 17 30  215  6.8  8.5  1.4   9.8   6.3 195 1013.2  20.0  22.2  14.9   MM   MM    MM
"""


class TestTryNdbcStation:
    def test_parses_wind_speed_and_wave_height(self):
        from services.ndbc import _try_ndbc_station

        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.text = _NDBC_SAMPLE

        with patch("services.ndbc.http_get", return_value=mock_resp):
            wind, wave, direction = _try_ndbc_station("41110")

        assert wind is not None
        assert wave is not None
        assert direction is not None
        # Wind: 7.2 m/s * 1.94384 ≈ 14.0 kt
        assert wind[0] == wind[1] or wind[0] <= wind[1]
        assert wind[0] > 0
        # Wave: 1.5 m * 3.28084 ≈ 4.9 ft
        assert wave[0] > 0
        # Direction: 220° → SW
        assert direction == "SW"

    def test_returns_none_for_short_response(self):
        from services.ndbc import _try_ndbc_station

        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.text = "#header1\n#header2\n"  # only 2 lines

        with patch("services.ndbc.http_get", return_value=mock_resp):
            wind, wave, direction = _try_ndbc_station("41110")

        assert wind is None
        assert wave is None
        assert direction is None

    def test_skips_missing_values(self):
        from services.ndbc import _try_ndbc_station

        # All MM values — should return all None
        sample = """\
#YY  MM DD hh mm WDIR WSPD GST  WVHT   DPD
#yr  mo dy hr mn degT m/s  m/s     m   sec
2024 04 15 18 00   MM   MM   MM    MM    MM
"""
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.text = sample

        with patch("services.ndbc.http_get", return_value=mock_resp):
            wind, wave, direction = _try_ndbc_station("41110")

        assert wind is None
        assert wave is None
        assert direction is None


# ---------------------------------------------------------------------------
# services/ndbc.py — fetch_barometric_pressure (mocked HTTP)
# ---------------------------------------------------------------------------

_NDBC_PRESSURE_SAMPLE = """\
#YY  MM DD hh mm WDIR WSPD GST  WVHT   DPD   APD MWD   PRES  ATMP  WTMP
#yr  mo dy hr mn degT m/s  m/s     m   sec   sec degT   hPa  degC  degC
2024 04 15 18 00  220  7.2  9.1  1.5  10.0   6.5 200 1022.4  20.1  22.3
2024 04 15 17 30  215  6.8  8.5  1.4   9.8   6.3 195 1021.2  20.0  22.2
2024 04 15 17 00  210  6.5  8.0  1.3   9.5   6.1 190 1020.8  19.9  22.1
"""


class TestFetchBarometricPressure:
    def test_returns_pressure_dict_with_trend(self):
        from services.ndbc import fetch_barometric_pressure

        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.text = _NDBC_PRESSURE_SAMPLE

        with patch("services.ndbc.http_get", return_value=mock_resp):
            result = fetch_barometric_pressure({"ndbc_stations": ["41110"]})

        assert result is not None
        assert "pressure_mb" in result
        assert "pressure_inhg" in result
        assert "trend" in result
        assert "fishing_impact" in result
        assert result["pressure_mb"] == 1022.4
        assert result["trend"] == "Rising"  # 1022.4 > 1020.8 by > 1 mb

    def test_returns_none_on_all_failures(self):
        from services.ndbc import fetch_barometric_pressure

        with patch("services.ndbc.http_get", side_effect=Exception("network")):
            result = fetch_barometric_pressure({"ndbc_stations": ["41110"]})
        assert result is None

    def test_falling_pressure_impact(self):
        from services.ndbc import fetch_barometric_pressure

        # Pressure dropping significantly
        falling_sample = """\
#YY  MM DD hh mm WDIR WSPD GST  WVHT   DPD   APD MWD   PRES  ATMP  WTMP
#yr  mo dy hr mn degT m/s  m/s     m   sec   sec degT   hPa  degC  degC
2024 04 15 18 00  220  7.2  9.1  1.5  10.0   6.5 200 1008.0  20.1  22.3
2024 04 15 17 30  215  6.8  8.5  1.4   9.8   6.3 195 1009.5  20.0  22.2
2024 04 15 17 00  210  6.5  8.0  1.3   9.5   6.1 190 1010.5  19.9  22.1
"""
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.text = falling_sample

        with patch("services.ndbc.http_get", return_value=mock_resp):
            result = fetch_barometric_pressure({"ndbc_stations": ["41110"]})

        assert result is not None
        assert result["trend"] == "Falling"
        assert (
            "falling" in result["fishing_impact"].lower()
            or "dropping" in result["fishing_impact"].lower()
        )

    def test_uses_default_stations_when_no_location(self):
        from services.ndbc import fetch_barometric_pressure, NDBC_STATIONS

        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.text = _NDBC_PRESSURE_SAMPLE

        with patch("services.ndbc.http_get", return_value=mock_resp) as mock_get:
            fetch_barometric_pressure()  # no location arg

        # Should have called http_get with one of the default station IDs
        call_url = mock_get.call_args[0][0]
        default_ids = {s[0] for s in NDBC_STATIONS}
        assert any(sid in call_url for sid in default_ids)
