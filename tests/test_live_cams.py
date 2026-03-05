"""Tests for live cam location filtering."""

from locations import find_nearby_live_cams


def test_find_nearby_live_cams_within_radius_sorted():
    cams = find_nearby_live_cams(34.2257, -77.7950, max_miles=10)
    assert cams, "Expected at least one nearby cam for Wrightsville Beach coordinates"
    distances = [cam["distance_miles"] for cam in cams]
    assert distances == sorted(distances)
    assert all(d <= 10 for d in distances)


def test_find_nearby_live_cams_can_exclude_pier_cams():
    all_cams = find_nearby_live_cams(34.2257, -77.7950, max_miles=10, include_pier_cams=True)
    beach_only = find_nearby_live_cams(34.2257, -77.7950, max_miles=10, include_pier_cams=False)

    assert any(cam["cam_type"] == "pier" for cam in all_cams)
    assert all(cam["cam_type"] != "pier" for cam in beach_only)


def test_live_cam_context_parses_string_fishing_types(monkeypatch):
    from web import views

    monkeypatch.setattr(
        views,
        "find_nearby_live_cams",
        lambda *_args, **kwargs: [{"url": "https://cam.example", "name": "Example", "cam_type": "pier", "distance_miles": 2.5}],
    )
    monkeypatch.setattr(views, "_cam_status", lambda _url: {"is_live": True, "status_label": "Live now"})

    context = views._build_live_cam_context(
        {"lat": 34.0, "lng": -77.0},
        {"fishing_types": "pier,surf"},
    )

    assert context["pier_cams_enabled"] is True
    assert context["nearby_live_cams"][0]["is_live"] is True


def test_live_cam_context_handles_unknown_status(monkeypatch):
    from web import views

    monkeypatch.setattr(
        views,
        "find_nearby_live_cams",
        lambda *_args, **kwargs: [{"url": "https://cam.example", "name": "Example", "cam_type": "beach", "distance_miles": 1.1}],
    )

    def _explode(_url):
        raise RuntimeError("boom")

    monkeypatch.setattr(views, "_cam_status", _explode)

    context = views._build_live_cam_context({"lat": 34.0, "lng": -77.0}, None)
    assert context["nearby_live_cams"][0]["status_label"] == "Unavailable"
    assert context["nearby_live_cams"][0]["is_live"] is False
