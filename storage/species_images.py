"""Species photo lookup: fetches a representative photo per species from
Wikipedia, Wikimedia Commons, or NOAA Fisheries (tried in that order) and
caches the result in SQLite.

Only the small number of species actually shown in a given forecast (capped
at 10 by ``build_species_ranking``) are ever looked up, and each species is
looked up at most once ever — the cache has no meaningful TTL because a
species' photo essentially never changes. A miss (no usable photo found from
either source) is cached too, but for a much shorter window, so a temporary
outage doesn't wrongly stick a species with "no photo" forever.

Three sources are tried in order:

1. Wikipedia's page-summary API (falling back to opensearch when the common
   name doesn't match an article title directly) -- broad species coverage,
   images are Commons-hosted and machine-taggable with a license.
2. Wikimedia Commons' own search API -- catches species that have Commons
   photos but no full Wikipedia article. Since this is a fuzzy full-text
   search rather than a curated article lookup, results are filtered to
   photo file extensions and away from filenames that look like maps/
   diagrams/icons rather than an actual photo of the fish.
3. NOAA Fisheries species profile pages (``fisheries.noaa.gov/species/<slug>``)
   -- narrower coverage (only species NOAA profiles, which happens to overlap
   heavily with recreationally-fished species this app covers) but the
   photos are U.S. government work, so public domain. The slug is guessed
   from the species' common name; when the guess misses, this source simply
   contributes nothing rather than guessing further (Wikipedia is the primary
   source for that reason).

Network access degrades gracefully everywhere: any failure (timeout, 404, no
usable image, malformed response) simply returns ``None`` from that source,
so an outage at any one provider never breaks forecast generation -- it just
means that run's cards render without a photo.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any, Optional
from urllib.parse import quote, urljoin

from bs4 import BeautifulSoup

from services.http_client import get as http_get
from storage.sqlite import get_db

logger = logging.getLogger(__name__)

_FOUND_TTL_SECONDS = 180 * 24 * 3600  # 180 days -- photos rarely change
_NOT_FOUND_TTL_SECONDS = 14 * 24 * 3600  # 14 days -- allow retrying misses
_TIMEOUT: tuple[float, float] = (3.05, 8)

_WIKIPEDIA_SUMMARY = "https://en.wikipedia.org/api/rest_v1/page/summary/{}"
_WIKIPEDIA_OPENSEARCH = (
    "https://en.wikipedia.org/w/api.php"
    "?action=opensearch&limit=1&namespace=0&format=json&search={}"
)
_NOAA_SPECIES_PAGE = "https://www.fisheries.noaa.gov/species/{}"
_NOAA_MAX_RESPONSE_BYTES = 2 * 1024 * 1024

_COMMONS_SEARCH = (
    "https://commons.wikimedia.org/w/api.php"
    "?action=query&generator=search&gsrsearch={query}&gsrnamespace=6&gsrlimit=5"
    "&prop=imageinfo&iiprop=url&iiurlwidth={width}&format=json"
)
_PHOTO_EXT_RE = re.compile(r"\.(jpe?g|png)$", re.IGNORECASE)

# Requested rendering width for both Wikipedia and Commons photos. Shared by
# the small card thumbnail and the larger regs-modal photo (~220px tall), and
# generous enough to stay sharp at 2-3x device pixel ratios; Wikimedia's
# thumbnailing service caps this at the source image's own size, so it never
# upscales a small original into a blurry mess.
_TARGET_WIDTH = 480
_THUMB_WIDTH_RE = re.compile(r"/(\d+)px-")
# Commons File: search returns diagrams, range maps, and icons alongside real
# photos; filenames for those non-photo files reliably contain one of these
# words, so they're excluded rather than risk showing a map as "the fish".
_NON_PHOTO_NAME_RE = re.compile(
    r"(map|distribution|range|diagram|icon|logo)", re.IGNORECASE
)


def _cache_key(species_name: str) -> str:
    """Normalize a species name to a cache key.

    Common names carry an alt-name parenthetical (e.g. "Red drum (puppy
    drum)") that isn't part of the Wikipedia article title, so it's stripped
    the same way ``build_bait_ranking`` derives its lookup key.
    """
    return species_name.split("(")[0].strip().lower()


# Sentinel distinguishing "cached: no photo available" from "not cached at
# all" -- both would otherwise look like a plain ``None`` to the caller.
_NEGATIVE = object()


def _cache_get(species_key: str) -> Any:
    """Return the cached image dict, ``_NEGATIVE``, or ``None``.

    ``None`` means there's no usable cache entry (never cached, or cached but
    past its TTL) and a live lookup should run. ``_NEGATIVE`` means a prior
    lookup found nothing and that result is still within its (shorter) TTL.
    """
    try:
        conn = get_db()
        row = conn.execute(
            "SELECT found, image_json, fetched_at FROM species_image_cache "
            "WHERE species_key=?",
            (species_key,),
        ).fetchone()
        conn.close()
        if not row:
            return None
        fetched_at = datetime.fromisoformat(str(row["fetched_at"]))
        age = (datetime.utcnow() - fetched_at).total_seconds()
        ttl = _FOUND_TTL_SECONDS if row["found"] else _NOT_FOUND_TTL_SECONDS
        if age > ttl:
            return None
        return json.loads(row["image_json"]) if row["image_json"] else _NEGATIVE
    except Exception:
        return None


def _cache_set(species_key: str, image: Optional[dict[str, Any]]) -> None:
    try:
        conn = get_db()
        conn.execute(
            "INSERT OR REPLACE INTO species_image_cache "
            "(species_key, found, image_json, fetched_at) "
            "VALUES (?, ?, ?, datetime('now'))",
            (species_key, 1 if image else 0, json.dumps(image) if image else None),
        )
        conn.commit()
        conn.close()
    except Exception:
        logger.warning(
            "species_images: failed to write cache for %r", species_key, exc_info=True
        )


def _opensearch_title(name: str) -> Optional[str]:
    """Find the closest-matching Wikipedia article title for ``name``."""
    try:
        resp = http_get(
            _WIKIPEDIA_OPENSEARCH.format(quote(name)),
            endpoint="species_images.opensearch",
            timeout=_TIMEOUT,
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        titles = data[1] if isinstance(data, list) and len(data) > 1 else []
        return titles[0] if titles else None
    except Exception:
        return None


def _fetch_summary(title: str) -> Optional[dict[str, Any]]:
    try:
        resp = http_get(
            _WIKIPEDIA_SUMMARY.format(quote(title)),
            endpoint="species_images.wikipedia_summary",
            timeout=_TIMEOUT,
        )
        if resp.status_code != 200:
            return None
        return resp.json()
    except Exception:
        return None


def _resize_wikimedia_thumb(thumb_url: str, width: int) -> str:
    """Request a differently-sized rendering of a Wikimedia thumbnail.

    Wikimedia's thumbnail URLs encode the requested pixel width right before
    the filename (e.g. ``.../thumb/a/ab/Red_drum.jpg/320px-Red_drum.jpg``),
    and the thumbnailing service will render any width up to the source
    image's own size on request -- editing that segment is enough, no extra
    API call needed. The page-summary API's default thumbnail is sized for a
    mobile preview card, which is too small to stay sharp once displayed at
    2-3x device pixel ratio; this asks for something sharper instead.
    Left unchanged if the URL doesn't look like a thumb URL (best-effort).
    """
    return _THUMB_WIDTH_RE.sub(f"/{width}px-", thumb_url, count=1)


def _fetch_from_wikipedia(species_name: str) -> Optional[dict[str, Any]]:
    title = species_name.split("(")[0].strip()
    data = _fetch_summary(title)
    if data is None or "thumbnail" not in data:
        alt_title = _opensearch_title(title)
        if alt_title and alt_title.lower() != title.lower():
            data = _fetch_summary(alt_title)

    if not isinstance(data, dict):
        return None
    thumb_url = (data.get("thumbnail") or {}).get("source")
    if not thumb_url:
        return None
    thumb_url = _resize_wikimedia_thumb(thumb_url, _TARGET_WIDTH)
    page_url = ((data.get("content_urls") or {}).get("desktop") or {}).get("page", "")

    return {
        "thumb_url": thumb_url,
        "page_url": page_url,
        "title": data.get("title", title),
        "credit": "Wikipedia",
    }


def _fetch_from_commons(species_name: str) -> Optional[dict[str, Any]]:
    """Search Wikimedia Commons directly for a species photo.

    Unlike ``_fetch_from_wikipedia``, this isn't gated on the species having
    a full Wikipedia article -- it's a fuzzy full-text search over Commons'
    File: namespace, so results are filtered down to plausible photo files
    (see ``_PHOTO_EXT_RE`` / ``_NON_PHOTO_NAME_RE``) before accepting one.
    """
    title = species_name.split("(")[0].strip()
    if not title:
        return None
    query = f"{title} fish"
    try:
        resp = http_get(
            _COMMONS_SEARCH.format(query=quote(query), width=_TARGET_WIDTH),
            endpoint="species_images.commons_search",
            timeout=_TIMEOUT,
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
    except Exception:
        return None
    if not isinstance(data, dict):
        return None

    pages = ((data.get("query") or {}).get("pages")) or {}
    # `index` is MediaWiki's own relevance rank for generator=search results;
    # dict iteration order isn't guaranteed to match it.
    candidates = sorted(pages.values(), key=lambda p: p.get("index", 0))
    for page in candidates:
        imageinfo = page.get("imageinfo") or []
        if not imageinfo:
            continue
        info = imageinfo[0]
        # The extension check must run against the *original* file, not the
        # resized thumbnail -- Commons renders every thumbnail as PNG/JPG
        # regardless of source type, so an SVG range map would otherwise slip
        # past the filter disguised as a "photo.png".
        original_url = info.get("url") or ""
        page_title = page.get("title", "")
        if not _PHOTO_EXT_RE.search(original_url):
            continue
        if _NON_PHOTO_NAME_RE.search(page_title):
            continue
        return {
            "thumb_url": info.get("thumburl") or original_url,
            "page_url": info.get("descriptionurl", ""),
            "title": title,
            "credit": "Wikimedia Commons",
        }
    return None


def _noaa_slug(species_name: str) -> str:
    """Guess a fisheries.noaa.gov species-page slug from a common name."""
    title = species_name.split("(")[0].strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", title).strip("-")
    return slug


def _fetch_from_noaa(species_name: str) -> Optional[dict[str, Any]]:
    """Look up a species' NOAA Fisheries profile page and pull its hero photo.

    NOAA doesn't publish a species-lookup API, so the page URL is guessed
    from the common name (``"Red drum" -> ".../species/red-drum"``). A wrong
    guess just 404s and this source contributes nothing -- callers should
    treat NOAA as a supplementary source behind Wikipedia, not a primary one.
    """
    slug = _noaa_slug(species_name)
    if not slug:
        return None
    page_url = _NOAA_SPECIES_PAGE.format(slug)
    try:
        resp = http_get(
            page_url, endpoint="species_images.noaa_species_page", timeout=_TIMEOUT
        )
        if resp.status_code != 200:
            return None
        if len(resp.content) > _NOAA_MAX_RESPONSE_BYTES:
            return None
        soup = BeautifulSoup(resp.content, "html.parser")
    except Exception:
        return None

    og_image = soup.find("meta", property="og:image")
    raw_thumb_url = og_image.get("content") if og_image else None
    if not raw_thumb_url:
        return None
    # The Open Graph spec requires an absolute URL here, but resolve it
    # against the page URL anyway in case a real-world page emits a
    # protocol-relative or root-relative path -- a relative <img src> would
    # otherwise resolve against *this app's* origin in the browser and 404.
    thumb_url = urljoin(page_url, str(raw_thumb_url))

    og_title = soup.find("meta", property="og:title")
    title = (og_title.get("content") if og_title else None) or species_name

    return {
        "thumb_url": thumb_url,
        "page_url": page_url,
        "title": title,
        "credit": "NOAA Fisheries",
    }


def _fetch_image(species_name: str) -> Optional[dict[str, Any]]:
    """Try each photo source in order, returning the first usable result."""
    return (
        _fetch_from_wikipedia(species_name)
        or _fetch_from_commons(species_name)
        or _fetch_from_noaa(species_name)
    )


def get_species_image(species_name: str) -> Optional[dict[str, Any]]:
    """Return ``{"thumb_url", "page_url", "title", "credit"}`` for a species,
    or ``None`` if no source has a usable photo.

    Backed by a persistent cache (see module docstring), so a live lookup
    only happens the first time -- ever, across all users -- a given species
    is looked up.
    """
    if not species_name:
        return None
    key = _cache_key(species_name)
    if not key:
        return None

    cached = _cache_get(key)
    if cached is _NEGATIVE:
        return None
    if cached is not None:
        return cached

    image = _fetch_image(species_name)
    _cache_set(key, image)
    return image
