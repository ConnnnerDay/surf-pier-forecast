"""Species database, scoring logic, and fishing knowledge."""

from __future__ import annotations

import json
import logging
import operator as _operator
import re
from pathlib import Path
from typing import Any, Callable, Optional

from locations import get_monthly_water_temps
from regulations import (
    classify_legality,
    lookup_regulation,
    season_status as _season_status,
    should_hide_from_forecast,
)
from storage.species_loader import SPECIES_DB, SPECIES_DB_BY_COAST, SPECIES_DB_MAP

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Profile-based species classification
# ---------------------------------------------------------------------------
# These sets are loaded from storage/species_classifications.json at import time.
# ---------------------------------------------------------------------------

_CLASSIFICATIONS_PATH = Path(__file__).parent.parent / "storage" / "species_classifications.json"
with _CLASSIFICATIONS_PATH.open(encoding="utf-8") as _f:
    _CLASSIFICATIONS = json.load(_f)

_ft = _CLASSIFICATIONS["fishing_type"]
_tc = _CLASSIFICATIONS["target_categories"]

_OFFSHORE_ONLY_SPECIES: frozenset = frozenset(_ft["offshore_only"])
_PIER_SPECIES: frozenset = frozenset(_ft["pier"])
_SURF_SPECIES: frozenset = frozenset(_ft["surf"])
_JETTY_SPECIES: frozenset = frozenset(_ft["jetty"])
_BRIDGE_SPECIES: frozenset = frozenset(_ft["bridge"])
_WADE_SPECIES: frozenset = frozenset(_ft["wade"])
_KAYAK_SPECIES: frozenset = frozenset(_ft["kayak"])
_CHARTER_SPECIES: frozenset = frozenset(_ft["charter"])
_FLY_SPECIES: frozenset = frozenset(_ft["fly"])
_INSHORE_SPECIES: frozenset = frozenset(_ft["inshore"])

_BOTTOM_SPECIES: frozenset = frozenset(_tc["bottom"])
_PELAGIC_SPECIES: frozenset = frozenset(_tc["pelagic"])
_STRUCTURE_SPECIES: frozenset = frozenset(_tc["structure"])
_GAMEFISH_SPECIES: frozenset = frozenset(_tc["gamefish"])
_INSHORE_SLAM_SPECIES: frozenset = frozenset(_tc["inshore_slam"])

_NUISANCE_SPECIES: frozenset = frozenset(_CLASSIFICATIONS["nuisance"])
_SPECIES_CATEGORIES: dict[str, list[str]] = _CLASSIFICATIONS["species_categories"]

_SPECIES_BY_COAST: dict[str, list[dict[str, Any]]] = {
    coast: [sp for sp in sps if sp["name"] not in _NUISANCE_SPECIES]
    for coast, sps in SPECIES_DB_BY_COAST.items()
}

def _species_matches_profile(
    sp_name: str,
    fishing_types: Optional[list[str]] = None,
    targets: Optional[list[str]] = None,
) -> bool:
    """Return True if a species matches the user's profile preferences.

    If the user selected specific fishing types, exclude species that
    can only be caught from methods they don't use.  If the user selected
    specific target categories, only include species from those categories
    (unless they chose 'anything').
    """
    # --- Fishing type filter ---
    if fishing_types and "all" not in fishing_types:
        has_surf = "surf" in fishing_types
        has_pier = "pier" in fishing_types
        has_inshore = "inshore" in fishing_types
        has_offshore = "offshore" in fishing_types
        has_jetty = "jetty" in fishing_types
        has_bridge = "bridge" in fishing_types
        has_wade = "wade" in fishing_types
        has_kayak = "kayak" in fishing_types
        has_charter = "charter" in fishing_types
        has_fly = "fly" in fishing_types

        # Jetty/rock is structurally pier-like; bridge is pier+inshore.
        # Kayak and charter both include offshore species.
        # Fly fishing skews inshore/flats.
        effective_pier = has_pier or has_jetty or has_bridge
        effective_inshore = has_inshore or has_wade or has_fly
        effective_offshore = has_offshore or has_charter or has_kayak

        # Offshore-only species: only show if at least one boat/offshore method
        if not effective_offshore and sp_name in _OFFSHORE_ONLY_SPECIES:
            return False

        # Charter: if the ONLY types selected are charter, gate to charter set
        only_charter = has_charter and not (
            has_surf
            or has_pier
            or has_inshore
            or has_offshore
            or has_jetty
            or has_bridge
            or has_wade
            or has_kayak
            or has_fly
        )
        if only_charter and sp_name not in _CHARTER_SPECIES:
            return False

        # Fly-only gate: if the only type selected is fly, restrict to fly species
        only_fly = has_fly and not (
            has_surf
            or has_pier
            or has_inshore
            or has_offshore
            or has_jetty
            or has_bridge
            or has_wade
            or has_kayak
            or has_charter
        )
        if only_fly and sp_name not in _FLY_SPECIES:
            return False

        # Build the set of all species accessible from the user's fishing types.
        # A species in any primary method set is only shown when the user has at
        # least one method that can reach it.  This replaces the fragile
        # "is_X_only" logic which broke whenever a species appeared in multiple
        # sets (e.g. Pompano in both _SURF_SPECIES and _FLY_SPECIES prevented it
        # from ever being recognised as surf-only from a pier-only user's view).
        _primary_sets = (
            _SURF_SPECIES,
            _PIER_SPECIES,
            _INSHORE_SPECIES,
            _JETTY_SPECIES,
            _BRIDGE_SPECIES,
            _WADE_SPECIES,
            _KAYAK_SPECIES,
            _CHARTER_SPECIES,
            _FLY_SPECIES,
        )
        _accessible: set[str] = set()
        if has_surf:
            _accessible |= _SURF_SPECIES
        if effective_pier:  # pier, jetty, or bridge
            _accessible |= _PIER_SPECIES
        if has_jetty or has_pier:
            _accessible |= _JETTY_SPECIES
        if has_bridge or has_pier or effective_inshore:
            _accessible |= _BRIDGE_SPECIES
        if effective_inshore:  # inshore, wade, or fly
            _accessible |= _INSHORE_SPECIES
        if has_wade or effective_inshore:
            _accessible |= _WADE_SPECIES
        if has_kayak:
            _accessible |= _KAYAK_SPECIES
        if has_charter:
            _accessible |= _CHARTER_SPECIES
        if has_fly or effective_inshore:
            _accessible |= _FLY_SPECIES
        # Exclude species that belong to at least one primary method set but
        # are not reachable from any of the user's selected fishing methods.
        # Species not present in any primary set pass through unfiltered.
        # Species already gated by _OFFSHORE_ONLY_SPECIES (handled above) are
        # exempt so that offshore users can still see them.
        if (
            any(sp_name in s for s in _primary_sets)
            and sp_name not in _accessible
            and sp_name not in _OFFSHORE_ONLY_SPECIES
        ):
            return False

    # --- Target category filter ---
    if targets and "anything" not in targets:
        in_any_target = False
        if "bottom" in targets and sp_name in _BOTTOM_SPECIES:
            in_any_target = True
        if "pelagic" in targets and sp_name in _PELAGIC_SPECIES:
            in_any_target = True
        if "structure" in targets and sp_name in _STRUCTURE_SPECIES:
            in_any_target = True
        if "gamefish" in targets and sp_name in _GAMEFISH_SPECIES:
            in_any_target = True
        if "inshore_slam" in targets and sp_name in _INSHORE_SLAM_SPECIES:
            in_any_target = True
        if not in_any_target:
            return False

    return True

# ---------------------------------------------------------------------------
# Seasonal explanation overrides -- species that behave differently during
# spring/fall transitions get specific text.  Species NOT listed here fall
# back to explanation_cold (winter) or explanation_warm (summer).
# ---------------------------------------------------------------------------

def _get_season(month: int) -> str:
    """Map month number to meteorological season name."""
    if month in (12, 1, 2):
        return "winter"
    if month in (3, 4, 5):
        return "spring"
    if month in (6, 7, 8):
        return "summer"
    return "fall"

SEASONAL_EXPLANATIONS: dict[str, dict[str, str]] = {
    "Red drum (puppy drum)": {
        "spring": "Red drum are pushing into the surf zone and inlets as water warms; they feed aggressively on shrimp, crabs and mullet during the spring transition.",
        "fall": "The fall red drum run is on — large schools move through inlets and along the beach, feeding heavily on mullet and menhaden before winter.",
    },
    "Speckled trout (spotted seatrout)": {
        "spring": "Speckled trout are moving onto grass flats and into creeks as water warms; the spring bite picks up fast on live shrimp under popping corks.",
        "fall": "Speckled trout are feeding heavily in creeks and along grass lines before cold weather; this is prime time for big gator trout.",
    },
    "Black drum": {
        "spring": "Black drum are spawning in inlets and around structure; large fish congregate and feed on crabs, clams and shrimp during the spring run.",
        "fall": "Black drum are stacking up around inlets and pilings, feeding aggressively before winter; cut crab and shrimp on bottom rigs are productive.",
    },
    "Sheepshead": {
        "spring": "Sheepshead are spawning around nearshore structure and pilings; this is peak season — fish straight down with fiddler crabs or sand fleas.",
        "fall": "Sheepshead are returning to pier pilings and jetties as water cools; they pick at barnacles and crabs around structure.",
    },
    "Flounder (summer flounder)": {
        "spring": "Summer flounder are migrating inshore through inlets; ambush them with live finger mullet drifted slowly along the bottom near structure.",
        "fall": "Flounder are staging at inlets for their fall migration offshore; this is prime time as they feed heavily before moving to deeper water.",
    },
    "Southern flounder": {
        "spring": "Southern flounder are moving into creeks and inshore waters as spring warms up; live finger mullet near creek mouths is the top producer.",
        "fall": "The fall flounder run is the best fishing of the year — southern flounder push through inlets and creeks heading offshore to spawn.",
    },
    "Bluefish": {
        "spring": "Bluefish are arriving from the south in big schools, feeding voraciously on everything; cut menhaden and metal jigs produce explosive strikes.",
        "fall": "Large bluefish (choppers) are running south through the surf and around piers; the fall run produces the biggest fish of the year.",
    },
    "Spanish mackerel": {
        "spring": "Spanish mackerel are just arriving as water hits the upper 60s; early fish are hungry and hit shiny spoons and live bait aggressively.",
        "fall": "Spanish mackerel are still around but thinning out as water cools; catch them before they migrate south for winter.",
    },
    "Pompano": {
        "spring": "Pompano are running the surf line in spring, feeding on sand fleas and small crustaceans; target the troughs with double-dropper rigs.",
        "fall": "The fall pompano run brings fish back through the surf zone; sand fleas and Fishbites in the wash zone are the ticket.",
    },
    "Spot": {
        "spring": "Spot are starting to move inshore and school along the beach; bloodworms and shrimp on small hooks produce steady catches.",
        "fall": "The fall spot run is a major NC fishing event — massive schools move through the surf and around piers, biting everything.",
    },
    "Atlantic croaker": {
        "spring": "Croaker are beginning to move inshore as water warms; fresh shrimp and bloodworms on bottom rigs catch early fish.",
        "fall": "Fall croaker runs bring big numbers to the surf and piers; they school up and bite aggressively on shrimp and worms.",
    },
    "Striped bass (rockfish)": {
        "spring": "Striped bass are feeding heavily before moving north for summer; target them at dawn and dusk with cut menhaden and live mullet.",
        "fall": "Striped bass are returning from the north and feeding in the surf and around inlets; the fall run offers the best inshore action.",
    },
    "Cobia": {
        "spring": "Cobia are arriving with the warming water; early fish show up around buoys, piers and channel markers — sight-cast live eels or menhaden.",
        "fall": "Late-season cobia are still cruising near structure before migrating south; fish near buoys and pier ends with live bait.",
    },
    "King mackerel (kingfish)": {
        "spring": "King mackerel are arriving from the south; early kings cruise near piers and along the beach chasing baitfish schools.",
        "fall": "The fall king mackerel run brings big fish close to shore and piers; slow-troll live baits on wire leader for smoker kings.",
    },
    "False albacore (little tunny)": {
        "spring": "False albacore are passing through during spring migration; look for surface blitzes and cast jigs or live baits into breaking fish.",
        "fall": "The fall false albacore blitz is legendary — massive schools chase bait to the surface near piers and along the beach.",
    },
    "Whiting (sea mullet, kingfish)": {
        "spring": "Whiting are moving into the surf as water warms; fresh shrimp and sand fleas on double-dropper rigs in the wash zone are deadly.",
        "fall": "Whiting are schooling up in the surf before moving to deeper water; bloodworms and shrimp produce fast action on light tackle.",
    },
    "Gray trout (weakfish)": {
        "spring": "Gray trout are moving inshore through inlets and along the beach; live shrimp on light tackle near structure is the best approach.",
        "fall": "Gray trout are feeding in inlets and along the beach before winter; target the fall run with live shrimp drifted near the bottom.",
    },
    "Tautog (blackfish)": {
        "spring": "Tautog are actively feeding around jetties and pilings as water warms in early spring; this is a brief but productive window.",
        "fall": "Tautog are moving back to nearshore structure as water cools; the fall bite around rock piles and jetties is excellent.",
    },
    "Hickory shad": {
        "spring": "The spring hickory shad run is one of NC's best seasonal events — fish stack up in rivers and at bridges, hammering small shad darts.",
        "fall": "Hickory shad are offshore and not available inshore during fall months.",
    },
    "American shad": {
        "spring": "American shad are making their massive spring spawning run up NC rivers; the Cape Fear and Neuse are packed with fish hitting small bright jigs.",
        "fall": "American shad are in the ocean and not available for inshore fishing during fall.",
    },
    "Striped mullet": {
        "spring": "Mullet are scattered in inlets and creeks; cast-net them for bait or target them on tiny hooks with bread dough.",
        "fall": "The fall mullet run is THE bait event of the year — huge schools push through inlets and along the beach. Stock your freezer.",
    },
    "Cownose ray": {
        "spring": "Cownose rays are beginning to arrive in large migrating schools; they move through the surf and inlets heading north.",
        "fall": "Massive schools of cownose rays migrate south through NC waters in fall; they are commonly hooked while bottom fishing.",
    },
    "Atlantic bonito": {
        "spring": "Atlantic bonito are passing through during spring migration; they school nearshore and hit small metal jigs and live baits.",
        "fall": "Fall is prime bonito season — they blitz bait nearshore and around piers, hitting jigs and small live baits at high speed.",
    },
    "Jack crevalle": {
        "spring": "Jack crevalle are arriving with warm water; early fish push bait in inlets and along the surf line.",
        "fall": "Jack crevalle are still feeding aggressively before migrating south; they crash baitfish schools in inlets and around piers.",
    },
    "Greater amberjack": {
        "spring": "Amberjack are moving onto nearshore wrecks and reefs as water warms; they hit live baits and heavy jigs with brute force.",
        "fall": "Amberjack are feeding aggressively on nearshore structure before winter; the fall bite on wrecks and reefs is excellent.",
    },
    "Gag grouper": {
        "spring": "Gag grouper are moving shallower onto nearshore wrecks and reefs; live bait on heavy tackle near structure is the play.",
        "fall": "Gag grouper are feeding heavily on nearshore reefs before moving to deeper spawning grounds; fall is prime nearshore grouper season.",
    },
    "Red snapper": {
        "spring": "Red snapper are becoming more active on nearshore wrecks as water warms; cut squid and live bait on bottom rigs produce bites.",
        "fall": "Red snapper are aggressive on nearshore structure during fall; they hit cut and live baits readily before winter slowdown.",
    },
    "Tripletail": {
        "spring": "Tripletail are just arriving near buoys and crab pot floats; sight-cast live shrimp to fish laying on their sides near the surface.",
        "fall": "Late-season tripletail are still found near floating structure before migrating south; they become less common as water cools.",
    },
    "Ribbonfish (Atlantic cutlassfish)": {
        "spring": "Ribbonfish are starting to show up around piers and lighted docks as water warms.",
        "fall": "Fall is peak ribbonfish season — they swarm pier lights at night, hitting small shiny jigs and cut bait strips.",
    },
    "Mahi-mahi (dolphinfish)": {
        "spring": "Early mahi are showing up along weedlines and temperature breaks as the Gulf Stream pushes warm water closer to shore.",
        "fall": "Late-season mahi are still available along the Gulf Stream edge; smaller schoolies are common around floating debris.",
    },
    "Black sea bass": {
        "spring": "Black sea bass are active on nearshore wrecks and hard bottom during spring; squid strips and cut bait on bottom rigs are productive.",
        "fall": "Black sea bass are feeding on nearshore reefs before moving inshore for winter; the fall bite over structure is strong.",
    },
    "Blacktip shark": {
        "spring": "Blacktip sharks are arriving with warming water; they begin patrolling the surf zone following schools of mullet and menhaden.",
        "fall": "Blacktip sharks are still feeding in the surf before migrating south; they follow the fall mullet run down the coast.",
    },
    "Smooth dogfish": {
        "spring": "Smooth dogfish are one of the first sharks to arrive inshore in spring; they school along the bottom feeding on crabs, shrimp and small fish.",
        "fall": "Smooth dogfish are feeding heavily before their fall migration; they are abundant from piers and in the surf on any cut bait.",
    },
    "Thresher shark": {
        "spring": "Thresher sharks pass through NC waters during their spring northward migration, following schools of menhaden and herring.",
        "fall": "Thresher sharks are migrating south through NC waters in fall; they are most commonly encountered during the seasonal transition.",
    },
    "Clearnose skate": {
        "spring": "Clearnose skates are still abundant inshore during early spring; they are common bottom catches from piers before moving deeper as water warms.",
        "fall": "Clearnose skates are moving back inshore as water cools; they become increasingly common from piers during the fall transition.",
    },
    "Silver perch": {
        "spring": "Silver perch are moving inshore as water warms; they school along the beach and around piers, biting small shrimp and worm baits.",
        "fall": "Silver perch are schooling up before heading to deeper water; the fall bite from piers and the surf is productive.",
    },
    "Sand seatrout (white trout)": {
        "spring": "Sand seatrout are moving inshore and schooling around structure as water warms; live shrimp and cut bait produce steady action.",
        "fall": "Sand seatrout are feeding actively before winter; they school in good numbers around piers and in the surf.",
    },
    "Atlantic menhaden (bunker)": {
        "spring": "Menhaden schools are pushing inshore and through inlets; stock up on bait with cast nets and sabiki rigs for the season ahead.",
        "fall": "The fall menhaden run brings massive schools along the beach and through inlets; this is the premier bait event — fill your freezer.",
    },
    "Butterfish": {
        "spring": "Butterfish are moving inshore as water cools in late spring; occasional catches from piers during the transition.",
        "fall": "Butterfish are arriving inshore in fall as water cools; they school around pier lights and structure in good numbers.",
    },
    "American eel": {
        "spring": "American eels are becoming more active as water warms; night fishing around piers and docks produces catches.",
        "fall": "Fall is peak eel season as they migrate toward the ocean to spawn; catch them at night around piers for excellent striper bait.",
    },
    "Gulf flounder": {
        "spring": "Gulf flounder are migrating inshore through inlets alongside summer flounder; live finger mullet near structure is the best approach.",
        "fall": "Gulf flounder are staging at inlets for their fall offshore migration; target them with live mullet in the troughs and near pilings.",
    },
    "Southern kingfish (ground mullet)": {
        "spring": "Southern kingfish are moving into the surf as water warms; they arrive slightly earlier than northern kingfish and hit sand fleas and shrimp.",
        "fall": "Southern kingfish are schooling in the surf before heading to deeper water; fall action is fast on shrimp and sand fleas.",
    },
    "Striped burrfish (spiny boxfish)": {
        "spring": "Striped burrfish are common inshore during spring; these spiny puffers inflate when caught and are frequently hooked on bottom baits.",
        "fall": "Striped burrfish are abundant inshore during fall on structure and grass beds; they are common incidental catches.",
    },
    "Atlantic herring": {
        "spring": "Atlantic herring are thinning out as water warms; catch remaining schools on sabiki rigs for striper bait before they leave.",
        "fall": "Atlantic herring are arriving inshore as water cools; sabiki rig them from piers for excellent striper and bluefish bait.",
    },
    "Blueback herring": {
        "spring": "Blueback herring are running up NC rivers for spawning alongside shad; they hit small, bright darts and are excellent bait.",
        "fall": "Blueback herring are offshore and not available inshore during fall.",
    },
    "Alewife": {
        "spring": "Alewife are making their spring spawning run up NC rivers; they hit small darts and jigs at bridges and dams.",
        "fall": "Alewife are offshore and not available inshore during fall.",
    },
    "White perch": {
        "spring": "White perch are moving into tidal creeks and brackish water as temperatures rise; bloodworms and small shrimp produce steady catches.",
        "fall": "White perch are feeding actively in brackish creeks before winter; they school in good numbers and bite small baits readily.",
    },
    "Spotted hake": {
        "spring": "Spotted hake are still present inshore during early spring; they will move deeper as water warms past the upper 50s.",
        "fall": "Spotted hake are moving inshore as water cools; they become increasingly common bottom catches from piers during late fall.",
    },
}

