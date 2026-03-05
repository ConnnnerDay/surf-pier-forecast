"""Species database, scoring logic, and fishing knowledge."""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from locations import get_monthly_water_temps
from regulations import lookup_regulation
from storage.species_loader import SPECIES_DB

logger = logging.getLogger(__name__)



# ---------------------------------------------------------------------------
# Profile-based species classification
# ---------------------------------------------------------------------------
# These sets map species to the fishing styles and target categories from
# the user's profile.  Species not listed in a set are considered accessible
# to that style (e.g. many species can be caught from both surf and pier).
# ---------------------------------------------------------------------------

# -- Fishing type (where you fish) --
# Species that are ONLY realistic from a boat offshore — exclude for
# surf/pier/inshore-only anglers.
_OFFSHORE_ONLY_SPECIES: set = {
    "Mahi-mahi (dolphinfish)", "Wahoo", "Blackfin tuna", "Yellowfin tuna",
    "Sailfish", "Blue marlin", "White marlin", "Skipjack tuna",
    "Bigeye tuna", "Albacore tuna", "Frigate mackerel",
    "Blueline tilefish", "Golden tilefish", "Snowy grouper", "Wreckfish",
    "Tilefish (blueline juvenile/grey)", "Tilefish (golden juvenile)",
    "Barrelfish", "Blackbelly rosefish", "Misty grouper",
    "Yellowedge grouper", "Queen snapper", "Silk snapper",
    "Greater amberjack", "Almaco jack", "Almaco jack (large adult)",
    "Lesser amberjack", "Rainbow runner",
    "Shortfin mako shark", "Thresher shark", "Tiger shark",
    "Ocean sunfish (mola mola)",
}

# Species best caught from a pier or jetty (structure-dependent).
_PIER_SPECIES: set = {
    "Sheepshead", "Tautog (blackfish)", "Black sea bass", "Spadefish (Atlantic)",
    "Triggerfish (gray)", "Lookdown", "Mangrove snapper (gray snapper)",
    "Hogfish", "Planehead filefish", "Northern puffer (blowfish)",
    "Bermuda chub (sea chub)", "Sergeant major (damselfish)",
    "Cunner (bergall)", "Slippery dick (wrasse)", "Puddingwife (wrasse)",
    "Striped blenny", "Feather blenny",
    "Ocean triggerfish", "Queen triggerfish", "Scrawled filefish",
    "Orange filefish", "Bandtail puffer", "Checkered puffer",
    "Striped burrfish (spiny boxfish)", "Scrawled cowfish",
    "Blue tang (surgeonfish)", "Gray angelfish", "Spotfin butterflyfish",
    "Doctorfish (tang)", "Squirrelfish", "Porkfish",
    "Smooth trunkfish", "Honeycomb cowfish",
    "Menpachi (soldierfish)", "Aholehole (Hawaiian flagtail)",
    "Dungeness crab (from pier)",
    "Kelp bass (calico bass)", "Sand bass (barred sand bass)",
    "California sheephead", "Opaleye", "Halfmoon (Catalina perch)",
    "Kelp greenling", "Rock greenling",
}

# Species primarily caught from the surf zone.
_SURF_SPECIES: set = {
    "Red drum (puppy drum)", "Pompano", "Whiting (sea mullet, kingfish)",
    "Southern kingfish (ground mullet)", "Gulf kingfish (gulf whiting)",
    "Northern kingfish",
    "Barred surfperch", "Redtail surfperch", "Calico surfperch",
    "Walleye surfperch", "Rubberlip seaperch",
    "Corbina", "Spotfin croaker", "Yellowfin croaker",
    "Leopard shark", "Shovelnose guitarfish",
    "Moi (Pacific threadfin)", "Bonefish (oio)",
}

