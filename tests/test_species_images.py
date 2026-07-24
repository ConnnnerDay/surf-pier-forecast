"""Tests for storage/species_images.py -- Wikipedia photo lookup + SQLite cache."""

from __future__ import annotations

import pytest

import storage.species_images as si
from storage.sqlite import init_db


@pytest.fixture
def db(tmp_path, monkeypatch):
    """Isolated SQLite DB with the species_image_cache table."""
    monkeypatch.setattr("storage.sqlite.DB_PATH", str(tmp_path / "test.db"))
    init_db()


class _FakeResponse:
    def __init__(self, status_code=200, payload=None, content=b""):
        self.status_code = status_code
        self._payload = payload
        self.content = content

    def json(self):
        return self._payload


def _noaa_html(image="https://www.fisheries.noaa.gov/s3/red-drum.jpg", title="Red Drum"):
    return (
        f'<html><head><meta property="og:image" content="{image}">'
        f'<meta property="og:title" content="{title}"></head><body></body></html>'
    ).encode()


def _summary(title="Red drum", thumb="https://upload.wikimedia.org/thumb.jpg", page="https://en.wikipedia.org/wiki/Red_drum"):
    return {
        "title": title,
        "thumbnail": {"source": thumb},
        "content_urls": {"desktop": {"page": page}},
    }


def test_resize_wikimedia_thumb_rewrites_width_segment():
    url = "https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/Red_drum.jpg/220px-Red_drum.jpg"
    assert si._resize_wikimedia_thumb(url, 480) == (
        "https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/Red_drum.jpg/480px-Red_drum.jpg"
    )


def test_resize_wikimedia_thumb_leaves_non_thumb_url_unchanged():
    url = "https://upload.wikimedia.org/wikipedia/commons/a/ab/Red_drum.jpg"
    assert si._resize_wikimedia_thumb(url, 480) == url


def test_fetch_from_wikipedia_upsizes_thumbnail(db, monkeypatch):
    small_thumb = "https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/Red_drum.jpg/220px-Red_drum.jpg"
    monkeypatch.setattr(
        si, "http_get", lambda url, **kw: _FakeResponse(200, _summary(thumb=small_thumb))
    )
    result = si._fetch_from_wikipedia("Red drum (puppy drum)")
    assert result["thumb_url"] == (
        f"https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/Red_drum.jpg/{si._TARGET_WIDTH}px-Red_drum.jpg"
    )


def test_cache_key_strips_parenthetical_and_lowercases():
    assert si._cache_key("Red drum (puppy drum)") == "red drum"
    assert si._cache_key("Bluefish") == "bluefish"


def test_get_species_image_empty_name_returns_none(db):
    assert si.get_species_image("") is None
    assert si.get_species_image(None) is None


def test_get_species_image_fetches_and_caches(db, monkeypatch):
    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        return _FakeResponse(200, _summary())

    monkeypatch.setattr(si, "http_get", fake_get)

    result = si.get_species_image("Red drum (puppy drum)")
    assert result == {
        "thumb_url": "https://upload.wikimedia.org/thumb.jpg",
        "page_url": "https://en.wikipedia.org/wiki/Red_drum",
        "title": "Red drum",
        "credit": "Wikipedia",
    }
    assert len(calls) == 1

    # Second lookup hits the cache -- no new HTTP call.
    result2 = si.get_species_image("Red drum (puppy drum)")
    assert result2 == result
    assert len(calls) == 1


def test_get_species_image_negative_result_is_cached(db, monkeypatch):
    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        return _FakeResponse(404, {})

    monkeypatch.setattr(si, "http_get", fake_get)

    assert si.get_species_image("Totally Fake Species") is None
    first_call_count = len(calls)
    assert first_call_count > 0

    # Still within the negative-cache TTL -- no further HTTP calls.
    assert si.get_species_image("Totally Fake Species") is None
    assert len(calls) == first_call_count


def test_fetch_falls_back_to_opensearch_when_no_thumbnail(db, monkeypatch):
    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        if "opensearch" in url:
            return _FakeResponse(200, ["Gray trout", ["Weakfish"], [], []])
        if "Weakfish" in url:
            return _FakeResponse(200, _summary(title="Weakfish"))
        # Base title summary has no usable thumbnail.
        return _FakeResponse(200, {"title": "Gray trout"})

    monkeypatch.setattr(si, "http_get", fake_get)

    result = si.get_species_image("Gray trout (weakfish)")
    assert result["title"] == "Weakfish"
    assert any("opensearch" in c for c in calls)


def test_fetch_from_wikipedia_returns_none_on_malformed_json(db, monkeypatch):
    monkeypatch.setattr(si, "http_get", lambda url, **kw: _FakeResponse(200, ["unexpected", "shape"]))
    assert si._fetch_from_wikipedia("Bluefish") is None


