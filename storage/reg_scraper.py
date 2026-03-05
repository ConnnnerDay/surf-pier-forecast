"""Live fishing regulation scraper with SQLite cache.

Scrapes official state agency pages on demand and caches results for 24 hours.

Supported states with live scraping:
  FL — FWC per-species pages at myfwc.com
  VA — VA Marine Resources Commission single-page listing

All other states return None so the caller falls back to the static JSON
snapshot in storage/regulations_data.json.
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter
from datetime import datetime
from threading import Lock
from typing import Any, Dict, List, Optional

import requests

from storage.sqlite import DB_PATH, get_db

_log = logging.getLogger(__name__)

_CACHE_TTL_SECONDS = 86_400   # 24 hours
_REQUEST_TIMEOUT   = 12       # seconds
_USER_AGENT = (
    "Mozilla/5.0 (compatible; SurfForecast/1.0 fishing-regulation-lookup)"
)


# ──────────────────────────────────────────────────────────────────
# Shared helpers
# ──────────────────────────────────────────────────────────────────

def _normalize_name(name: str) -> str:
    """Convert a display species name to a snake_case key."""
    return (
        str(name or "")
        .lower()
        .replace("(", "").replace(")", "")
        .replace("/", " ").replace("-", " ").replace(",", "")
        .strip()
        .replace(" ", "_")
    )


def _name_variants(display_name: str) -> List[str]:
    """Return candidate snake_case keys for a species display name.

    Returns the full normalized form first, then the short form with any
    parenthetical stripped.  The dicts in this module use short keys like
    'red_drum' rather than 'red_drum_puppy_drum'.
    """
    raw = str(display_name or "").strip()
    full = _normalize_name(raw)
    short = _normalize_name(re.sub(r"\s*\([^)]*\)", "", raw).strip())
    variants: List[str] = []
    if full:
        variants.append(full)
    if short and short != full:
        variants.append(short)
    return variants


def _strip_html(html: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&[a-zA-Z]+;", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _most_common(values: List[str]) -> str:
    """Return the most frequently occurring non-empty string in *values*."""
    cleaned = [v.strip() for v in values if v.strip()]
    if not cleaned:
        return ""
    return Counter(cleaned).most_common(1)[0][0]


# ──────────────────────────────────────────────────────────────────
# Florida — FWC per-species pages
# ──────────────────────────────────────────────────────────────────

_FL_BASE = "https://myfwc.com/fishing/saltwater/recreational/"

# species_key → URL slug on myfwc.com/fishing/saltwater/recreational/{slug}/
_FL_SLUGS: Dict[str, str] = {
    # Keys are the short snake_case form from _name_variants() (parenthetical stripped)
    "red_drum":           "red-drum",
    "spotted_seatrout":   "spotted-seatrout",
    "speckled_trout":     "spotted-seatrout",   # display name "Speckled trout (...)"
    "snook":              "snook",
    "tarpon":             "tarpon",
    "cobia":              "cobia",
    "spanish_mackerel":   "spanish-mackerel",
    "king_mackerel":      "king-mackerel",
    "gag_grouper":        "grouper-gag",
    "black_sea_bass":     "black-sea-bass",
    "pompano":            "florida-pompano",
    "sheepshead":         "sheepshead",
    "summer_flounder":    "flounder",
    "southern_flounder":  "flounder",
    "flounder":           "flounder",
    "bluefish":           "bluefish",
    "striped_bass":       "striped-bass",
    "false_albacore":     "little-tunny",
    "red_snapper":        "red-snapper",
    "weakfish":           "weakfish",
    "permit":             "permit",
    "tautog":             "tautog",
    "black_drum":         "black-drum",
    "scup":               "scup",
}


def _parse_fl_page(html: str) -> Optional[Dict[str, str]]:
    """Extract dominant regulations from a FWC species page.

    FWC pages list regulations per management region using labels like
    "Season :", "Slot limit:", "Bag limit:", and "Daily Bag Limit :".
    Capitalization and spacing vary by species page.

    We scan for all label occurrences, stop each value at the next label
    or section boundary, then return the most common value per field.
    """
    text = _strip_html(html)

    # A boundary is any regulation label or a section header keyword.
    # This stops a captured value from bleeding into the next region/field.
    # The (?:"\s) pattern catches FWC pages that end a value with a closing
    # quote followed by a space before the next region name.
    _BOUND = (
        r"(?=Season\s*:|Slot\s+[Ll]imit\s*:|Minimum\s+Size|"
        r"(?:Daily\s+)?Bag\s+[Ll]imit\s*:|"
        r"Area\s|Limits\s|Seasons\s+and\s+other|"
        r"Expand/Collapse|Region\b)"
    )

    seasons = re.findall(
        r"Season\s*:\s*(.+?)" + _BOUND,
        text, re.IGNORECASE,
    )
    sizes = re.findall(
        r"(?:Slot\s+[Ll]imit|Minimum\s+Size(?:\s+Limit)?)\s*:\s*(.+?)" + _BOUND,
        text, re.IGNORECASE,
    )
    bags = re.findall(
        r"(?:Daily\s+)?Bag\s+[Ll]imit\s*:\s*(.+?)" + _BOUND,
        text, re.IGNORECASE,
    )

    if not bags and not sizes:
        return None

    _FL_REGIONS = re.compile(
        r'\s+(?:Big Bend|Panhandle|Tampa|Sarasota|Charlotte|Southwest|'
        r'Southeast|Northeast|Indian River|Central East)\b.*$',
        re.IGNORECASE,
    )

    def _clean(val: str) -> str:
        # Trim natural end-phrases for size limits; drop explanatory text after them
        for end_phrase in (' total length', ' fork length'):
            idx = val.lower().find(end_phrase)
            if idx >= 0:
                val = val[:idx + len(end_phrase)]
        # Strip closing-quote followed by a proper noun (FWC section boundary)
        val = re.sub(r'["\u201d]\s+[A-Z].*$', '', val)
        # Strip FL management region names that bled in
        val = _FL_REGIONS.sub('', val)
        # Strip "Federal Waters Regulations" tail
        val = re.sub(r'\s+Federal\s+Waters.*$', '', val, flags=re.IGNORECASE)
        # Strip additional context after "per harvester" or "per person per day"
        val = re.sub(r'(?<=per harvester)\s+.*$', '', val, flags=re.IGNORECASE)
        val = re.sub(r'(?<=per day)\s+[A-Z].*$', '', val)
        return val.strip().rstrip('",;')[:120]

    # Use up to first 8 occurrences (≈ number of FL management regions)
    season = _clean(_most_common(seasons[:8]))
    size   = _clean(_most_common(sizes[:8]))
    bag    = _clean(_most_common(bags[:8]))

    return {
        "min_size":  size,
        "bag_limit": bag,
        "season":    season,
        "notes":     (
            "Limits may vary by FL management region. "
            "Verify current rules at myfwc.com before fishing."
        ),
        "scraped_source": "myfwc.com",
    }


def _scrape_fl(species_name: str) -> Optional[Dict[str, str]]:
    slug = None
    for candidate in _name_variants(species_name):
        slug = _FL_SLUGS.get(candidate)
        if slug:
            break
    if not slug:
        return None
    url = f"{_FL_BASE}{slug}/"
    try:
        resp = requests.get(
            url, timeout=_REQUEST_TIMEOUT,
            headers={"User-Agent": _USER_AGENT},
        )
        resp.raise_for_status()
        return _parse_fl_page(resp.text)
    except Exception as exc:
        _log.warning("FL scrape failed for %s: %s", species_name, exc)
        return None


# ──────────────────────────────────────────────────────────────────
# Virginia — VA Marine Resources Commission (single-page listing)
# ──────────────────────────────────────────────────────────────────

_VA_URL = "https://webapps.mrc.virginia.gov/public/reports/swrecfishingrules.php"

# Cached full-page HTML (one fetch per process run)
_va_page_cache: Optional[str] = None
_va_page_lock = Lock()

# species_key → uppercase names as they appear in the VA MRC page
_VA_NAMES: Dict[str, List[str]] = {
    "red_drum":          ["RED DRUM", "CHANNEL BASS"],
    "striped_bass":      ["STRIPED BASS", "ROCKFISH"],
    "summer_flounder":   ["SUMMER FLOUNDER"],
    "southern_flounder": ["SOUTHERN FLOUNDER", "FLOUNDER"],
    "spotted_seatrout":  ["SPOTTED SEA TROUT", "SPECKLED TROUT"],
    "bluefish":          ["BLUEFISH"],
    "tautog":            ["TAUTOG"],
    "black_sea_bass":    ["BLACK SEA BASS"],
    "scup":              ["SCUP"],
    "weakfish":          ["WEAKFISH"],
    "cobia":             ["COBIA"],
    "spanish_mackerel":  ["SPANISH MACKEREL"],
    "sheepshead":        ["SHEEPSHEAD"],
    "red_snapper":       ["RED SNAPPER"],
}


def _get_va_html() -> Optional[str]:
    global _va_page_cache
    with _va_page_lock:
        if _va_page_cache is not None:
            return _va_page_cache
        try:
            resp = requests.get(
                _VA_URL, timeout=_REQUEST_TIMEOUT,
                headers={"User-Agent": _USER_AGENT},
            )
            resp.raise_for_status()
            _va_page_cache = resp.text
            return _va_page_cache
        except Exception as exc:
            _log.warning("VA page fetch failed: %s", exc)
            return None


def _parse_va_page(html: str, species_name: str) -> Optional[Dict[str, str]]:
    names = None
    for candidate in _name_variants(species_name):
        names = _VA_NAMES.get(candidate)
        if names:
            break
    if not names:
        return None

    text = _strip_html(html)

    for name in names:
        idx = text.find(name)
        if idx < 0:
            continue
        section = text[idx: idx + 700]

        # Stop each value at the next label keyword or record marker.
        # "Maximum Size Limit" is included so slot-limit ranges don't bleed.
        # "As required" catches lengthy statutory footnotes on the VA page.
        _va_stop = (
            r"(?=Maximum\s+Size|Possession|Bag\s+Limit|Season|"
            r"Capture\s+Citation|Release\s+Citation|State\s+Record|"
            r"Minimum\s+Size|As\s+required|$)"
        )
        size_m = re.search(
            r"(?:Minimum\s+Size\s+Limit|Size\s+Limit)[:\s]+(.+?)" + _va_stop,
            section, re.IGNORECASE,
        )
        bag_m = re.search(
            r"(?:Possession\s+Limit|Bag\s+Limit)[:\s]+(.+?)" + _va_stop,
            section, re.IGNORECASE,
        )
        season_m = re.search(
            r"(?:Open\s+Season\s*:|(?<!Closed\s)Season\s*:)\s*(.+?)" + _va_stop,
            section, re.IGNORECASE,
        )

        def _cv(m: Optional[re.Match]) -> str:  # type: ignore[type-arg]
            return m.group(1).strip().rstrip(".,;")[:120] if m else ""

        if size_m or bag_m:
            return {
                "min_size":  _cv(size_m),
                "bag_limit": _cv(bag_m),
                "season":    _cv(season_m),
                "notes":     (
                    "Verify current rules with VA Marine Resources Commission "
                    "(mrc.virginia.gov)."
                ),
                "scraped_source": "mrc.virginia.gov",
            }

    return None


def _scrape_va(species_name: str) -> Optional[Dict[str, str]]:
    html = _get_va_html()
    if not html:
        return None
    return _parse_va_page(html, species_name)


# ──────────────────────────────────────────────────────────────────
# State dispatcher
# ──────────────────────────────────────────────────────────────────

_SCRAPERS = {
    "FL": _scrape_fl,
    "VA": _scrape_va,
}


# ──────────────────────────────────────────────────────────────────
# SQLite cache helpers
# ──────────────────────────────────────────────────────────────────

def _cache_get(species_key: str, state: str) -> Optional[Dict[str, Any]]:
    """Return cached regulation dict, or None if missing / expired."""
    try:
        conn = get_db()
        row = conn.execute(
            "SELECT reg_json, scraped_at FROM reg_scrape_cache "
            "WHERE species_key=? AND state=?",
            (species_key, state),
        ).fetchone()
        conn.close()
        if not row:
            return None
        scraped_at = datetime.fromisoformat(str(row["scraped_at"]))
        age = (datetime.utcnow() - scraped_at).total_seconds()
        if age > _CACHE_TTL_SECONDS:
            return None
        return json.loads(row["reg_json"])
    except Exception:
        return None


def _cache_set(species_key: str, state: str, data: Dict[str, Any]) -> None:
    try:
        conn = get_db()
        conn.execute(
            "INSERT OR REPLACE INTO reg_scrape_cache "
            "(species_key, state, reg_json, scraped_at) "
            "VALUES (?, ?, ?, datetime('now'))",
            (species_key, state, json.dumps(data)),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


# ──────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────

def scrape_regulation(
    species_name: str,
    state: str,
) -> Optional[Dict[str, str]]:
    """Return live-scraped regulation data for *species_name* in *state*.

    Results are cached in SQLite for 24 hours so state agency sites are
    not hit on every request.  Returns None when the state is not yet
    supported or when scraping fails, so the caller can fall back to
    the static JSON snapshot.
    """
    state = (state or "").upper().strip()
    if not state or state not in _SCRAPERS:
        return None

    variants = _name_variants(species_name)
    if not variants:
        return None
    # Use the shortest variant as the stable cache key
    cache_key = variants[-1]

    # Return cached result (including a cached "nothing found" sentinel)
    cached = _cache_get(cache_key, state)
    if cached is not None:
        return cached if cached else None   # empty dict → cached miss

    # Live scrape — pass the display name so sub-functions can derive variants
    scraper = _SCRAPERS[state]
    result = scraper(species_name)

    # Cache even a failed scrape (empty dict) to avoid hammering state sites
    _cache_set(cache_key, state, result or {})

    return result


def invalidate_cache(state: Optional[str] = None) -> int:
    """Delete cached scrape entries, optionally filtered to one state.

    Returns the number of rows removed.  Useful for admin endpoints that
    force a regulation refresh.
    """
    try:
        conn = get_db()
        if state:
            cur = conn.execute(
                "DELETE FROM reg_scrape_cache WHERE state=?",
                (state.upper().strip(),),
            )
        else:
            cur = conn.execute("DELETE FROM reg_scrape_cache")
        count = cur.rowcount
        conn.commit()
        conn.close()
        return count
    except Exception:
        return 0
