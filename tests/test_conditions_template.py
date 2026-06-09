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


def test_species_card_embeds_official_source():
    """Species cards should embed the official regulation source URL."""
    template = Path("templates/partials/_species.html").read_text(encoding="utf-8")
    assert "data-reg-official-source" in template


def test_regulation_modal_shows_official_source_link():
    """The regulation modal should render a link to the official source URL.

    The modal markup lives in index.html, the source URL is emitted onto each
    species card in _species.html, and the modal-rendering JS (which reads it)
    lives in page.js after the inline script was extracted into that file.
    """
    index = Path("templates/index.html").read_text(encoding="utf-8")
    species = Path("templates/partials/_species.html").read_text(encoding="utf-8")
    page_js = Path("static/js/page.js").read_text(encoding="utf-8")

    # Modal shell is present in the dashboard.
    assert "reg-modal" in index
    # The official source URL is carried on each species card…
    assert "official_source" in species
    assert "data-reg-official-source" in species
    # …and rendered as a link by the modal JS.
    assert "regOfficialSource" in page_js
    assert "reg-source" in page_js


def test_shows_air_temp_stat_card():
    template = Path("templates/partials/_conditions.html").read_text(encoding="utf-8")

    assert "Air Temp" in template
    assert "air_temp_f" in template


def test_air_temp_falls_back_to_environmental_metrics():
    template = Path("templates/partials/_conditions.html").read_text(encoding="utf-8")

    assert "forecast.environment.air_temp_f" in template


def test_pressure_falls_back_to_environmental_metrics():
    template = Path("templates/partials/_conditions.html").read_text(encoding="utf-8")

    assert "forecast.environment.air_pressure_mb" in template
    assert "CO-OPS latest reading" in template