def _get_explanation(sp: dict[str, Any], month: int, water_temp: float) -> str:
    """Pick the best seasonal explanation for a species.

    Checks for a season-specific override first (spring/fall for species with
    distinct transitional behaviour).  Falls back to the cold/warm explanation
    based on current water temperature.
    """
    season = _get_season(month)
    name = sp["name"]

    overrides = SEASONAL_EXPLANATIONS.get(name)
    if overrides and season in overrides:
        return overrides[season]

    # Default: cold/warm split based on water temperature
    is_cold = water_temp < 65
    return sp["explanation_cold"] if is_cold else sp["explanation_warm"]

def _get_technique_tip(
    sp_name: str,
    hour: int = 12,
    tide_state: str = "",
    wind_strength: str = "",
) -> str:
    """Generate a short, context-specific fishing technique tip.

    Returns a 1-sentence tip based on species category, time of day,
    tide state, and wind conditions.
    """
    name_lower = sp_name.lower()

    # Time-based tips
    is_dawn = 5 <= hour <= 8
    is_dusk = 17 <= hour <= 21
    is_night = hour >= 21 or hour <= 4
    is_midday = 10 <= hour <= 14

    # Categorize by species type
    if any(k in name_lower for k in ["drum", "red drum", "puppy"]):
        if tide_state == "Rising":
            return (
                "Cast to sandbars and troughs as rising water pushes bait toward shore"
            )
        if is_dawn or is_dusk:
            return "Work cut mullet along the bottom near structure and drop-offs"
        return "Use fresh cut bait on a fish-finder rig, let it soak on the bottom"

    if "trout" in name_lower or "seatrout" in name_lower:
        if is_dawn:
            return (
                "Fish live shrimp under a popping cork near grass flats at first light"
            )
        if tide_state == "Falling":
            return "Target outflow points as falling tide concentrates baitfish"
        return "Pop a cork rig in 3-6 feet of water near grassy edges"

    if "flounder" in name_lower or "fluke" in name_lower:
        if tide_state == "Rising":
            return "Drift live minnows along the bottom on incoming tide channels"
        return "Slowly bump a bucktail jig tipped with Gulp along sandy bottoms"

    if "bluefish" in name_lower or "blue" in name_lower:
        if is_dawn or is_dusk:
            return "Cast metal spoons or Got-Cha plugs into surface activity"
        return "Use wire leader — bluefish bite through mono quickly"

    if "sheepshead" in name_lower:
        return "Tip small hooks with fiddler crabs, fish tight to pilings and structure"

    if any(k in name_lower for k in ["shark", "ray"]):
        if is_night:
            return "Fresh cut bait on heavy tackle, use a steel leader and long cast"
        return "Present large cut baits on the bottom with enough weight to hold"

    if "pompano" in name_lower:
        if tide_state == "Rising":
            return "Work sand fleas on a pompano rig in the first trough on rising tide"
        return "Fish the first and second troughs with a double-drop pompano rig"

    if "whiting" in name_lower:
        return "Fish shrimp or Fishbites close to shore in the first trough"

    if any(k in name_lower for k in ["mackerel", "spanish"]):
        if is_dawn:
            return "Cast small spoons or Gotcha plugs when you see surface bait schools"
        return "Troll small Clark spoons or cast metal jigs near offshore structure"

    if "croaker" in name_lower:
        return "Use small pieces of shrimp or bloodworm on a bottom rig in channels"

    if "spot" in name_lower and "trout" not in name_lower:
        return "Fish bloodworms or small shrimp pieces on a two-hook bottom rig"

    if any(k in name_lower for k in ["bass", "striper"]):
        if is_dawn or is_dusk:
            return (
                "Cast soft plastics or live eels around rocks and jetties at dawn/dusk"
            )
        return "Work the water column with bucktails or soft plastics near structure"

    if "rockfish" in name_lower or "lingcod" in name_lower:
        return "Drift cut bait or heavy jigs near rocky bottom and kelp edges"

    if "surfperch" in name_lower:
        return "Fish sand crabs or grubs in the wash zone during incoming tide"

    if "halibut" in name_lower:
        return "Drag live bait slowly along sandy bottoms near drop-offs"

    if "corbina" in name_lower:
        return "Wade the surf with sand crabs on a Carolina rig, fish the troughs"

    if "yellowtail" in name_lower:
        return "Use live bait or iron jigs near kelp paddies and structure"

    # Generic tip based on conditions
    if is_dawn or is_dusk:
        return "Low-light periods are prime — work bait slowly near structure"
    if tide_state == "Rising":
        return "Rising tide brings bait closer to shore — fish the troughs"
    if tide_state == "Falling":
        return "Falling tide concentrates fish at outflow points and channels"
    if is_midday:
        return "Fish deeper water or shaded structure during bright midday conditions"
    return "Match your bait to what's naturally in the water right now"

# ---------------------------------------------------------------------------
# Dynamic rig recommendations -- built from active species
# ---------------------------------------------------------------------------

RIG_CATEGORIES: dict[str, dict[str, Any]] = {
    "fishfinder": {
        "name": "Fish Finder Rig (Carolina Rig)",
        "description": (
            "The most versatile surf and pier rig. A sliding egg or "
            "barrel sinker on the main line above a barrel swivel, "
            "then 18-36 in of fluorocarbon leader to a circle hook. "
            "The sliding sinker lets fish pick up bait without feeling "
            "weight, making it ideal for drum, flounder, and most "
            "bottom species."
        ),
        "mainline": "20-30 lb braid with 40-50 lb shock leader",
        "leader": "18-36 in of 20-30 lb fluorocarbon",
        "image": "images/rigs/fishfinder.svg",
    },
    "hi-lo": {
        "name": "Hi-Lo Rig (Double Drop / Bottom Rig)",
        "description": (
            "Two hooks on dropper loops spaced 12-18 in apart along "
            "a mono leader, with a pyramid or bank sinker at the "
            "bottom. Lets you fish two baits at different depths. "
            "The standard pier and surf panfish rig for whiting, "
            "spot, croaker and sea bass."
        ),
        "mainline": "15-20 lb mono or braid",
        "leader": "30-40 lb mono with two dropper loops",
        "image": "images/rigs/hi-lo.svg",
    },
    "knocker": {
        "name": "Knocker Rig",
        "description": (
            "A sinker threaded directly onto the leader that rests "
            "right against the hook eye. Used for fishing tight to "
            "pier pilings, jetties and rocks where sheepshead, "
            "tautog and structure fish live. The short drop prevents "
            "snags and the direct contact lets you feel subtle bites."
        ),
        "mainline": "30-50 lb braid",
        "leader": "12-24 in of 30-50 lb fluorocarbon",
        "image": "images/rigs/knocker.svg",
    },
    "pompano": {
        "name": "Pompano Rig",
        "description": (
            "A modified hi-lo rig with small floats (beads or "
            "foam) above each hook to lift the bait off the bottom. "
            "Often includes bright beads or teaser blades. Designed "
            "for pompano, whiting, and permit in the surf zone wash. "
            "Fish it in the troughs between sandbars."
        ),
        "mainline": "15-20 lb mono or braid",
        "leader": "30-40 lb mono with two dropper loops and float beads",
        "image": "images/rigs/pompano.svg",
    },
    "float": {
        "name": "Float Rig (Bobber Rig)",
        "description": (
            "A hook suspended beneath a popping cork, cigar float, "
            "or balloon at a set depth. The leader hangs below the "
            "float with a circle or J-hook. Used for pelagics, "
            "mackerel, bluefish, and live-bait fishing from piers. "
            "Adjust depth to match where fish are feeding."
        ),
        "mainline": "15-30 lb mono or braid",
        "leader": "2-4 ft of wire or 30-50 lb fluorocarbon",
        "image": "images/rigs/float.svg",
    },
    "popping-cork": {
        "name": "Popping Cork Rig",
        "description": (
            "A concave-top cork float above 18-36 in of leader and "
            "a circle hook. Pop the rod tip to make the cork splash "
            "and gurgle, imitating feeding fish. The commotion draws "
            "in speckled trout, redfish and flounder. The standard "
            "inshore rig for live shrimp."
        ),
        "mainline": "15-20 lb braid",
        "leader": "18-36 in of 15-20 lb fluorocarbon",
        "image": "images/rigs/popping-cork.svg",
    },
    "kingfish-stinger": {
        "name": "King Mackerel Stinger Rig",
        "description": (
            "A live bait rig with a nose hook and a trailing treble "
            "stinger hook connected by wire. Suspended under a float "
            "or slow-trolled. Kings often slash at the tail of a "
            "baitfish — the stinger hook catches short strikes. "
            "Essential for pier king fishing."
        ),
        "mainline": "20-30 lb mono or braid",
        "leader": "#4-#7 wire with nose hook and trailing stinger treble",
        "image": "images/rigs/kingfish-stinger.svg",
    },
    "shark": {
        "name": "Shark Rig (Heavy Bottom Rig)",
        "description": (
            "A heavy-duty fish finder setup with 3-6 ft of coated "
            "wire or heavy cable leader to a large circle hook. "
            "A balloon or large float optional for suspend-fishing. "
            "Heavy pyramid sinker holds position in current. Pair "
            "with a sand spike and a fighting belt."
        ),
        "mainline": "50-80 lb braid with 80-100 lb shock leader",
        "leader": "3-6 ft of #9-#19 coated wire or 200+ lb cable",
        "image": "images/rigs/shark.svg",
    },
    "sabiki": {
        "name": "Sabiki Rig (Bait Catcher)",
        "description": (
            "A string of 4-8 tiny hooks dressed with flashy material "
            "(fish skin, tinsel, feathers) on short branches above a "
            "small weight. Jigged vertically to catch baitfish like "
            "menhaden, cigar minnows, herring and scad. Essential "
            "for stocking your bait bucket from the pier."
        ),
        "mainline": "10-15 lb mono or braid",
        "leader": "Pre-tied sabiki rig (size #6-#10 hooks)",
        "image": "images/rigs/sabiki.svg",
    },
    "deep-drop": {
        "name": "Deep Drop Rig",
        "description": (
            "A heavy bottom rig for fishing 200-1500+ ft depths "
            "targeting tilefish, snowy grouper, and other deep reef "
            "species. Uses 2-8 lb weights, electric or manual reels, "
            "and typically 2-3 circle hooks on dropper loops above "
            "the sinker. LED lights often added to attract fish."
        ),
        "mainline": "80-130 lb braid on electric reel",
        "leader": "100-200 lb mono with 2-3 dropper loops",
        "image": "images/rigs/deep-drop.svg",
    },
    "trolling": {
        "name": "Trolling Rig",
        "description": (
            "A lure or rigged bait towed behind a moving boat. "
            "Uses outriggers, planers, or downriggers to spread "
            "lines at different depths. Standard for offshore "
            "targeting of tuna, wahoo, marlin, and mahi-mahi."
        ),
        "mainline": "30-80 lb mono or braid",
        "leader": "6-15 ft of 60-200 lb fluorocarbon or wire",
        "image": "images/rigs/trolling.svg",
    },
    "tandem-jig": {
        "name": "Tandem Jig Rig (Shad Dart Rig)",
        "description": (
            "Two small jigs or shad darts tied in tandem on a light "
            "leader. Cast upstream and retrieved or jigged through "
            "current. The standard rig for shad and herring spring "
            "runs in NC rivers."
        ),
        "mainline": "6-10 lb mono or braid",
        "leader": "8-12 lb mono, 18 in between jigs",
        "image": "images/rigs/tandem-jig.svg",
    },
    "fly_pattern": {
        "name": "Saltwater Fly Leader Setup",
        "description": (
            "A weight-forward floating or intermediate fly line connected "
            "to a 7-12 ft tapered leader ending in fluorocarbon tippet. "
            "Leader length varies: 9-12 ft for calm flats (spooked fish), "
            "7-9 ft in wind. Tippet weight matches species: 12-16 lb for "
            "redfish and speckled trout, 20-30 lb for snook and tarpon. "
            "Tie a non-slip mono loop knot for fly action. "
            "Common patterns: Clouser Minnow (redfish, striped bass), "
            "EP Crab/Merkin (permit, bonefish), Toad fly (snook), "
            "Deceivers (bluefish, false albacore)."
        ),
        "mainline": "Weight-forward fly line matched to rod weight (8-12 wt)",
        "leader": "7-12 ft tapered to 12-30 lb fluorocarbon tippet",
        "image": "images/rigs/fly-leader.svg",
    },
    "current_jig": {
        "name": "Current Jig / Bridge Jig Rig",
        "description": (
            "A heavy bucktail or soft-plastic jig (1-3 oz) tied direct "
            "or with a short fluorocarbon leader, dropped vertically down "
            "bridge pilings or jetty faces and worked with a slow lift-drop. "
            "The weight must be heavy enough to reach bottom in tidal current — "
            "increase jig weight until you feel the bottom. "
            "White, chartreuse, and olive are standard colors. "
            "Optional: add a soft-plastic paddle tail or a live shrimp on "
            "the hook for scent. Targets sheepshead, snook, flounder, "
            "striped bass, and jack crevalle."
        ),
        "mainline": "20-30 lb braid",
        "leader": "18-24 in of 20-30 lb fluorocarbon",
        "image": "images/rigs/current-jig.svg",
    },
    "wade_light": {
        "name": "Wade / Flats Light Tackle Rig",
        "description": (
            "A jig head (1/8-1/4 oz) with a soft-plastic shrimp, paddle "
            "tail, or DOA-style bait, fished on light spinning or baitcasting "
            "gear. Alternatively, a popping cork 18-30 in above a "
            "fluorocarbon leader and circle hook for live or artificial shrimp. "
            "The goal is natural presentation with minimal splash — "
            "waded inshore fish are easily spooked. "
            "Use scented baits (GULP, DOA) in off-color water "
            "and finesse unscented plastics in clear conditions."
        ),
        "mainline": "10-15 lb braid on light spinning rod",
        "leader": "12-18 in of 15-20 lb fluorocarbon",
        "image": "images/rigs/wade-light.svg",
    },
    "kayak_live_bait": {
        "name": "Kayak Live Bait Freeline Rig",
        "description": (
            "A live baitfish (pinfish, mullet, grunt) or live shrimp "
            "hooked lightly through the lips or back and fished with "
            "minimal weight (split shot only, or weightless) so the bait "
            "swims freely near structure. From a kayak, anchor upcurrent "
            "and let the bait drift into the strike zone — snook, cobia, "
            "and tripletail will track a struggling live bait from a distance. "
            "Use a circle hook (3/0-5/0) for easy, keel-stable hook sets "
            "without a stand-up fighting platform."
        ),
        "mainline": "20-30 lb braid",
        "leader": "2-4 ft of 25-40 lb fluorocarbon",
        "image": "images/rigs/kayak-live-bait.svg",
    },
    "light_bottom_reef": {
        "name": "Light Bottom Rig (Reef & Structure)",
        "description": (
            "A single or double dropper-loop hook on a light fluorocarbon "
            "leader above a small egg or bank sinker — just enough weight "
            "to hold near sandy, muddy, or rocky bottom without spooking "
            "line-shy reef and structure feeders. The everyday setup for "
            "grunts, small snapper, porgies, flatfish, and nearshore "
            "rockfish over natural bottom, wrecks, and grass edges."
        ),
        "mainline": "10-20 lb mono or braid",
        "leader": "12-24 in of 15-25 lb fluorocarbon",
        "image": "images/rigs/light-bottom-reef.svg",
    },
    "ultralight_panfish": {
        "name": "Ultralight Panfish Rig",
        "description": (
            "A tiny #6-#2 hook tied straight to a light leader with just a "
            "split shot or two for weight — no swivel or heavy hardware to "
            "spook small-mouthed fish. Built for pinfish, silver perch, "
            "gobies, blennies, and other bite-sized bait-stealers around "
            "docks, jetties, and grass flats where finesse beats power."
        ),
        "mainline": "6-10 lb mono or braid",
        "leader": "12-18 in of 8-12 lb fluorocarbon",
        "image": "images/rigs/ultralight-panfish.svg",
    },
    "dropper_loop_deep": {
        "name": "Deep Dropper Loop Rig",
        "description": (
            "Two to three circle hooks on stiff dropper loops spaced above "
            "a heavy bank or torpedo sinker, dropped straight down to "
            "offshore rocky banks and reefs in 100-500+ ft. Weight is sized "
            "to reach bottom fast and hold in current. The standard setup "
            "for schooling rockfish, deepwater snapper, and other bank "
            "structure fish located on sonar and jigged straight down."
        ),
        "mainline": "50-80 lb braid",
        "leader": "40-60 lb mono with 2-3 dropper loops",
        "image": "images/rigs/dropper-loop-deep.svg",
    },
    "drift_bottom": {
        "name": "Drift Rig (Slip Sinker)",
        "description": (
            "A slip or torpedo sinker above a barrel swivel and 3-5 ft "
            "fluorocarbon leader to a circle hook, drifted slowly over "
            "sand, gravel, or mud bottom with the boat instead of anchored. "
            "Covers ground to find scattered flatfish and bottom feeders — "
            "the standard halibut- and flounder-style presentation."
        ),
        "mainline": "30-50 lb braid",
        "leader": "3-5 ft of 30-50 lb fluorocarbon",
        "image": "images/rigs/drift-bottom.svg",
    },
    "light_spin_cast": {
        "name": "Light Spinning/Casting Rig",
        "description": (
            "A jig head, soft plastic, or live bait fished weightless or "
            "on a light leader straight off spinning or baitcasting gear — "
            "no float or bottom weight. Cast to surface-schooling fish, "
            "mangrove edges, and inlet current for snook, tarpon, jacks, "
            "and other fish that key on a natural, unweighted presentation."
        ),
        "mainline": "10-20 lb braid",
        "leader": "18-24 in of 20-30 lb fluorocarbon",
        "image": "images/rigs/light-spin-cast.svg",
    },
}

def _classify_rig(rig_text: str) -> str:
    """Map a species' rig description to a canonical rig category key."""
    text = rig_text.lower()
    if (
        "n/a" in text
        or "observe" in text
        or "protected" in text
        or "not a hook-and-line" in text
        or "not applicable" in text
        or "too small for hook and line" in text
        or "not a rod-and-reel fishery" in text
        or "release immediately" in text
        or "used as bait" in text
        or "tined spear" in text
        or "hoop net" in text
        or "snorkel" in text
        or "scuba" in text
        or "free-diving" in text
        or "trap" in text
        or "crab pot" in text
        or "crab ring" in text
        or (
            "cast net" in text
            and not any(k in text for k in ("hook", "sabiki", "rig", "rod"))
        )
    ):
        return ""
    if (
        "deep-drop" in text
        or "deep drop" in text
        or "deepdrop" in text
        or "electric reel" in text
    ):
        return "deep-drop"
    if "trolling" in text and "slow" not in text:
        return "trolling"
    if "sabiki" in text or "bait catcher" in text or "gold-hook bait" in text:
        return "sabiki"
    if "shad dart" in text or "tandem" in text:
        return "tandem-jig"
    # Fly fishing patterns / leader setups
    if (
        "fly pattern" in text
        or "fly leader" in text
        or "clouser" in text
        or "deceiver" in text
        or "fly line" in text
        or "tippet" in text
        or "spey" in text
        or ("fly" in text and "rod" in text)
    ):
        return "fly_pattern"
    # Wade / flats light tackle
    if (
        "jig head" in text
        or "doa" in text
        or ("wade" in text and "light" in text)
        or "flats rig" in text
    ):
        return "wade_light"
    # Bridge / current jig
    if (
        "bridge jig" in text
        or "current jig" in text
        or "bucktail jig" in text
        or ("piling" in text and "jig" in text)
    ):
        return "current_jig"
    # Kayak live bait freeline
    if "freeline" in text or ("kayak" in text and "live bait" in text):
        return "kayak_live_bait"
    if "popping" in text or "cork" in text:
        return "popping-cork"
    if "stinger" in text or ("king" in text and "wire" in text):
        return "kingfish-stinger"
    if (
        "shark" in text
        or "very heavy wire" in text
        or "stand-up" in text
        or "heavy wire leader and heavy" in text
        or "steel cable leader" in text
        or ("wire leader" in text and ("heavy conventional" in text or "offshore" in text))
    ):
        return "shark"
    if "knocker" in text:
        return "knocker"
    if "pier" in text or "structure" in text or "vertical" in text:
        return "knocker"
    if (
        "pompano" in text
        or "float bead" in text
        or "floats above" in text
        or (
            "surf" in text
            and "light" in text
            and any(
                s in text for s in ("trough", "sandbar", "first bar", "swash", "inlet")
            )
        )
    ):
        return "pompano"
    if "double-dropper" in text or "hi-lo" in text or "two-hook" in text:
        return "hi-lo"
    if "float" in text or "free-line" in text or "balloon" in text:
        return "float"
    if (
        "carolina" in text
        or "fishfinder" in text
        or "fish finder" in text
        or "sliding" in text
    ):
        return "fishfinder"
    # Ultralight panfish — tiny hooks, or light tackle worked tight to
    # reef/dock structure for small bait-stealing bycatch.
    if (
        "ultra-light" in text
        or "ultralight" in text
        or "tiny hook" in text
        or "very small hook" in text
        or "micro hook" in text
        or "very light" in text
        or (
            "light rig" in text
            and any(
                s in text
                for s in (
                    "reef",
                    "kelp",
                    "rubble",
                    "piling",
                    "wreck",
                    "seagrass",
                    "sargassum",
                    "dock",
                )
            )
        )
    ):
        return "ultralight_panfish"
    # Deep dropper loop — offshore banks/reefs or mid-water suspended jigging.
    if (
        (
            "dropper loop" in text
            and (
                "offshore" in text
                or "bank" in text
                or "deep" in text
                or re.search(r"\b([2-9]\d\d|1[5-9]\d)\s*ft", text)
            )
        )
        or "mid-water rig" in text
        or "also caught jigging" in text
    ):
        return "dropper_loop_deep"
    # Drift rig — covered ground over sand/gravel/mud instead of anchored.
    if "drift" in text:
        return "drift_bottom"
    # Light/medium bottom rig near reef, rock, sand, mud, grass, mangrove,
    # or dock structure.
    if (
        "bottom rig" in text
        or (
            "bottom" in text
            and any(
                s in text
                for s in ("reef", "rocky", "sandy", "sand", "mud", "grass", "hard bottom")
            )
        )
        or "mangrove" in text
        or "dock" in text
        or ("live-bait rig" in text and ("reef" in text or "wreck" in text))
        or (
            ("sinker" in text or "dropper loop" in text)
            and ("near rocks" in text or "rocky" in text)
        )
        or (
            "single hook" in text
            and any(
                s in text for s in ("reef", "rocky", "kelp", "structure", "ledge", "wreck")
            )
        )
    ):
        return "light_bottom_reef"
    # Light spinning/casting — unweighted lure or live bait, no float/bottom rig.
    if (
        "spinning" in text
        or "baitcasting" in text
        or "casting" in text
        or ("live-bait rig" in text and "surface" in text)
    ):
        return "light_spin_cast"
    return "fishfinder"

