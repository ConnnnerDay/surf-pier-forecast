"""Saltwater fishing regulations by state.

Contains minimum size limits, bag limits, and season closures for common
species.  Sourced from state fish & wildlife agency regulations.

IMPORTANT: These are approximate / educational references.  Regulations
change frequently.  Anglers should always verify current rules with their
state's official fish & wildlife agency before fishing.

Structure:
    REGS[state_abbrev][species_key] = {
        "min_size": "14 in TL",       # minimum size to keep (or "")
        "bag_limit": "5 per day",      # daily bag limit (or "")
        "season": "",                  # season notes / closures (or "")
        "notes": "",                   # extra notes (or "")
    }

Species keys are lowercase canonical names that map from the full species
name in SPECIES_DB (see ``lookup_regulation``).
"""

from __future__ import annotations

from typing import Any, Dict, Optional


# ---------------------------------------------------------------------------
# Species name normalization: full SPECIES_DB name -> regulation key
# ---------------------------------------------------------------------------

_NAME_MAP: Dict[str, str] = {
    # Atlantic / Gulf
    "Red drum (puppy drum)": "red_drum",
    "Black drum": "black_drum",
    "Speckled trout (spotted seatrout)": "spotted_seatrout",
    "Flounder (summer flounder)": "summer_flounder",
    "Southern flounder": "southern_flounder",
    "Gulf flounder": "gulf_flounder",
    "Bluefish": "bluefish",
    "Striped bass (rockfish)": "striped_bass",
    "Sheepshead": "sheepshead",
    "Pompano": "pompano",
    "Black sea bass": "black_sea_bass",
    "Cobia": "cobia",
    "King mackerel (kingfish)": "king_mackerel",
    "Spanish mackerel": "spanish_mackerel",
    "Red snapper": "red_snapper",
    "Gray triggerfish": "gray_triggerfish",
    "Triggerfish (gray)": "gray_triggerfish",
    "Greater amberjack": "greater_amberjack",
    "Mahi-mahi (dolphinfish)": "mahi_mahi",
    "Tarpon": "tarpon",
    "Snook": "snook",
    "Permit": "permit",
    "Whiting (sea mullet, kingfish)": "whiting",
    "Spot": "spot",
    "Atlantic croaker": "croaker",
    "Blacktip shark": "shark_blacktip",
    "Bull shark": "shark_bull",
    "Red grouper": "red_grouper",
    "Gag grouper": "gag_grouper",
    "Mangrove snapper (gray snapper)": "mangrove_snapper",
    "Yellowtail snapper": "yellowtail_snapper",
    "Tripletail": "tripletail",
    # Pacific
    "California halibut": "ca_halibut",
    "Lingcod": "lingcod",
    "Vermilion rockfish": "rockfish",
    "Blue rockfish": "rockfish",
    "Copper rockfish": "rockfish",
    "Grass rockfish": "rockfish",
    "Cabezon": "cabezon",
    "Kelp bass (calico bass)": "kelp_bass",
    "Sand bass (barred sand bass)": "sand_bass",
    "Barred surfperch": "surfperch",
    "Redtail surfperch": "surfperch",
    "Calico surfperch": "surfperch",
    "Rubberlip seaperch": "surfperch",
    "Walleye surfperch": "surfperch",
    "California sheephead": "ca_sheephead",
    "White seabass": "white_seabass",
    "Yellowtail (California yellowtail)": "yellowtail",
    "Leopard shark": "leopard_shark",
    "Corbina": "corbina",
    "Spotfin croaker": "spotfin_croaker",
    "Yellowfin croaker": "yellowfin_croaker",
    "Starry flounder": "starry_flounder",
    "Dungeness crab (from pier)": "dungeness_crab",
    # Hawaii
    "Giant trevally (ulua)": "ulua",
    "Bluefin trevally (omilu)": "omilu",
    "Papio (juvenile jack)": "papio",
    "Bonefish (oio)": "oio",
    "Moi (Pacific threadfin)": "moi",
}


# ---------------------------------------------------------------------------
# State regulations database
# ---------------------------------------------------------------------------