# Species best caught inshore (inlet, marsh, flats).
_INSHORE_SPECIES: set = {
    "Speckled trout (spotted seatrout)", "Red drum (puppy drum)",
    "Southern flounder", "Flounder (summer flounder)", "Gulf flounder",
    "Snook", "Tripletail", "Ladyfish", "Jack crevalle",
    "Tarpon", "Permit", "Black drum",
    "Sand seatrout (white trout)", "Silver seatrout",
    "Gray trout (weakfish)", "Striped bass (rockfish)",
    "Striped bass (hybrid)",
    "Hickory shad", "American shad",
    "Channel catfish", "Blue catfish", "Flathead catfish",
    "Largemouth bass", "White perch", "Yellow perch",
    "Redear sunfish (shellcracker)", "Bluegill", "Warmouth",
    "American eel",
    "Giant trevally (ulua)", "Bluefin trevally (omilu)", "Papio (juvenile jack)",
}

# -- Target categories (what you want to catch) --
_BOTTOM_SPECIES: set = {
    "Red drum (puppy drum)", "Black drum", "Black drum (large bull)",
    "Whiting (sea mullet, kingfish)", "Spot", "Atlantic croaker",
    "Southern kingfish (ground mullet)", "Gulf kingfish (gulf whiting)",
    "Northern kingfish",
    "Flounder (summer flounder)", "Southern flounder", "Gulf flounder",
    "Windowpane flounder", "Fringed flounder", "Hogchoker",
    "California halibut", "Starry flounder",
    "Pompano", "White croaker (tomcod)",
    "Corbina", "Spotfin croaker", "Yellowfin croaker",
    "Barred surfperch", "Redtail surfperch", "Calico surfperch",
    "Walleye surfperch", "Rubberlip seaperch",
    "Hardhead catfish (sea catfish)", "Gafftopsail catfish",
    "Bonefish (oio)", "Moi (Pacific threadfin)",
}

_PELAGIC_SPECIES: set = {
    "Bluefish", "Spanish mackerel", "King mackerel (kingfish)",
    "False albacore (little tunny)", "Atlantic bonito",
    "Cobia", "Jack crevalle", "Blue runner (hardtail)",
    "Mahi-mahi (dolphinfish)", "Wahoo", "Blackfin tuna", "Yellowfin tuna",
    "Skipjack tuna", "Bigeye tuna", "Albacore tuna",
    "Sailfish", "Blue marlin", "White marlin",
    "Pacific mackerel (chub mackerel)", "Jack mackerel (Spanish jack)",
    "Pacific bonito", "Yellowtail (California yellowtail)", "White seabass",
    "Ladyfish", "Great barracuda", "Tarpon",
    "Giant trevally (ulua)", "Bluefin trevally (omilu)", "Papio (juvenile jack)",
    "Striped bass (rockfish)",
}

_STRUCTURE_SPECIES: set = {
    "Sheepshead", "Tautog (blackfish)", "Black sea bass",
    "Triggerfish (gray)", "Spadefish (Atlantic)",
    "Red snapper", "Vermilion snapper (beeliner)",
    "Mangrove snapper (gray snapper)", "Lane snapper",
    "Yellowtail snapper", "Mutton snapper", "Cubera snapper",
    "Schoolmaster snapper",
    "Gag grouper", "Red grouper", "Scamp grouper", "Black grouper",
    "Hogfish", "Red porgy", "Scup (porgy)",
    "White grunt", "Tomtate grunt", "Blue-striped grunt",
    "Lingcod", "Vermilion rockfish", "Blue rockfish",
    "Copper rockfish", "Grass rockfish", "Cabezon",
    "Kelp bass (calico bass)", "Sand bass (barred sand bass)",
    "California sheephead", "Opaleye",
    "Mu (bigeye emperor)", "Menpachi (soldierfish)",
}