# Maps rig keys to their primary gear style ("bait", "lure", or "mixed").
# Used to filter recommendations based on user bait/lure preferences.
_RIG_GEAR_TYPE: dict[str, str] = {
    "fishfinder": "bait",
    "hi-lo": "bait",
    "knocker": "bait",
    "pompano": "bait",
    "float": "mixed",
    "popping-cork": "mixed",
    "kingfish-stinger": "bait",
    "shark": "bait",
    "sabiki": "bait",
    "deep-drop": "bait",
    "trolling": "mixed",
    "tandem-jig": "lure",
    "fly_pattern": "lure",
    "current_jig": "lure",
    "wade_light": "mixed",
    "kayak_live_bait": "bait",
    "light_bottom_reef": "bait",
    "ultralight_panfish": "bait",
    "dropper_loop_deep": "bait",
    "drift_bottom": "bait",
    "light_spin_cast": "mixed",
}

# Rigs that work well as a first introduction — simple setup, forgiving tackle.
_BEGINNER_FRIENDLY_RIGS = frozenset(
    {
        "fishfinder",
        "hi-lo",
        "pompano",
        "float",
        "popping-cork",
        "light_bottom_reef",
        "ultralight_panfish",
    }
)

def _condition_rig_tip(
    wind_range: Optional[tuple[float, float]],
    wave_range: Optional[tuple[float, float]],
    water_temp: Optional[float],
    tide_state: str,
    fishing_types: set[str],
    gear_type: str,
) -> str:
    """A short, actionable gear tweak for the current conditions.

    Tailored by *gear_type* ("bait" / "lure" / "fly" / "mixed") so a bottom
    rig hears about sinker weight while a lure rig hears about line/clarity.
    Returns "" when there's nothing condition-specific worth saying.
    """
    avg_wind = (wind_range[0] + wind_range[1]) / 2 if wind_range else None
    avg_wave = (wave_range[0] + wave_range[1]) / 2 if wave_range else None
    moving_tide = tide_state in ("Rising", "Falling")
    parts: list[str] = []

    # Sinker / holding weight — most relevant to bottom & bait presentations.
    if gear_type in ("bait", "mixed"):
        rough = (avg_wave is not None and avg_wave >= 4) or (
            avg_wind is not None and avg_wind >= 20
        )
        moderate = (avg_wave is not None and avg_wave >= 2.5) or (
            avg_wind is not None and avg_wind >= 14
        )
        calm = (avg_wave is not None and avg_wave < 1.5) and (
            avg_wind is not None and avg_wind < 8
        )
        if rough:
            parts.append(
                "Heavy surf/wind — step up to a 5-6 oz pyramid or sputnik to hold bottom"
            )
        elif moderate:
            parts.append("Choppy — a 3-4 oz pyramid holds best in the wash")
        elif calm:
            parts.append("Calm — a 1-2 oz egg or bank sinker is plenty")
        if moving_tide and ("bridge" in fishing_types or "jetty" in fishing_types):
            parts.append("add weight to hold against the moving current")

    # Line / leader — clarity proxy from sea state, relevant to lures & fly.
    if gear_type in ("lure", "fly", "mixed"):
        if avg_wave is not None and avg_wind is not None and avg_wave < 1.5 and avg_wind < 8:
            parts.append(
                "Clear, calm water — drop to a lighter 15-20 lb fluorocarbon "
                "leader; fish are line-shy"
            )
        elif avg_wave is not None and avg_wave >= 4:
            parts.append(
                "Stirred-up water — a heavier 40-50 lb leader is fine and adds "
                "abrasion resistance"
            )

    # Rod length — surf distance and pier height.
    if "surf" in fishing_types and avg_wave is not None and avg_wave >= 3:
        parts.append("a 10-12 ft surf rod helps reach past the breakers")
    elif "pier" in fishing_types and avg_wave is not None and avg_wave >= 4:
        parts.append("bring a pier gaff or drop net to land fish from height")

    # Water temperature presentation cue.
    if water_temp is not None:
        if water_temp < 55:
            parts.append(
                "Cold water — downsize baits and slow the presentation for finicky fish"
            )
        elif water_temp > 82:
            parts.append("Warm water — fish deeper or shaded structure and keep bait lively")

    if not parts:
        return ""
    # Capitalize the first fragment; join the rest as a single sentence.
    tip = parts[0]
    for extra in parts[1:]:
        tip += "; " + extra[0].lower() + extra[1:]
    return tip + "."


def build_rig_recommendations(
    species_ranking: list[dict[str, Any]],
    fishing_types: Optional[list[str]] = None,
    experience: str = "",
    live_bait: str = "",
    cut_bait: str = "",
    lures: str = "",
    wind_range: Optional[tuple[float, float]] = None,
    wave_range: Optional[tuple[float, float]] = None,
    water_temp: Optional[float] = None,
    tide_state: str = "",
) -> list[dict[str, Any]]:
    """Build rig recommendations based on currently-active species.

    Groups active species by rig type and produces one recommendation
    per rig, ordered by the highest-ranked species that uses it.
    When *fishing_types* is provided, type-specific rigs are prepended
    even if no species in the ranking explicitly reference them.

    Bait/lure preference filtering:
    - lures=="no": lure-only rigs are removed.
    - live_bait=="no" and cut_bait=="no" (lure-only): bait rigs are deprioritised.
    Experience-level filtering:
    - experience=="beginner": only beginner-friendly rigs are shown (top 3 max).
    """
    ft = set(fishing_types or [])

    wants_lures = lures != "no"
    lures_only = (
        (lures == "yes" or lures == "sometimes")
        and live_bait == "no"
        and cut_bait == "no"
    )

    rig_groups: dict[str, list[dict[str, Any]]] = {}
    rig_order: list[str] = []

    for sp in species_ranking:
        key = _classify_rig(sp["rig"])
        if key not in rig_groups:
            rig_groups[key] = []
            rig_order.append(key)
        rig_groups[key].append(sp)

    # Prepend fishing-type-specific rigs that may not emerge from species data
    type_rig_keys: list[str] = []
    if "fly" in ft and "fly_pattern" not in rig_order:
        type_rig_keys.append("fly_pattern")
    if ("bridge" in ft or "jetty" in ft) and "current_jig" not in rig_order:
        type_rig_keys.append("current_jig")
    if "wade" in ft and "wade_light" not in rig_order:
        type_rig_keys.append("wade_light")
    if "kayak" in ft and "kayak_live_bait" not in rig_order:
        type_rig_keys.append("kayak_live_bait")

    def _make_rec(
        key: str, group: Optional[list[dict[str, Any]]] = None
    ) -> Optional[dict[str, Any]]:
        category = RIG_CATEGORIES.get(key)
        if category is None:
            return None
        species_names = [sp["name"] for sp in (group or [])]
        hooks = list(dict.fromkeys(sp["hook_size"] for sp in (group or [])))
        sinkers = list(dict.fromkeys(sp["sinker"] for sp in (group or [])))
        return {
            "name": category["name"],
            "description": category["description"],
            "mainline": category["mainline"],
            "leader": category["leader"],
            "hook": " or ".join(hooks[:3]) if hooks else "",
            "sinker": " or ".join(sinkers[:3]) if sinkers else "",
            "targets": species_names,
            "image": category.get("image", ""),
            "knots": get_knots_for_rig(key),
        }

    recommendations: list[dict[str, Any]] = []

    # Type-specific rigs first
    for key in type_rig_keys:
        rec = _make_rec(key)
        if rec:
            recommendations.append(rec)

    # Species-derived rigs
    for key in rig_order:
        if key in type_rig_keys:
            continue  # already added
        rec = _make_rec(key, rig_groups[key])
        if rec:
            recommendations.append(rec)

    # ── Bait/lure preference filtering ─────────────────────────────────────
    # Annotate each rec with its gear type for template use, then filter.
    # We need to track which key produced each rec — rebuild with key metadata.
    def _gear_type_for_rec(rec: dict[str, Any]) -> str:
        # Match rec name back to a rig key via RIG_CATEGORIES.
        for k, cat in RIG_CATEGORIES.items():
            if cat.get("name") == rec.get("name"):
                return _RIG_GEAR_TYPE.get(k, "mixed")
        return "mixed"

    if live_bait or cut_bait or lures:
        if not wants_lures:
            # User explicitly doesn't use lures — drop lure-only rigs.
            recommendations = [
                r for r in recommendations if _gear_type_for_rec(r) != "lure"
            ]
        elif lures_only:
            # Lure-only angler: sort lure/mixed rigs first, bait rigs last.
            recommendations.sort(
                key=lambda r: 0 if _gear_type_for_rec(r) in ("lure", "mixed") else 1
            )

    # ── Experience-level filtering ──────────────────────────────────────────
    if experience == "beginner":
        beginner = [
            r
            for r in recommendations
            if any(
                RIG_CATEGORIES.get(k, {}).get("name") == r.get("name")
                and k in _BEGINNER_FRIENDLY_RIGS
                for k in RIG_CATEGORIES
            )
        ]
        advanced = [r for r in recommendations if r not in beginner]
        # Beginners get friendly rigs first, capped at 3 total.
        recommendations = (beginner + advanced)[:3]

    # ── Condition-aware gear tweaks ─────────────────────────────────────────
    # Annotate each rig with an actionable adjustment for today's wind, waves,
    # current, and water temp (heavier sinkers in surf, lighter leaders in
    # clear calm water, rod-length and presentation cues).
    if wind_range or wave_range or water_temp is not None:
        for rec in recommendations:
            tip = _condition_rig_tip(
                wind_range,
                wave_range,
                water_temp,
                tide_state,
                ft,
                _gear_type_for_rec(rec),
            )
            if tip:
                rec["cond_tip"] = tip

    return recommendations

# ---------------------------------------------------------------------------
# Fishing knot recommendations
# ---------------------------------------------------------------------------

KNOTS_DB: dict[str, dict[str, str]] = {
    "improved_clinch": {
        "name": "Improved Clinch Knot",
        "use": "Hook or swivel to mono/fluoro leader",
        "strength": "95%",
        "steps": (
            '1. Thread 6" of line through the hook eye. '
            "2. Wrap the tag end around the standing line 5-7 times. "
            "3. Pass the tag end through the small loop at the hook eye. "
            "4. Pass it again through the big loop you just created. "
            "5. Moisten, pull tight, trim tag."
        ),
    },
    "palomar": {
        "name": "Palomar Knot",
        "use": "Hook to braid or mono — strongest simple knot",
        "strength": "98%",
        "steps": (
            '1. Double 6" of line and pass the loop through the hook eye. '
            "2. Tie a simple overhand knot with the doubled line. "
            "3. Pass the loop over the entire hook. "
            "4. Moisten and pull both ends to tighten. Trim tag."
        ),
    },
    "uni_knot": {
        "name": "Uni Knot",
        "use": "All-purpose: hook, swivel, or line-to-line",
        "strength": "90%",
        "steps": (
            '1. Pass line through the eye, double back 6". '
            "2. Form a loop alongside the doubled line. "
            "3. Wrap the tag end through the loop 4-6 times. "
            "4. Moisten, pull tag end to tighten, then slide knot to eye."
        ),
    },
    "uni_to_uni": {
        "name": "Double Uni Knot",
        "use": "Braid to fluoro/mono leader connection",
        "strength": "90%",
        "steps": (
            '1. Overlap braid and leader by 8". '
            "2. Make a Uni Knot with each tag end (4 wraps for mono, 8 for braid). "
            "3. Moisten both knots. "
            "4. Pull standing lines to slide knots together. Trim tags."
        ),
    },
    "fg_knot": {
        "name": "FG Knot",
        "use": "Braid to leader — slimmest, strongest connection",
        "strength": "98%",
        "steps": (
            "1. Tension braid between teeth and rod. "
            "2. Weave leader over and under braid 15-20 alternating wraps. "
            "3. Cinch tight with half hitches (3-4). "
            "4. Lock with 2 half hitches on braid only. Trim and melt tag."
        ),
    },
    "surgeons_loop": {
        "name": "Surgeon's Loop",
        "use": "Create a loop for dropper rigs and quick-change clips",
        "strength": "95%",
        "steps": (
            '1. Double 3" of line to form a loop. '
            "2. Tie an overhand knot with the doubled section. "
            "3. Pass the loop through a second time (double overhand). "
            "4. Moisten and pull tight."
        ),
    },
    "dropper_loop": {
        "name": "Dropper Loop",
        "use": "Create a standing loop mid-leader for hi-lo rigs",
        "strength": "90%",
        "steps": (
            "1. Form a loop in the middle of your leader. "
            "2. Wrap one side through the loop 5-6 times. "
            "3. Push the middle of the original loop through the center opening. "
            "4. Pull both ends to tighten around the new loop."
        ),
    },
    "haywire_twist": {
        "name": "Haywire Twist",
        "use": "Single-strand wire to hook or swivel",
        "strength": "100%",
        "steps": (
            "1. Pass wire through hook eye. "
            "2. Cross wires and twist together 4-5 times (barrel twist). "
            "3. Wrap tag end tightly around standing wire 5-6 times (tight wraps). "
            "4. Bend tag end into a handle, crank to break clean."
        ),
    },
}

# Map rig types to their recommended knots
_RIG_KNOTS: dict[str, list[str]] = {
    "fishfinder": ["improved_clinch", "uni_to_uni"],
    "hi-lo": ["dropper_loop", "improved_clinch"],
    "knocker": ["palomar", "uni_to_uni"],
    "pompano": ["dropper_loop", "surgeons_loop"],
    "float": ["improved_clinch", "uni_knot"],
    "popping-cork": ["uni_knot", "uni_to_uni"],
    "kingfish-stinger": ["haywire_twist", "improved_clinch"],
    "shark": ["haywire_twist", "fg_knot"],
    "sabiki": ["uni_knot"],
    "deep-drop": ["dropper_loop", "fg_knot"],
    "tandem-jig": ["palomar", "uni_to_uni"],
    "trolling": ["improved_clinch", "fg_knot"],
    "fly_pattern": ["uni_to_uni", "surgeons_loop"],
    "current_jig": ["palomar", "fg_knot"],
    "wade_light": ["palomar", "uni_knot"],
    "kayak_live_bait": ["uni_knot", "uni_to_uni"],
    "light_bottom_reef": ["dropper_loop", "improved_clinch"],
    "ultralight_panfish": ["improved_clinch", "uni_knot"],
    "dropper_loop_deep": ["dropper_loop", "fg_knot"],
    "drift_bottom": ["improved_clinch", "uni_to_uni"],
    "light_spin_cast": ["palomar", "uni_to_uni"],
}

def get_knots_for_rig(rig_key: str) -> list[dict[str, str]]:
    """Return the recommended knots for a rig type."""
    knot_keys = _RIG_KNOTS.get(rig_key, ["improved_clinch"])
    return [KNOTS_DB[k] for k in knot_keys if k in KNOTS_DB]