REGS: Dict[str, Dict[str, Dict[str, str]]] = {
    # ── North Carolina ────────────────────────────────────────────────────
    "NC": {
        "red_drum": {
            "min_size": "18 in TL",
            "bag_limit": "1 per day",
            "season": "Open year-round",
            "notes": "Slot limit: 18-27 in; only 1 fish over 27 in per year with tag",
        },
        "black_drum": {
            "min_size": "14 in TL",
            "bag_limit": "10 per day",
            "season": "Open year-round",
            "notes": "",
        },
        "spotted_seatrout": {
            "min_size": "14 in TL",
            "bag_limit": "4 per day",
            "season": "Open year-round",
            "notes": "Only 1 fish over 24 in allowed",
        },
        "summer_flounder": {
            "min_size": "15 in TL",
            "bag_limit": "4 per day",
            "season": "Open year-round",
            "notes": "",
        },
        "southern_flounder": {
            "min_size": "15 in TL",
            "bag_limit": "4 per day (combined flounder)",
            "season": "Gigging closed Oct 16 - Dec 31",
            "notes": "",
        },
        "bluefish": {
            "min_size": "No minimum",
            "bag_limit": "3 per day",
            "season": "Open year-round",
            "notes": "",
        },
        "striped_bass": {
            "min_size": "18 in TL",
            "bag_limit": "1 per day",
            "season": "Varies by area; check current regulations",
            "notes": "Coastal slot: 18-27 in. Joint waters vary.",
        },
        "sheepshead": {
            "min_size": "No minimum",
            "bag_limit": "No limit",
            "season": "Open year-round",
            "notes": "",
        },
        "pompano": {
            "min_size": "No minimum",
            "bag_limit": "No limit",
            "season": "Open year-round",
            "notes": "",
        },
        "cobia": {
            "min_size": "33 in FL",
            "bag_limit": "1 per day",
            "season": "Open year-round",
            "notes": "Fork length measurement",
        },
        "king_mackerel": {
            "min_size": "24 in FL",
            "bag_limit": "3 per day",
            "season": "Open year-round",
            "notes": "",
        },
        "spanish_mackerel": {
            "min_size": "12 in FL",
            "bag_limit": "15 per day",
            "season": "Open year-round",
            "notes": "",
        },
        "black_sea_bass": {
            "min_size": "13 in TL",
            "bag_limit": "7 per day",
            "season": "Check current season dates",
            "notes": "",
        },
        "spot": {
            "min_size": "No minimum",
            "bag_limit": "No limit",
            "season": "Open year-round",
            "notes": "",
        },
        "croaker": {
            "min_size": "No minimum",
            "bag_limit": "No limit",
            "season": "Open year-round",
            "notes": "",
        },
        "whiting": {
            "min_size": "No minimum",
            "bag_limit": "No limit",
            "season": "Open year-round",
            "notes": "",
        },
        "mahi_mahi": {
            "min_size": "20 in FL",
            "bag_limit": "10 per day",
            "season": "Open year-round",
            "notes": "",
        },
        "red_snapper": {
            "min_size": "16 in TL",
            "bag_limit": "1 per day",
            "season": "Limited season; check NOAA dates",
            "notes": "",
        },
    },

    # ── South Carolina ────────────────────────────────────────────────────
    "SC": {
        "red_drum": {
            "min_size": "15 in TL",
            "bag_limit": "3 per day",
            "season": "Open year-round",
            "notes": "Slot limit: 15-23 in; only 1 may exceed 23 in",
        },
        "spotted_seatrout": {
            "min_size": "14 in TL",
            "bag_limit": "10 per day",
            "season": "Open year-round",
            "notes": "",
        },
        "southern_flounder": {
            "min_size": "15 in TL",
            "bag_limit": "5 per day",
            "season": "Check current season",
            "notes": "",
        },
        "sheepshead": {
            "min_size": "10 in",
            "bag_limit": "10 per day",
            "season": "Open year-round",
            "notes": "",
        },
        "bluefish": {
            "min_size": "No minimum",
            "bag_limit": "15 per day",
            "season": "Open year-round",
            "notes": "",
        },
        "cobia": {
            "min_size": "33 in FL",
            "bag_limit": "1 per day",
            "season": "Open year-round",
            "notes": "",
        },
        "king_mackerel": {
            "min_size": "24 in FL",
            "bag_limit": "3 per day",
            "season": "Open year-round",
            "notes": "",
        },
        "black_drum": {
            "min_size": "14 in TL",
            "bag_limit": "5 per day",
            "season": "Open year-round",
            "notes": "",
        },
    },

    # ── Florida ───────────────────────────────────────────────────────────
    "FL": {
        "red_drum": {
            "min_size": "18 in TL",
            "bag_limit": "1 per day",
            "season": "Open year-round",
            "notes": "Slot limit: 18-27 in; no oversized harvest",
        },
        "spotted_seatrout": {
            "min_size": "15 in TL",
            "bag_limit": "3 per day (varies by region)",
            "season": "Closures Nov-Dec in some regions",
            "notes": "Only 1 over 20 in allowed; region-specific rules apply",
        },
        "snook": {
            "min_size": "28 in TL",
            "bag_limit": "1 per day",
            "season": "Closed Dec 1 - end Feb; closed Jun 1 - Aug 31",
            "notes": "Slot limit: 28-33 in; snook stamp required",
        },
        "tarpon": {
            "min_size": "75 in FL (if kept)",
            "bag_limit": "1 per day (tag required)",
            "season": "Open year-round (catch & release encouraged)",
            "notes": "Tarpon tag ($51.50) required to possess; most are released",
        },
        "pompano": {
            "min_size": "11 in FL",
            "bag_limit": "6 per day",
            "season": "Open year-round",
            "notes": "",
        },
        "sheepshead": {
            "min_size": "12 in TL",
            "bag_limit": "8 per day",
            "season": "Open year-round",
            "notes": "",
        },
        "cobia": {
            "min_size": "33 in FL",
            "bag_limit": "1 per day",
            "season": "Open year-round",
            "notes": "",
        },
        "mangrove_snapper": {
            "min_size": "10 in TL",
            "bag_limit": "5 per day",
            "season": "Open year-round",
            "notes": "",
        },
        "red_snapper": {
            "min_size": "16 in TL",
            "bag_limit": "2 per day",
            "season": "Very limited season; check FWC",
            "notes": "",
        },
        "gag_grouper": {
            "min_size": "24 in TL",
            "bag_limit": "2 per day",
            "season": "Check current season (Gulf vs Atlantic)",
            "notes": "",
        },
        "permit": {
            "min_size": "22 in FL",
            "bag_limit": "2 per day",
            "season": "Open year-round",
            "notes": "",
        },
        "bluefish": {
            "min_size": "No minimum",
            "bag_limit": "10 per day",
            "season": "Open year-round",
            "notes": "",
        },
        "king_mackerel": {
            "min_size": "24 in FL",
            "bag_limit": "2 per day",
            "season": "Open year-round",
            "notes": "",
        },
        "mahi_mahi": {
            "min_size": "20 in FL",
            "bag_limit": "10 per day",
            "season": "Open year-round",
            "notes": "",
        },
        "tripletail": {
            "min_size": "18 in TL",
            "bag_limit": "2 per day",
            "season": "Open year-round",
            "notes": "",
        },
        "yellowtail_snapper": {
            "min_size": "12 in TL",
            "bag_limit": "10 per day",
            "season": "Open year-round",
            "notes": "",
        },
    },

    # ── Georgia ───────────────────────────────────────────────────────────
    "GA": {
        "red_drum": {
            "min_size": "14 in TL",
            "bag_limit": "5 per day",
            "season": "Open year-round",
            "notes": "Slot limit: 14-23 in; 1 per day may exceed 23 in",
        },
        "spotted_seatrout": {
            "min_size": "14 in TL",
            "bag_limit": "8 per day",
            "season": "Open year-round",
            "notes": "",
        },
        "southern_flounder": {
            "min_size": "14 in TL",
            "bag_limit": "5 per day",
            "season": "Open year-round",
            "notes": "",
        },
        "sheepshead": {
            "min_size": "No minimum",
            "bag_limit": "15 per day",
            "season": "Open year-round",
            "notes": "",
        },
    },

    # ── Virginia ──────────────────────────────────────────────────────────
    "VA": {
        "red_drum": {
            "min_size": "18 in TL",
            "bag_limit": "3 per day",
            "season": "Open year-round",
            "notes": "Slot limit: 18-26 in; 1 per day may be citation size",
        },
        "striped_bass": {
            "min_size": "20 in TL",
            "bag_limit": "1 per day",
            "season": "Check current season & slot limits",
            "notes": "Varies by area; slot limits apply",
        },
        "spotted_seatrout": {
            "min_size": "14 in TL",
            "bag_limit": "5 per day",
            "season": "Open year-round",
            "notes": "",
        },
        "summer_flounder": {
            "min_size": "19.5 in TL",
            "bag_limit": "4 per day",
            "season": "Check current season dates",
            "notes": "",
        },
        "cobia": {
            "min_size": "40 in TL",
            "bag_limit": "1 per day",
            "season": "Jun 15 - Sep 15",
            "notes": "",
        },
        "bluefish": {
            "min_size": "No minimum",
            "bag_limit": "3 per day",
            "season": "Open year-round",
            "notes": "",
        },
        "black_sea_bass": {
            "min_size": "12.5 in TL",
            "bag_limit": "15 per day",
            "season": "Check current season",
            "notes": "",
        },
        "sheepshead": {
            "min_size": "No minimum",
            "bag_limit": "4 per day",
            "season": "Open year-round",
            "notes": "",
        },
    },

    # ── New Jersey ────────────────────────────────────────────────────────
    "NJ": {
        "striped_bass": {
            "min_size": "28 in TL",
            "bag_limit": "1 per day",
            "season": "Check current NJ DEP regulations",
            "notes": "Slot limit applies; varies by season",
        },
        "summer_flounder": {
            "min_size": "18 in TL",
            "bag_limit": "3 per day",
            "season": "Check current season dates",
            "notes": "",
        },
        "bluefish": {
            "min_size": "No minimum",
            "bag_limit": "3 per day",
            "season": "Open year-round",
            "notes": "",
        },
        "black_sea_bass": {
            "min_size": "13 in TL",
            "bag_limit": "10 per day",
            "season": "Check current season",
            "notes": "",
        },
    },

    # ── New York ──────────────────────────────────────────────────────────
    "NY": {
        "striped_bass": {
            "min_size": "28 in TL",
            "bag_limit": "1 per day",
            "season": "Check current DEC regulations",
            "notes": "Slot limit applies",
        },
        "summer_flounder": {
            "min_size": "19 in TL",
            "bag_limit": "4 per day",
            "season": "Check current season dates",
            "notes": "",
        },
        "bluefish": {
            "min_size": "No minimum",
            "bag_limit": "3 per day",
            "season": "Open year-round",
            "notes": "",
        },
        "black_sea_bass": {
            "min_size": "15 in TL",
            "bag_limit": "7 per day",
            "season": "Check current season",
            "notes": "",
        },
    },

    # ── Maryland ──────────────────────────────────────────────────────────
    "MD": {
        "striped_bass": {
            "min_size": "19 in TL",
            "bag_limit": "2 per day",
            "season": "Varies by season and area",
            "notes": "Chesapeake Bay and coastal rules differ",
        },
        "summer_flounder": {
            "min_size": "18 in TL",
            "bag_limit": "4 per day",
            "season": "Check current season",
            "notes": "",
        },
        "bluefish": {
            "min_size": "No minimum",
            "bag_limit": "3 per day",
            "season": "Open year-round",
            "notes": "",
        },
    },

    # ── Texas ─────────────────────────────────────────────────────────────
    "TX": {
        "red_drum": {
            "min_size": "20 in TL",
            "bag_limit": "3 per day",
            "season": "Open year-round",
            "notes": "Slot limit: 20-28 in; 1 over 28 in with tag",
        },
        "spotted_seatrout": {
            "min_size": "15 in TL",
            "bag_limit": "5 per day",
            "season": "Open year-round",
            "notes": "Only 1 over 25 in allowed",
        },
        "southern_flounder": {
            "min_size": "15 in TL",
            "bag_limit": "5 per day",
            "season": "Nov 1 - Dec 14 closed",
            "notes": "",
        },
        "sheepshead": {
            "min_size": "15 in TL",
            "bag_limit": "5 per day",
            "season": "Open year-round",
            "notes": "",
        },
        "king_mackerel": {
            "min_size": "24 in FL",
            "bag_limit": "3 per day",
            "season": "Open year-round",
            "notes": "",
        },
        "cobia": {
            "min_size": "37 in FL",
            "bag_limit": "1 per day",
            "season": "Open year-round",
            "notes": "",
        },
        "snook": {
            "min_size": "24 in TL",
            "bag_limit": "1 per day",
            "season": "Open year-round",
            "notes": "Lower Laguna Madre only",
        },
        "tarpon": {
            "min_size": "85 in (if kept)",
            "bag_limit": "1 per day (tag required)",
            "season": "Open year-round",
            "notes": "Catch & release encouraged; $100 tag to harvest",
        },
    },

    # ── Alabama ───────────────────────────────────────────────────────────
    "AL": {
        "red_drum": {
            "min_size": "16 in TL",
            "bag_limit": "3 per day",
            "season": "Open year-round",
            "notes": "Slot limit: 16-26 in; 1 over 26 in allowed",
        },
        "spotted_seatrout": {
            "min_size": "14 in TL",
            "bag_limit": "6 per day",
            "season": "Open year-round",
            "notes": "",
        },
        "southern_flounder": {
            "min_size": "14 in TL",
            "bag_limit": "5 per day",
            "season": "Check current season",
            "notes": "",
        },
        "red_snapper": {
            "min_size": "16 in TL",
            "bag_limit": "2 per day",
            "season": "Very limited season; check ADCNR",
            "notes": "",
        },
    },

    # ── Mississippi ───────────────────────────────────────────────────────
    "MS": {
        "red_drum": {
            "min_size": "18 in TL",
            "bag_limit": "3 per day",
            "season": "Open year-round",
            "notes": "Slot limit: 18-30 in",
        },
        "spotted_seatrout": {
            "min_size": "14 in TL",
            "bag_limit": "7 per day",
            "season": "Open year-round",
            "notes": "",
        },
    },

    # ── Louisiana ─────────────────────────────────────────────────────────
    "LA": {
        "red_drum": {
            "min_size": "16 in TL",
            "bag_limit": "5 per day",
            "season": "Open year-round",
            "notes": "Slot limit: 16-27 in",
        },
        "spotted_seatrout": {
            "min_size": "13 in TL",
            "bag_limit": "15 per day",
            "season": "Open year-round",
            "notes": "",
        },
        "southern_flounder": {
            "min_size": "12 in TL",
            "bag_limit": "10 per day",
            "season": "Check current season",
            "notes": "",
        },
    },

    # ── California ────────────────────────────────────────────────────────
    "CA": {
        "ca_halibut": {
            "min_size": "22 in TL",
            "bag_limit": "5 per day",
            "season": "Open year-round",
            "notes": "",
        },
        "lingcod": {
            "min_size": "22 in TL",
            "bag_limit": "2 per day",
            "season": "Check current season (varies by district)",
            "notes": "",
        },
        "rockfish": {
            "min_size": "Varies by species (most 10 in)",
            "bag_limit": "10 per day (combined RCG complex)",
            "season": "Open season varies by district; check CDFW",
            "notes": "Rockfish, cabezon, and greenling combined limit of 10",
        },
        "cabezon": {
            "min_size": "15 in TL",
            "bag_limit": "10 combined (RCG complex)",
            "season": "Check current district season",
            "notes": "Included in RCG complex limit",
        },
        "kelp_bass": {
            "min_size": "14 in TL",
            "bag_limit": "5 per day",
            "season": "Open year-round",
            "notes": "",
        },
        "sand_bass": {
            "min_size": "14 in TL",
            "bag_limit": "5 per day (combined with kelp & spotted bass)",
            "season": "Open year-round",
            "notes": "Mar 1 - Jun 30 closed in some areas (spawning)",
        },
        "ca_sheephead": {
            "min_size": "12 in TL",
            "bag_limit": "5 per day",
            "season": "Open year-round",
            "notes": "",
        },
        "white_seabass": {
            "min_size": "28 in TL",
            "bag_limit": "3 per day",
            "season": "Open year-round",
            "notes": "",
        },
        "yellowtail": {
            "min_size": "24 in FL",
            "bag_limit": "10 per day",
            "season": "Open year-round",
            "notes": "",
        },
        "surfperch": {
            "min_size": "No minimum",
            "bag_limit": "20 per day (combined surfperch)",
            "season": "Open year-round",
            "notes": "All surfperch species combined",
        },
        "corbina": {
            "min_size": "No minimum",
            "bag_limit": "10 per day",
            "season": "Open year-round",
            "notes": "",
        },
        "spotfin_croaker": {
            "min_size": "No minimum",
            "bag_limit": "10 per day",
            "season": "Open year-round",
            "notes": "",
        },
        "yellowfin_croaker": {
            "min_size": "No minimum",
            "bag_limit": "10 per day",
            "season": "Open year-round",
            "notes": "",
        },
        "leopard_shark": {
            "min_size": "36 in TL",
            "bag_limit": "3 per day",
            "season": "Open year-round",
            "notes": "",
        },
        "starry_flounder": {
            "min_size": "No minimum",
            "bag_limit": "No limit",
            "season": "Open year-round",
            "notes": "",
        },
        "dungeness_crab": {
            "min_size": "5.75 in (shoulder width)",
            "bag_limit": "10 per day",
            "season": "Nov - Jun (varies); check CDFW",
            "notes": "Males only; must use measuring gauge",
        },
    },

    # ── Oregon ────────────────────────────────────────────────────────────
    "OR": {
        "lingcod": {
            "min_size": "22 in TL",
            "bag_limit": "2 per day",
            "season": "Check ODFW for current season",
            "notes": "",
        },
        "rockfish": {
            "min_size": "Varies by species",
            "bag_limit": "5 per day (marine fish bag)",
            "season": "Check ODFW for current season",
            "notes": "Part of the combined marine fish daily bag",
        },
        "surfperch": {
            "min_size": "No minimum",
            "bag_limit": "15 per day (combined)",
            "season": "Open year-round",
            "notes": "",
        },
        "cabezon": {
            "min_size": "16 in TL",
            "bag_limit": "1 per day",
            "season": "Check ODFW season",
            "notes": "",
        },
        "starry_flounder": {
            "min_size": "No minimum",
            "bag_limit": "5 per day",
            "season": "Open year-round",
            "notes": "",
        },
        "dungeness_crab": {
            "min_size": "5.75 in (shoulder width)",
            "bag_limit": "12 per day",
            "season": "Dec 1 - Aug 14 (varies)",
            "notes": "Males only; Oregon shellfish license required",
        },
    },

    # ── Washington ────────────────────────────────────────────────────────
    "WA": {
        "lingcod": {
            "min_size": "26 in TL (varies by area)",
            "bag_limit": "2 per day",
            "season": "Check WDFW for current season",
            "notes": "",
        },
        "rockfish": {
            "min_size": "Varies by species",
            "bag_limit": "7 per day (varies)",
            "season": "Check WDFW for current season",
            "notes": "Canary and yelloweye rockfish must be released",
        },
        "surfperch": {
            "min_size": "No minimum",
            "bag_limit": "15 per day",
            "season": "Open year-round",
            "notes": "",
        },
        "dungeness_crab": {
            "min_size": "6.25 in (shoulder width)",
            "bag_limit": "5 per day",
            "season": "Varies by area; check WDFW",
            "notes": "Males only; Dungeness crab endorsement required",
        },
    },

    # ── Hawaii ────────────────────────────────────────────────────────────
    "HI": {
        "ulua": {
            "min_size": "10 in FL",
            "bag_limit": "No bag limit",
            "season": "Open year-round",
            "notes": "Very large specimens (100+ lbs) are rare and treasured",
        },
        "omilu": {
            "min_size": "10 in FL",
            "bag_limit": "No bag limit",
            "season": "Open year-round",
            "notes": "",
        },
        "papio": {
            "min_size": "10 in FL",
            "bag_limit": "No bag limit",
            "season": "Open year-round",
            "notes": "Papio (under 10 lbs) very popular family fish",
        },
        "oio": {
            "min_size": "No minimum",
            "bag_limit": "No bag limit",
            "season": "Open year-round",
            "notes": "Catch & release encouraged in many areas",
        },
        "moi": {
            "min_size": "11 in TL",
            "bag_limit": "15 per day",
            "season": "Closed Jun-Sep in some areas",
            "notes": "Once reserved for Hawaiian royalty; highly prized",
        },
    },
}


def lookup_regulation(
    species_name: str, state: str,
) -> Optional[Dict[str, str]]:
    """Look up fishing regulations for a species in a state.

    Returns a dict with min_size, bag_limit, season, notes -- or None
    if no regulation data is available.
    """
    key = _NAME_MAP.get(species_name)
    if not key:
        return None
    state_regs = REGS.get(state)
    if not state_regs:
        return None
    return state_regs.get(key)