_GAMEFISH_SPECIES: set = {
    "Blacktip shark", "Spinner shark", "Bull shark", "Sandbar shark",
    "Lemon shark", "Dusky shark", "Bonnethead shark",
    "Atlantic sharpnose shark", "Scalloped hammerhead shark",
    "Nurse shark", "Finetooth shark",
    "Leopard shark", "Shovelnose guitarfish",
    "Cobia", "Tarpon", "King mackerel (kingfish)",
    "Mahi-mahi (dolphinfish)", "Wahoo", "Sailfish", "Blue marlin", "White marlin",
    "Blackfin tuna", "Yellowfin tuna",
    "Greater amberjack", "Jack crevalle",
    "Giant trevally (ulua)", "Kaku (barracuda)",
    "Snook", "Permit",
}


def _species_matches_profile(
    sp_name: str,
    fishing_types: Optional[List[str]] = None,
    targets: Optional[List[str]] = None,
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

        # If user doesn't fish offshore, exclude offshore-only species
        if not has_offshore and sp_name in _OFFSHORE_ONLY_SPECIES:
            return False

        # If species is exclusively surf/pier/inshore AND user doesn't do
        # that type, consider excluding.  But most species are catchable
        # from multiple locations, so we only restrict the tight sets.
        is_surf_only = sp_name in _SURF_SPECIES and sp_name not in _PIER_SPECIES and sp_name not in _INSHORE_SPECIES
        is_pier_only = sp_name in _PIER_SPECIES and sp_name not in _SURF_SPECIES and sp_name not in _INSHORE_SPECIES
        is_inshore_only = sp_name in _INSHORE_SPECIES and sp_name not in _SURF_SPECIES and sp_name not in _PIER_SPECIES

        # Only exclude if the species' primary method doesn't overlap with
        # any of the user's chosen types
        if is_surf_only and not has_surf:
            return False
        if is_pier_only and not has_pier:
            return False
        if is_inshore_only and not has_inshore:
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


SEASONAL_EXPLANATIONS: Dict[str, Dict[str, str]] = {
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


def _get_explanation(sp: Dict[str, Any], month: int, water_temp: float) -> str:
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
            return "Cast to sandbars and troughs as rising water pushes bait toward shore"
        if is_dawn or is_dusk:
            return "Work cut mullet along the bottom near structure and drop-offs"
        return "Use fresh cut bait on a fish-finder rig, let it soak on the bottom"

    if "trout" in name_lower or "seatrout" in name_lower:
        if is_dawn:
            return "Fish live shrimp under a popping cork near grass flats at first light"
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
            return "Cast soft plastics or live eels around rocks and jetties at dawn/dusk"
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

RIG_CATEGORIES: Dict[str, Dict[str, Any]] = {
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
}


def _classify_rig(rig_text: str) -> str:
    """Map a species' rig description to a canonical rig category key."""
    text = rig_text.lower()
    if "n/a" in text or "observe" in text or "protected" in text:
        return ""
    if "deep-drop" in text or "deep drop" in text or "electric reel" in text:
        return "deep-drop"
    if "trolling" in text and "slow" not in text:
        return "trolling"
    if "sabiki" in text or "bait catcher" in text or "gold-hook bait" in text:
        return "sabiki"
    if "shad dart" in text or "tandem" in text:
        return "tandem-jig"
    if "popping" in text or "cork" in text:
        return "popping-cork"
    if "stinger" in text or ("king" in text and "wire" in text):
        return "kingfish-stinger"
    if ("shark" in text or "very heavy wire" in text
            or "stand-up" in text or "heavy wire leader and heavy" in text):
        return "shark"
    if "knocker" in text:
        return "knocker"
    if "pier" in text or "structure" in text or "vertical" in text:
        return "knocker"
    if "pompano" in text or "float bead" in text or ("floats above" in text):
        return "pompano"
    if "double-dropper" in text or "hi-lo" in text or "two-hook" in text:
        return "hi-lo"
    if "float" in text or "free-line" in text or "balloon" in text:
        return "float"
    if ("carolina" in text or "fishfinder" in text or "fish finder" in text
            or "sliding" in text):
        return "fishfinder"
    return "fishfinder"


def build_rig_recommendations(
    species_ranking: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Build rig recommendations based on currently-active species.

    Groups active species by rig type and produces one recommendation
    per rig, ordered by the highest-ranked species that uses it.
    """
    rig_groups: Dict[str, List[Dict[str, Any]]] = {}
    rig_order: List[str] = []

    for sp in species_ranking:
        key = _classify_rig(sp["rig"])
        if key not in rig_groups:
            rig_groups[key] = []
            rig_order.append(key)
        rig_groups[key].append(sp)

    recommendations: List[Dict[str, Any]] = []
    for key in rig_order:
        group = rig_groups[key]
        category = RIG_CATEGORIES.get(key)
        if category is None:
            continue

        species_names = [sp["name"] for sp in group]
        hooks = list(dict.fromkeys(sp["hook_size"] for sp in group))
        sinkers = list(dict.fromkeys(sp["sinker"] for sp in group))

        recommendations.append({
            "name": category["name"],
            "description": category["description"],
            "mainline": category["mainline"],
            "leader": category["leader"],
            "hook": " or ".join(hooks[:3]),
            "sinker": " or ".join(sinkers[:3]),
            "targets": species_names,
            "image": category.get("image", ""),
            "knots": get_knots_for_rig(key),
        })

    return recommendations


# ---------------------------------------------------------------------------
# Fishing knot recommendations
# ---------------------------------------------------------------------------

KNOTS_DB: Dict[str, Dict[str, str]] = {
    "improved_clinch": {
        "name": "Improved Clinch Knot",
        "use": "Hook or swivel to mono/fluoro leader",
        "strength": "95%",
        "steps": (
            "1. Thread 6\" of line through the hook eye. "
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
            "1. Double 6\" of line and pass the loop through the hook eye. "
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
            "1. Pass line through the eye, double back 6\". "
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
            "1. Overlap braid and leader by 8\". "
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
            "1. Double 3\" of line to form a loop. "
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
_RIG_KNOTS: Dict[str, List[str]] = {
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
}


def get_knots_for_rig(rig_key: str) -> List[Dict[str, str]]:
    """Return the recommended knots for a rig type."""
    knot_keys = _RIG_KNOTS.get(rig_key, ["improved_clinch"])
    return [KNOTS_DB[k] for k in knot_keys if k in KNOTS_DB]


# Natural baits with the species they target and seasonal availability.
# ``available_months`` controls when a bait is practical to obtain/use.
# ``notes_seasonal`` overrides the default ``notes`` during specific seasons.
# Baits out of season are demoted in the ranking so anglers see what they
# can actually get their hands on right now.
BAIT_DB: List[Dict[str, Any]] = [
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
        "targets": ["Whiting", "Black drum", "Northern puffer", "Spot", "Atlantic croaker"],
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
        "targets": ["Blacktip shark", "Bull shark", "Sandbar shark", "Spinner shark", "Dusky shark"],
    },
    {
        "bait": "Live blue runners",
        "available_months": [5, 6, 7, 8, 9, 10],
        "notes": "Top live bait for large gamefish; catch on sabiki rigs and fish on heavy tackle.",
        "notes_seasonal": {
            "winter": "Blue runners are not available inshore in winter months.",
        },
        "targets": ["Greater amberjack", "King mackerel", "Cobia", "Black grouper", "Gag grouper"],
    },
    {
        "bait": "Live menhaden (pogies)",
        "available_months": [3, 4, 5, 6, 7, 8, 9, 10, 11],
        "notes": "The king of live baits in NC; cast-net schools and fish on circle hooks for almost any large gamefish.",
        "notes_seasonal": {
            "winter": "Live pogies are hard to find in winter; use frozen cut menhaden or live mullet as alternatives.",
            "fall": "Massive menhaden schools are along the beach in fall; cast-net more than you need and freeze the rest.",
        },
        "targets": ["Red drum", "Cobia", "Tarpon", "King mackerel", "Jack crevalle", "Gag grouper"],
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
        "targets": ["Sheepshead", "Tautog", "Permit", "Triggerfish", "Bonnethead shark"],
    },
    {
        "bait": "Cut squid strips",
        "available_months": list(range(1, 13)),
        "notes": "Durable and versatile bottom fishing bait; stays on the hook well for reef and wreck species. Frozen year-round.",
        "targets": ["Red snapper", "Vermilion snapper", "Black sea bass", "Red porgy", "White grunt", "Scup"],
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
    sp: Dict[str, Any],
    month: int,
    water_temp: float,
    wind_dir: Optional[str] = None,
    wind_range: Optional[Tuple[float, float]] = None,
    wave_range: Optional[Tuple[float, float]] = None,
    hour: int = 12,
    coast: str = "east",
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
    """
    score = 0.0

    if water_temp < sp["temp_min"] or water_temp > sp["temp_max"]:
        return -100.0

    ideal_low = sp["temp_ideal_low"]
    ideal_high = sp["temp_ideal_high"]
    if ideal_low <= water_temp <= ideal_high:
        score += 50.0
    elif water_temp < ideal_low:
        distance = ideal_low - water_temp
        temp_range = ideal_low - sp["temp_min"]
        score += max(0, 50.0 * (1 - distance / temp_range)) if temp_range > 0 else 25.0
    else:
        distance = water_temp - ideal_high
        temp_range = sp["temp_max"] - ideal_high
        score += max(0, 50.0 * (1 - distance / temp_range)) if temp_range > 0 else 25.0

    if month in sp["peak_months"]:
        score += 30.0
    elif month in sp["good_months"]:
        score += 15.0

    # --- Dynamic conditions modifiers ---
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
    "Red drum (puppy drum)", "Bluefish", "Pompano", "Whiting (sea mullet, kingfish)",
    "Spot", "Atlantic croaker", "Flounder (summer flounder)", "Southern flounder",
    "Gulf flounder", "Spanish mackerel", "Jack crevalle", "Cobia",
    "Southern kingfish (ground mullet)", "Gulf kingfish (gulf whiting)",
    "Blacktip shark", "Spinner shark", "Bull shark",
    "Striped bass (rockfish)", "Black drum",
    # Pacific
    "Barred surfperch", "Redtail surfperch", "Calico surfperch",
    "Corbina", "Spotfin croaker", "Yellowfin croaker",
    "Leopard shark", "Shovelnose guitarfish",
    # Hawaii
    "Giant trevally (ulua)", "Moi (Pacific threadfin)", "Bonefish (oio)",
}

# Species that prefer calmer conditions and/or offshore wind (clearer water).
_CALM_WATER_SPECIES: set = {
    "Sheepshead", "Tautog (blackfish)", "Triggerfish (gray)", "Spadefish (Atlantic)",
    "Mangrove snapper (gray snapper)", "Hogfish", "Bermuda chub (sea chub)",
    "Lookdown", "Sergeant major (damselfish)", "Planehead filefish",
    "Scrawled cowfish", "Ocean triggerfish", "Queen triggerfish",
    "Gray snapper (juvenile)", "Speckled trout (spotted seatrout)",
    "Tarpon", "Tripletail", "Permit", "Snook",
    # Pacific
    "Kelp bass (calico bass)", "Sand bass (barred sand bass)", "California sheephead",
    "Opaleye", "Halfmoon (Catalina perch)", "California halibut", "White seabass",
    # Hawaii
    "Mu (bigeye emperor)", "Menpachi (soldierfish)", "Aholehole (Hawaiian flagtail)",
}

# Species that feed more actively in rougher surf.
_ROUGH_SURF_SPECIES: set = {
    "Red drum (puppy drum)", "Bluefish", "Striped bass (rockfish)",
    "Whiting (sea mullet, kingfish)", "Pompano", "Black drum",
    "Smooth dogfish", "Atlantic croaker", "Spot",
    "Southern kingfish (ground mullet)", "Gulf kingfish (gulf whiting)",
    # Pacific
    "Barred surfperch", "Redtail surfperch", "Calico surfperch",
    "Corbina", "Spotfin croaker", "Yellowfin croaker",
}

# Species that feed best in low-light conditions (dawn, dusk, night).
_LOW_LIGHT_SPECIES: set = {
    "Striped bass (rockfish)", "Speckled trout (spotted seatrout)",
    "Red drum (puppy drum)", "Cobia", "Tarpon", "Flounder (summer flounder)",
    "Southern flounder", "Gulf flounder", "Ribbonfish (Atlantic cutlassfish)",
    "Spotted moray eel", "Green moray eel", "American eel", "Conger eel",
    "Squirrelfish", "Bigeye (Priacanthus arenatus)", "Short bigeye",
    "Blacktip shark", "Bull shark", "Sandbar shark", "Lemon shark",
    # Pacific
    "Lingcod", "Cabezon", "Leopard shark", "Bat ray",
    # Hawaii
    "Giant trevally (ulua)", "Menpachi (soldierfish)", "Mu (bigeye emperor)",
}

# Species that are more active during bright midday conditions.
_DAYTIME_SPECIES: set = {
    "Spanish mackerel", "King mackerel (kingfish)", "Cero mackerel",
    "False albacore (little tunny)", "Mahi-mahi (dolphinfish)",
    "Sergeant major (damselfish)", "Blue tang (surgeonfish)",
    "Spotfin butterflyfish", "Gray angelfish",
    "Bermuda chub (sea chub)", "Pinfish", "Pigfish",
    # Pacific
    "Pacific mackerel (chub mackerel)", "Jack mackerel (Spanish jack)",
    "Pacific bonito", "Yellowtail (California yellowtail)",
    "Jacksmelt", "Pacific sardine", "Northern anchovy",
    # Hawaii
    "Bluefin trevally (omilu)", "Papio (juvenile jack)", "Kaku (barracuda)",
}

# Compass directions grouped for onshore/offshore determination.
# East-facing coasts (Atlantic): onshore = easterly, offshore = westerly
# West-facing coasts (Pacific): onshore = westerly, offshore = easterly
# Hawaii / Gulf south: mixed, so use east-facing defaults
_ONSHORE_DIRS_EAST: set = {"S", "SE", "E", "SSE", "ESE", "SSW", "ENE"}
_OFFSHORE_DIRS_EAST: set = {"N", "NW", "W", "NNW", "WNW", "NNE", "NE"}
_ONSHORE_DIRS_WEST: set = {"W", "NW", "SW", "WNW", "WSW", "NNW", "SSW"}
_OFFSHORE_DIRS_WEST: set = {"E", "NE", "SE", "ENE", "ESE", "NNE", "SSE"}

# Default for backward compatibility
_ONSHORE_DIRS = _ONSHORE_DIRS_EAST
_OFFSHORE_DIRS = _OFFSHORE_DIRS_EAST


def _conditions_modifier(
    sp: Dict[str, Any],
    wind_dir: Optional[str],
    wind_range: Optional[Tuple[float, float]],
    wave_range: Optional[Tuple[float, float]],
    hour: int,
    coast: str = "east",
) -> float:
    """Compute a conditions-based score modifier for a species.

    Returns a value between roughly -5 and +15 based on how well current
    wind direction, wind speed, wave height, and time of day match the
    species' preferred conditions.

    ``coast`` should be ``"east"`` for Atlantic/Gulf or ``"west"`` for Pacific.
    """
    modifier = 0.0
    name = sp["name"]

    # --- Wind direction modifier (up to +5 / -3) ---
    if wind_dir:
        onshore_dirs = _ONSHORE_DIRS_WEST if coast == "west" else _ONSHORE_DIRS_EAST
        offshore_dirs = _OFFSHORE_DIRS_WEST if coast == "west" else _OFFSHORE_DIRS_EAST
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


# Minimum score to include a species in the forecast.
# This filters out species that technically survive but aren't really biting.
SPECIES_SCORE_THRESHOLD = 30


def build_species_ranking(
    month: int,
    water_temp: float,
    wind_dir: Optional[str] = None,
    wind_range: Optional[Tuple[float, float]] = None,
    wave_range: Optional[Tuple[float, float]] = None,
    hour: int = 12,
    coast: str = "east",
    state: str = "",
    fishing_types: Optional[List[str]] = None,
    targets: Optional[List[str]] = None,
    fish_region: str = "",
) -> List[Dict[str, Any]]:
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
    """
    # For wind scoring, Hawaii uses "east" wind patterns (NE trades)
    wind_coast = "west" if coast == "west" else "east"
    scored = []
    for sp in SPECIES_DB:
        # Skip species from a different coast/region
        if sp.get("coast", "east") != coast:
            continue
        # Skip species not found in this geographic region
        if fish_region and "regions" in sp and fish_region not in sp["regions"]:
            continue
        # Skip species that don't match user's fishing profile
        if not _species_matches_profile(sp["name"], fishing_types, targets):
            continue
        s = _score_species(
            sp, month, water_temp,
            wind_dir=wind_dir,
            wind_range=wind_range,
            wave_range=wave_range,
            hour=hour,
            coast=wind_coast,
        )
        if s >= SPECIES_SCORE_THRESHOLD:
            explanation = _get_explanation(sp, month, water_temp)
            scored.append((s, sp, explanation))

    scored.sort(key=lambda x: x[0], reverse=True)

    result: List[Dict[str, Any]] = []
    for rank, (score, sp, explanation) in enumerate(scored[:10], start=1):
        if score >= 65:
            activity = "Hot"
        elif score >= 50:
            activity = "Active"
        else:
            activity = "Possible"

        entry: Dict[str, Any] = {
            "rank": rank,
            "name": sp["name"],
            "score": round(score, 1),
            "activity": activity,
            "explanation": explanation,
            "bait": sp["bait"],
            "rig": sp["rig"],
            "hook_size": sp["hook_size"],
            "sinker": sp["sinker"],
        }

        # Attach regulation data if available
        if state:
            reg = lookup_regulation(sp["name"], state)
            if reg:
                entry["regulation"] = reg

        result.append(entry)

    return result


def build_bait_ranking(
    species_ranking: List[Dict[str, Any]],
    month: int,
) -> List[Dict[str, str]]:
    """Rank baits by relevance to the current top species and season.

    Baits whose target species rank highly are scored higher.  Baits that are
    out of season (``available_months``) receive a penalty so anglers see what
    they can actually get right now.  Season-specific notes override defaults.
    """
    season = _get_season(month)

    # Map species short names to their rank for quick lookup.
    species_ranks: Dict[str, int] = {}
    for sp in species_ranking:
        short = sp["name"].split("(")[0].strip()
        species_ranks[short] = sp["rank"]

    scored_baits: List[Tuple[float, Dict[str, str]]] = []
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

    scored_baits.sort(key=lambda x: x[0], reverse=True)
    return [b for _, b in scored_baits]


# ---------------------------------------------------------------------------
# Natural bait / forage species availability
# ---------------------------------------------------------------------------

NATURAL_BAIT_DB: List[Dict[str, Any]] = [
    # Atlantic / Gulf
    {"name": "Menhaden (bunker)", "months": [3,4,5,6,7,8,9,10,11], "coast": "east",
     "note": "Schools visible at surface — look for diving birds"},
    {"name": "Mullet", "months": [1,2,3,4,5,6,7,8,9,10,11,12], "coast": "east",
     "note": "Year-round; large fall runs Sept-Nov along beaches"},
    {"name": "Sand fleas (mole crabs)", "months": [4,5,6,7,8,9,10], "coast": "east",
     "note": "Dig in wet sand at surf's edge during wave retreat"},
    {"name": "Shrimp", "months": [4,5,6,7,8,9,10,11], "coast": "east",
     "note": "Peak summer/fall; run on outgoing tides at night"},
    {"name": "Fiddler crabs", "months": [4,5,6,7,8,9,10], "coast": "east",
     "note": "Found in mud flats at low tide — top sheepshead bait"},
    {"name": "Bloodworms", "months": [1,2,3,4,5,6,7,8,9,10,11,12], "coast": "east",
     "note": "Available year-round at bait shops; pricey but effective"},
    {"name": "Cut bait (spot/croaker)", "months": [5,6,7,8,9,10], "coast": "east",
     "note": "Catch small spot/croaker on Sabiki rigs for fresh cut bait"},
    {"name": "Finger mullet", "months": [6,7,8,9,10,11], "coast": "east",
     "note": "Cast net along shore; top live bait for predator species"},
    {"name": "Silversides", "months": [3,4,5,6,7,8,9,10,11], "coast": "east",
     "note": "Tiny baitfish in surf zone — match with small spoons/jigs"},
    {"name": "Blue crab", "months": [4,5,6,7,8,9,10,11], "coast": "east",
     "note": "Cut in half for drum/sheepshead; chicken necks to trap"},

    # Pacific
    {"name": "Sand crabs", "months": [3,4,5,6,7,8,9,10,11], "coast": "west",
     "note": "Dig at wave line for prime surfperch and corbina bait"},
    {"name": "Mussels", "months": [1,2,3,4,5,6,7,8,9,10,11,12], "coast": "west",
     "note": "Pry from rocks at low tide — excellent all-purpose bait"},
    {"name": "Anchovies", "months": [3,4,5,6,7,8,9,10,11], "coast": "west",
     "note": "Buy live or use Sabiki rig; top live bait for gamefish"},
    {"name": "Sardines", "months": [4,5,6,7,8,9,10], "coast": "west",
     "note": "Available live at bait barges; great for halibut and bass"},
    {"name": "Squid", "months": [1,2,3,4,5,10,11,12], "coast": "west",
     "note": "Market squid runs in winter; cut strips or use whole"},
    {"name": "Ghost shrimp", "months": [1,2,3,4,5,6,7,8,9,10,11,12], "coast": "west",
     "note": "Pump from mudflats at low tide; perch and surfperch love them"},
    {"name": "Mackerel (bait)", "months": [4,5,6,7,8,9,10], "coast": "west",
     "note": "Catch on Sabiki rigs at piers; cut for halibut and bass"},
    {"name": "Grunion", "months": [3,4,5,6,7,8], "coast": "west",
     "note": "Beach spawning runs on full/new moon nights — check regulations"},
]


def build_natural_bait_chart(month: int, coast: str = "east") -> List[Dict[str, str]]:
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
        available.append({
            "name": bait["name"],
            "note": bait["note"],
            "status": status,
        })
    # Sort: available first, then off-season
    available.sort(key=lambda x: (0 if x["status"] == "available" else 1, x["name"]))
    return available


_MONTH_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

# Curated headline species shown on the year-round calendar, per region.
# These are the iconic target species anglers actually plan trips around —
# independent of current day-to-day conditions.
_NOTABLE_SPECIES_BY_REGION: Dict[str, List[str]] = {
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
    species_list: List[Dict[str, Any]],
    location: Optional[Dict[str, Any]] = None,
    fish_region: str = "",
) -> List[Dict[str, Any]]:
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
    # Build a name → SPECIES_DB entry lookup
    db_map: Dict[str, Dict[str, Any]] = {sp["name"]: sp for sp in SPECIES_DB}

    # Get regional water temps (12 months) for temp filtering
    monthly_temps: Dict[int, float] = {}
    if location:
        monthly_temps = get_monthly_water_temps(location)

    # Determine which species to show on the calendar
    if fish_region and fish_region in _NOTABLE_SPECIES_BY_REGION:
        source = [{"name": n} for n in _NOTABLE_SPECIES_BY_REGION[fish_region]]
    else:
        source = species_list[:10]

    calendar: List[Dict[str, Any]] = []
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

        calendar.append({
            "name": ranked_sp["name"],
            "months": months,
        })

    return calendar