# Natural baits with the species they target and seasonal availability.
# ``available_months`` controls when a bait is practical to obtain/use.
# ``notes_seasonal`` overrides the default ``notes`` during specific seasons.
# Baits out of season are demoted in the ranking so anglers see what they
# can actually get their hands on right now.
BAIT_DB: list[dict[str, Any]] = [
    {
        "bait": "Live shrimp",
        "available_months": [3, 4, 5, 6, 7, 8, 9, 10, 11],
        "notes": "Top choice for speckled trout and versatile for many species; use under a popping cork or on bottom rigs.",
        "notes_seasonal": {
            "winter": "Live shrimp are scarce at bait shops in winter; frozen shrimp or Fishbites are a reliable substitute.",
        },
        "targets": ["Speckled trout", "Red drum", "Sheepshead", "Black drum"],
    },
    {
        "bait": "Cut mullet",
        "available_months": list(range(1, 13)),
        "notes": "Excellent for red drum and black drum; fresh cut strips release scent and stay on the hook.",
        "notes_seasonal": {
            "fall": "Fall mullet run makes fresh mullet abundant and free; stock up and freeze for year-round use.",
        },
        "targets": ["Red drum", "Black drum", "Bluefish", "Striped bass"],
    },
    {
        "bait": "Menhaden (live or cut)",
        "available_months": [3, 4, 5, 6, 7, 8, 9, 10, 11],
        "notes": "Prime bait for red drum, bluefish and striped bass; live menhaden offer a distinct advantage in calm conditions.",
        "notes_seasonal": {
            "winter": "Menhaden are offshore in winter; use frozen cut menhaden or substitute with cut mullet.",
            "fall": "Menhaden schools are thick along the beach during fall; cast-net your own for the freshest bait.",
        },
        "targets": ["Red drum", "Bluefish", "Striped bass", "Cobia"],
    },
    {
        "bait": "Sand fleas (mole crabs)",
        "available_months": [4, 5, 6, 7, 8, 9, 10],
        "notes": "Effective for whiting and pompano; dig in the swash zone for fresh fleas.",
        "notes_seasonal": {
            "winter": "Sand fleas are buried deep or absent in cold months; use Fishbites Sand Flea flavor as a substitute.",
            "spring": "Sand fleas are returning to the swash zone; dig at the water's edge as waves recede.",
        },
        "targets": ["Whiting", "Pompano", "Sheepshead"],
    },
    {
        "bait": "Squid strips",
        "available_months": list(range(1, 13)),
        "notes": "Durable on the hook; attract black sea bass, whiting and puffer fish. Available frozen year-round at any bait shop.",
        "targets": ["Black sea bass", "Whiting", "Northern puffer", "Triggerfish"],
    },
    {
        "bait": "Fiddler crabs",
        "available_months": [3, 4, 5, 6, 7, 8, 9, 10, 11],
        "notes": "Essential for sheepshead and tautog; use whole crabs on small strong hooks.",
        "notes_seasonal": {
            "winter": "Fiddler crabs are dormant in winter burrows; check bait shops or use fresh shrimp as a substitute.",
            "spring": "Fiddler crabs are emerging from winter burrows; trap them in marsh mud at low tide.",
        },
        "targets": ["Sheepshead", "Tautog", "Triggerfish"],
    },
    {
        "bait": "Bloodworms",
        "available_months": list(range(1, 13)),
        "notes": "Popular for whiting, black drum, spot and puffer fish; cut into small pieces for double-dropper rigs.",
        "notes_seasonal": {
            "winter": "Bloodworms are a top winter bait; their scent and movement attract cold-water bottom feeders when other baits are scarce.",
            "fall": "Bloodworms during the fall spot run are unbeatable; small pieces on #6 hooks catch spot after spot.",
        },
        "targets": [
            "Whiting",
            "Black drum",
            "Northern puffer",
            "Spot",
            "Atlantic croaker",
        ],
    },
    {
        "bait": "Clams and crab pieces",
        "available_months": list(range(1, 13)),
        "notes": "Best for black drum; larger pieces stay on the hook and deter small pickers. Available year-round.",
        "targets": ["Black drum", "Tautog", "Sheepshead"],
    },
    {
        "bait": "Live finger mullet",
        "available_months": [4, 5, 6, 7, 8, 9, 10, 11],
        "notes": "Top producer for flounder and red drum; hook through the lips and drift slowly along the bottom.",
        "notes_seasonal": {
            "winter": "Live finger mullet are hard to find in winter; try frozen mullet strips or live shrimp as alternatives.",
            "fall": "Finger mullet are everywhere during the fall run; cast-net your own for the liveliest bait.",
        },
        "targets": ["Flounder", "Red drum", "Speckled trout"],
    },
    {
        "bait": "Fresh shrimp pieces",
        "available_months": list(range(1, 13)),
        "notes": "Cut shrimp on bottom rigs is a universal bait for smaller surf and pier species. Available frozen year-round.",
        "targets": ["Atlantic croaker", "Pinfish", "Pigfish", "Spot", "Gray trout"],
    },
    {
        "bait": "Live cigar minnows or threadfin",
        "available_months": [5, 6, 7, 8, 9, 10],
        "notes": "Prime bait for king mackerel and false albacore; rig on wire leader with stinger hook.",
        "notes_seasonal": {
            "winter": "Cigar minnows are not available inshore in winter; frozen are a poor substitute for kings.",
        },
        "targets": ["King mackerel", "False albacore", "Cobia"],
    },
    {
        "bait": "Large cut menhaden or bluefish chunks",
        "available_months": list(range(1, 13)),
        "notes": "Heavy shark baits; use fresh, bloody chunks on wire leader for maximum scent trail. Frozen works year-round.",
        "targets": [
            "Blacktip shark",
            "Bull shark",
            "Sandbar shark",
            "Spinner shark",
            "Dusky shark",
        ],
    },
    {
        "bait": "Live blue runners",
        "available_months": [5, 6, 7, 8, 9, 10],
        "notes": "Top live bait for large gamefish; catch on sabiki rigs and fish on heavy tackle.",
        "notes_seasonal": {
            "winter": "Blue runners are not available inshore in winter months.",
        },
        "targets": [
            "Greater amberjack",
            "King mackerel",
            "Cobia",
            "Black grouper",
            "Gag grouper",
        ],
    },
    {
        "bait": "Live menhaden (pogies)",
        "available_months": [3, 4, 5, 6, 7, 8, 9, 10, 11],
        "notes": "The king of live baits in NC; cast-net schools and fish on circle hooks for almost any large gamefish.",
        "notes_seasonal": {
            "winter": "Live pogies are hard to find in winter; use frozen cut menhaden or live mullet as alternatives.",
            "fall": "Massive menhaden schools are along the beach in fall; cast-net more than you need and freeze the rest.",
        },
        "targets": [
            "Red drum",
            "Cobia",
            "Tarpon",
            "King mackerel",
            "Jack crevalle",
            "Gag grouper",
        ],
    },
    {
        "bait": "Ballyhoo (rigged or live)",
        "available_months": [4, 5, 6, 7, 8, 9, 10, 11],
        "notes": "Premier offshore trolling bait; rig on wire or heavy fluorocarbon for billfish and pelagics.",
        "notes_seasonal": {
            "winter": "Frozen ballyhoo are available at offshore tackle shops year-round for winter Gulf Stream trips.",
        },
        "targets": ["Mahi-mahi", "Sailfish", "Wahoo", "White marlin", "Blue marlin"],
    },
    {
        "bait": "Shad darts and small jigs",
        "available_months": list(range(1, 13)),
        "notes": "Essential tackle for spring shad runs; fish tandem rigs in current near river mouths and bridges.",
        "notes_seasonal": {
            "spring": "This is THE time for shad darts; bright colors (pink, chartreuse, white) in 1/16-1/8 oz are the standard.",
            "fall": "Off-season for shad; save these for the spring river runs.",
        },
        "targets": ["Hickory shad", "American shad"],
    },
    {
        "bait": "Live crabs (blue crab, fiddler)",
        "available_months": [3, 4, 5, 6, 7, 8, 9, 10, 11],
        "notes": "Top bait for structure-oriented species; use whole small crabs or halved blue crabs on circle hooks.",
        "notes_seasonal": {
            "winter": "Crabs are dormant in cold months; use fresh shrimp or clam pieces as a substitute for structure species.",
        },
        "targets": [
            "Sheepshead",
            "Tautog",
            "Permit",
            "Triggerfish",
            "Bonnethead shark",
        ],
    },
    {
        "bait": "Cut squid strips",
        "available_months": list(range(1, 13)),
        "notes": "Durable and versatile bottom fishing bait; stays on the hook well for reef and wreck species. Frozen year-round.",
        "targets": [
            "Red snapper",
            "Vermilion snapper",
            "Black sea bass",
            "Red porgy",
            "White grunt",
            "Scup",
        ],
    },
    {
        "bait": "Cedar plugs and trolling feathers",
        "available_months": list(range(1, 13)),
        "notes": "Classic offshore trolling lures for tuna; troll at 6-8 knots in clean blue water. Tackle shop staple.",
        "targets": ["Blackfin tuna", "Yellowfin tuna", "Atlantic bonito"],
    },
    {
        "bait": "Fishbites (artificial strips)",
        "available_months": list(range(1, 13)),
        "notes": "Scented artificial bait strips that stay on the hook forever; great substitute when live/fresh bait is unavailable.",
        "notes_seasonal": {
            "winter": "Fishbites are the go-to winter bait when live options are scarce; Sand Flea and Shrimp flavors work best.",
        },
        "targets": ["Whiting", "Pompano", "Spot", "Atlantic croaker", "Black drum"],
    },
]

def _score_species(
    sp: dict[str, Any],
    month: int,
    water_temp: float,
    wind_dir: Optional[str] = None,
    wind_range: Optional[tuple[float, float]] = None,
    wave_range: Optional[tuple[float, float]] = None,
    hour: int = 12,
    coast: str = "east",
    _cond_modifier: Optional["Callable[[str], float]"] = None,
) -> float:
    """Compute a bite-likelihood score for a species given current conditions.

    Score components (max ~100):
    - Temperature fit (0-50): how close current water temp is to the
      species' ideal range.
    - Seasonal fit (0-30): whether the current month is a peak, good,
      or off month.
    - Conditions modifier (-5 to +15): wind direction, wind speed,
      wave height, and time-of-day adjustments.
    - Presence penalty (-100): water temp outside survivable range.

    When *_cond_modifier* is supplied (a pre-built closure from
    _build_conditions_modifier), it replaces the _conditions_modifier call so
    wind/wave constants are not recomputed per species.
    """
    score = 0.0

    if water_temp < sp["temp_min"] or water_temp > sp["temp_max"]:
        return -100.0

    ideal_low = sp["temp_ideal_low"]
    ideal_high = sp["temp_ideal_high"]
    if ideal_low <= water_temp <= ideal_high:
        score += 50.0
    elif water_temp < ideal_low:
        _d = ideal_low - water_temp
        _r = ideal_low - sp["temp_min"]
        # _d/_r is always in [0,1] when water_temp is in [temp_min, ideal_low)
        score += (50.0 * (1 - _d / _r)) if _r > 0 else 25.0
    else:
        _d = water_temp - ideal_high
        _r = sp["temp_max"] - ideal_high
        # _d/_r is always in [0,1] when water_temp is in (ideal_high, temp_max]
        score += (50.0 * (1 - _d / _r)) if _r > 0 else 25.0

    if month in sp["_peak_months_set"]:
        score += 30.0
    elif month in sp["_good_months_set"]:
        score += 15.0

    # --- Dynamic conditions modifiers ---
    if _cond_modifier is not None:
        score += _cond_modifier(sp["name"])
    else:
        score += _conditions_modifier(sp, wind_dir, wind_range, wave_range, hour, coast)

    return score

# ---------------------------------------------------------------------------
# Conditions-based scoring modifiers
# ---------------------------------------------------------------------------
# These tables classify species by their preferred fishing conditions.
# Species not explicitly listed get no conditions bonus or penalty.
# ---------------------------------------------------------------------------

# Species that bite better on an incoming (onshore) wind -- the wind pushes
# bait and turbid water toward shore, stimulating feeding.
_ONSHORE_WIND_SPECIES: set = {
    "Red drum (puppy drum)",
    "Bluefish",
    "Pompano",
    "Whiting (sea mullet, kingfish)",
    "Spot",
    "Atlantic croaker",
    "Flounder (summer flounder)",
    "Southern flounder",
    "Gulf flounder",
    "Spanish mackerel",
    "Jack crevalle",
    "Cobia",
    "Southern kingfish (ground mullet)",
    "Gulf kingfish (gulf whiting)",
    "Blacktip shark",
    "Spinner shark",
    "Bull shark",
    "Striped bass (rockfish)",
    "Black drum",
    # Pacific
    "Barred surfperch",
    "Redtail surfperch",
    "Calico surfperch",
    "Corbina",
    "Spotfin croaker",
    "Yellowfin croaker",
    "Leopard shark",
    "Shovelnose guitarfish",
    # Hawaii
    "Giant trevally (ulua)",
    "Moi (Pacific threadfin)",
    "Bonefish (oio)",
}

# Species that prefer calmer conditions and/or offshore wind (clearer water).
_CALM_WATER_SPECIES: set = {
    "Sheepshead",
    "Tautog (blackfish)",
    "Triggerfish (gray)",
    "Spadefish (Atlantic)",
    "Mangrove snapper (gray snapper)",
    "Hogfish",
    "Bermuda chub (sea chub)",
    "Lookdown",
    "Sergeant major (damselfish)",
    "Planehead filefish",
    "Scrawled cowfish",
    "Ocean triggerfish",
    "Queen triggerfish",
    "Gray snapper (juvenile)",
    "Speckled trout (spotted seatrout)",
    "Tarpon",
    "Tripletail",
    "Permit",
    "Snook",
    # Pacific
    "Kelp bass (calico bass)",
    "Sand bass (barred sand bass)",
    "California sheephead",
    "Opaleye",
    "Halfmoon (Catalina perch)",
    "California halibut",
    "White seabass",
    # Hawaii
    "Mu (bigeye emperor)",
    "Menpachi (soldierfish)",
    "Aholehole (Hawaiian flagtail)",
}

# Species that feed more actively in rougher surf.
_ROUGH_SURF_SPECIES: set = {
    "Red drum (puppy drum)",
    "Bluefish",
    "Striped bass (rockfish)",
    "Whiting (sea mullet, kingfish)",
    "Pompano",
    "Black drum",
    "Smooth dogfish",
    "Atlantic croaker",
    "Spot",
    "Southern kingfish (ground mullet)",
    "Gulf kingfish (gulf whiting)",
    # Pacific
    "Barred surfperch",
    "Redtail surfperch",
    "Calico surfperch",
    "Corbina",
    "Spotfin croaker",
    "Yellowfin croaker",
}

# Species that feed best in low-light conditions (dawn, dusk, night).
_LOW_LIGHT_SPECIES: set = {
    "Striped bass (rockfish)",
    "Speckled trout (spotted seatrout)",
    "Red drum (puppy drum)",
    "Cobia",
    "Tarpon",
    "Flounder (summer flounder)",
    "Southern flounder",
    "Gulf flounder",
    "Ribbonfish (Atlantic cutlassfish)",
    "Spotted moray eel",
    "Green moray eel",
    "American eel",
    "Conger eel",
    "Squirrelfish",
    "Bigeye (Priacanthus arenatus)",
    "Short bigeye",
    "Blacktip shark",
    "Bull shark",
    "Sandbar shark",
    "Lemon shark",
    # Pacific
    "Lingcod",
    "Cabezon",
    "Leopard shark",
    "Bat ray",
    # Hawaii
    "Giant trevally (ulua)",
    "Menpachi (soldierfish)",
    "Mu (bigeye emperor)",
}

# Species that are more active during bright midday conditions.
_DAYTIME_SPECIES: set = {
    "Spanish mackerel",
    "King mackerel (kingfish)",
    "Cero mackerel",
    "False albacore (little tunny)",
    "Mahi-mahi (dolphinfish)",
    "Sergeant major (damselfish)",
    "Blue tang (surgeonfish)",
    "Spotfin butterflyfish",
    "Gray angelfish",
    "Bermuda chub (sea chub)",
    "Pinfish",
    "Pigfish",
    # Pacific
    "Pacific mackerel (chub mackerel)",
    "Jack mackerel (Spanish jack)",
    "Pacific bonito",
    "Yellowtail (California yellowtail)",
    "Jacksmelt",
    "Pacific sardine",
    "Northern anchovy",
    # Hawaii
    "Bluefin trevally (omilu)",
    "Papio (juvenile jack)",
    "Kaku (barracuda)",
}

# Compass directions grouped for onshore/offshore determination, keyed by which
# way the open ocean faces (the coastline's *orientation*):
#   - East-facing coasts (Atlantic): ocean east → onshore easterly, offshore westerly
#   - West-facing coasts (Pacific):  ocean west → onshore westerly, offshore easterly
#   - South-facing coasts (Gulf):    ocean south → onshore southerly, offshore northerly
# Treating the Gulf as east-facing (the previous behaviour) mislabelled its
# alongshore E/W winds, so the Gulf gets its own south-facing set.
_ONSHORE_DIRS_EAST: set = {"S", "SE", "E", "SSE", "ESE", "SSW", "ENE", "NE"}
_OFFSHORE_DIRS_EAST: set = {"N", "NW", "W", "NNW", "WNW", "NNE"}
_ONSHORE_DIRS_WEST: set = {"W", "NW", "SW", "WNW", "WSW", "NNW", "SSW"}
_OFFSHORE_DIRS_WEST: set = {"E", "NE", "SE", "ENE", "ESE", "NNE", "SSE"}
_ONSHORE_DIRS_GULF: set = {"S", "SSW", "SW", "SSE", "SE", "WSW", "ESE"}
_OFFSHORE_DIRS_GULF: set = {"N", "NNE", "NE", "NNW", "NW", "ENE", "WNW"}

# Hawaii (and any island/omnidirectional coast): onshore/offshore is
# ambiguous because the shore faces every direction, so apply no wind-direction
# bonus or penalty rather than guessing.
_DIRS_BY_ORIENTATION: dict[str, tuple[set, set]] = {
    "east": (_ONSHORE_DIRS_EAST, _OFFSHORE_DIRS_EAST),
    "west": (_ONSHORE_DIRS_WEST, _OFFSHORE_DIRS_WEST),
    "gulf": (_ONSHORE_DIRS_GULF, _OFFSHORE_DIRS_GULF),
    "hawaii": (set(), set()),
}


def onshore_offshore_dirs(orientation: str) -> tuple[set, set]:
    """Return ``(onshore_dirs, offshore_dirs)`` for a coastline orientation.

    ``orientation`` is one of ``"east"``, ``"west"``, ``"gulf"``, ``"hawaii"``.
    Unknown values fall back to the east-facing (Atlantic) sets so existing
    callers keep their previous behaviour.
    """
    return _DIRS_BY_ORIENTATION.get(orientation, _DIRS_BY_ORIENTATION["east"])


# Default for backward compatibility
_ONSHORE_DIRS = _ONSHORE_DIRS_EAST
_OFFSHORE_DIRS = _OFFSHORE_DIRS_EAST

# Union of all species that appear in at least one conditions modifier set.
# Used for a fast early-return in the _modifier closure: ~90% of species are in
# none of these sets, so this single lookup avoids 5-9 unnecessary lookups per call.
_ANY_MODIFIER_SPECIES: frozenset = frozenset(
    _ONSHORE_WIND_SPECIES | _CALM_WATER_SPECIES | _ROUGH_SURF_SPECIES
    | _LOW_LIGHT_SPECIES | _DAYTIME_SPECIES
)

def _conditions_modifier(
    sp: dict[str, Any],
    wind_dir: Optional[str],
    wind_range: Optional[tuple[float, float]],
    wave_range: Optional[tuple[float, float]],
    hour: int,
    coast: str = "east",
) -> float:
    """Compute a conditions-based score modifier for a species.

    Returns a value between roughly -5 and +15 based on how well current
    wind direction, wind speed, wave height, and time of day match the
    species' preferred conditions.

    ``coast`` is the coastline orientation used for wind direction — one of
    ``"east"`` (Atlantic), ``"west"`` (Pacific), ``"gulf"`` (south-facing), or
    ``"hawaii"`` (omnidirectional, no wind-direction effect).
    """
    modifier = 0.0
    name = sp["name"]

    # --- Wind direction modifier (up to +5 / -3) ---
    if wind_dir:
        onshore_dirs, offshore_dirs = onshore_offshore_dirs(coast)
        is_onshore = wind_dir in onshore_dirs
        is_offshore = wind_dir in offshore_dirs

        if name in _ONSHORE_WIND_SPECIES:
            modifier += 5.0 if is_onshore else (-3.0 if is_offshore else 0.0)
        elif name in _CALM_WATER_SPECIES:
            modifier += 5.0 if is_offshore else (-3.0 if is_onshore else 0.0)

    # --- Wind speed modifier (up to +3 / -2) ---
    if wind_range:
        wind_avg = (wind_range[0] + wind_range[1]) / 2.0
        if name in _ROUGH_SURF_SPECIES:
            # Moderate wind (10-18 kt) stirs up bait -- bonus
            if 10 <= wind_avg <= 18:
                modifier += 3.0
            elif wind_avg < 5:
                modifier -= 2.0
        elif name in _CALM_WATER_SPECIES:
            # Calm conditions (< 8 kt) are ideal
            if wind_avg < 8:
                modifier += 3.0
            elif wind_avg > 15:
                modifier -= 2.0

    # --- Wave height modifier (up to +4 / -2) ---
    if wave_range:
        wave_avg = (wave_range[0] + wave_range[1]) / 2.0
        if name in _ROUGH_SURF_SPECIES:
            # Moderate surf (2-5 ft) concentrates bait in troughs
            if 2 <= wave_avg <= 5:
                modifier += 4.0
            elif wave_avg < 1:
                modifier -= 1.0
        elif name in _CALM_WATER_SPECIES:
            if wave_avg < 2:
                modifier += 4.0
            elif wave_avg > 4:
                modifier -= 2.0

    # --- Time of day modifier (up to +3 / -1) ---
    is_low_light = hour < 7 or hour > 18  # before 7am or after 6pm
    is_midday = 10 <= hour <= 15

    if name in _LOW_LIGHT_SPECIES:
        modifier += 3.0 if is_low_light else (-1.0 if is_midday else 0.0)
    elif name in _DAYTIME_SPECIES:
        modifier += 3.0 if is_midday else (-1.0 if is_low_light else 0.0)

    return modifier


def _build_conditions_modifier(
    wind_dir: Optional[str],
    wind_range: Optional[tuple[float, float]],
    wave_range: Optional[tuple[float, float]],
    hour: int,
    coast: str = "east",
) -> "Callable[[str], float]":
    """Return a per-species conditions modifier with all constants pre-evaluated.

    Calling this once per scoring loop (instead of recomputing wind/wave averages
    and boolean flags inside _conditions_modifier for every species) eliminates
    ~5 repeated arithmetic operations × 572 species per build_species_ranking call.
    """
    onshore_dirs, offshore_dirs = onshore_offshore_dirs(coast)
    is_onshore = bool(wind_dir and wind_dir in onshore_dirs)
    is_offshore = bool(wind_dir and wind_dir in offshore_dirs)
    wind_avg: Optional[float] = (
        (wind_range[0] + wind_range[1]) / 2.0 if wind_range else None
    )
    wave_avg: Optional[float] = (
        (wave_range[0] + wave_range[1]) / 2.0 if wave_range else None
    )
    is_low_light = hour < 7 or hour > 18
    is_midday = 10 <= hour <= 15

    def _modifier(name: str) -> float:
        if name not in _ANY_MODIFIER_SPECIES:
            return 0.0
        mod = 0.0

        if wind_dir:
            if name in _ONSHORE_WIND_SPECIES:
                mod += 5.0 if is_onshore else (-3.0 if is_offshore else 0.0)
            elif name in _CALM_WATER_SPECIES:
                mod += 5.0 if is_offshore else (-3.0 if is_onshore else 0.0)

        if wind_avg is not None:
            if name in _ROUGH_SURF_SPECIES:
                if 10 <= wind_avg <= 18:
                    mod += 3.0
                elif wind_avg < 5:
                    mod -= 2.0
            elif name in _CALM_WATER_SPECIES:
                if wind_avg < 8:
                    mod += 3.0
                elif wind_avg > 15:
                    mod -= 2.0

        if wave_avg is not None:
            if name in _ROUGH_SURF_SPECIES:
                if 2 <= wave_avg <= 5:
                    mod += 4.0
                elif wave_avg < 1:
                    mod -= 1.0
            elif name in _CALM_WATER_SPECIES:
                if wave_avg < 2:
                    mod += 4.0
                elif wave_avg > 4:
                    mod -= 2.0

        if name in _LOW_LIGHT_SPECIES:
            mod += 3.0 if is_low_light else (-1.0 if is_midday else 0.0)
        elif name in _DAYTIME_SPECIES:
            mod += 3.0 if is_midday else (-1.0 if is_low_light else 0.0)

        return mod

    return _modifier

# Minimum score to include a species in the forecast.
# This filters out species that technically survive but aren't really biting.
SPECIES_SCORE_THRESHOLD = 30

_MONTH_ABBREVS: dict[str, int] = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}

def _parse_closed_months(text: str) -> set:
    """Parse month ranges from regulation text like 'closed Jan-May' or 'Gulf closed Jan–May'.

    Returns a set of month numbers (1-12) that are closed.
    Handles year-wrap ranges like 'closed Nov-Feb'.
    """
    closed: set[int] = set()
    # Match "closed MMM-MMM", "closed MMM–MMM", "closed MonthName-MonthName"
    pattern = r"closed\s+([a-z]+)[–\-]([a-z]+)"
    for m in re.finditer(pattern, text.lower()):
        start_str = m.group(1)[:3]
        end_str = m.group(2)[:3]
        start = _MONTH_ABBREVS.get(start_str)
        end = _MONTH_ABBREVS.get(end_str)
        if start and end:
            if end >= start:
                closed.update(range(start, end + 1))
            else:
                # Wraps around the year end, e.g., Nov-Feb
                closed.update(range(start, 13))
                closed.update(range(1, end + 1))
    return closed