def test_fetch_returns_none_on_request_exception(db, monkeypatch):
    def fake_get(url, **kwargs):
        raise ConnectionError("boom")

    monkeypatch.setattr(si, "http_get", fake_get)

    assert si.get_species_image("Bluefish") is None


def test_cache_get_db_exception_degrades_to_none(db, monkeypatch):
    def boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(si, "get_db", boom)

    assert si._cache_get("bluefish") is None


def test_cache_set_db_exception_does_not_raise(db, monkeypatch):
    def boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(si, "get_db", boom)

    si._cache_set("bluefish", {"thumb_url": "x", "page_url": "y", "title": "z"})


# ---------------------------------------------------------------------------
# NOAA Fisheries fallback source
# ---------------------------------------------------------------------------


def test_noaa_slug_normalizes_common_names():
    assert si._noaa_slug("Red drum (puppy drum)") == "red-drum"
    assert si._noaa_slug("Spanish mackerel") == "spanish-mackerel"
    assert si._noaa_slug("") == ""


def test_fetch_from_noaa_extracts_og_image(db, monkeypatch):
    def fake_get(url, **kwargs):
        assert url == "https://www.fisheries.noaa.gov/species/red-drum"
        return _FakeResponse(200, content=_noaa_html())

    monkeypatch.setattr(si, "http_get", fake_get)

    result = si._fetch_from_noaa("Red drum (puppy drum)")
    assert result == {
        "thumb_url": "https://www.fisheries.noaa.gov/s3/red-drum.jpg",
        "page_url": "https://www.fisheries.noaa.gov/species/red-drum",
        "title": "Red Drum",
        "credit": "NOAA Fisheries",
    }


def test_fetch_from_noaa_resolves_relative_og_image_url(db, monkeypatch):
    monkeypatch.setattr(
        si,
        "http_get",
        lambda url, **kw: _FakeResponse(200, content=_noaa_html(image="/s3/2021-05/red-drum.jpg")),
    )
    result = si._fetch_from_noaa("Red drum")
    assert result["thumb_url"] == "https://www.fisheries.noaa.gov/s3/2021-05/red-drum.jpg"


def test_fetch_from_noaa_returns_none_without_og_image(db, monkeypatch):
    monkeypatch.setattr(
        si, "http_get", lambda url, **kw: _FakeResponse(200, content=b"<html><head></head></html>")
    )
    assert si._fetch_from_noaa("Bluefish") is None


def test_fetch_from_noaa_returns_none_on_404(db, monkeypatch):
    monkeypatch.setattr(si, "http_get", lambda url, **kw: _FakeResponse(404))
    assert si._fetch_from_noaa("Bluefish") is None


def test_fetch_from_noaa_returns_none_on_oversized_response(db, monkeypatch):
    monkeypatch.setattr(
        si,
        "http_get",
        lambda url, **kw: _FakeResponse(200, content=b"x" * (si._NOAA_MAX_RESPONSE_BYTES + 1)),
    )
    assert si._fetch_from_noaa("Bluefish") is None


def test_get_species_image_falls_back_to_noaa_when_others_have_nothing(db, monkeypatch):
    def fake_get(url, **kwargs):
        if "en.wikipedia.org" in url or "commons.wikimedia.org" in url:
            return _FakeResponse(404, {})
        return _FakeResponse(200, content=_noaa_html(title="Bluefish"))

    monkeypatch.setattr(si, "http_get", fake_get)

    result = si.get_species_image("Bluefish")
    assert result["credit"] == "NOAA Fisheries"
    assert result["title"] == "Bluefish"


def test_get_species_image_none_when_all_sources_miss(db, monkeypatch):
    monkeypatch.setattr(si, "http_get", lambda url, **kw: _FakeResponse(404, {}))
    assert si.get_species_image("Totally Fake Species") is None


# ---------------------------------------------------------------------------
# Wikimedia Commons fallback source
# ---------------------------------------------------------------------------


def _commons_search_result(pages):
    return {"query": {"pages": pages}}


def test_fetch_from_commons_returns_first_photo_candidate(db, monkeypatch):
    payload = _commons_search_result(
        {
            "1": {
                "index": 2,
                "title": "File:Red drum range map.svg",
                "imageinfo": [
                    {
                        "url": "https://upload.wikimedia.org/red-drum-map.svg",
                        "descriptionurl": "https://commons.wikimedia.org/wiki/File:Red_drum_range_map.svg",
                    }
                ],
            },
            "2": {
                "index": 1,
                "title": "File:Red drum.jpg",
                "imageinfo": [
                    {
                        "url": "https://upload.wikimedia.org/red-drum.jpg",
                        "descriptionurl": "https://commons.wikimedia.org/wiki/File:Red_drum.jpg",
                    }
                ],
            },
        }
    )
    monkeypatch.setattr(si, "http_get", lambda url, **kw: _FakeResponse(200, payload))

    result = si._fetch_from_commons("Red drum (puppy drum)")
    # The map (index 2) is skipped for both its filename and its .svg
    # extension; the photo (index 1, ranked first by Commons) wins.
    assert result == {
        "thumb_url": "https://upload.wikimedia.org/red-drum.jpg",
        "page_url": "https://commons.wikimedia.org/wiki/File:Red_drum.jpg",
        "title": "Red drum",
        "credit": "Wikimedia Commons",
    }


