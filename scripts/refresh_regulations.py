#!/usr/bin/env python3
"""Pre-warm the live regulation scraper cache for all supported states.

Run this as a daily cron job (ideally overnight) so users always get fresh
data when they open the app rather than triggering a live scrape on demand.

Usage:
    # Refresh all 9 live-scraper states
    python scripts/refresh_regulations.py

    # Refresh a specific state only
    python scripts/refresh_regulations.py NC FL

    # Dry-run: show what would be refreshed without hitting state sites
    python scripts/refresh_regulations.py --dry-run

Suggested crontab entry (3 AM daily, app directory):
    0 3 * * * cd /opt/surf-pier-forecast && python scripts/refresh_regulations.py >> logs/reg_refresh.log 2>&1

States with live scrapers (everything else falls back to the static snapshot):
    FL  VA  GA  NC  NY  AL  RI  TX  MS
"""

from __future__ import annotations

import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Allow running from the project root or from the scripts/ dir
_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
sys.path.insert(0, str(_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger("refresh_regulations")

# States that have a live scraper — all others fall back to the JSON snapshot
LIVE_STATES = ["FL", "VA", "GA", "NC", "NY", "AL", "RI", "TX", "MS"]

# Pause between requests to be polite to state agency websites
REQUEST_DELAY_SECONDS = 0.5


def _load_species_list() -> list[str]:
    """Return all species display names from regulations_data.json."""
    data_path = _ROOT / "storage" / "regulations_data.json"
    try:
        raw = json.loads(data_path.read_text(encoding="utf-8"))
        return list(raw.get("name_map", {}).keys())
    except Exception as exc:
        logger.error("Could not load species list from %s: %s", data_path, exc)
        return []


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]

    dry_run = "--dry-run" in args
    state_args = [a.upper() for a in args if not a.startswith("--")]
    target_states = state_args if state_args else LIVE_STATES

    unsupported = [s for s in target_states if s not in LIVE_STATES]
    if unsupported:
        logger.warning(
            "No live scraper for %s — these states will be skipped "
            "(they fall back to the static JSON snapshot).",
            ", ".join(unsupported),
        )
        target_states = [s for s in target_states if s in LIVE_STATES]

    if not target_states:
        logger.error("No supported states to refresh. Exiting.")
        return 1

    species_list = _load_species_list()
    if not species_list:
        return 1

    logger.info(
        "Starting regulation refresh — %d states × %d species%s",
        len(target_states),
        len(species_list),
        " (DRY RUN)" if dry_run else "",
    )

    if dry_run:
        for state in target_states:
            for sp in species_list:
                logger.info("[DRY RUN] Would scrape  [%s] %s", state, sp)
        return 0

    from storage.reg_scraper import invalidate_cache, scrape_regulation

    # Invalidate existing cache so we actually hit state sites
    for state in target_states:
        n = invalidate_cache(state)
        logger.info("Invalidated %d cached entries for %s", n, state)

    totals: dict[str, int] = {"ok": 0, "missing": 0, "error": 0}
    start = time.monotonic()

    for state in target_states:
        logger.info("── Refreshing %s ──────────────────────────", state)
        for sp in species_list:
            try:
                reg = scrape_regulation(sp, state)
                if reg and (reg.get("min_size") or reg.get("bag_limit") or reg.get("season")):
                    logger.info("  [%s] %-42s  ✓ OK", state, sp)
                    totals["ok"] += 1
                else:
                    logger.debug("  [%s] %-42s  – not found", state, sp)
                    totals["missing"] += 1
                time.sleep(REQUEST_DELAY_SECONDS)
            except Exception as exc:
                logger.error("  [%s] %-42s  ✗ ERROR: %s", state, sp, exc)
                totals["error"] += 1

    elapsed = time.monotonic() - start
    logger.info(
        "Done in %.1fs — verified: %d  not found: %d  errors: %d",
        elapsed,
        totals["ok"],
        totals["missing"],
        totals["error"],
    )

    if totals["error"] > 0:
        logger.warning(
            "%d scrape errors — regulation data for those species may be stale. "
            "Check state agency websites for outages or layout changes.",
            totals["error"],
        )
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
