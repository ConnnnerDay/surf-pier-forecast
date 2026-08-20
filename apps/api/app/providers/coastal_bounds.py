"""Coastal coordinate validation (sprint 18).

Two independent, composable checks for "is this a plausible US coastal
point," per docs/CANONICAL_ROADMAP.md's product contract ("any valid US
coastal coordinate, including Atlantic, Gulf, Pacific, Alaska, and
Hawaii") and the legacy `locations.py`'s dynamic-point machinery
(`parse_dynamic_id`'s range check, `_DYN_GATE_MILES`'s inland gate):

1. `is_valid_coordinate` / `classify_coast_region` — pure, offline,
   coarse. A lat/lng range check, plus a bounding box per supported
   coast region. This rejects obviously-wrong input (out-of-range
   numbers, a point in Kansas or mid-Europe) with zero network calls,
   before any station lookup is worth doing.
2. `gate_coastal_point` — the actual inland-rejection mechanism,
   ported from `locations.py`'s `_resolve_dynamic_location`/
   `dynamic_location_for_point` (`_DYN_GATE_MILES = 60.0`): a point
   only counts as coastal if it's within `max_miles` of a real CO-OPS
   or NDBC station from sprint 17's `app.providers.stations` catalogs.
   A bounding box alone can't do this — the Atlantic-region box, for
   instance, necessarily contains the Appalachians.

Scope for this sprint: the two checks above. The legacy module's third
inland-rejection input — falling back to the nearest *curated* location
when no station is close enough — is deliberately not ported here: it
needs the curated-locations dataset, which is sprint 19's job (location
resolution), not this one's. `gate_coastal_point` therefore only sees
what sprint 17 already gives it (the public station catalogs), which is
a strict subset of the legacy gate's inputs — this sprint's gate can be
stricter (reject a point the legacy gate would have accepted via a
nearby curated location) but never more permissive.

The five region bounding boxes are coarse rectangles, not real
coastline geometry — no shapefile dependency is available here. They're
deliberately generous (a bounding box false negative, silently refusing
a real coastal point, is worse than a false positive an inland-distance
check downstream still catches) and, being independent rectangles, can
overlap at region borders (e.g. the Florida peninsula, where "Atlantic"
and "Gulf" are both plausible near the Keys) — `classify_coast_region`
returns the first match, which is an arbitrary but stable tie-break, not
a geographic claim. The Alaska box does not handle the Aleutian
Islands' crossing of the antimeridian (positive longitude past 180°);
that's a known, documented gap, not a silent one.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.providers.stations import (
    CoopsStationCatalogEntry,
    NdbcStationCatalogEntry,
    nearest_coops_station,
    nearest_ndbc_stations,
)

_DEFAULT_MAX_ANCHOR_MILES = 60.0


class CoastRegion(str, Enum):
    ATLANTIC = "atlantic"
    GULF = "gulf"
    PACIFIC = "pacific"
    ALASKA = "alaska"
    HAWAII = "hawaii"


@dataclass(frozen=True)
class _BoundingBox:
    min_lat: float
    max_lat: float
    min_lng: float
    max_lng: float

    def contains(self, lat: float, lng: float) -> bool:
        return (
            self.min_lat <= lat <= self.max_lat and self.min_lng <= lng <= self.max_lng
        )


# Order matters only as the classify_coast_region tie-break at overlapping
# borders (see module docstring) — checked in this order.
_COAST_BOUNDING_BOXES: tuple[tuple[CoastRegion, _BoundingBox], ...] = (
    (CoastRegion.ATLANTIC, _BoundingBox(24.3, 45.5, -81.9, -66.8)),
    (CoastRegion.GULF, _BoundingBox(24.3, 31.0, -97.9, -80.0)),
    (CoastRegion.PACIFIC, _BoundingBox(32.0, 49.1, -125.5, -116.5)),
    (CoastRegion.ALASKA, _BoundingBox(51.0, 71.5, -179.9, -129.9)),
    (CoastRegion.HAWAII, _BoundingBox(18.5, 22.5, -160.5, -154.5)),
)


@dataclass(frozen=True)
class CoastalGateResult:
    """`nearest_station_miles` is `None` only when neither catalog had
    any usable station (e.g. both fetches degraded) — not the same as
    "no station is nearby."
    """

    is_coastal: bool
    nearest_station_miles: float | None


def is_valid_coordinate(lat: float, lng: float) -> bool:
    """Reject out-of-range latitude/longitude values."""
    return -90.0 <= lat <= 90.0 and -180.0 <= lng <= 180.0


def classify_coast_region(lat: float, lng: float) -> CoastRegion | None:
    """Return the first supported coast region whose bounding box
    contains `(lat, lng)`, or `None` if it falls in none of them.
    """
    for region, box in _COAST_BOUNDING_BOXES:
        if box.contains(lat, lng):
            return region
    return None


def gate_coastal_point(
    lat: float,
    lng: float,
    coops_stations: list[CoopsStationCatalogEntry],
    ndbc_stations: list[NdbcStationCatalogEntry],
    *,
    max_miles: float = _DEFAULT_MAX_ANCHOR_MILES,
) -> CoastalGateResult:
    """A point counts as coastal only if it's within `max_miles` of the
    nearest CO-OPS or NDBC station in the given catalogs.
    """
    candidates: list[float] = []

    coops_nearest = nearest_coops_station(coops_stations, lat, lng)
    if coops_nearest is not None:
        candidates.append(coops_nearest.distance_miles)

    ndbc_nearest = nearest_ndbc_stations(ndbc_stations, lat, lng, n=1)
    if ndbc_nearest:
        candidates.append(ndbc_nearest[0].distance_miles)

    if not candidates:
        return CoastalGateResult(is_coastal=False, nearest_station_miles=None)

    nearest_miles = min(candidates)
    return CoastalGateResult(
        is_coastal=nearest_miles <= max_miles, nearest_station_miles=nearest_miles
    )