def _retention_prohibited(regulation: dict[str, str], month: int = 0) -> bool:
    """Return True when regulations prohibit retaining / keeping the fish.

    This is a **retention-only** check — it answers "must the angler release
    this fish?" and returns True for both catch-and-release-only regs AND
    closed-season regs, because in both cases the fish cannot be kept.

    ╔══════════════════════════════════════════════════════════╗
    ║  DO NOT use this function to decide whether to hide a   ║
    ║  species from the forecast.                             ║
    ║                                                         ║
    ║  Forecast visibility is controlled exclusively by:      ║
    ║    should_hide_from_forecast(classify_legality(...))    ║
    ║  in regulations.py, which correctly keeps C&R species   ║
    ║  visible (targeting is legal) while suppressing only    ║
    ║  fisheries where targeting itself is not permitted.     ║
    ╚══════════════════════════════════════════════════════════╝

    Appropriate uses of this function:
      - Displaying a "do not keep" warning on a species card
      - Validating a catch log entry before marking it as a keeper
      - Any UI element that needs to distinguish "harvestable" from "must release"

    Pass ``month`` (1-12) to also check month-specific seasonal closures
    embedded in the regulation text (e.g. "Closed Jan–May").
    """
    bag_limit = str(regulation.get("bag_limit") or "").strip().lower()
    season = str(regulation.get("season") or "").strip().lower()
    notes = str(regulation.get("notes") or "").strip().lower()

    combined = " ".join(part for part in (bag_limit, season, notes) if part)
    if not combined:
        return False

    if bag_limit in {"0", "0/day", "0 per day", "0 fish", "none"}:
        return True

    blocked_phrases = (
        "catch and release only",
        "catch-and-release only",
        "no harvest",
        "harvest prohibited",
        "retention prohibited",
        "possession prohibited",
        "must be released",
        "cannot be retained",
        "closed season",
        "season closed",
        "closed year-round",
    )
    if any(phrase in combined for phrase in blocked_phrases):
        return True

    # Check month-specific closures when a current month is supplied
    if month:
        if month in _parse_closed_months(combined):
            return True

    return False

def _build_profile_filter(
    fishing_types: Optional[list[str]],
    targets: Optional[list[str]],
) -> Callable[[str], bool]:
    """Pre-compute profile-derived sets once and return an O(1) per-species filter.

    Called once per forecast run by build_species_ranking so the set unions and
    boolean flags are not rebuilt for every one of the ~900 species.
    """
    _apply_ft = bool(fishing_types and "all" not in fishing_types)
    if _apply_ft:
        ft = fishing_types  # type: ignore[assignment]
        has_surf = "surf" in ft
        has_pier = "pier" in ft
        has_inshore = "inshore" in ft
        has_offshore = "offshore" in ft
        has_jetty = "jetty" in ft
        has_bridge = "bridge" in ft
        has_wade = "wade" in ft
        has_kayak = "kayak" in ft
        has_charter = "charter" in ft
        has_fly = "fly" in ft

        effective_pier = has_pier or has_jetty or has_bridge
        effective_inshore = has_inshore or has_wade or has_fly
        effective_offshore = has_offshore or has_charter or has_kayak

        only_charter = has_charter and not (
            has_surf or has_pier or has_inshore or has_offshore
            or has_jetty or has_bridge or has_wade or has_kayak or has_fly
        )
        only_fly = has_fly and not (
            has_surf or has_pier or has_inshore or has_offshore
            or has_jetty or has_bridge or has_wade or has_kayak or has_charter
        )

        _primary_union: set[str] = (
            _SURF_SPECIES | _PIER_SPECIES | _INSHORE_SPECIES | _JETTY_SPECIES
            | _BRIDGE_SPECIES | _WADE_SPECIES | _KAYAK_SPECIES
            | _CHARTER_SPECIES | _FLY_SPECIES
        )
        _accessible: set[str] = set()
        if has_surf:
            _accessible |= _SURF_SPECIES
        if effective_pier:
            _accessible |= _PIER_SPECIES
        if has_jetty or has_pier:
            _accessible |= _JETTY_SPECIES
        if has_bridge or has_pier or effective_inshore:
            _accessible |= _BRIDGE_SPECIES
        if effective_inshore:
            _accessible |= _INSHORE_SPECIES
        if has_wade or effective_inshore:
            _accessible |= _WADE_SPECIES
        if has_kayak:
            _accessible |= _KAYAK_SPECIES
        if has_charter:
            _accessible |= _CHARTER_SPECIES
        if has_fly or effective_inshore:
            _accessible |= _FLY_SPECIES
    else:
        effective_offshore = True
        only_charter = False
        only_fly = False
        _primary_union = set()
        _accessible = set()

    _apply_tgt = bool(targets and "anything" not in targets)
    if _apply_tgt:
        tgt_union: set[str] = set()
        if "bottom" in targets:  # type: ignore[operator]
            tgt_union |= _BOTTOM_SPECIES
        if "pelagic" in targets:  # type: ignore[operator]
            tgt_union |= _PELAGIC_SPECIES
        if "structure" in targets:  # type: ignore[operator]
            tgt_union |= _STRUCTURE_SPECIES
        if "gamefish" in targets:  # type: ignore[operator]
            tgt_union |= _GAMEFISH_SPECIES
        if "inshore_slam" in targets:  # type: ignore[operator]
            tgt_union |= _INSHORE_SLAM_SPECIES
    else:
        tgt_union = set()

    def _filter(sp_name: str) -> bool:
        if _apply_ft:
            if not effective_offshore and sp_name in _OFFSHORE_ONLY_SPECIES:
                return False
            if only_charter and sp_name not in _CHARTER_SPECIES:
                return False
            if only_fly and sp_name not in _FLY_SPECIES:
                return False
            if (
                sp_name in _primary_union
                and sp_name not in _accessible
                and sp_name not in _OFFSHORE_ONLY_SPECIES
            ):
                return False
        if _apply_tgt and sp_name not in tgt_union:
            return False
        return True

    return _filter


