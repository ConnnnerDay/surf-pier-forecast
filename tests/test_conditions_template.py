from pathlib import Path


def test_removed_redundant_conditions_sections():
    template = Path("templates/partials/_conditions.html").read_text(encoding="utf-8")

    assert "Light, Lunar & Safety Outlook" not in template
    assert "Target Species Seasonality" not in template
    assert "Pier Info & Amenities" not in template
    assert "Education & Safety" not in template


def test_keeps_core_forecast_sections():
    template = Path("templates/partials/_conditions.html").read_text(encoding="utf-8")

    assert "Currents & Slack Windows" in template
    assert "When to Fish" in template


def test_shows_uv_index_stat_in_marine_conditions():
    template = Path("templates/partials/_conditions.html").read_text(encoding="utf-8")

    assert "UV Index" in template
    assert "forecast.uv.index" in template
