"""Tests for services/forecast_refresh.py — background forecast refresh queue."""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock


# ---------------------------------------------------------------------------
# refresh_forecast
# ---------------------------------------------------------------------------


class TestRefreshForecast:
    def test_returns_false_for_invalid_location(self, monkeypatch):
        monkeypatch.setattr(
            "services.forecast_refresh.get_location", lambda loc_id: None
        )
        from services.forecast_refresh import refresh_forecast

        result = refresh_forecast("nonexistent-loc")
        assert result is False

    def test_calls_generate_and_save_for_valid_location(self, monkeypatch):
        fake_loc = {"id": "loc1", "name": "Test Beach"}
        fake_forecast = {"verdict": "Good", "conditions": {}}
        monkeypatch.setattr(
            "services.forecast_refresh.get_location", lambda loc_id: fake_loc
        )
        mock_generate = MagicMock(return_value=fake_forecast)
        mock_save = MagicMock()
        monkeypatch.setattr(
            "services.forecast_refresh.generate_forecast", mock_generate
        )
        monkeypatch.setattr("services.forecast_refresh.save_forecast", mock_save)

        from services.forecast_refresh import refresh_forecast

        result = refresh_forecast("loc1", user_id=42)
        assert result is True
        mock_generate.assert_called_once_with(fake_loc)
        mock_save.assert_called_once_with(fake_forecast, "loc1", user_id=42)

    def test_returns_true_without_user_id(self, monkeypatch):
        fake_loc = {"id": "loc1", "name": "Test Beach"}
        monkeypatch.setattr(
            "services.forecast_refresh.get_location", lambda loc_id: fake_loc
        )
        monkeypatch.setattr(
            "services.forecast_refresh.generate_forecast", MagicMock(return_value={})
        )
        monkeypatch.setattr("services.forecast_refresh.save_forecast", MagicMock())

        from services.forecast_refresh import refresh_forecast

        result = refresh_forecast("loc1")
        assert result is True


# ---------------------------------------------------------------------------
# enqueue_forecast_refresh and is_refreshing
# ---------------------------------------------------------------------------


class TestEnqueueAndIsRefreshing:
    def _reset_module_state(self):
        """Reset global state in forecast_refresh module between tests."""
        import services.forecast_refresh as m

        m._refresh_queue.queue.clear()
        m._refreshing.clear()
        m._enqueued.clear()
        # Don't reset _worker_started — the worker thread keeps running

    def test_enqueue_returns_true_first_time(self, monkeypatch):
        # Prevent the worker from actually running forecast generation
        monkeypatch.setattr(
            "services.forecast_refresh.get_location", lambda loc_id: None
        )
        self._reset_module_state()

        from services.forecast_refresh import enqueue_forecast_refresh

        result = enqueue_forecast_refresh("loc1", user_id=None)
        assert result is True

    def test_enqueue_returns_false_when_already_enqueued(self, monkeypatch):
        monkeypatch.setattr(
            "services.forecast_refresh.get_location", lambda loc_id: None
        )
        self._reset_module_state()

        from services.forecast_refresh import enqueue_forecast_refresh

        first = enqueue_forecast_refresh("loc-dup", user_id=None)
        second = enqueue_forecast_refresh("loc-dup", user_id=None)
        assert first is True
        assert second is False

    def test_is_refreshing_true_when_enqueued(self, monkeypatch):
        monkeypatch.setattr(
            "services.forecast_refresh.get_location", lambda loc_id: None
        )
        self._reset_module_state()

        from services.forecast_refresh import enqueue_forecast_refresh, is_refreshing

        enqueue_forecast_refresh("loc-check", user_id=None)
        assert is_refreshing("loc-check") is True

    def test_is_refreshing_false_for_unknown_location(self):
        self._reset_module_state()
        from services.forecast_refresh import is_refreshing

        assert is_refreshing("no-such-location") is False

    def test_different_users_are_independent_queues(self, monkeypatch):
        monkeypatch.setattr(
            "services.forecast_refresh.get_location", lambda loc_id: None
        )
        self._reset_module_state()

        from services.forecast_refresh import enqueue_forecast_refresh

        r1 = enqueue_forecast_refresh("loc-multi", user_id=1)
        r2 = enqueue_forecast_refresh("loc-multi", user_id=2)
        assert r1 is True
        assert r2 is True  # different user → different key

    def test_worker_processes_queue_and_clears_refreshing(self, monkeypatch):
        """Worker should call refresh_forecast and remove entry from _refreshing."""
        done = threading.Event()

        def fake_refresh(loc_id, user_id=None):
            done.set()
            return True

        monkeypatch.setattr("services.forecast_refresh.refresh_forecast", fake_refresh)
        # Give the worker a fresh state
        self._reset_module_state()

        from services.forecast_refresh import enqueue_forecast_refresh, is_refreshing

        enqueue_forecast_refresh("loc-worker", user_id=None)
        # Wait for worker to process
        done.wait(timeout=5.0)
        # After processing, should no longer be "refreshing"
        time.sleep(0.05)  # small buffer for finally block
        assert not is_refreshing("loc-worker")