def build_species_ranking(
    month: int,
    water_temp: float,
    wind_dir: Optional[str] = None,
    wind_range: Optional[tuple[float, float]] = None,
    wave_range: Optional[tuple[float, float]] = None,
    hour: int = 12,
    coast: Optional[str] = None,
    state: str = "",
    fishing_types: Optional[list[str]] = None,
    targets: Optional[list[str]] = None,
    fish_region: str = "",
    closures_out: Optional[list[dict[str, Any]]] = None,
    wind_orientation: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Dynamically rank species based on conditions and user profile.

    Factors in water temperature, month, wind direction, wind speed,
    wave height, and time of day.  Only species scoring above
    SPECIES_SCORE_THRESHOLD are included.  Each species gets an
    activity label: Hot, Active, or Possible.

    If ``fishing_types`` or ``targets`` are provided (from user profile),
    species are filtered to match how the user actually fishes.

    If ``state`` is provided, regulation data (size/bag limits) is
    attached to each species entry.

    If ``fish_region`` is provided, species with a ``regions`` list are
    filtered to only appear if the fish_region matches.

    ``coast`` must be one of ``"east"``, ``"west"``, or ``"hawaii"``.
    Passing ``None`` (or omitting the argument) returns an empty list —
    the coast must be known to avoid leaking wrong-region species.

    ``wind_orientation`` overrides the coastline orientation used for the
    wind-direction modifier (e.g. ``"gulf"`` for south-facing Gulf shores,
    which the species ``coast`` folds into ``"east"`` for selection).  When
    omitted it is derived from ``coast``.
    """
    # Coastline orientation for wind scoring is independent of the species
    # coast: the Gulf selects east-coast species but faces south for wind.
    wind_coast = wind_orientation or ("west" if coast == "west" else "east")
    # Pre-compute per-run constants to avoid repeating the same work per species.
    _profile_filter = _build_profile_filter(fishing_types, targets)
    _cond_modifier = _build_conditions_modifier(
        wind_dir, wind_range, wave_range, hour, wind_coast
    )
    _season = _get_season(month)
    _is_cold = water_temp < 65
    scored = []
    for sp in _SPECIES_BY_COAST.get(coast, []) if coast else []:
        # Skip species not found in this geographic region
        if fish_region and "regions" in sp and fish_region not in sp["regions"]:
            continue
        # Skip species that don't match user's fishing profile
        if not _profile_filter(sp["name"]):
            continue
        s = _score_species(
            sp,
            month,
            water_temp,
            _cond_modifier=_cond_modifier,
        )
        if s >= SPECIES_SCORE_THRESHOLD:
            overrides = SEASONAL_EXPLANATIONS.get(sp["name"])
            if overrides and _season in overrides:
                explanation = overrides[_season]
            else:
                explanation = sp["explanation_cold"] if _is_cold else sp["explanation_warm"]
            scored.append((s, sp, explanation))

    scored.sort(key=_operator.itemgetter(0), reverse=True)

    # Max possible raw score: 50 (temp) + 30 (season) + 15 (conditions) = 95
    _MAX_RAW_SCORE = 95.0

    result: list[dict[str, Any]] = []
    for score, sp, explanation in scored:
        if score >= 65:
            activity = "Hot"
        elif score >= 50:
            activity = "Active"
        else:
            activity = "Possible"

        # ── Visibility gate ─────────────────────────────────────────────────
        # Only species with status "legal" (or "unknown" where no data exists)
        # appear in the forecast.  All other statuses are hidden to avoid
        # recommending unlawful catches:
        #   • "prohibited"        — year-round closure or federal protection
        #   • "out_of_season"     — current month falls inside a closed season
        #   • "catch_and_release" — retention/harvest prohibited; cannot keep
        #   • "restricted"        — conditional rules that may prohibit retention
        regulation = None
        regulation_status: Optional[str] = None
        if state:
            reg = lookup_regulation(sp["name"], state)
            if reg:
                regulation_status = classify_legality(reg, month)
                if should_hide_from_forecast(regulation_status):
                    # Capture genuine closures (can't target/keep right now) so
                    # the dashboard can warn about otherwise-relevant species
                    # that are off-limits this month. C&R/restricted are not
                    # closures (you can still target), so they're excluded.
                    if closures_out is not None and regulation_status in (
                        "out_of_season",
                        "prohibited",
                    ):
                        closures_out.append(
                            {
                                "name": sp["name"],
                                "status": regulation_status,
                                "season": reg.get("season", ""),
                                "official_source": reg.get("official_source", ""),
                                "score": score,
                            }
                        )
                    continue
                regulation = reg

        # Normalize raw score (max ~95) to a clean 0-100 display percentage
        display_score = min(100, round(score / _MAX_RAW_SCORE * 100))

        # Look up display categories: JSON field takes precedence if present,
        # then the curated _SPECIES_CATEGORIES dict, then default to ["other"].
        categories: list[str] = (
            sp.get("categories") or _SPECIES_CATEGORIES.get(sp["name"]) or ["other"]
        )

        entry: dict[str, Any] = {
            "rank": len(result) + 1,
            "name": sp["name"],
            "score": display_score,
            "activity": activity,
            "explanation": explanation,
            "bait": sp["bait"],
            "rig": sp["rig"],
            "hook_size": sp["hook_size"],
            "sinker": sp["sinker"],
            "lures": sp.get("lures", ""),
            "categories": categories,
        }

        if regulation:
            entry["regulation"] = regulation
            # Whether this species is in its open season *right now*, so the
            # card can show an accurate badge instead of a raw "Closed Jan-Apr"
            # string that's actually inactive this month.
            entry["season_status"] = _season_status(regulation, month)
        # Expose legality status so templates can show uncertainty / stale warnings
        # without needing to re-parse regulation text.  Only set when state was queried.
        if regulation_status is not None:
            entry["regulation_status"] = regulation_status

        result.append(entry)
        if len(result) >= 10:
            break

    return result

def build_bait_ranking(
    species_ranking: list[dict[str, Any]],
    month: int,
) -> list[dict[str, str]]:
    """Rank baits by relevance to the current top species and season.

    Baits whose target species rank highly are scored higher.  Baits that are
    out of season (``available_months``) receive a penalty so anglers see what
    they can actually get right now.  Season-specific notes override defaults.
    """
    season = _get_season(month)

    # Map species short names to their rank for quick lookup.
    species_ranks: dict[str, int] = {}
    for sp in species_ranking:
        short = sp["name"].split("(")[0].strip()
        species_ranks[short] = sp["rank"]

    def canonical_bait_name(name: str) -> str:
        """Return a canonical label for de-duplicating near-identical bait names."""
        cleaned = " ".join(name.lower().replace("-", " ").split())
        alias_map = {
            "cut squid strips": "squid strips",
        }
        return alias_map.get(cleaned, cleaned)

    scored_baits: list[tuple[float, dict[str, str]]] = []
    for bait_entry in BAIT_DB:
        bait_score = 0.0
        for target in bait_entry["targets"]:
            rank = species_ranks.get(target)
            if rank is not None:
                bait_score += max(0, 20 - rank)

        # Penalise out-of-season baits so in-season options float to the top
        available = bait_entry.get("available_months")
        if available and month not in available:
            bait_score *= 0.25

        # Pick season-specific notes when available
        notes = bait_entry["notes"]
        seasonal_notes = bait_entry.get("notes_seasonal", {})
        if season in seasonal_notes:
            notes = seasonal_notes[season]

        scored_baits.append((bait_score, {"bait": bait_entry["bait"], "notes": notes}))

    scored_baits.sort(key=_operator.itemgetter(0), reverse=True)

    deduped_rankings: list[dict[str, str]] = []
    seen_baits: set[str] = set()
    for _, bait in scored_baits:
        key = canonical_bait_name(bait["bait"])
        if key in seen_baits:
            continue
        seen_baits.add(key)
        deduped_rankings.append(bait)

    return deduped_rankings

# ---------------------------------------------------------------------------
# Lure recommendations
# ---------------------------------------------------------------------------

# Lure categories with descriptions, target species, and seasonal notes.
# ``available_months`` can optionally restrict when a lure category is useful.
# ``targets`` lists species short-names that commonly respond to this lure type.
LURE_DB: list[dict[str, Any]] = [
    {
        "lure": "Soft plastic paddle-tail swimbait",
        "sizes": '3-5"',
        "colors": "White, chartreuse, natural (match baitfish)",
        "retrieve": "Slow steady retrieve or bounce on bottom",
        "notes": "Versatile inshore lure; works for almost any predator species. Use on 1/4-3/8 oz jighead.",
        "image": "images/lures/swimbait-paddle.svg",
        "available_months": list(range(1, 13)),
        "targets": [
            "Red drum",
            "Speckled trout",
            "Flounder",
            "Gray trout",
            "Striped bass",
            "Snook",
            "Sand bass",
            "Kelp bass",
            "California halibut",
        ],
    },
    {
        "lure": "Gold/silver spoon",
        "sizes": "1/2-1 oz",
        "colors": "Gold, silver, copper",
        "retrieve": "Medium to fast steady retrieve; varies flash",
        "notes": "Classic surf and pier lure for mackerel, bluefish, and drum. Attach with split ring to reduce line twist.",
        "image": "images/lures/spoon-gold.svg",
        "available_months": list(range(1, 13)),
        "targets": [
            "Spanish mackerel",
            "Bluefish",
            "Red drum",
            "Jack crevalle",
            "Pacific bonito",
            "False albacore",
        ],
    },
    {
        "lure": "Metal jig",
        "sizes": "1/2-3 oz",
        "colors": "Silver, chrome, blue/white",
        "retrieve": "Fast jerk-and-fall or speed retrieve through the water column",
        "notes": "Deadly for pelagic species when fish are busting bait on the surface. Also vertical-jig from piers.",
        "image": "images/lures/metal-jig.svg",
        "available_months": list(range(1, 13)),
        "targets": [
            "False albacore",
            "Bluefish",
            "Spanish mackerel",
            "Atlantic bonito",
            "Blackfin tuna",
            "Striped bass",
            "Pacific bonito",
            "Yellowtail",
        ],
    },
    {
        "lure": "Topwater plug / walk-the-dog",
        "sizes": '3.5-5"',
        "colors": "White, bone, mullet pattern",
        "retrieve": "Walk-the-dog with slack-line twitches at dawn and dusk",
        "notes": "Most productive during low-light periods and calm conditions. Surface explosions make this a favorite lure.",
        "image": "images/lures/topwater-plug.svg",
        "notes_seasonal": {
            "winter": "Topwater bite slows in cold water; switch to subsurface lures in water below 60°F.",
        },
        "available_months": [3, 4, 5, 6, 7, 8, 9, 10, 11],
        "targets": [
            "Speckled trout",
            "Red drum",
            "Striped bass",
            "Snook",
            "Jack crevalle",
            "Bluefish",
            "Tarpon",
        ],
    },
    {
        "lure": "Bucktail jig",
        "sizes": "1/4-1 oz",
        "colors": "White, chartreuse, pink",
        "retrieve": "Bounce on bottom with rod tip lifts, or slow swim near structure",
        "notes": "Old-school lure that still outfishes everything for flounder and stripers. Add a soft plastic trailer for extra action.",
        "image": "images/lures/bucktail-jig.svg",
        "available_months": list(range(1, 13)),
        "targets": [
            "Flounder",
            "Striped bass",
            "Weakfish",
            "Gray trout",
            "Fluke",
            "White seabass",
        ],
    },
    {
        "lure": "Pompano jig",
        "sizes": "1/4-3/8 oz",
        "colors": "Yellow, orange, chartreuse, pink",
        "retrieve": "Bounce and hop along the sandy bottom through the trough",
        "notes": "Purpose-built jig for surf-zone pompano and whiting. Bright colors are key.",
        "image": "images/lures/pompano-jig.svg",
        "available_months": [3, 4, 5, 6, 7, 8, 9, 10, 11],
        "targets": ["Pompano", "Whiting", "Permit"],
    },
    {
        "lure": "Gulp! soft bait (shrimp / sand crab)",
        "sizes": '2-3"',
        "colors": "New penny, pink shine, natural",
        "retrieve": "Slow drag or short hops on bottom; extremely versatile",
        "notes": "Scented soft plastic that outperforms natural bait in many situations. Popular for surf perch and flatfish.",
        "image": "images/lures/gulp-soft.svg",
        "available_months": list(range(1, 13)),
        "targets": [
            "Barred surfperch",
            "Corbina",
            "California halibut",
            "Pompano",
            "Flounder",
            "Whiting",
        ],
    },
    {
        "lure": 'Swimbaits (large, 5-9")',
        "sizes": '5-9"',
        "colors": "Mullet pattern, sardine pattern, white",
        "retrieve": "Slow to medium; allow natural swimming action near structure",
        "notes": "Best for big predators — lingcod, yellowtail, cobia, tarpon. Match local baitfish size.",
        "image": "images/lures/swimbait-large.svg",
        "notes_seasonal": {
            "fall": "Fall baitfish migrations bring large predators close to shore; large swimbaits shine during this period.",
        },
        "available_months": list(range(1, 13)),
        "targets": [
            "Lingcod",
            "Yellowtail",
            "Cobia",
            "Tarpon",
            "Greater amberjack",
            "White seabass",
            "Snook",
        ],
    },
    {
        "lure": "Iron jig (flylined)",
        "sizes": "2-4 oz",
        "colors": "Sardine/blue, chrome, anchovy pattern",
        "retrieve": "Cast and let flutter on a semi-slack line; or fast retrieve with rod pumps",
        "notes": "West Coast staple for yellowtail and white seabass at the kelp. Keep the bail open and let it flutter on the drop.",
        "image": "images/lures/iron-jig.svg",
        "available_months": [3, 4, 5, 6, 7, 8, 9, 10, 11],
        "targets": ["Yellowtail", "White seabass", "Pacific bonito", "Kelp bass"],
    },
    {
        "lure": "Shad dart",
        "sizes": "1/16-1/4 oz",
        "colors": "Pink, chartreuse, white, yellow",
        "retrieve": "Drift downstream in current; short hops near river mouths",
        "notes": "Essential spring lure for shad runs. Tandem rigs with two darts increase hook-up rates.",
        "image": "images/lures/shad-dart.svg",
        "notes_seasonal": {
            "spring": "Shad darts are at their best during the spring river run; bright colors (pink, chartreuse) in fast water.",
            "fall": "Not productive in fall; shad are offshore. Save these for the spring run.",
        },
        "available_months": [2, 3, 4, 5],
        "targets": ["Hickory shad", "American shad"],
    },
    {
        "lure": "Popper / surface plug",
        "sizes": '3-4"',
        "colors": "White, chartreuse, fire tiger",
        "retrieve": "Aggressive popping action; pause between pops",
        "notes": "Excellent for pier and jetty fishing when birds are working. Creates a commotion that calls fish from a distance.",
        "image": "images/lures/popper.svg",
        "notes_seasonal": {
            "winter": "Poppers slow down in cold water; fish them when water temp is above 60°F.",
        },
        "available_months": [4, 5, 6, 7, 8, 9, 10, 11],
        "targets": [
            "Bluefish",
            "Spanish mackerel",
            "Jack crevalle",
            "Giant trevally",
            "Bluefin trevally",
            "Snook",
        ],
    },
    {
        "lure": "Offshore trolling lure",
        "sizes": '6-12"',
        "colors": "Blue/white, pink/white, black/red",
        "retrieve": "Trolled at 6-18 knots (speed depends on species)",
        "notes": "Skirted lures run behind the boat in spread formation. Ilanders for wahoo and marlin; smaller skirts for mahi and tuna.",
        "image": "images/lures/trolling-lure.svg",
        "available_months": [4, 5, 6, 7, 8, 9, 10, 11],
        "targets": [
            "Mahi-mahi",
            "Wahoo",
            "Yellowfin tuna",
            "Blackfin tuna",
            "Sailfish",
            "Blue marlin",
            "White marlin",
        ],
    },
    {
        "lure": "Clarkspoon / drone spoon",
        "sizes": "#1-#3",
        "colors": "Silver, gold, chartreuse",
        "retrieve": "Trolled on planer or inline spinner at 4-8 knots",
        "notes": "Classic king mackerel and Spanish mackerel lure. Add a stinger hook for short-striking fish.",
        "image": "images/lures/clarkspoon.svg",
        "available_months": [3, 4, 5, 6, 7, 8, 9, 10, 11],
        "targets": ["King mackerel", "Spanish mackerel", "False albacore"],
    },
]

def build_lure_recommendations(
    species_ranking: list[dict[str, Any]],
    month: int,
) -> list[dict[str, Any]]:
    """Rank lure types by relevance to current top species and season.

    Follows the same scoring approach as build_bait_ranking: lure types whose
    target species rank highly float to the top, and out-of-season lures are
    penalised.  Returns a list of lure entries with full details plus
    ``active_species`` — the currently-ranked species that respond to each lure.
    The full list (all 13 entries, sorted by relevance) is returned so the
    front-end can show both a "top picks today" view and a complete "lure bag"
    selector showing which lures have active fish right now.
    """
    season = _get_season(month)

    # Map species short names (before parenthetical) to their full display name
    # and rank for scoring / active-fish display.
    species_ranks: dict[str, int] = {}
    species_display: dict[str, str] = {}
    for sp in species_ranking:
        short = sp["name"].split("(")[0].strip()
        species_ranks[short] = sp.get("rank", 0)
        species_display[short] = sp["name"]

    scored_lures: list[tuple] = []
    for lure_entry in LURE_DB:
        score = 0.0
        active: list[str] = []
        for target in lure_entry["targets"]:
            rank = species_ranks.get(target)
            if rank is not None:
                score += max(0, 20 - rank)
                active.append(species_display.get(target, target))

        # Penalise out-of-season lures
        available = lure_entry.get("available_months")
        if available and month not in available:
            score *= 0.25

        # Season-specific notes override default notes
        notes = lure_entry["notes"]
        seasonal_notes = lure_entry.get("notes_seasonal", {})
        if season in seasonal_notes:
            notes = seasonal_notes[season]

        scored_lures.append(
            (
                score,
                {
                    "lure": lure_entry["lure"],
                    "sizes": lure_entry.get("sizes", ""),
                    "colors": lure_entry.get("colors", ""),
                    "retrieve": lure_entry.get("retrieve", ""),
                    "notes": notes,
                    "image": lure_entry.get("image", ""),
                    "active_species": active,
                    "score": round(score, 1),
                },
            )
        )

    scored_lures.sort(key=_operator.itemgetter(0), reverse=True)
    return [entry for _, entry in scored_lures]

# ---------------------------------------------------------------------------
# Natural bait / forage species availability
# ---------------------------------------------------------------------------

NATURAL_BAIT_DB: list[dict[str, Any]] = [
    # Atlantic / Gulf
    {
        "name": "Menhaden (bunker)",
        "months": [3, 4, 5, 6, 7, 8, 9, 10, 11],
        "coast": "east",
        "note": "Schools visible at surface — look for diving birds",
    },
    {
        "name": "Mullet",
        "months": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12],
        "coast": "east",
        "note": "Year-round; large fall runs Sept-Nov along beaches",
    },
    {
        "name": "Sand fleas (mole crabs)",
        "months": [4, 5, 6, 7, 8, 9, 10],
        "coast": "east",
        "note": "Dig in wet sand at surf's edge during wave retreat",
    },
    {
        "name": "Shrimp",
        "months": [4, 5, 6, 7, 8, 9, 10, 11],
        "coast": "east",
        "note": "Peak summer/fall; run on outgoing tides at night",
    },
    {
        "name": "Fiddler crabs",
        "months": [4, 5, 6, 7, 8, 9, 10],
        "coast": "east",
        "note": "Found in mud flats at low tide — top sheepshead bait",
    },
    {
        "name": "Bloodworms",
        "months": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12],
        "coast": "east",
        "note": "Available year-round at bait shops; pricey but effective",
    },
    {
        "name": "Cut bait (spot/croaker)",
        "months": [5, 6, 7, 8, 9, 10],
        "coast": "east",
        "note": "Catch small spot/croaker on Sabiki rigs for fresh cut bait",
    },
    {
        "name": "Finger mullet",
        "months": [6, 7, 8, 9, 10, 11],
        "coast": "east",
        "note": "Cast net along shore; top live bait for predator species",
    },
    {
        "name": "Silversides",
        "months": [3, 4, 5, 6, 7, 8, 9, 10, 11],
        "coast": "east",
        "note": "Tiny baitfish in surf zone — match with small spoons/jigs",
    },
    {
        "name": "Blue crab",
        "months": [4, 5, 6, 7, 8, 9, 10, 11],
        "coast": "east",
        "note": "Cut in half for drum/sheepshead; chicken necks to trap",
    },
    # Pacific
    {
        "name": "Sand crabs",
        "months": [3, 4, 5, 6, 7, 8, 9, 10, 11],
        "coast": "west",
        "note": "Dig at wave line for prime surfperch and corbina bait",
    },
    {
        "name": "Mussels",
        "months": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12],
        "coast": "west",
        "note": "Pry from rocks at low tide — excellent all-purpose bait",
    },
    {
        "name": "Anchovies",
        "months": [3, 4, 5, 6, 7, 8, 9, 10, 11],
        "coast": "west",
        "note": "Buy live or use Sabiki rig; top live bait for gamefish",
    },
    {
        "name": "Sardines",
        "months": [4, 5, 6, 7, 8, 9, 10],
        "coast": "west",
        "note": "Available live at bait barges; great for halibut and bass",
    },
    {
        "name": "Squid",
        "months": [1, 2, 3, 4, 5, 10, 11, 12],
        "coast": "west",
        "note": "Market squid runs in winter; cut strips or use whole",
    },
    {
        "name": "Ghost shrimp",
        "months": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12],
        "coast": "west",
        "note": "Pump from mudflats at low tide; perch and surfperch love them",
    },
    {
        "name": "Mackerel (bait)",
        "months": [4, 5, 6, 7, 8, 9, 10],
        "coast": "west",
        "note": "Catch on Sabiki rigs at piers; cut for halibut and bass",
    },
    {
        "name": "Grunion",
        "months": [3, 4, 5, 6, 7, 8],
        "coast": "west",
        "note": "Beach spawning runs on full/new moon nights — check regulations",
    },
    # Hawaii
    {
        "name": "Squid (ika)",
        "months": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12],
        "coast": "hawaii",
        "note": "All-purpose frozen bait for papio, goatfish, and reef species",
    },
    {
        "name": "ʻOpae (shrimp)",
        "months": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12],
        "coast": "hawaii",
        "note": "Small shrimp on light tackle — deadly for papio and oio (bonefish)",
    },
    {
        "name": "ʻOama (juvenile goatfish)",
        "months": [6, 7, 8, 9, 10],
        "coast": "hawaii",
        "note": "Net in the summer shallows — premier live bait for papio (jacks)",
    },
    {
        "name": "Halalu (juvenile akule)",
        "months": [7, 8, 9, 10, 11],
        "coast": "hawaii",
        "note": "Summer/fall scad run; live-line or cut for ulua and papio",
    },
    {
        "name": "Tako (octopus)",
        "months": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12],
        "coast": "hawaii",
        "note": "Tough cut bait that stays on the hook for ulua and large reef fish",
    },
    {
        "name": "Aku belly (cut bait)",
        "months": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12],
        "coast": "hawaii",
        "note": "Oily strip bait prized for ulua slide-baiting off the rocks",
    },
    {
        "name": "Limu (seaweed)",
        "months": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12],
        "coast": "hawaii",
        "note": "Targets herbivores like manini and nenue (rudderfish)",
    },
]

def build_natural_bait_chart(month: int, coast: str = "east") -> list[dict[str, str]]:
    """Return the list of natural bait species available this month.

    Filters by coast and month, returns a list of dicts with name, note,
    and availability status.
    """
    available = []
    for bait in NATURAL_BAIT_DB:
        if bait["coast"] != coast and bait["coast"] != "both":
            continue
        if month in bait["months"]:
            status = "available"
        else:
            status = "off-season"
        available.append(
            {
                "name": bait["name"],
                "note": bait["note"],
                "status": status,
            }
        )
    # Sort: available first, then off-season
    available.sort(key=lambda x: (0 if x["status"] == "available" else 1, x["name"]))
    return available

# ---------------------------------------------------------------------------
# Spawning data — months, temperature windows, and behavior notes
# ---------------------------------------------------------------------------
# Each entry covers when and where a species spawns so the forecast can
# surface a "What's Spawning" section.  Temp ranges are °F.
# ---------------------------------------------------------------------------

SPAWNING_DATA: list[Dict] = [
    # ── East Coast & Gulf (coast="east") ────────────────────────────────────
    # Gulf locations use coast="east" in the species database, so Gulf species
    # are included here with temperature constraints naturally limiting them to
    # the correct region (warm-water Gulf species won't appear for cold-water
    # New England locations where water never reaches their spawn temp window).
    {
        "name": "American shad",
        "coast": "east",
        "spawn_months": [2, 3, 4, 5],
        "spawn_temp_low": 50,
        "spawn_temp_high": 70,
        "spawn_note": "Shad make their famous upriver spawning migration; the run timing is closely tied to water temperature crossing 50°F — one of the year's most reliable seasonal triggers.",
    },
    {
        "name": "Atlantic bonito",
        "coast": "east",
        "spawn_months": [5, 6, 7, 8],
        "spawn_temp_low": 64,
        "spawn_temp_high": 76,
        "spawn_note": "Bonito spawn in open water offshore; active schools blitzing baitfish near the surface are a reliable sign of spawning season presence.",
    },
    {
        "name": "Atlantic croaker",
        "coast": "east",
        "spawn_months": [8, 9, 10, 11],
        "spawn_temp_low": 65,
        "spawn_temp_high": 78,
        "spawn_note": "Croaker move offshore in fall to spawn; the 'croaker run' concentrates fish along piers and inlets before they head to deeper water.",
    },
    {
        "name": "Black drum",
        "coast": "east",
        "spawn_months": [3, 4, 5],
        "spawn_temp_low": 60,
        "spawn_temp_high": 74,
        "spawn_note": "Black drum gather in large pods near inlets and inshore structure to spawn; their low drumming is audible underwater and signals active fish.",
    },
    {
        "name": "Black sea bass",
        "coast": "east",
        "spawn_months": [5, 6, 7],
        "spawn_temp_low": 58,
        "spawn_temp_high": 72,
        "spawn_note": "Black sea bass gather on nearshore reefs and wrecks to spawn; males darken dramatically and defend territories, making them more aggressive biters.",
    },
    {
        "name": "Bluefish",
        "coast": "east",
        "spawn_months": [5, 6, 7],
        "spawn_temp_low": 65,
        "spawn_temp_high": 76,
        "spawn_note": "Bluefish spawn in nearshore and offshore waters; large schools blitzing baitfish near the surface are common sights during spawning season.",
    },
    {
        "name": "Cobia",
        "coast": "east",
        "spawn_months": [4, 5, 6, 7],
        "spawn_temp_low": 68,
        "spawn_temp_high": 82,
        "spawn_note": "Cobia follow rays and large sharks inshore to spawn; sight-casting to individual fish near channel markers and nearshore structure is highly effective.",
    },
    {
        "name": "Flounder (summer flounder)",
        "coast": "east",
        "spawn_months": [10, 11, 12],
        "spawn_temp_low": 55,
        "spawn_temp_high": 70,
        "spawn_note": "Summer flounder migrate offshore to the continental shelf edge to spawn; the fall run through inlets consistently produces the season's largest fish.",
    },
    {
        "name": "Gag grouper",
        "coast": "east",
        "spawn_months": [1, 2, 3, 4, 5],
        "spawn_temp_low": 62,
        "spawn_temp_high": 74,
        "spawn_note": "Gag grouper aggregate on deep offshore reefs and ledges to spawn; nearshore wrecks hold fish staging for the offshore run — live bait on heavy tackle is key.",
    },
    {
        "name": "Greater amberjack",
        "coast": "east",
        "spawn_months": [2, 3, 4, 5, 6],
        "spawn_temp_low": 68,
        "spawn_temp_high": 80,
        "spawn_note": "Amberjack spawn over offshore structure; their aggressive feeding during spawn staging makes them susceptible to live bait and heavy jigs.",
    },
    {
        "name": "Jack crevalle",
        "coast": "east",
        "spawn_months": [5, 6, 7, 8],
        "spawn_temp_low": 74,
        "spawn_temp_high": 84,
        "spawn_note": "Jack crevalle spawn in nearshore and estuarine waters; aggressive schools herding baitfish along the beach and in passes are a summer spectacle.",
    },
    {
        "name": "King mackerel (kingfish)",
        "coast": "east",
        "spawn_months": [5, 6, 7, 8],
        "spawn_temp_low": 70,
        "spawn_temp_high": 84,
        "spawn_note": "King mackerel spawn in offshore waters; fish push large baitfish schools to the surface and are highly aggressive near any bait concentration.",
    },
    {
        "name": "Mahi-mahi (dolphinfish)",
        "coast": "east",
        "spawn_months": [4, 5, 6, 7, 8, 9],
        "spawn_temp_low": 72,
        "spawn_temp_high": 86,
        "spawn_note": "Mahi spawn repeatedly in warm offshore water; weedlines and floating debris concentrate both bait and spawning fish — look for the sargassum.",
    },
    {
        "name": "Mangrove snapper (gray snapper)",
        "coast": "east",
        "spawn_months": [6, 7, 8, 9, 10],
        "spawn_temp_low": 72,
        "spawn_temp_high": 84,
        "spawn_note": "Gray snapper move to nearshore reefs and passes to spawn; this is the easiest time to target larger fish that normally hug structure tightly.",
    },
    {
        "name": "Permit",
        "coast": "east",
        "spawn_months": [5, 6, 7, 8],
        "spawn_temp_low": 74,
        "spawn_temp_high": 84,
        "spawn_note": "Permit spawn offshore then return to flats and passes; large aggregations near inlet edges before the spawn are a rare opportunity.",
    },
    {
        "name": "Pompano",
        "coast": "east",
        "spawn_months": [3, 4, 5],
        "spawn_temp_low": 65,
        "spawn_temp_high": 76,
        "spawn_note": "Pompano spawn in nearshore surf waters; fish are especially active along beach troughs and passes during this run — sand fleas and Fishbites are top baits.",
    },
    {
        "name": "Red drum (puppy drum)",
        "coast": "east",
        "spawn_months": [8, 9, 10, 11],
        "spawn_temp_low": 70,
        "spawn_temp_high": 82,
        "spawn_note": "Red drum spawn near inlets, passes, and the surf zone; large 'bull reds' congregate in accessible numbers — releasing large breeders protects the fishery.",
    },
    {
        "name": "Red grouper",
        "coast": "east",
        "spawn_months": [2, 3, 4, 5, 6],
        "spawn_temp_low": 64,
        "spawn_temp_high": 76,
        "spawn_note": "Red grouper form spawning aggregations on offshore rocky bottom and ledges; males hold territories aggressively and will attack almost any presented bait.",
    },
    {
        "name": "Red snapper",
        "coast": "east",
        "spawn_months": [5, 6, 7, 8, 9],
        "spawn_temp_low": 70,
        "spawn_temp_high": 84,
        "spawn_note": "Red snapper spawn repeatedly near reefs and hard structure; peak activity aligns with full and new moons — one of the most moon-phase-sensitive species.",
    },
    {
        "name": "Scup (porgy)",
        "coast": "east",
        "spawn_months": [5, 6, 7],
        "spawn_temp_low": 60,
        "spawn_temp_high": 72,
        "spawn_note": "Scup spawn in nearshore and inshore waters; large aggregations make late spring one of the most productive times for porgies off piers and jetties.",
    },
    {
        "name": "Sheepshead",
        "coast": "east",
        "spawn_months": [2, 3, 4],
        "spawn_temp_low": 55,
        "spawn_temp_high": 68,
        "spawn_note": "Sheepshead move to nearshore reefs, jetties, and passes to spawn; large females are especially active — fiddler crabs fished straight down on structure are deadly.",
    },
    {
        "name": "Snook",
        "coast": "east",
        "spawn_months": [5, 6, 7, 8],
        "spawn_temp_low": 74,
        "spawn_temp_high": 84,
        "spawn_note": "Snook aggregate at passes and inlets to spawn on outgoing tides; topwater lures and large live baits work best during low-light spawning pushes.",
    },
    {
        "name": "Southern flounder",
        "coast": "east",
        "spawn_months": [10, 11, 12],
        "spawn_temp_low": 55,
        "spawn_temp_high": 68,
        "spawn_note": "Southern flounder migrate from estuaries to nearshore water to spawn in fall; creek mouths and inlets funnel fish and concentrate large catches.",
    },
    {
        "name": "Spanish mackerel",
        "coast": "east",
        "spawn_months": [6, 7, 8],
        "spawn_temp_low": 72,
        "spawn_temp_high": 82,
        "spawn_note": "Spanish mackerel spawn in nearshore and offshore waters; large surface-busting schools are common — a white or silver spoon on a long cast is hard to beat.",
    },
    {
        "name": "Speckled trout (spotted seatrout)",
        "coast": "east",
        "spawn_months": [4, 5, 6, 7, 8],
        "spawn_temp_low": 65,
        "spawn_temp_high": 80,
        "spawn_note": "Seatrout spawn repeatedly over seagrass beds and shallow bays; males produce a distinctive drumming call at dawn and dusk to attract females — active fish are vocal fish.",
    },
    {
        "name": "Spot",
        "coast": "east",
        "spawn_months": [9, 10, 11],
        "spawn_temp_low": 62,
        "spawn_temp_high": 74,
        "spawn_note": "Spot migrate offshore to spawn in fall; the 'spot run' is one of the most anticipated autumn events for surf and pier anglers across the Southeast.",
    },
    {
        "name": "Striped bass (rockfish)",
        "coast": "east",
        "spawn_months": [3, 4, 5],
        "spawn_temp_low": 50,
        "spawn_temp_high": 65,
        "spawn_note": "Striped bass migrate up coastal rivers and estuaries to spawn; fish stage at river mouths and inlet channels — early morning tidal flows concentrate pre-spawn fish.",
    },
    {
        "name": "Tarpon",
        "coast": "east",
        "spawn_months": [5, 6, 7, 8],
        "spawn_temp_low": 74,
        "spawn_temp_high": 86,
        "spawn_note": "Tarpon spawn offshore in summer and stage in nearshore passes; large 'rolling' fish visible at dawn are staging for offshore spawning runs — permit-style presentations work.",
    },
    {
        "name": "Tautog (blackfish)",
        "coast": "east",
        "spawn_months": [4, 5, 6],
        "spawn_temp_low": 52,
        "spawn_temp_high": 65,
        "spawn_note": "Tautog spawn on rocky reefs and around jetty structure; spawning fish are less wary than usual and respond well to fresh green crab on a knocker rig.",
    },
    {
        "name": "Tripletail",
        "coast": "east",
        "spawn_months": [5, 6, 7, 8, 9],
        "spawn_temp_low": 72,
        "spawn_temp_high": 84,
        "spawn_note": "Tripletail hang near floating structure and channel buoys during spawn staging; sight-fishing to individual fish with a live shrimp is extremely effective.",
    },
    {
        "name": "Weakfish",
        "coast": "east",
        "spawn_months": [4, 5, 6],
        "spawn_temp_low": 58,
        "spawn_temp_high": 72,
        "spawn_note": "Weakfish move into estuaries and bays to spawn; they are most active at night around piers and jetty edges — soft plastics and live shrimp under a light both work.",
    },
    {
        "name": "Whiting (sea mullet, kingfish)",
        "coast": "east",
        "spawn_months": [5, 6, 7, 8],
        "spawn_temp_low": 65,
        "spawn_temp_high": 76,
        "spawn_note": "Whiting spawn in the nearshore surf over sandy bottom; schooling fish stage along the beach face and in cuts — light tackle with small hooks and fresh shrimp is ideal.",
    },
    {
        "name": "Yellowtail snapper",
        "coast": "east",
        "spawn_months": [5, 6, 7, 8, 9],
        "spawn_temp_low": 74,
        "spawn_temp_high": 84,
        "spawn_note": "Yellowtail snapper spawn over offshore reefs; spawning fish feed aggressively near structure and respond to free-lined bait drifted back in the current.",
    },
    # ── West Coast ───────────────────────────────────────────────────────────
    {
        "name": "Barred surfperch",
        "coast": "west",
        "spawn_months": [1, 2, 3, 4],
        "spawn_temp_low": 50,
        "spawn_temp_high": 62,
        "spawn_note": "Surfperch are livebearers that release pups in late winter and early spring; newly delivered females feed aggressively in the heavy shorebreak — the best surf fishing of the year.",
    },
    {
        "name": "Cabezon",
        "coast": "west",
        "spawn_months": [1, 2, 3],
        "spawn_temp_low": 50,
        "spawn_temp_high": 58,
        "spawn_note": "Cabezon spawn on rocky reefs in winter; males guard adhesive egg masses fiercely and attack lures or baits that intrude on the nest site.",
    },
    {
        "name": "California halibut",
        "coast": "west",
        "spawn_months": [3, 4, 5, 6],
        "spawn_temp_low": 55,
        "spawn_temp_high": 65,
        "spawn_note": "California halibut spawn over nearshore sandy bottom; post-spawn fish flood into bays and estuaries in large numbers and bite aggressively on live anchovies.",
    },
    {
        "name": "Corbina",
        "coast": "west",
        "spawn_months": [6, 7, 8],
        "spawn_temp_low": 65,
        "spawn_temp_high": 72,
        "spawn_note": "Corbina spawn in the summer surf zone over sandy bottom; sight-fishing to fish tailing in the shallows with a sand crab on a light leader is a California tradition.",
    },
    {
        "name": "Jack mackerel (Spanish jack)",
        "coast": "west",
        "spawn_months": [3, 4, 5, 6],
        "spawn_temp_low": 56,
        "spawn_temp_high": 68,
        "spawn_note": "Jack mackerel spawn in open water off the coast; large nearshore schools in spring are a reliable sign of spawning aggregations — they hit small chrome jigs readily.",
    },
    {
        "name": "Kelp bass (calico bass)",
        "coast": "west",
        "spawn_months": [4, 5, 6, 7, 8],
        "spawn_temp_low": 60,
        "spawn_temp_high": 72,
        "spawn_note": "Kelp bass spawn repeatedly over kelp beds in warm months; males defend nest sites aggressively and bite surface plugs and swimbaits more readily than at any other time.",
    },
    {
        "name": "Leopard shark",
        "coast": "west",
        "spawn_months": [6, 7, 8, 9],
        "spawn_temp_low": 62,
        "spawn_temp_high": 70,
        "spawn_note": "Leopard sharks pup in warm, shallow bays in summer; large aggregations of pregnant females in very shallow water are an iconic California sight — strictly catch-and-release.",
    },
    {
        "name": "Lingcod",
        "coast": "west",
        "spawn_months": [1, 2, 3],
        "spawn_temp_low": 46,
        "spawn_temp_high": 54,
        "spawn_note": "Lingcod spawn on rocky reefs in winter; males guard large white egg masses and are highly aggressive — fishing near the nest is extremely effective but regulated.",
    },
    {
        "name": "Pacific bonito",
        "coast": "west",
        "spawn_months": [5, 6, 7, 8],
        "spawn_temp_low": 62,
        "spawn_temp_high": 72,
        "spawn_note": "Pacific bonito spawn in open water off Southern California; surface-busting schools chasing anchovies are the hallmark of spawning season — fast-moving chrome jigs are go-to.",
    },
    {
        "name": "Sand bass (barred sand bass)",
        "coast": "west",
        "spawn_months": [6, 7, 8],
        "spawn_temp_low": 64,
        "spawn_temp_high": 72,
        "spawn_note": "Barred sand bass aggregate in massive spawning schools over sandy bottom in summer; this is when the largest bass of the year are caught on swimbaits and drop-shots.",
    },
    {
        "name": "Spotfin croaker",
        "coast": "west",
        "spawn_months": [6, 7, 8],
        "spawn_temp_low": 65,
        "spawn_temp_high": 72,
        "spawn_note": "Spotfin croaker spawn in the nearshore surf zone in summer; males produce a loud drumming sound at night — surf anglers targeting them after dark have an advantage.",
    },
    {
        "name": "Striped bass (California)",
        "coast": "west",
        "spawn_months": [3, 4, 5, 6],
        "spawn_temp_low": 57,
        "spawn_temp_high": 68,
        "spawn_note": "California stripers migrate up the Sacramento-San Joaquin Delta to spawn; tidal flows near spawning shoals concentrate fish — this is the season's most celebrated striper run.",
    },
    {
        "name": "White seabass",
        "coast": "west",
        "spawn_months": [3, 4, 5, 6],
        "spawn_temp_low": 58,
        "spawn_temp_high": 68,
        "spawn_note": "White seabass move into kelp beds to spawn during squid spawning aggregations; following squid boats at night is one of the most productive WSB tactics of the year.",
    },
    {
        "name": "Yellowfin croaker",
        "coast": "west",
        "spawn_months": [6, 7, 8],
        "spawn_temp_low": 65,
        "spawn_temp_high": 74,
        "spawn_note": "Yellowfin croaker spawn in the surf zone in summer alongside spotfin croaker; beach fishing with sand crabs and fresh clam siphon at night is highly productive.",
    },
    {
        "name": "Yellowtail (California yellowtail)",
        "coast": "west",
        "spawn_months": [5, 6, 7, 8],
        "spawn_temp_low": 62,
        "spawn_temp_high": 72,
        "spawn_note": "Yellowtail spawn in offshore waters; warm-water eddies push fish close to the kelp line and island structure — live sardines on a slow-troll or flylined are the top producers.",
    },
    # ── Hawaii ───────────────────────────────────────────────────────────────
    {
        "name": "Giant trevally (ulua)",
        "coast": "hawaii",
        "spawn_months": [5, 6, 7, 8, 9],
        "spawn_temp_low": 76,
        "spawn_temp_high": 84,
        "spawn_note": "Ulua spawn in open water near current rips and offshore pinnacles; spawning fish are highly aggressive and prowl reef edges at dawn and dusk.",
    },
    {
        "name": "Bluefin trevally (omilu)",
        "coast": "hawaii",
        "spawn_months": [4, 5, 6, 7, 8],
        "spawn_temp_low": 74,
        "spawn_temp_high": 82,
        "spawn_note": "Omilu spawn in nearshore reef areas; active schools herding baitfish near the surface are common during spawning season — small poppers and jigs are very effective.",
    },
    {
        "name": "Moi (Pacific threadfin)",
        "coast": "hawaii",
        "spawn_months": [6, 7, 8, 9],
        "spawn_temp_low": 76,
        "spawn_temp_high": 82,
        "spawn_note": "Moi spawn nearshore in summer; schooling fish in the surf along sandy beaches are at their most catchable — light tackle with small hooks and cut shrimp are traditional.",
    },
    {
        "name": "Bonefish (oio)",
        "coast": "hawaii",
        "spawn_months": [4, 5, 6, 7, 8],
        "spawn_temp_low": 74,
        "spawn_temp_high": 84,
        "spawn_note": "Bonefish aggregate in large schools on shallow Hawaiian flats to spawn; spawning pods of 50+ fish are visible from shore and respond to small crab imitations.",
    },
    # --- East Coast additions ---
    {
        "name": "False albacore (little tunny)",
        "coast": "east",
        "spawn_months": [7, 8, 9, 10],
        "spawn_temp_low": 68,
        "spawn_temp_high": 78,
        "spawn_note": "False albacore push inshore during their summer-fall spawn, crashing baitfish at the surface within casting range of beaches and jetties — fast-retrieved epoxy jigs and small metal slabs draw explosive strikes.",
    },
    {
        "name": "Striped mullet",
        "coast": "east",
        "spawn_months": [10, 11, 12, 1],
        "spawn_temp_low": 62,
        "spawn_temp_high": 74,
        "spawn_note": "The fall mullet run is one of the most reliable coastal events on the East Coast — huge schools stage near inlets before migrating offshore to spawn, drawing bluefish, stripers, and tarpon into feeding frenzies along the beach.",
    },
    {
        "name": "Ladyfish",
        "coast": "east",
        "spawn_months": [9, 10, 11, 12, 1],
        "spawn_temp_low": 65,
        "spawn_temp_high": 78,
        "spawn_note": "Ladyfish move to offshore spawning grounds in fall, but pre-spawn fish concentrate in inlets and passes where light-tackle anglers find them aggressive on small spoons and soft plastics.",
    },
    {
        "name": "Alewife",
        "coast": "east",
        "spawn_months": [3, 4, 5],
        "spawn_temp_low": 48,
        "spawn_temp_high": 64,
        "spawn_note": "Alewife spawning runs up coastal rivers in spring create dense bait concentrations at river mouths; striped bass and bluefish stack up at these chokepoints to intercept the migrants.",
    },
    {
        "name": "Blueback herring",
        "coast": "east",
        "spawn_months": [3, 4, 5],
        "spawn_temp_low": 50,
        "spawn_temp_high": 68,
        "spawn_note": "Blueback herring run slightly later than alewives and push further upriver; the overlapping runs create weeks of sustained bait-school activity at river mouths that draws stripers, bass, and shad.",
    },
    {
        "name": "Hickory shad",
        "coast": "east",
        "spawn_months": [2, 3, 4],
        "spawn_temp_low": 52,
        "spawn_temp_high": 68,
        "spawn_note": "Hickory shad are among the earliest spring river runners and eagerly strike small darts and shad rigs near current seams; their arrival signals that American shad and stripers are not far behind.",
    },
    {
        "name": "Spadefish (Atlantic)",
        "coast": "east",
        "spawn_months": [5, 6, 7, 8, 9],
        "spawn_temp_low": 72,
        "spawn_temp_high": 82,
        "spawn_note": "Atlantic spadefish gather in large schools around nearshore buoys, wrecks, and bridge pilings to spawn; chumming with small moon jellies or clam bits and free-lining tiny circle hooks into the school is the classic approach.",
    },
    {
        "name": "Hogfish",
        "coast": "east",
        "spawn_months": [3, 4, 5, 6, 7, 8, 9],
        "spawn_temp_low": 68,
        "spawn_temp_high": 78,
        "spawn_note": "Hogfish form harems on nearshore reefs through the warm months with dominant males actively defending territory, making them visible and catchable on the bottom with small live shrimp or sand fleas.",
    },
    {
        "name": "Mutton snapper",
        "coast": "east",
        "spawn_months": [5, 6, 7, 8],
        "spawn_temp_low": 72,
        "spawn_temp_high": 82,
        "spawn_note": "Mutton snapper congregate at predictable aggregation sites on offshore reefs during summer full moons; these spawning groupings offer a rare chance to catch multiple large fish from one spot on live pilchards or cut bait.",
    },
    {
        "name": "Vermilion snapper (beeliner)",
        "coast": "east",
        "spawn_months": [3, 4, 5, 6, 7, 8, 9, 10],
        "spawn_temp_low": 68,
        "spawn_temp_high": 78,
        "spawn_note": "Vermilion snapper are active spawners through most of the warm season at offshore ledges and structure, responding well to small cut squid or shrimp on a double-drop rig fished just off the bottom.",
    },
    {
        "name": "Lane snapper",
        "coast": "east",
        "spawn_months": [5, 6, 7, 8, 9],
        "spawn_temp_low": 72,
        "spawn_temp_high": 82,
        "spawn_note": "Lane snapper spawn through the summer on nearshore reefs and are among the most willing biters; they respond to small pieces of shrimp or squid and make great table fare despite their modest size.",
    },
    {
        "name": "Cero mackerel",
        "coast": "east",
        "spawn_months": [4, 5, 6, 7, 8],
        "spawn_temp_low": 72,
        "spawn_temp_high": 82,
        "spawn_note": "Cero mackerel are a spring-summer species concentrated in South Florida and the Keys where spawning fish roam near reef edges; trolling small feathers or casting silver spoons near surface activity produces fast action.",
    },
    {
        "name": "Wahoo",
        "coast": "east",
        "spawn_months": [4, 5, 6, 7, 8],
        "spawn_temp_low": 72,
        "spawn_temp_high": 84,
        "spawn_note": "Wahoo spawn offshore in warm blue water and are caught by high-speed trolling along temperature breaks and current edges — the faster the lure, the better for triggering their aggressive strike.",
    },
    {
        "name": "Blackfin tuna",
        "coast": "east",
        "spawn_months": [4, 5, 6, 7, 8, 9, 10],
        "spawn_temp_low": 72,
        "spawn_temp_high": 82,
        "spawn_note": "Blackfin tuna spawn offshore through the warm season and school near the surface around weedlines and current rips; chunking with small pieces of ballyhoo or fast-trolling cedar plugs are reliable methods.",
    },
    {
        "name": "Skipjack tuna",
        "coast": "east",
        "spawn_months": [5, 6, 7, 8, 9],
        "spawn_temp_low": 72,
        "spawn_temp_high": 82,
        "spawn_note": "Skipjack tuna are prolific summer spawners offshore and travel in large, fast-moving schools that crash the surface; small metal jigs and feathers cast into the melee produce nonstop action.",
    },
    {
        "name": "Sand seatrout (white trout)",
        "coast": "east",
        "spawn_months": [4, 5, 6, 7, 8],
        "spawn_temp_low": 68,
        "spawn_temp_high": 80,
        "spawn_note": "Sand seatrout spawn in estuaries and nearshore waters through spring and summer; they respond readily to small jigs and live shrimp fished over sandy bottom near grass edges.",
    },
    {
        "name": "Silver seatrout",
        "coast": "east",
        "spawn_months": [3, 4, 5, 6, 7, 8, 9, 10],
        "spawn_temp_low": 64,
        "spawn_temp_high": 78,
        "spawn_note": "Silver seatrout have one of the longest spawn seasons of any coastal species, remaining active in nearshore and estuarine waters from spring through fall and responding to small soft plastics and shrimp.",
    },
    {
        "name": "Scamp grouper",
        "coast": "east",
        "spawn_months": [3, 4, 5, 6],
        "spawn_temp_low": 65,
        "spawn_temp_high": 76,
        "spawn_note": "Scamp gather at predictable aggregation sites on offshore reefs in spring; fishing live bait on the bottom around these ledges during the full moon phase can yield multiple fish from a single drop.",
    },
    {
        "name": "Black grouper",
        "coast": "east",
        "spawn_months": [1, 2, 3, 4, 5],
        "spawn_temp_low": 62,
        "spawn_temp_high": 76,
        "spawn_note": "Black grouper are winter-spring aggregation spawners that gather on deep offshore reefs; live pinfish or grunts fished on heavy bottom rigs near ledge structure are the most effective approach.",
    },
    {
        "name": "Gulf flounder",
        "coast": "east",
        "spawn_months": [9, 10, 11, 12],
        "spawn_temp_low": 55,
        "spawn_temp_high": 68,
        "spawn_note": "Gulf flounder migrate toward offshore spawning grounds in fall, staging in passes and inlets where gigging and slow-dragged live mud minnows intercept fish making the transition from estuaries to open water.",
    },
    {
        "name": "Gulf kingfish (gulf whiting)",
        "coast": "east",
        "spawn_months": [5, 6, 7, 8],
        "spawn_temp_low": 65,
        "spawn_temp_high": 76,
        "spawn_note": "Gulf kingfish move into the surf zone to spawn in warm months and feed actively in the wash; small pieces of fresh shrimp or bloodworm on a bottom rig cast beyond the breakers produces consistent results.",
    },
    {
        "name": "Northern kingfish",
        "coast": "east",
        "spawn_months": [5, 6, 7, 8],
        "spawn_temp_low": 60,
        "spawn_temp_high": 74,
        "spawn_note": "Northern kingfish are surf-zone spawners from spring through summer, best targeted in the trough just behind the breakers using sand fleas, bloodworms, or small pieces of shrimp on a simple bottom rig.",
    },
    {
        "name": "Southern kingfish (ground mullet)",
        "coast": "east",
        "spawn_months": [5, 6, 7, 8],
        "spawn_temp_low": 65,
        "spawn_temp_high": 76,
        "spawn_note": "Southern kingfish spawn in the nearshore surf and are considered prime table fare; they hit shrimp and sand fleas fished on the bottom in the trough and are especially active on incoming tides.",
    },
    {
        "name": "Winter flounder",
        "coast": "east",
        "spawn_months": [1, 2, 3],
        "spawn_temp_low": 34,
        "spawn_temp_high": 48,
        "spawn_note": "Winter flounder are unique in spawning inshore on sandy bottom near eelgrass beds during the coldest months; they congregate in harbors and estuaries and are taken on small hooks baited with bloodworms or clam fished tight to the bottom.",
    },
    {
        "name": "Triggerfish (gray)",
        "coast": "east",
        "spawn_months": [4, 5, 6, 7, 8, 9],
        "spawn_temp_low": 70,
        "spawn_temp_high": 82,
        "spawn_note": "Gray triggerfish spawn near reefs and hard structure through the warm months and nest on the bottom where males guard egg beds, making them especially territorial and catchable on small pieces of blue crab or squid.",
    },
    {
        "name": "Gafftopsail catfish",
        "coast": "east",
        "spawn_months": [6, 7, 8, 9],
        "spawn_temp_low": 72,
        "spawn_temp_high": 84,
        "spawn_note": "Gafftopsail catfish are summer mouthbrooders with males carrying egg clusters in their mouths; they school near piers and bridges and readily take cut shrimp or fish, offering easy action on light tackle.",
    },
    {
        "name": "Blue runner (hardtail)",
        "coast": "east",
        "spawn_months": [4, 5, 6, 7, 8, 9],
        "spawn_temp_low": 72,
        "spawn_temp_high": 82,
        "spawn_note": "Blue runners spawn nearshore through the warm season and are excellent live baits for larger predators; schools chasing bait near the surface can be taken on small jigs and used immediately as live offerings for kingfish or cobia.",
    },
    {
        "name": "Cubera snapper",
        "coast": "east",
        "spawn_months": [6, 7, 8],
        "spawn_temp_low": 74,
        "spawn_temp_high": 84,
        "spawn_note": "Cubera snapper form deep-reef spawning aggregations in summer, most reliably during full-moon periods; large live blue crabs or whole grunt fished on heavy tackle near vertical structure produce these trophy fish.",
    },
    {
        "name": "Red hind",
        "coast": "east",
        "spawn_months": [2, 3, 4, 5, 6],
        "spawn_temp_low": 66,
        "spawn_temp_high": 76,
        "spawn_note": "Red hind are spring-summer reef spawners that congregate at established aggregation sites; they eagerly take small live baitfish or cut squid dropped to the bottom and are a prized target in the Florida Keys and Caribbean waters.",
    },
    {
        "name": "Pollock",
        "coast": "east",
        "spawn_months": [1, 2, 3, 4],
        "spawn_temp_low": 32,
        "spawn_temp_high": 48,
        "spawn_note": "Pollock spawn in cold northern waters in late winter and are caught from shore on jigs and metal lures near rocky structure; winter concentrations near offshore banks bring them within reach of boat anglers.",
    },
    {
        "name": "Atlantic cod",
        "coast": "east",
        "spawn_months": [1, 2, 3, 4],
        "spawn_temp_low": 34,
        "spawn_temp_high": 50,
        "spawn_note": "Atlantic cod aggregate on offshore banks in winter to spawn, and these cold-weather concentrations are targeted by boat anglers with heavy jigs or bait rigs fished deep on rocky bottom.",
    },
    {
        "name": "Atlantic menhaden (bunker)",
        "coast": "east",
        "spawn_months": [10, 11, 12],
        "spawn_temp_low": 60,
        "spawn_temp_high": 74,
        "spawn_note": "Atlantic menhaden move offshore in fall to spawn in massive pods — these bunker schools draw the most intense striper and bluefish blitzes of the year, and casting into or just beyond the pod with a large chunk of bunker or a bunker-colored plug triggers explosive strikes.",
    },
    {
        "name": "Albacore tuna",
        "coast": "east",
        "spawn_months": [6, 7, 8, 9],
        "spawn_temp_low": 60,
        "spawn_temp_high": 72,
        "spawn_note": "Albacore tuna spawn in warmer offshore water and appear along temperature breaks during summer and fall; trolling feathered jigs or cedar plugs along color changes or current edges is the standard approach.",
    },
    {
        "name": "Rainbow runner",
        "coast": "east",
        "spawn_months": [4, 5, 6, 7, 8],
        "spawn_temp_low": 74,
        "spawn_temp_high": 84,
        "spawn_note": "Rainbow runner spawn offshore in warm water and travel in fast-moving schools near the surface around floating structure and weedlines; small jigs and spoons retrieved quickly trigger their aggressive feeding response.",
    },
    {
        "name": "Lookdown",
        "coast": "east",
        "spawn_months": [5, 6, 7, 8],
        "spawn_temp_low": 72,
        "spawn_temp_high": 82,
        "spawn_note": "Lookdown spawn in nearshore waters near piers and structure in summer; they gather in loose schools and can be taken on tiny jigs or small pieces of shrimp, offering a fun light-tackle challenge.",
    },
    {
        "name": "Palometa",
        "coast": "east",
        "spawn_months": [4, 5, 6, 7, 8],
        "spawn_temp_low": 74,
        "spawn_temp_high": 84,
        "spawn_note": "Palometa spawn in warm nearshore waters and are common in South Florida and Keys surf zones where they can be taken on small jigs and flies in the wash — their bold finnage and scrappy fight make them a top light-tackle target.",
    },
    {
        "name": "White grunt",
        "coast": "east",
        "spawn_months": [3, 4, 5, 6, 7, 8, 9],
        "spawn_temp_low": 70,
        "spawn_temp_high": 82,
        "spawn_note": "White grunt are prolific spawners on nearshore reefs through the warm months and are one of the most abundant reef fish anglers encounter; they take shrimp and cut squid readily and are excellent live bait for larger grouper and snapper.",
    },
    {
        "name": "Red porgy",
        "coast": "east",
        "spawn_months": [3, 4, 5, 6],
        "spawn_temp_low": 60,
        "spawn_temp_high": 72,
        "spawn_note": "Red porgy spawn over hard bottom and offshore reefs in spring; they are caught on small hooks baited with shrimp or cut squid fished close to the bottom and are a reliable target when offshore conditions allow.",
    },
    # --- West Coast additions ---
    {
        "name": "California sheephead",
        "coast": "west",
        "spawn_months": [4, 5, 6, 7, 8],
        "spawn_temp_low": 60,
        "spawn_temp_high": 70,
        "spawn_note": "California sheephead are hermaphroditic — all fish are born female and larger individuals transition to bold red-and-black males that dominate spawning areas in the kelp; targeting structure near kelp edges with live lobster or crab imitations is most productive.",
    },
    {
        "name": "Starry flounder",
        "coast": "west",
        "spawn_months": [12, 1, 2, 3],
        "spawn_temp_low": 46,
        "spawn_temp_high": 56,
        "spawn_note": "Starry flounder enter bays and estuaries to spawn in winter, making them accessible from shore during cold months; slow-drifted live pile worms or ghost shrimp on a light bottom rig produce fish in shallow estuarine flats.",
    },
    {
        "name": "Pacific mackerel (chub mackerel)",
        "coast": "west",
        "spawn_months": [4, 5, 6, 7, 8, 9],
        "spawn_temp_low": 58,
        "spawn_temp_high": 70,
        "spawn_note": "Pacific mackerel are pelagic spring-summer spawners that show up in large schools near piers and kelp; they are caught on Sabiki rigs or small jigs and make excellent live bait for larger pelagics and halibut.",
    },
    {
        "name": "Calico surfperch",
        "coast": "west",
        "spawn_months": [1, 2, 3, 4],
        "spawn_temp_low": 52,
        "spawn_temp_high": 62,
        "spawn_note": "Calico surfperch are livebearers that release young in late winter and early spring; gravid females are the largest fish of the year and congregate in the inner surf trough, where sand crabs and small worms produce the best results.",
    },
    {
        "name": "Walleye surfperch",
        "coast": "west",
        "spawn_months": [3, 4, 5, 6],
        "spawn_temp_low": 56,
        "spawn_temp_high": 66,
        "spawn_note": "Walleye surfperch give birth to live young in spring and gravid fish stack up in the surf zone and around piers; they are caught on small hooks baited with pieces of bloodworm or tiny jigs and are often the most abundant species in the wash.",
    },
    {
        "name": "Opaleye",
        "coast": "west",
        "spawn_months": [4, 5, 6],
        "spawn_temp_low": 58,
        "spawn_temp_high": 68,
        "spawn_note": "Opaleye spawn over rocky reefs in spring and gather in schools near kelp and rocky structure; they are taken on small hooks baited with green sea lettuce or peas and require light line to fool their wary behavior.",
    },
    {
        "name": "Halfmoon (Catalina perch)",
        "coast": "west",
        "spawn_months": [4, 5, 6, 7],
        "spawn_temp_low": 60,
        "spawn_temp_high": 70,
        "spawn_note": "Halfmoon spawn near kelp and rocky areas in spring and early summer; they are a popular pier and boat target taken on small hooks with pieces of mussel or shrimp, and their aggressive nature makes them easy to locate around structure.",
    },
    {
        "name": "Blue rockfish",
        "coast": "west",
        "spawn_months": [12, 1, 2, 3, 4],
        "spawn_temp_low": 50,
        "spawn_temp_high": 60,
        "spawn_note": "Blue rockfish release live larvae in late fall through early spring and school in dense mid-water columns near rocky reefs and kelp; they aggressively hit small jigs and soft plastics worked at mid-depth and are among the most accessible rockfish for shore anglers.",
    },
    {
        "name": "Vermilion rockfish",
        "coast": "west",
        "spawn_months": [1, 2, 3, 4, 5],
        "spawn_temp_low": 52,
        "spawn_temp_high": 62,
        "spawn_note": "Vermilion rockfish are livebearers that release larvae in winter and early spring; they hold near rocky structure and respond to jigs and live anchovies fished at depth, with larger fish often found near the base of steep rocky walls.",
    },
    {
        "name": "Bat ray",
        "coast": "west",
        "spawn_months": [6, 7, 8, 9],
        "spawn_temp_low": 62,
        "spawn_temp_high": 72,
        "spawn_note": "Bat rays pup in very shallow bay flats in summer and are often visible as they cruise in only inches of water; large baits of squid or clam fished on the bottom in mudflat areas produce these hard-fighting rays.",
    },
    {
        "name": "Shovelnose guitarfish",
        "coast": "west",
        "spawn_months": [6, 7, 8, 9],
        "spawn_temp_low": 62,
        "spawn_temp_high": 70,
        "spawn_note": "Shovelnose guitarfish pup in nearshore sandy areas in summer and are a fun target for beach and pier anglers; whole squid or pieces of anchovy fished on the bottom near sandflats produce these unique-looking rays.",
    },
    {
        "name": "Kelp greenling",
        "coast": "west",
        "spawn_months": [9, 10, 11],
        "spawn_temp_low": 52,
        "spawn_temp_high": 62,
        "spawn_note": "Kelp greenling spawn in fall with males guarding colorful blue-speckled egg masses on rocky substrate; spawning fish are concentrated near rocky reefs and respond to small jigs and soft crabs fished tight to the bottom.",
    },
    {
        "name": "Pacific herring",
        "coast": "west",
        "spawn_months": [1, 2, 3, 4],
        "spawn_temp_low": 46,
        "spawn_temp_high": 56,
        "spawn_note": "Pacific herring mass-spawn in bays and nearshore in winter, turning the water milky green with milt — these massive baitball events draw striped bass, halibut, and diving birds, and Sabiki rigs or small jigs fished in the melee produce instant limits.",
    },
    {
        "name": "White croaker (tomcod)",
        "coast": "west",
        "spawn_months": [4, 5, 6, 7, 8, 9],
        "spawn_temp_low": 58,
        "spawn_temp_high": 70,
        "spawn_note": "White croaker spawn in nearshore sandy areas through the warm months and are among the most common fish caught from piers and beaches; small pieces of shrimp or squid on a bottom rig produce consistent action.",
    },
    {
        "name": "Jacksmelt",
        "coast": "west",
        "spawn_months": [3, 4, 5, 6],
        "spawn_temp_low": 56,
        "spawn_temp_high": 66,
        "spawn_note": "Jacksmelt spawn in bays and nearshore in spring and are a targeted species for pier anglers using multi-hook Sabiki or live bait rigs suspended under a float; spawning fish school tightly and can be taken several at a time.",
    },
    # --- Hawaii additions ---
    {
        "name": "Mu (bigeye emperor)",
        "coast": "hawaii",
        "spawn_months": [4, 5, 6, 7, 8, 9],
        "spawn_temp_low": 76,
        "spawn_temp_high": 84,
        "spawn_note": "Mu spawn on Hawaiian reefs through the warm season and are prized table fish targeted by bottom anglers using live shrimp or small crabs fished near coral structure at dusk and dawn.",
    },
    {
        "name": "Menpachi (soldierfish)",
        "coast": "hawaii",
        "spawn_months": [3, 4, 5, 6, 7, 8],
        "spawn_temp_low": 74,
        "spawn_temp_high": 82,
        "spawn_note": "Menpachi are nocturnal spawners that hide in reef crevices by day and emerge at night; nighttime bottom fishing with small shrimp or cut squid near rocky ledges and caves produces these prized reef fish.",
    },
    {
        "name": "Aholehole (Hawaiian flagtail)",
        "coast": "hawaii",
        "spawn_months": [5, 6, 7, 8, 9],
        "spawn_temp_low": 76,
        "spawn_temp_high": 84,
        "spawn_note": "Aholehole spawn nearshore in summer and are an important baitfish and light-tackle target; they school in shallow nearshore areas and under pier lights at night where small hooks baited with tiny pieces of shrimp produce quick catches.",
    },
    {
        "name": "Kaku (barracuda)",
        "coast": "hawaii",
        "spawn_months": [4, 5, 6, 7, 8],
        "spawn_temp_low": 76,
        "spawn_temp_high": 84,
        "spawn_note": "Kaku spawn nearshore around Hawaii in spring and summer; larger fish cruise reef edges and sandy flats and respond well to fast-retrieved silver lures or live fish baits cast along reef drop-offs.",
    },
]

# SPAWNING_DATA pre-bucketed by coast — eliminates the coast filter from the
# build_spawning_report hot loop (109 entries → 8-71 depending on coast).
_SPAWNING_BY_COAST: dict[str, list[Dict]] = {}
for _se in SPAWNING_DATA:
    _SPAWNING_BY_COAST.setdefault(_se["coast"], []).append(_se)
del _se

def _format_spawn_window(spawn_months: list[int]) -> str:
    """Format a list of spawn months into a human-readable string.

    Handles wrap-around ranges (e.g. Nov–Feb) and non-contiguous lists.
    """
    _MA = [
        "",
        "Jan",
        "Feb",
        "Mar",
        "Apr",
        "May",
        "Jun",
        "Jul",
        "Aug",
        "Sep",
        "Oct",
        "Nov",
        "Dec",
    ]
    sm = sorted(set(spawn_months))
    if not sm:
        return "Unknown"
    if len(sm) == 1:
        return _MA[sm[0]]

    # Find the largest gap between consecutive months (including the year-wrap gap).
    # The spawn window is the contiguous run on the *other* side of that gap.
    gaps = [(sm[i + 1] - sm[i], i) for i in range(len(sm) - 1)]
    wrap_gap = (12 - sm[-1] + sm[0], len(sm) - 1)
    all_gaps = gaps + [wrap_gap]
    max_gap, max_idx = max(all_gaps, key=lambda g: g[0])

    # Check whether months are contiguous on either side of the biggest gap
    other_gaps = [g for g, idx in all_gaps if (g, idx) != (max_gap, max_idx)]
    mostly_contiguous = all(g == 1 for g in other_gaps)

    if mostly_contiguous:
        if max_idx == len(sm) - 1:
            # Gap is at the year-wrap point — simple linear range
            return f"{_MA[sm[0]]} – {_MA[sm[-1]]}"
        else:
            # Gap is in the middle — range wraps around the year
            start = sm[max_idx + 1]
            end = sm[max_idx]
            return f"{_MA[start]} – {_MA[end]}"

    # Multiple gaps — list months individually
    return ", ".join(_MA[m] for m in sm)

_SPAWN_STATUS_ORDER: dict[str, int] = {
    "spawning": 0, "pre_spawn": 1, "temp_pending": 2, "post_spawn": 3
}

def build_spawning_report(
    month: int,
    water_temp: float,
    coast: Optional[str] = None,
    state: str = "",
) -> list[dict[str, Any]]:
    """Return species that are currently spawning or approaching their spawn window.

    Each entry is a dict::

        {
            "name": str,          # species common name
            "status": str,        # "spawning" | "pre_spawn" | "post_spawn" | "temp_pending"
            "temp_ok": bool,      # True if water temp is within spawn range
            "temp_delta": int,    # degrees outside the spawn temp range (0 = in range)
            "spawn_note": str,    # behaviour / fishing tip
            "temp_range": str,    # human-readable, e.g. "65–78 °F"
            "spawn_window": str,  # human-readable, e.g. "May – Aug" or "Nov – Feb"
            "legal_status": str,  # "catch_release" | "restricted" | "open" | "unknown"
            "regulation": dict|None,  # full regulation payload for this state, or None
        }

    Status meanings:
      spawning     — month is in spawn window AND temp is within range
      temp_pending — month is in spawn window but water hasn't reached spawn temp
      pre_spawn    — spawn window starts next month (fish are staging)
      post_spawn   — spawn window ended last month (fish may be recovering / feeding up)

    Legal status meanings:
      catch_release — harvest prohibited (closed season, C&R only, bag limit 0)
      restricted    — seasonal rules apply; always verify before keeping fish
      open          — currently open per regulation data; size/bag limits shown
      unknown       — no regulation data found for this state/species combination

    Only species within ±1 month of the spawn window are included so the
    list stays immediately actionable.

    ``coast`` must be one of ``"east"``, ``"west"``, or ``"hawaii"``.
    Passing ``None`` returns an empty list — the coast must be known to
    avoid showing wrong-region spawning data.
    """
    _LEGACY_MAP = {
        "legal": "open",
        "catch_and_release": "catch_release",
        "restricted": "restricted",
        "out_of_season": "catch_release",
        "prohibited": "catch_release",
        "unknown": "unknown",
    }
    results: list[dict[str, Any]] = []

    for entry in _SPAWNING_BY_COAST.get(coast, []) if coast else []:
        spawn_months = entry["spawn_months"]
        temp_low = entry["spawn_temp_low"]
        temp_high = entry["spawn_temp_high"]
        temp_ok = temp_low <= water_temp <= temp_high

        # How far is the current water temp from the spawn range?
        if water_temp < temp_low:
            temp_delta = int(temp_low - water_temp)
        elif water_temp > temp_high:
            temp_delta = int(water_temp - temp_high)
        else:
            temp_delta = 0

        in_window = month in spawn_months
        prev_month = 12 if month == 1 else month - 1
        next_month = 1 if month == 12 else month + 1

        # Determine direction of adjacency
        after_window = (not in_window) and (prev_month in spawn_months)  # just ended
        before_window = (not in_window) and (
            next_month in spawn_months
        )  # about to start

        if not in_window and not after_window and not before_window:
            continue  # too far away to be actionable

        if in_window and temp_ok:
            status = "spawning"
        elif in_window and not temp_ok:
            status = "temp_pending"
        elif before_window:
            status = "pre_spawn"
        else:
            status = "post_spawn"

        # Regulation lookup — only query when a state is known.
        reg: Optional[dict[str, str]] = None
        if state:
            try:
                reg = lookup_regulation(entry["name"], state)
            except Exception:
                reg = None

        # ── Visibility gate ─────────────────────────────────────────────────
        # Only species with status "legal" (or "unknown" where no data exists)
        # appear in the forecast.  All other statuses are hidden to avoid
        # recommending unlawful catches:
        #   • "prohibited"        — year-round closure or federal protection
        #   • "out_of_season"     — current month falls inside a closed season
        #   • "catch_and_release" — retention/harvest prohibited; cannot keep
        #   • "restricted"        — conditional rules that may prohibit retention
        regulation_status = classify_legality(reg, month)
        if should_hide_from_forecast(regulation_status):
            continue

        legal_status = _LEGACY_MAP.get(regulation_status, "unknown")

        # Resolve display categories from the canonical dict (or JSON field).
        spawn_sp = SPECIES_DB_MAP.get(entry["name"])
        sp_categories: list[str] = (
            (spawn_sp.get("categories") if spawn_sp else None)
            or _SPECIES_CATEGORIES.get(entry["name"])
            or ["other"]
        )

        results.append(
            {
                "name": entry["name"],
                "status": status,
                "temp_ok": temp_ok,
                "temp_delta": temp_delta,
                "spawn_note": entry["spawn_note"],
                "temp_range": f"{temp_low}–{temp_high}\u202f°F",
                "spawn_window": _format_spawn_window(spawn_months),
                "legal_status": legal_status,
                "regulation_status": regulation_status,
                "regulation": reg,
                "categories": sp_categories,
            }
        )

    results.sort(key=lambda x: (_SPAWN_STATUS_ORDER.get(x["status"], 9), x["name"]))
    return results

_MONTH_ABBR = [
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
]

# Curated headline species shown on the year-round calendar, per region.
# These are the iconic target species anglers actually plan trips around —
# independent of current day-to-day conditions.
_NOTABLE_SPECIES_BY_REGION: dict[str, list[str]] = {
    "northeast": [
        "Striped bass (rockfish)",
        "Bluefish",
        "Fluke (summer flounder)",
        "Tautog (blackfish)",
        "Black sea bass",
        "Scup (porgy)",
        "False albacore (little tunny)",
        "Atlantic bonito",
        "Winter flounder",
        "Weakfish",
    ],
    "midatlantic": [
        "Striped bass (rockfish)",
        "Bluefish",
        "Red drum (puppy drum)",
        "Fluke (summer flounder)",
        "Black sea bass",
        "Tautog (blackfish)",
        "Cobia",
        "Spanish mackerel",
        "False albacore (little tunny)",
        "Scup (porgy)",
    ],
    "southeast": [
        "Red drum (puppy drum)",
        "Speckled trout (spotted seatrout)",
        "Flounder (summer flounder)",
        "Spanish mackerel",
        "King mackerel (kingfish)",
        "Cobia",
        "Pompano",
        "Sheepshead",
        "Black drum",
        "Bluefish",
    ],
    "florida": [
        "Tarpon",
        "Snook",
        "Red drum (puppy drum)",
        "Speckled trout (spotted seatrout)",
        "Permit",
        "Spanish mackerel",
        "King mackerel (kingfish)",
        "Cobia",
        "Red snapper",
        "Gag grouper",
    ],
    "gulf": [
        "Red drum (puppy drum)",
        "Speckled trout (spotted seatrout)",
        "Flounder (summer flounder)",
        "Red snapper",
        "King mackerel (kingfish)",
        "Cobia",
        "Pompano",
        "Spanish mackerel",
        "Mahi-mahi (dolphinfish)",
        "Tarpon",
    ],
    "socal": [
        "Yellowtail (California yellowtail)",
        "California halibut",
        "White seabass",
        "Corbina",
        "Kelp bass (calico bass)",
        "Sand bass (barred sand bass)",
        "Spotfin croaker",
        "Yellowfin croaker",
        "Leopard shark",
        "California sheephead",
    ],
    "norcal": [
        "Lingcod",
        "California halibut",
        "White seabass",
        "Redtail surfperch",
        "Cabezon",
        "Kelp greenling",
        "Rock greenling",
        "Leopard shark",
    ],
    "pacific_nw": [
        "Lingcod",
        "Redtail surfperch",
        "Cabezon",
        "Kelp greenling",
        "Rock greenling",
    ],
    "hawaii": [
        "Giant trevally (ulua)",
        "Moi (Pacific threadfin)",
        "Bonefish (oio)",
        "Bluefin trevally (omilu)",
        "Papio (juvenile jack)",
        "Kaku (barracuda)",
        "Menpachi (soldierfish)",
    ],
}

def build_species_calendar(
    species_list: list[dict[str, Any]],
    location: Optional[dict[str, Any]] = None,
    fish_region: str = "",
) -> list[dict[str, Any]]:
    """Build a 12-month availability calendar.

    When *fish_region* is provided the calendar shows the region's notable
    target species (the fish people plan trips around) rather than whatever
    happens to be scoring highest under today's conditions.  Falls back to
    the dynamic top-10 ranked list when no region is known.

    For each species, looks up peak_months and good_months from SPECIES_DB
    and returns a list of dicts::

        {
            "name": "Red drum",
            "months": [
                {"abbr": "Jan", "level": "good"},   # "peak", "good", or ""
                ...
            ]
        }

    Temperature feasibility is also considered: months where the regional
    average water temp falls outside the species' temp range are marked empty.
    """
    db_map = SPECIES_DB_MAP  # pre-built at import; ~900 entries, never changes

    # Get regional water temps (12 months) for temp filtering
    monthly_temps: dict[int, float] = {}
    if location:
        monthly_temps = get_monthly_water_temps(location)

    # Determine which species to show on the calendar
    if fish_region and fish_region in _NOTABLE_SPECIES_BY_REGION:
        source = [{"name": n} for n in _NOTABLE_SPECIES_BY_REGION[fish_region]]
    else:
        source = species_list[:10]

    calendar: list[dict[str, Any]] = []
    for ranked_sp in source:
        sp = db_map.get(ranked_sp["name"])
        if not sp:
            continue
        months = []
        for m in range(1, 13):
            # Check if water temp makes this species viable this month
            if monthly_temps:
                t = monthly_temps.get(m, 65)
                if t < sp["temp_min"] - 5 or t > sp["temp_max"] + 5:
                    months.append({"abbr": _MONTH_ABBR[m - 1], "level": ""})
                    continue

            if m in sp.get("peak_months", []):
                level = "peak"
            elif m in sp.get("good_months", []):
                level = "good"
            else:
                level = ""
            months.append({"abbr": _MONTH_ABBR[m - 1], "level": level})

        calendar.append(
            {
                "name": ranked_sp["name"],
                "months": months,
            }
        )

    return calendar
