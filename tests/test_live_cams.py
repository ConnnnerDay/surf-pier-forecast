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
