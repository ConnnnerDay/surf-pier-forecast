"""Tests for the opt-in notification service (services/notifications.py).

The pure decision logic and the orchestration are tested without real email,
push, network, or forecast generation by injecting collaborators.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import services.notifications as notif


EAST = ZoneInfo("America/New_York")


def _forecast(verdict="Good", score=72, tags=None, best_times=None, tides=None, gear=None):
    timeline = []
    for h in range(24):
        tag = (tags or {}).get(h, "low")
        timeline.append({"hour": h, "tag": tag, "level": 80 if tag in ("high", "prime") else 20})
    return {
        "conditions": {
            "verdict": verdict,
            "verdict_for_angler": verdict,
            "fishability_score_for_angler": score,
            "summary": "Light wind, flat surf",
        },
        "activity_timeline": timeline,
        "best_times": best_times if best_times is not None else [
            {"window": "5:30 - 7:30 AM", "reason": "Dawn bite", "quality": "Prime"}
        ],
        "tides": tides if tides is not None else [
            {"hour": 14.5, "type": "High", "time": "2:30 PM"},
            {"hour": 8.0, "type": "Low", "time": "8:00 AM"},
        ],
        "gear_checklist": gear if gear is not None else [
            {"category": "Rigs", "item": "Hi-lo rig"},
            {"category": "Bait", "item": "Fresh shrimp"},
        ],
    }


class TestEvaluateForecast:
    def test_meets_threshold_fires(self):
        now = datetime(2026, 6, 9, 5, 0, tzinfo=EAST)
        d = notif.evaluate_forecast(_forecast("Good"), {"min_rating": "Good"}, now)
        assert d is not None
        assert d["verdict"] == "Good"
        assert d["window"] == "5:30 - 7:30 AM"

    def test_below_threshold_skipped(self):
        now = datetime(2026, 6, 9, 5, 0, tzinfo=EAST)
        d = notif.evaluate_forecast(_forecast("Fair"), {"min_rating": "Good"}, now)
        assert d is None

    def test_excellent_threshold_requires_excellent(self):
        now = datetime(2026, 6, 9, 5, 0, tzinfo=EAST)
        assert notif.evaluate_forecast(_forecast("Good"), {"min_rating": "Excellent"}, now) is None
        assert notif.evaluate_forecast(_forecast("Excellent"), {"min_rating": "Excellent"}, now) is not None

    def test_lead_hours_requires_upcoming_window(self):
        now = datetime(2026, 6, 9, 5, 0, tzinfo=EAST)
        # Prime window at hour 7 → within a 4h lead from 5 AM.
        fc = _forecast("Good", tags={7: "prime"})
        assert notif.evaluate_forecast(fc, {"min_rating": "Good", "lead_hours": 4}, now) is not None
        # No good window in the next 1h → suppressed despite a qualifying day.
        assert notif.evaluate_forecast(fc, {"min_rating": "Good", "lead_hours": 1}, now) is None

    def test_unknown_verdict_skipped(self):
        now = datetime(2026, 6, 9, 5, 0, tzinfo=EAST)
        assert notif.evaluate_forecast(_forecast(""), {"min_rating": "Good"}, now) is None


class TestBuildEmail:
    def test_subject_and_bodies(self):
        d = notif.evaluate_forecast(
            _forecast("Excellent", 91), {"min_rating": "Good"},
            datetime(2026, 6, 9, 5, 0, tzinfo=EAST),
        )
        subject, text, html = notif.build_email("Montauk", d)
        assert "Excellent" in subject and "Montauk" in subject
        assert "91/100" in text
        assert "5:30 - 7:30 AM" in text
        assert "<h2>" in html and "Montauk" in html

    def test_includes_next_tide_and_gear(self):
        d = notif.evaluate_forecast(
            _forecast("Good"), {"min_rating": "Good"},
            datetime(2026, 6, 9, 5, 0, tzinfo=EAST),
        )
        assert d["next_tide"] == "Low tide at 8:00 AM"  # 8 AM is the next after 5 AM
        assert "Hi-lo rig" in d["gear"]
        _s, text, html = notif.build_email("Montauk", d)
        assert "Next tide: Low tide at 8:00 AM" in text
        assert "Don't forget: Hi-lo rig" in text
        assert "Next tide:" in html

    def test_manage_link_included_when_url_given(self):
        d = notif.evaluate_forecast(
            _forecast("Good"), {"min_rating": "Good"},
            datetime(2026, 6, 9, 5, 0, tzinfo=EAST),
        )
        _s, text, html = notif.build_email("Montauk", d, manage_url="https://x.test/account")
        assert "https://x.test/account" in text
        assert "https://x.test/account" in html
        # Omitted when no URL is supplied.
        _s2, text2, html2 = notif.build_email("Montauk", d)
        assert "Manage or turn off" not in text2


class TestRunNotificationCheck:
    @pytest.fixture
    def patched(self, monkeypatch):
        sent_emails = []
        sent_push = []

        def fake_email(to, subject, text, html):
            sent_emails.append((to, subject))
            return True

        def fake_push(sub, title, body, url):
            sent_push.append((sub["endpoint"], title))
            return True

        monkeypatch.setattr(
            notif, "get_location",
            lambda lid: {"id": lid, "name": "Montauk", "timezone": "America/New_York"}
            if lid == "montauk-ny" else None,
        )
        return sent_emails, sent_push, fake_email, fake_push

    def _candidate(self, **prefs):
        base = {"enabled": True, "email": True, "push": False, "min_rating": "Good"}
        base.update(prefs)
        return {
            "user_id": 7,
            "email": "a@b.com",
            "default_location_id": None,
            "notification_prefs": base,
            "fishing_profile": {},
            "favorites": ["montauk-ny"],
            "timezone": "America/New_York",
        }

    def test_sends_email_and_dedupes(self, monkeypatch, patched):
        sent_emails, _sp, fake_email, fake_push = patched
        monkeypatch.setattr(notif, "iter_notification_candidates", lambda: [self._candidate()])
        recorded = []
        monkeypatch.setattr(notif, "was_notified", lambda u, loc, d: (u, loc, d) in recorded)
        monkeypatch.setattr(
            notif, "record_notification",
            lambda u, loc, d, w="", c="": recorded.append((u, loc, d)),
        )

        def loader(lid, loc, prof):
            return _forecast("Good")

        now = datetime(2026, 6, 9, 5, 0, tzinfo=EAST)

        s1 = notif.run_notification_check(now, forecast_loader=loader, email_sender=fake_email, push_sender=fake_push)
        assert s1["sent"] == 1
        assert sent_emails == [("a@b.com", "Good fishing at Montauk today")]

        # Second run same day → deduped, nothing sent.
        s2 = notif.run_notification_check(now, forecast_loader=loader, email_sender=fake_email, push_sender=fake_push)
        assert s2["sent"] == 0
        assert len(sent_emails) == 1

    def test_disabled_user_skipped(self, monkeypatch, patched):
        sent_emails, _sp, fake_email, fake_push = patched
        monkeypatch.setattr(notif, "iter_notification_candidates", lambda: [self._candidate(enabled=False)])
        monkeypatch.setattr(notif, "was_notified", lambda *_: False)
        monkeypatch.setattr(notif, "record_notification", lambda *a, **k: None)
        s = notif.run_notification_check(
            datetime(2026, 6, 9, 5, 0, tzinfo=EAST),
            forecast_loader=lambda *a: _forecast("Excellent"),
            email_sender=fake_email, push_sender=fake_push,
        )
        assert s["sent"] == 0
        assert sent_emails == []

    def test_push_channel_uses_subscriptions(self, monkeypatch, patched):
        _se, sent_push, fake_email, fake_push = patched
        monkeypatch.setattr(notif, "iter_notification_candidates", lambda: [self._candidate(email=False, push=True)])
        monkeypatch.setattr(notif, "was_notified", lambda *_: False)
        monkeypatch.setattr(notif, "record_notification", lambda *a, **k: None)
        monkeypatch.setattr(notif, "get_push_subscriptions", lambda uid: [{"endpoint": "https://p/ep", "p256dh": "x", "auth": "y"}])
        s = notif.run_notification_check(
            datetime(2026, 6, 9, 5, 0, tzinfo=EAST),
            forecast_loader=lambda *a: _forecast("Good"),
            email_sender=fake_email, push_sender=fake_push,
        )
        assert s["sent"] == 1
        assert sent_push and sent_push[0][0] == "https://p/ep"

    def test_no_location_match_no_send(self, monkeypatch, patched):
        sent_emails, _sp, fake_email, fake_push = patched
        cand = self._candidate()
        cand["favorites"] = ["nonexistent-zz"]
        monkeypatch.setattr(notif, "iter_notification_candidates", lambda: [cand])
        monkeypatch.setattr(notif, "was_notified", lambda *_: False)
        monkeypatch.setattr(notif, "record_notification", lambda *a, **k: None)
        s = notif.run_notification_check(
            datetime(2026, 6, 9, 5, 0, tzinfo=EAST),
            forecast_loader=lambda *a: _forecast("Excellent"),
            email_sender=fake_email, push_sender=fake_push,
        )
        assert s["sent"] == 0


class TestBuildDigestEmail:
    def test_single_item_matches_build_email(self):
        d = notif.evaluate_forecast(
            _forecast("Good"), {"min_rating": "Good"},
            datetime(2026, 6, 9, 5, 0, tzinfo=EAST),
        )
        single = notif.build_digest_email([("Montauk", d)])
        assert single == notif.build_email("Montauk", d)

    def test_multiple_items_roll_up(self):
        d = notif.evaluate_forecast(
            _forecast("Excellent", 90), {"min_rating": "Good"},
            datetime(2026, 6, 9, 5, 0, tzinfo=EAST),
        )
        subject, text, html = notif.build_digest_email(
            [("Montauk", d), ("Cape May", d)], manage_url="https://x/account"
        )
        assert "2 of your spots" in subject
        assert "Montauk" in text and "Cape May" in text
        assert "Montauk" in html and "Cape May" in html
        assert "https://x/account" in text


class TestDigestBatching:
    def test_two_locations_one_email_two_records(self, app, monkeypatch):
        from storage.sqlite import create_user, save_preferences, get_db

        uid = create_user("digest", "pass1234", email="d@example.com")
        con = get_db()
        con.execute("UPDATE users SET email_confirmed=1 WHERE id=?", (uid,))
        con.commit()
        con.close()
        save_preferences(
            uid,
            favorites=["montauk-ny", "cape-may-nj"],
            notification_prefs={"enabled": True, "email": True, "min_rating": "Good"},
        )
        monkeypatch.setattr(
            notif, "get_location",
            lambda lid: {"id": lid, "name": lid, "timezone": "America/New_York"},
        )
        emails = []
        s = notif.run_notification_check(
            datetime(2026, 6, 9, 5, 0, tzinfo=EAST),
            forecast_loader=lambda *a: _forecast("Good"),
            email_sender=lambda to, subj, t, h: emails.append(subj) or True,
            push_sender=lambda *a: False,
        )
        # One email, but both locations recorded (so dedupe covers each).
        assert len(emails) == 1
        assert "2 of your spots" in emails[0]
        assert s["sent"] == 2


class TestRunNotificationCheckRealDB:
    """End-to-end through the real SQLite layer (no mocked storage)."""

    def test_seeded_user_sends_then_dedupes(self, app, monkeypatch):
        from storage.sqlite import create_user, save_preferences, get_db

        uid = create_user("notif_e2e", "pass1234", email="e2e@example.com")
        con = get_db()
        con.execute("UPDATE users SET email_confirmed=1 WHERE id=?", (uid,))
        con.commit()
        con.close()
        save_preferences(
            uid,
            favorites=["montauk-ny"],
            notification_prefs={"enabled": True, "email": True, "min_rating": "Good"},
        )

        monkeypatch.setattr(
            notif, "get_location",
            lambda lid: {"id": lid, "name": "Montauk", "timezone": "America/New_York"}
            if lid == "montauk-ny" else None,
        )
        sent = []
        now = datetime(2026, 6, 9, 5, 0, tzinfo=EAST)

        s1 = notif.run_notification_check(
            now,
            forecast_loader=lambda *a: _forecast("Good"),
            email_sender=lambda *a: (sent.append(a[0]) or True),
            push_sender=lambda *a: False,
        )
        assert s1["candidates"] == 1 and s1["sent"] == 1
        assert sent == ["e2e@example.com"]

        # Same day → real notification_log dedupe suppresses a second send.
        s2 = notif.run_notification_check(
            now,
            forecast_loader=lambda *a: _forecast("Good"),
            email_sender=lambda *a: (sent.append(a[0]) or True),
            push_sender=lambda *a: False,
        )
        assert s2["sent"] == 0
        assert len(sent) == 1
