"""Live fishing regulation scraper with SQLite cache.

Scrapes official state agency pages on demand and caches results for 24 hours.

Supported states with live scraping:
  FL — FWC per-species pages at myfwc.com
  VA — VA Marine Resources Commission single-page listing
  GA — Coastal GA DNR definition-list page at coastalgadnr.org
  NC — NC Division of Marine Fisheries size/bag limits table at deq.nc.gov
  NY — NY DEC saltwater recreational regulations table at dec.ny.gov

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
# Georgia — Coastal GA DNR definition-list page
# ──────────────────────────────────────────────────────────────────

_GA_URL = "https://coastalgadnr.org/Limits"

# Cached full-page HTML (one fetch per process run)
_ga_page_cache: Optional[str] = None
_ga_page_lock = Lock()

_GA_NAMES: Dict[str, List[str]] = {
    "red_drum":          ["red drum"],
    "spotted_seatrout":  ["spotted seatrout", "speckled trout"],
    "striped_bass":      ["striped bass", "rockfish"],
    "bluefish":          ["bluefish"],
    "summer_flounder":   ["summer flounder", "fluke"],
    "southern_flounder": ["southern flounder"],
    "black_sea_bass":    ["black sea bass"],
    "sheepshead":        ["sheepshead"],
    # GA page uses "Mackerel, Spanish" / "Mackerel, King" order
    "spanish_mackerel":  ["mackerel, spanish", "spanish mackerel"],
    "king_mackerel":     ["mackerel, king", "king mackerel"],
    "gag_grouper":       ["gag grouper"],
    "cobia":             ["cobia"],
    "flounder":          ["flounder"],
    "black_drum":        ["black drum"],
    "pompano":           ["pompano"],
    "red_snapper":       ["red snapper"],
    "weakfish":          ["weakfish"],
    "tarpon":            ["tarpon"],
    "amberjack":         ["amberjack"],
}


def _get_ga_html() -> Optional[str]:
    global _ga_page_cache
    with _ga_page_lock:
        if _ga_page_cache is not None:
            return _ga_page_cache
        try:
            resp = requests.get(
                _GA_URL, timeout=_REQUEST_TIMEOUT,
                headers={"User-Agent": _USER_AGENT},
            )
            resp.raise_for_status()
            _ga_page_cache = resp.text
            return _ga_page_cache
        except Exception as exc:
            _log.warning("GA page fetch failed: %s", exc)
            return None


def _parse_ga_dd(dd_text: str) -> Dict[str, str]:
    """Extract season/limit/size from a GA coastalgadnr DD text block.

    DD text format (space-separated labels):
      'Season: All year  Limit: 5  Minimum size: 14" TL (Maximum 23" TL) ...'
      'Season: March 1 - Oct. 31  Limit: 1 per angler, maximum 6 per boat  Minimum size: 36" FL'
    """
    # Scrub URLs and legal citations
    text = re.sub(r"https?://\S+", "", dd_text)
    text = re.sub(r"\[O\.C\.G\.A[^\]]*\]", "", text)
    text = re.sub(r"\*?Federal\s+regulations?[^.]*\.?", "", text, flags=re.IGNORECASE)
    text = text.strip()

    def _between(start_pat: str, stop_pat: str) -> str:
        m = re.search(
            start_pat + r"\s*:\s*(.+?)(?=\s+" + stop_pat + r"\s*:|$)",
            text, re.IGNORECASE,
        )
        return m.group(1).strip().rstrip(".,;") if m else ""

    season = _between("Season", "Limit")
    bag    = _between("Limit", "Minimum\\s+size")

    # Size: capture everything after "Minimum size:" then strip trailing
    # sentences (e.g. "Red Drum are a gamefish…")
    size = ""
    size_m = re.search(r"Minimum\s+size\s*:\s*(.+)", text, re.IGNORECASE)
    if size_m:
        size = size_m.group(1).strip()
        # Strip a second regulation block starting with proper-noun place name(s)
        # followed by "Season:" — e.g. "Savannah River Season: All year Limit: 2…"
        # Use capitalized-word requirement so measurement units (TL, FL) don't match.
        size = re.sub(
            r"\s+(?:[A-Z][a-z]+\s+)+Season\s*:.*$", "", size,
        )
        # Strip trailing location name with no following value
        size = re.sub(
            r"\s+\w+\s+(?:River|Lake|Sound|Bay|Coast|Ocean|Waters?)\s*$",
            "", size, flags=re.IGNORECASE,
        )
        # Strip trailing sentence: one or more Capitalised Words followed by a verb
        # e.g. "Red Drum are a gamefish…" or "Flounder may not be…"
        size = re.sub(
            r"\s+[A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)*\s+(?:are|is|may|were|have|can)\b.*$",
            "", size,
        )
        size = size.rstrip(".,; ").strip()

    return {
        "season":    season[:120],
        "bag_limit": bag[:120],
        "min_size":  size[:120],
    }


def _parse_ga_page(html: str, species_name: str) -> Optional[Dict[str, str]]:
    """Parse coastalgadnr.org/Limits which uses <dl>/<dt>/<dd> structure.

    Each <dt> is a species name; the paired <dd> contains text like:
      'Season: All year  Limit: 5  Minimum size: 14" TL (Maximum 23" TL)'
    """
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        _log.warning("beautifulsoup4 not installed; GA scraping unavailable")
        return None

    names = None
    for candidate in _name_variants(species_name):
        names = _GA_NAMES.get(candidate)
        if names:
            break
    if not names:
        return None

    soup = BeautifulSoup(html, "html.parser")
    for dt in soup.find_all("dt"):
        dt_text = dt.get_text(" ", strip=True).lower()
        for name in names:
            if name.lower() in dt_text:
                dd = dt.find_next_sibling("dd")
                if not dd:
                    continue
                parsed = _parse_ga_dd(dd.get_text(" ", strip=True))
                if parsed["min_size"] or parsed["bag_limit"]:
                    return {
                        **parsed,
                        "notes":          "Verify current rules with GA Coastal Resources (coastalgadnr.org).",
                        "scraped_source": "coastalgadnr.org",
                    }
    return None


def _scrape_ga(species_name: str) -> Optional[Dict[str, str]]:
    html = _get_ga_html()
    if not html:
        return None
    return _parse_ga_page(html, species_name)


# ──────────────────────────────────────────────────────────────────
# North Carolina — NC DMF size/bag limits table
# ──────────────────────────────────────────────────────────────────

_NC_URL = (
    "https://deq.nc.gov/about/divisions/marine-fisheries/"
    "rules-proclamations-and-size-and-bag-limits/recreational-size-and-bag-limits"
)

_nc_page_cache: Optional[str] = None
_nc_page_lock = Lock()

# species_key → substrings to look for in the NC table's first column (lowercased)
_NC_NAMES: Dict[str, List[str]] = {
    "red_drum":          ["red drum", "channel bass"],
    "spotted_seatrout":  ["spotted seatrout", "speckled trout"],
    "striped_bass":      ["striped bass"],
    "bluefish":          ["bluefish"],
    "summer_flounder":   ["flounder"],
    "southern_flounder": ["flounder"],
    "black_sea_bass":    ["black sea bass"],
    "sheepshead":        ["sheepshead"],
    "spanish_mackerel":  ["spanish mackerel"],
    "cobia":             ["cobia"],
    "king_mackerel":     ["king mackerel"],
    "weakfish":          ["weakfish", "gray trout"],
    "scup":              ["scup"],
    "tautog":            ["tautog"],
    "black_drum":        ["black drum"],
    "pompano":           ["pompano"],
    "red_snapper":       ["red snapper"],
    "gag_grouper":       ["snapper", "grouper"],  # grouped complex on NC page
}


def _get_nc_html() -> Optional[str]:
    global _nc_page_cache
    with _nc_page_lock:
        if _nc_page_cache is not None:
            return _nc_page_cache
        try:
            resp = requests.get(
                _NC_URL, timeout=_REQUEST_TIMEOUT,
                headers={"User-Agent": _USER_AGENT},
            )
            resp.raise_for_status()
            _nc_page_cache = resp.text
            return _nc_page_cache
        except Exception as exc:
            _log.warning("NC page fetch failed: %s", exc)
            return None


def _parse_nc_page(html: str, species_name: str) -> Optional[Dict[str, str]]:
    """Parse the NC DMF recreational size/bag limits table.

    Table column layout (4-cell rows):
      col 0 — Species name (may include annotations like '(C) ▲▲')
      col 1 — Minimum Length
      col 2 — Bag Limits (per person)
      col 3 — Federal Management / notes

    3-cell rows (second cell has colspan=2) use a single combined info cell
    for species that are closed or have complex proclamation references.
    """
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        _log.warning("beautifulsoup4 not installed; NC scraping unavailable")
        return None

    names = None
    for candidate in _name_variants(species_name):
        names = _NC_NAMES.get(candidate)
        if names:
            break
    if not names:
        return None

    soup = BeautifulSoup(html, "html.parser")
    tables = soup.find_all("table")
    if not tables:
        return None

    for row in tables[0].find_all("tr"):
        tds = row.find_all(["td", "th"])
        if not tds:
            continue
        cell0 = tds[0].get_text(" ", strip=True).lower()
        for name in names:
            if name.lower() in cell0:
                # 4-column row: size in col[1], bag in col[2]
                # 3-column row with colspan=2: combined info in col[1]
                if len(tds) >= 4:
                    size   = tds[1].get_text(" ", strip=True).strip()
                    bag    = tds[2].get_text(" ", strip=True).strip()
                    season = ""
                elif len(tds) == 3:
                    combined = tds[1].get_text(" ", strip=True).strip()
                    size, bag = combined, combined
                    season = ""
                else:
                    continue

                # Skip rows that are just proclamation references with no values
                skip_phrases = ("see the most recent", "see most recent")
                if any(p in size.lower() for p in skip_phrases):
                    size, bag = "", ""

                if size or bag:
                    return {
                        "min_size":       size[:120],
                        "bag_limit":      bag[:120],
                        "season":         season,
                        "notes":          "Verify current rules with NC Division of Marine Fisheries (deq.nc.gov).",
                        "scraped_source": "deq.nc.gov",
                    }
    return None


def _scrape_nc(species_name: str) -> Optional[Dict[str, str]]:
    html = _get_nc_html()
    if not html:
        return None
    return _parse_nc_page(html, species_name)


# ──────────────────────────────────────────────────────────────────
# New York — NY DEC recreational fishing regulations table
# ──────────────────────────────────────────────────────────────────

_NY_URL = (
    "https://dec.ny.gov/things-to-do/saltwater-fishing/recreational-fishing-regulations"
)

_ny_page_cache: Optional[str] = None
_ny_page_lock = Lock()

# species_key → substrings to look for in NY table first column (lowercased)
_NY_NAMES: Dict[str, List[str]] = {
    "red_drum":          ["red drum"],
    "striped_bass":      ["striped bass: marine"],     # prefer marine over Hudson River row
    "bluefish":          ["bluefish"],
    "summer_flounder":   ["summer flounder", "fluke"],
    "winter_flounder":   ["winter flounder"],
    "black_sea_bass":    ["black sea bass"],
    "scup":              ["scup (porgy)"],              # first row (not party/charter)
    "weakfish":          ["weakfish"],
    "tautog":            ["tautog (blackfish): ny bight"],  # prefer NY Bight
    "spanish_mackerel":  ["spanish mackerel"],
    "king_mackerel":     ["king mackerel"],
    "cobia":             ["cobia"],
    "southern_flounder": ["yellowtail flounder", "flounder"],
    "flounder":          ["summer flounder", "fluke"],
}


def _get_ny_html() -> Optional[str]:
    global _ny_page_cache
    with _ny_page_lock:
        if _ny_page_cache is not None:
            return _ny_page_cache
        try:
            resp = requests.get(
                _NY_URL, timeout=_REQUEST_TIMEOUT,
                headers={"User-Agent": _USER_AGENT},
            )
            resp.raise_for_status()
            _ny_page_cache = resp.text
            return _ny_page_cache
        except Exception as exc:
            _log.warning("NY page fetch failed: %s", exc)
            return None


def _parse_ny_page(html: str, species_name: str) -> Optional[Dict[str, str]]:
    """Parse NY DEC 4-column saltwater regulations table.

    Columns: Species | Min Size | Bag Limit | Open Season
    """
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        _log.warning("beautifulsoup4 not installed; NY scraping unavailable")
        return None

    names = None
    for candidate in _name_variants(species_name):
        names = _NY_NAMES.get(candidate)
        if names:
            break
    if not names:
        return None

    soup = BeautifulSoup(html, "html.parser")
    tables = soup.find_all("table")
    if not tables:
        return None

    for row in tables[0].find_all("tr"):
        tds = row.find_all(["td", "th"])
        if len(tds) < 3:
            continue
        cell0 = tds[0].get_text(" ", strip=True).lower()
        for name in names:
            if name.lower() in cell0:
                size   = tds[1].get_text(" ", strip=True).strip()
                bag    = tds[2].get_text(" ", strip=True).strip()
                season = tds[3].get_text(" ", strip=True).strip() if len(tds) > 3 else ""
                # Strip numeric footnote references like "(2)", "(5)"
                size   = re.sub(r"\s*\(\d+\)", "", size).strip()
                bag    = re.sub(r"\s*\(\d+\)", "", bag).strip()
                if size or bag:
                    return {
                        "min_size":       size[:120],
                        "bag_limit":      bag[:120],
                        "season":         season[:120],
                        "notes":          "Verify current rules with NY DEC (dec.ny.gov).",
                        "scraped_source": "dec.ny.gov",
                    }
    return None


def _scrape_ny(species_name: str) -> Optional[Dict[str, str]]:
    html = _get_ny_html()
    if not html:
        return None
    return _parse_ny_page(html, species_name)


# ──────────────────────────────────────────────────────────────────
# State dispatcher
# ──────────────────────────────────────────────────────────────────

_SCRAPERS = {
    "FL": _scrape_fl,
    "VA": _scrape_va,
    "GA": _scrape_ga,
    "NC": _scrape_nc,
    "NY": _scrape_ny,
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