def test_fetch_from_commons_prefers_resized_thumburl_over_original(db, monkeypatch):
    payload = _commons_search_result(
        {
            "1": {
                "index": 1,
                "title": "File:Red drum.jpg",
                "imageinfo": [
                    {
                        "url": "https://upload.wikimedia.org/red-drum-original-huge.jpg",
                        "thumburl": "https://upload.wikimedia.org/480px-red-drum.jpg",
                        "descriptionurl": "https://commons.wikimedia.org/wiki/File:Red_drum.jpg",
                    }
                ],
            }
        }
    )
    monkeypatch.setattr(si, "http_get", lambda url, **kw: _FakeResponse(200, payload))

    result = si._fetch_from_commons("Red drum")
    assert result["thumb_url"] == "https://upload.wikimedia.org/480px-red-drum.jpg"


def test_fetch_from_commons_extension_check_uses_original_not_thumburl(db, monkeypatch):
    """A range-map SVG's thumburl is rendered as .png by Commons, which must
    not let it slip past the photo-extension filter disguised as a photo."""
    payload = _commons_search_result(
        {
            "1": {
                "index": 1,
                "title": "File:Bluefish.jpg",
                "imageinfo": [
                    {
                        "url": "https://upload.wikimedia.org/range-map.svg",
                        "thumburl": "https://upload.wikimedia.org/480px-range-map.svg.png",
                        "descriptionurl": "x",
                    }
                ],
            }
        }
    )
    monkeypatch.setattr(si, "http_get", lambda url, **kw: _FakeResponse(200, payload))
    assert si._fetch_from_commons("Bluefish") is None


def test_fetch_from_commons_returns_none_when_only_non_photo_results(db, monkeypatch):
    payload = _commons_search_result(
        {
            "1": {
                "index": 1,
                "title": "File:Bluefish distribution map.svg",
                "imageinfo": [{"url": "https://upload.wikimedia.org/map.svg", "descriptionurl": "x"}],
            }
        }
    )
    monkeypatch.setattr(si, "http_get", lambda url, **kw: _FakeResponse(200, payload))
    assert si._fetch_from_commons("Bluefish") is None


def test_fetch_from_commons_returns_none_on_no_results(db, monkeypatch):
    monkeypatch.setattr(
        si, "http_get", lambda url, **kw: _FakeResponse(200, _commons_search_result({}))
    )
    assert si._fetch_from_commons("Bluefish") is None


def test_fetch_from_commons_returns_none_on_non_200(db, monkeypatch):
    monkeypatch.setattr(si, "http_get", lambda url, **kw: _FakeResponse(404, {}))
    assert si._fetch_from_commons("Bluefish") is None


def test_fetch_from_commons_returns_none_on_malformed_json(db, monkeypatch):
    """A non-dict JSON body (or a request exception) must degrade to None,
    not raise -- this is the bug caught while wiring the fallback chain."""
    monkeypatch.setattr(si, "http_get", lambda url, **kw: _FakeResponse(200, ["unexpected", "shape"]))
    assert si._fetch_from_commons("Bluefish") is None


def test_get_species_image_falls_back_to_commons_when_wikipedia_has_nothing(db, monkeypatch):
    def fake_get(url, **kwargs):
        if "en.wikipedia.org" in url:
            return _FakeResponse(404, {})
        if "commons.wikimedia.org" in url:
            payload = _commons_search_result(
                {
                    "1": {
                        "index": 1,
                        "title": "File:Bluefish.jpg",
                        "imageinfo": [
                            {
                                "url": "https://upload.wikimedia.org/bluefish.jpg",
                                "descriptionurl": "https://commons.wikimedia.org/wiki/File:Bluefish.jpg",
                            }
                        ],
                    }
                }
            )
            return _FakeResponse(200, payload)
        raise AssertionError(f"unexpected URL {url}")

    monkeypatch.setattr(si, "http_get", fake_get)

    result = si.get_species_image("Bluefish")
    assert result["credit"] == "Wikimedia Commons"
    assert result["thumb_url"] == "https://upload.wikimedia.org/bluefish.jpg"
