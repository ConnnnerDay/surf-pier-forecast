"""Species database, scoring logic, and fishing knowledge."""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from locations import get_monthly_water_temps
from regulations import classify_legality, lookup_regulation, should_hide_from_forecast
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
    "Mahi-mahi (dolphinfish)",
    "Wahoo",
    "Blackfin tuna",
    "Yellowfin tuna",
    "Sailfish",
    "Blue marlin",
    "White marlin",
    "Skipjack tuna",
    "Bigeye tuna",
    "Albacore tuna",
    "Frigate mackerel",
    "Blueline tilefish",
    "Golden tilefish",
    "Snowy grouper",
    "Wreckfish",
    "Tilefish (blueline juvenile/grey)",
    "Tilefish (golden juvenile)",
    "Barrelfish",
    "Blackbelly rosefish",
    "Misty grouper",
    "Yellowedge grouper",
    "Queen snapper",
    "Silk snapper",
    "Greater amberjack",
    "Almaco jack",
    "Almaco jack (large adult)",
    "Lesser amberjack",
    "Rainbow runner",
    "Shortfin mako shark",
    "Thresher shark",
    "Tiger shark",
    "Ocean sunfish (mola mola)",
    "Swordfish",
    "Black marlin",
    "Sablefish (black cod)",
    "Atlantic halibut",
    "Pacific halibut",
    "Atlantic bluefin tuna",
    "Yelloweye rockfish",
    "Striped marlin",
    "Opah (moonfish)",
    "Opakapaka (pink snapper)",
    "Onaga (long-tail red snapper)",
    "Hapu'upu'u (Hawaiian grouper)",
    "Lehi (amberjack / silvermouth jack)",
    "Acadian redfish (ocean perch)",
    "Cusk",
    "Rougheye rockfish",
    "Longspine thornyhead",
    "Shortspine thornyhead",
    "Sixgill shark",
    "Pacific ocean perch",
    "Oceanic whitetip shark",
    "Silky shark",
    "Common thresher shark",
    "Shortbill spearfish",
    "Splitnose rockfish",
    "Greenstriped rockfish",
    "Rougheye rockfish",
}

# Species best caught from a pier or jetty (structure-dependent).
_PIER_SPECIES: set = {
    "Sheepshead",
    "Tautog (blackfish)",
    "Black sea bass",
    "Spadefish (Atlantic)",
    "Triggerfish (gray)",
    "Lookdown",
    "Mangrove snapper (gray snapper)",
    "Hogfish",
    "Planehead filefish",
    "Northern puffer (blowfish)",
    "Bermuda chub (sea chub)",
    "Sergeant major (damselfish)",
    "Cunner (bergall)",
    "Slippery dick (wrasse)",
    "Puddingwife (wrasse)",
    "Striped blenny",
    "Feather blenny",
    "Ocean triggerfish",
    "Queen triggerfish",
    "Scrawled filefish",
    "Orange filefish",
    "Bandtail puffer",
    "Checkered puffer",
    "Striped burrfish (spiny boxfish)",
    "Scrawled cowfish",
    "Blue tang (surgeonfish)",
    "Gray angelfish",
    "Spotfin butterflyfish",
    "Doctorfish (tang)",
    "Squirrelfish",
    "Porkfish",
    "Smooth trunkfish",
    "Honeycomb cowfish",
    "Menpachi (soldierfish)",
    "Aholehole (Hawaiian flagtail)",
    "Kelp bass (calico bass)",
    "Sand bass (barred sand bass)",
    "California sheephead",
    "Opaleye",
    "Halfmoon (Catalina perch)",
    "Kelp greenling",
    "Rock greenling",
    "Pacific sanddab",
    "English sole",
    "Black-and-yellow rockfish",
    "Surf smelt (Pacific)",
    "Gray triggerfish",
    "Uku (gray snapper / jobfish)",
    "Brown rockfish",
    "Rosy rockfish",
    "Nenue (Hawaiian chub)",
    "Kala (unicornfish)",
    "Manini (convict tang)",
    "Kumu (whitesaddle goatfish)",
    "Akule (bigeye scad)",
    "Opelu (mackerel scad)",
    "Taape (bluestripe snapper)",
    "Moano (goatfish)",
    "Uhu (parrotfish)",
    "Nenue (Hawaiian chub)",
    "Shiner perch",
    "Pile perch",
    "Rainbow seaperch",
    "Squarespot rockfish",
    "Pygmy rockfish",
    "Spotted sand bass",
    "Atlantic silverside",
    "Southern puffer",
    "Gulf toadfish",
    "Sand drum",
}

# Species primarily caught from the surf zone.
_SURF_SPECIES: set = {
    "Red drum (puppy drum)",
    "Pompano",
    "Whiting (sea mullet, kingfish)",
    "Southern kingfish (ground mullet)",
    "Gulf kingfish (gulf whiting)",
    "Northern kingfish",
    "Barred surfperch",
    "Redtail surfperch",
    "Calico surfperch",
    "Walleye surfperch",
    "Rubberlip seaperch",
    "Corbina",
    "Spotfin croaker",
    "Yellowfin croaker",
    "Leopard shark",
    "Shovelnose guitarfish",
    "Moi (Pacific threadfin)",
    "Bonefish (oio)",
    "Pink salmon (humpy)",
    "Chum salmon (dog salmon)",
    "Sand drum",
    "Striped seaperch",
    "Pink seaperch",
    "Painted greenling",
    "Monkeyface prickleback",
}

# Species primarily caught from jetties and rock structures (inlet mouths,
# rocky points).  Heavily overlaps with pier but includes inlet-mouth
# species like snook and redfish that patrol jetty bases.
_JETTY_SPECIES: set = {
    "Sheepshead",
    "Tautog (blackfish)",
    "Black sea bass",
    "Spadefish (Atlantic)",
    "Triggerfish (gray)",
    "Lookdown",
    "Mangrove snapper (gray snapper)",
    "Hogfish",
    "Cunner (bergall)",
    "Kelp bass (calico bass)",
    "Sand bass (barred sand bass)",
    "California sheephead",
    "Kelp greenling",
    "Rock greenling",
    "Cabezon",
    "Lingcod",
    "Striped bass (rockfish)",
    "Snook",
    "Red drum (puppy drum)",
    "Jack crevalle",
    "Tarpon",
    "Cobia",
}

# Species commonly caught from bridges and causeways over tidal channels.
# Current-driven feeding is the defining characteristic — snook, sheepshead,
# and permit hold on pilings; migratory pelagics pass through inlets.
_BRIDGE_SPECIES: set = {
    "Sheepshead",
    "Snook",
    "Permit",
    "Jack crevalle",
    "Mangrove snapper (gray snapper)",
    "Lookdown",
    "Cobia",
    "Spanish mackerel",
    "Tarpon",
    "Red drum (puppy drum)",
    "Black drum",
    "Flounder (summer flounder)",
    "Southern flounder",
    "Striped bass (rockfish)",
    "Black sea bass",
    "Tautog (blackfish)",
    "Spadefish (Atlantic)",
    "Blue runner (hardtail)",
    "Bluefish",
}

# Species accessible by wading — shallow inshore/flats fish only.
# Excludes anything that requires a boat or depth to reach.
_WADE_SPECIES: set = {
    "Speckled trout (spotted seatrout)",
    "Red drum (puppy drum)",
    "Southern flounder",
    "Flounder (summer flounder)",
    "Gulf flounder",
    "Snook",
    "Ladyfish",
    "Sand seatrout (white trout)",
    "Silver seatrout",
    "Gray trout (weakfish)",
    "Black drum",
    "Bonefish (oio)",
    "Permit",
    "Corbina",
    "Spotfin croaker",
    "Yellowfin croaker",
    "Barred surfperch",
    "Redtail surfperch",
    "Walleye surfperch",
    "White perch",
    "Striped bass (rockfish)",
}

# Species reachable by kayak — inshore + nearshore.  Wider range than wade
# because kayaks can reach structure, nearshore reefs, and tidal creeks.
_KAYAK_SPECIES: set = {
    "Speckled trout (spotted seatrout)",
    "Red drum (puppy drum)",
    "Southern flounder",
    "Flounder (summer flounder)",
    "Gulf flounder",
    "Snook",
    "Tarpon",
    "Cobia",
    "Ladyfish",
    "Jack crevalle",
    "Spanish mackerel",
    "False albacore (little tunny)",
    "Sheepshead",
    "Black sea bass",
    "Mangrove snapper (gray snapper)",
    "Sand seatrout (white trout)",
    "Striped bass (rockfish)",
    "Bluefish",
    "Black drum",
    "Permit",
    "Tripletail",
    "Red snapper",
    "Gag grouper",
    "Red grouper",
    "King mackerel (kingfish)",
    "Yellowtail (California yellowtail)",
    "White seabass",
    "Kelp bass (calico bass)",
    "Lingcod",
    "Cabezon",
}

# Species targeted on chartered/head-boat trips — offshore and nearshore
# bottom structure focused.  Includes all offshore-only species.
_CHARTER_SPECIES: set = {
    "Mahi-mahi (dolphinfish)",
    "Wahoo",
    "Blackfin tuna",
    "Yellowfin tuna",
    "Sailfish",
    "Blue marlin",
    "White marlin",
    "Skipjack tuna",
    "Bigeye tuna",
    "Albacore tuna",
    "King mackerel (kingfish)",
    "Cobia",
    "Greater amberjack",
    "Almaco jack",
    "Red snapper",
    "Vermilion snapper (beeliner)",
    "Mangrove snapper (gray snapper)",
    "Lane snapper",
    "Yellowtail snapper",
    "Mutton snapper",
    "Gag grouper",
    "Red grouper",
    "Scamp grouper",
    "Black grouper",
    "Blueline tilefish",
    "Golden tilefish",
    "Snowy grouper",
    "Scup (porgy)",
    "Red porgy",
    "White grunt",
    "Hogfish",
    "Spanish mackerel",
    "False albacore (little tunny)",
    "Yellowtail (California yellowtail)",
    "White seabass",
    "Lingcod",
    "Copper rockfish",
    "Vermilion rockfish",
    "Blue rockfish",
    "Cabezon",
    "Pacific bonito",
    "Pacific halibut",
    "Chinook salmon (king salmon)",
    "Coho salmon (silver salmon)",
    "Chum salmon (dog salmon)",
    "Pink salmon (humpy)",
    "Haddock",
    "Atlantic cod",
    "Swordfish",
    "Black marlin",
    "Greater amberjack",
    "Atlantic bluefin tuna",
    "Striped marlin",
    "Opah (moonfish)",
    "Opakapaka (pink snapper)",
    "Onaga (long-tail red snapper)",
    "Hapu'upu'u (Hawaiian grouper)",
    "Lehi (amberjack / silvermouth jack)",
    "Acadian redfish (ocean perch)",
    "Pacific cod",
    "Black rockfish",
    "Yelloweye rockfish",
    "Quillback rockfish",
    "Olive rockfish",
    "China rockfish",
    "Black-and-yellow rockfish",
    "Petrale sole",
    "Steelhead trout (sea-run rainbow)",
    "Sockeye salmon (red salmon)",
    "Cusk",
    "Bocaccio",
    "Widow rockfish",
    "Tiger rockfish",
    "Gopher rockfish",
    "Kelp rockfish",
    "Brown rockfish",
    "Rosy rockfish",
    "Canary rockfish",
    "Starry rockfish",
    "Treefish",
    "Dover sole",
    "Rex sole",
    "Pacific ocean perch",
    "Rougheye rockfish",
    "Longspine thornyhead",
    "Shortspine thornyhead",
    "Pacific spiny dogfish",
    "Sixgill shark",
    "Sevengill shark",
    "Tope shark (soupfin)",
    "Common thresher shark",
    "Shortbill spearfish",
    "Bocaccio",
    "Widow rockfish",
    "Chilipepper rockfish",
    "Dover sole",
    "Petrale sole",
}

# Species well-suited for saltwater fly fishing — must be catchable on a fly
# in typical conditions (sight-fish, surface feeders, or active hunters).
_FLY_SPECIES: set = {
    "Speckled trout (spotted seatrout)",
    "Red drum (puppy drum)",
    "Snook",
    "Tarpon",
    "Bonefish (oio)",
    "Permit",
    "Jack crevalle",
    "Ladyfish",
    "False albacore (little tunny)",
    "Bluefish",
    "Striped bass (rockfish)",
    "Cobia",
    "Spanish mackerel",
    "King mackerel (kingfish)",
    "Pompano",
    "Tripletail",
    "Barracuda",
    "Great barracuda",
    "Giant trevally (ulua)",
    "Bluefin trevally (omilu)",
    "Papio (juvenile jack)",
    "Mahi-mahi (dolphinfish)",
    "Yellowtail (California yellowtail)",
    "White seabass",
    "Kelp bass (calico bass)",
    "Pacific bonito",
    "Corbina",
    "Barred surfperch",
    "Coho salmon (silver salmon)",
    "Pink salmon (humpy)",
    "Chum salmon (dog salmon)",
    "Chinook salmon (king salmon)",
    "Atlantic bonito",
    "Sockeye salmon (red salmon)",
    "Steelhead trout (sea-run rainbow)",
    "Atlantic salmon",
    "Striped marlin",
    "Atlantic bluefin tuna",
    "Uku (gray snapper / jobfish)",
    "Giant trevally (ulua)",
    "Kumu (whitesaddle goatfish)",
    "Sevengill shark",
    "Tope shark (soupfin)",
}

# Species best caught inshore (inlet, marsh, flats).
_INSHORE_SPECIES: set = {
    "Speckled trout (spotted seatrout)",
    "Red drum (puppy drum)",
    "Southern flounder",
    "Flounder (summer flounder)",
    "Gulf flounder",
    "Snook",
    "Tripletail",
    "Ladyfish",
    "Jack crevalle",
    "Tarpon",
    "Permit",
    "Black drum",
    "Sand seatrout (white trout)",
    "Silver seatrout",
    "Gray trout (weakfish)",
    "Striped bass (rockfish)",
    "Striped bass (hybrid)",
    "Hickory shad",
    "American shad",
    "Channel catfish",
    "Blue catfish",
    "Flathead catfish",
    "Largemouth bass",
    "White perch",
    "Yellow perch",
    "Redear sunfish (shellcracker)",
    "Bluegill",
    "Warmouth",
    "American eel",
    "Giant trevally (ulua)",
    "Bluefin trevally (omilu)",
    "Papio (juvenile jack)",
}

# -- Target categories (what you want to catch) --
_BOTTOM_SPECIES: set = {
    "Red drum (puppy drum)",
    "Black drum",
    "Black drum (large bull)",
    "Whiting (sea mullet, kingfish)",
    "Spot",
    "Atlantic croaker",
    "Southern kingfish (ground mullet)",
    "Gulf kingfish (gulf whiting)",
    "Northern kingfish",
    "Flounder (summer flounder)",
    "Southern flounder",
    "Gulf flounder",
    "Windowpane flounder",
    "Fringed flounder",
    "Hogchoker",
    "California halibut",
    "Pacific halibut",
    "Atlantic halibut",
    "Haddock",
    "Atlantic cod",
    "Starry flounder",
    "Pompano",
    "White croaker (tomcod)",
    "Corbina",
    "Spotfin croaker",
    "Yellowfin croaker",
    "Barred surfperch",
    "Redtail surfperch",
    "Calico surfperch",
    "Walleye surfperch",
    "Rubberlip seaperch",
    "Hardhead catfish (sea catfish)",
    "Gafftopsail catfish",
    "Bonefish (oio)",
    "Moi (Pacific threadfin)",
}

_PELAGIC_SPECIES: set = {
    "Bluefish",
    "Spanish mackerel",
    "King mackerel (kingfish)",
    "False albacore (little tunny)",
    "Atlantic bonito",
    "Cobia",
    "Jack crevalle",
    "Blue runner (hardtail)",
    "Mahi-mahi (dolphinfish)",
    "Wahoo",
    "Blackfin tuna",
    "Yellowfin tuna",
    "Skipjack tuna",
    "Bigeye tuna",
    "Albacore tuna",
    "Sailfish",
    "Blue marlin",
    "White marlin",
    "Pacific mackerel (chub mackerel)",
    "Jack mackerel (Spanish jack)",
    "Pacific bonito",
    "Yellowtail (California yellowtail)",
    "White seabass",
    "Ladyfish",
    "Great barracuda",
    "Tarpon",
    "Giant trevally (ulua)",
    "Bluefin trevally (omilu)",
    "Papio (juvenile jack)",
    "Striped bass (rockfish)",
    "Chinook salmon (king salmon)",
    "Coho salmon (silver salmon)",
    "Chum salmon (dog salmon)",
    "Pink salmon (humpy)",
    "Swordfish",
    "Black marlin",
    "Greater amberjack",
    "Wahoo",
    "Tripletail",
    "Atlantic bluefin tuna",
    "Striped marlin",
    "Opah (moonfish)",
    "Chinook salmon (king salmon)",
    "Coho salmon (silver salmon)",
    "Sockeye salmon (red salmon)",
    "Atlantic mackerel",
    "Atlantic salmon",
    "Oceanic whitetip shark",
    "Silky shark",
    "Common thresher shark",
    "Shortbill spearfish",
    "Akule (bigeye scad)",
    "Opelu (mackerel scad)",
    "Atlantic silverside",
    "Scaled sardine (pilchard)",
}

_STRUCTURE_SPECIES: set = {
    "Sheepshead",
    "Tautog (blackfish)",
    "Black sea bass",
    "Triggerfish (gray)",
    "Spadefish (Atlantic)",
    "Red snapper",
    "Vermilion snapper (beeliner)",
    "Mangrove snapper (gray snapper)",
    "Lane snapper",
    "Yellowtail snapper",
    "Mutton snapper",
    "Cubera snapper",
    "Schoolmaster snapper",
    "Gag grouper",
    "Red grouper",
    "Scamp grouper",
    "Black grouper",
    "Hogfish",
    "Red porgy",
    "Scup (porgy)",
    "White grunt",
    "Tomtate grunt",
    "Blue-striped grunt",
    "Lingcod",
    "Vermilion rockfish",
    "Blue rockfish",
    "Copper rockfish",
    "Grass rockfish",
    "Cabezon",
    "Kelp bass (calico bass)",
    "Sand bass (barred sand bass)",
    "California sheephead",
    "Opaleye",
    "Mu (bigeye emperor)",
    "Menpachi (soldierfish)",
    "Greater amberjack",
    "Haddock",
    "Atlantic cod",
    "Sablefish (black cod)",
    "Opakapaka (pink snapper)",
    "Onaga (long-tail red snapper)",
    "Uku (gray snapper / jobfish)",
    "Hapu'upu'u (Hawaiian grouper)",
    "Lehi (amberjack / silvermouth jack)",
    "Black rockfish",
    "Yelloweye rockfish",
    "Quillback rockfish",
    "Olive rockfish",
    "China rockfish",
    "Black-and-yellow rockfish",
    "Pacific cod",
    "Cusk",
    "Acadian redfish (ocean perch)",
    "Petrale sole",
    "Gray triggerfish",
    "Bocaccio",
    "Widow rockfish",
    "Tiger rockfish",
    "Treefish",
    "Gopher rockfish",
    "Kelp rockfish",
    "Brown rockfish",
    "Rosy rockfish",
    "Canary rockfish",
    "Starry rockfish",
    "Pacific ocean perch",
    "Rougheye rockfish",
    "Longspine thornyhead",
    "Shortspine thornyhead",
    "Opakapaka (pink snapper)",
    "Onaga (long-tail red snapper)",
    "Uku (gray snapper / jobfish)",
    "Hapu'upu'u (Hawaiian grouper)",
    "Lehi (amberjack / silvermouth jack)",
}

_GAMEFISH_SPECIES: set = {
    "Blacktip shark",
    "Spinner shark",
    "Bull shark",
    "Sandbar shark",
    "Lemon shark",
    "Dusky shark",
    "Bonnethead shark",
    "Atlantic sharpnose shark",
    "Scalloped hammerhead shark",
    "Nurse shark",
    "Finetooth shark",
    "Leopard shark",
    "Shovelnose guitarfish",
    "Cobia",
    "Tarpon",
    "King mackerel (kingfish)",
    "Mahi-mahi (dolphinfish)",
    "Wahoo",
    "Sailfish",
    "Blue marlin",
    "White marlin",
    "Blackfin tuna",
    "Yellowfin tuna",
    "Greater amberjack",
    "Jack crevalle",
    "Giant trevally (ulua)",
    "Kaku (barracuda)",
    "Snook",
    "Permit",
}

# The classic inshore slam: redfish, speckled trout, snook, and flounder —
# the four species most commonly referred to when anglers chase an inshore slam.
# Tarpon is included as the "grand slam" completion fish.
_INSHORE_SLAM_SPECIES: set = {
    "Red drum (puppy drum)",
    "Redfish (Gulf red drum)",
    "Speckled trout (spotted seatrout)",
    "Snook",
    "Flounder (summer flounder)",
    "Southern flounder",
    "Gulf flounder",
    "Tarpon",
    "Ladyfish",
    "Sand seatrout (white trout)",
    "Silver seatrout",
    "Gray trout (weakfish)",
    "Jack crevalle",
    "Tripletail",
}

# Species that are nuisance bycatch and not worth targeting from the surf or pier.
# These are excluded from the "What's Biting Now" ranking entirely.
_NUISANCE_SPECIES: set = {
    "Lizardfish",  # Pure nuisance — no food/sport value
    "Hardhead catfish (sea catfish)",  # Venomous spines, slimy, universally disliked
    "Gafftopsail catfish",  # Same deal as hardhead — bycatch pest
    "Pinfish",  # Tiny bait-thieves, nobody targets them
    "Spottail pinfish",  # Same as pinfish
    "Pigfish",  # Marginal grunt bycatch
    "Ribbonfish (Atlantic cutlassfish)",  # Occasional pier bycatch, not a target
    "Hogchoker",  # Tiny flatfish, no sport or food value
}

# ---------------------------------------------------------------------------
# Display taxonomy — controlled-vocabulary categories for the forecast UI.
# ---------------------------------------------------------------------------
# Maps each species name to one or more category labels from this vocabulary:
#   game_fish  — prized sport/food fish actively targeted by anglers
#   bait_fish  — species primarily used as live or cut bait
#   panfish    — small/medium edible fish; easy catches; great for eating
#   shark      — elasmobranchs (sharks)
#   ray        — rays, skates, guitarfish
#   reef_fish  — structure/reef-associated species (tropical or temperate)
#   pelagic    — open-water, mid-column or surface species
#   migratory  — species with strong seasonal coastal migrations
#   shellfish  — crustaceans and mollusks
#   other      — everything that doesn't fit the above
#
# Species not listed here default to ["other"].
# This dict is independent of _GAMEFISH_SPECIES / _PELAGIC_SPECIES / etc.;
# those sets drive profile-based filtering; this dict drives display taxonomy.
# ---------------------------------------------------------------------------
_SPECIES_CATEGORIES: Dict[str, List[str]] = {
    # ── East Coast — primary sport fish ────────────────────────────────────
    "Red drum (puppy drum)": ["game_fish"],
    "Speckled trout (spotted seatrout)": ["game_fish"],
    "Black drum": ["game_fish"],
    "Black drum (large bull)": ["game_fish"],
    "Sheepshead": ["game_fish", "reef_fish"],
    "Tautog (blackfish)": ["game_fish", "reef_fish"],
    "Black sea bass": ["game_fish", "reef_fish"],
    "Bluefish": ["game_fish", "pelagic", "migratory"],
    "Striped bass (rockfish)": ["game_fish", "migratory"],
    "Striped bass (hybrid)": ["game_fish"],
    "Flounder (summer flounder)": ["game_fish"],
    "Southern flounder": ["game_fish"],
    "Gulf flounder": ["game_fish"],
    "Windowpane flounder": ["other"],
    "Fringed flounder": ["other"],
    "Spanish mackerel": ["game_fish", "pelagic", "migratory"],
    "Cero mackerel": ["game_fish", "pelagic"],
    "Pompano": ["game_fish"],
    "African pompano": ["game_fish", "pelagic"],
    "Palometa": ["panfish", "pelagic"],
    "Cobia": ["game_fish", "pelagic"],
    "King mackerel (kingfish)": ["game_fish", "pelagic"],
    "False albacore (little tunny)": ["game_fish", "pelagic"],
    "Atlantic bonito": ["game_fish", "pelagic"],
    "Tarpon": ["game_fish", "migratory"],
    "Snook": ["game_fish"],
    "Jack crevalle": ["game_fish", "pelagic"],
    "Blue runner (hardtail)": ["bait_fish", "pelagic"],
    "Bar jack": ["game_fish", "pelagic"],
    "Yellow jack": ["game_fish", "pelagic"],
    "Horse-eye jack": ["game_fish", "pelagic"],
    "Lookdown": ["reef_fish"],
    "Greater amberjack": ["game_fish", "pelagic"],
    "Almaco jack": ["game_fish", "pelagic"],
    "Almaco jack (large adult)": ["game_fish", "pelagic"],
    "Lesser amberjack": ["game_fish", "pelagic"],
    "Amberjack (juvenile, banded)": ["game_fish"],
    "Banded rudderfish": ["game_fish", "pelagic"],
    "Rainbow runner": ["game_fish", "pelagic"],
    "Permit": ["game_fish"],
    "Tripletail": ["game_fish"],
    "Ladyfish": ["game_fish", "pelagic"],
    "Great barracuda": ["game_fish", "pelagic"],
    "Gray trout (weakfish)": ["game_fish"],
    "Sand seatrout (white trout)": ["panfish"],
    "Silver seatrout": ["panfish"],
    "Mahi-mahi (dolphinfish)": ["game_fish", "pelagic"],
    "Wahoo": ["game_fish", "pelagic"],
    "Blackfin tuna": ["game_fish", "pelagic"],
    "Yellowfin tuna": ["game_fish", "pelagic"],
    "Bigeye tuna": ["game_fish", "pelagic"],
    "Albacore tuna": ["game_fish", "pelagic"],
    "Skipjack tuna": ["game_fish", "pelagic"],
    "Frigate mackerel": ["pelagic", "bait_fish"],
    "Sailfish": ["game_fish", "pelagic"],
    "Blue marlin": ["game_fish", "pelagic"],
    "White marlin": ["game_fish", "pelagic"],
    # ── East Coast — reef & structure fish ─────────────────────────────────
    "Triggerfish (gray)": ["reef_fish"],
    "Ocean triggerfish": ["reef_fish"],
    "Queen triggerfish": ["reef_fish"],
    "Planehead filefish": ["reef_fish"],
    "Scrawled filefish": ["reef_fish"],
    "Orange filefish": ["reef_fish"],
    "Spadefish (Atlantic)": ["reef_fish"],
    "Red snapper": ["game_fish", "reef_fish"],
    "Vermilion snapper (beeliner)": ["game_fish", "reef_fish"],
    "Mangrove snapper (gray snapper)": ["game_fish", "reef_fish"],
    "Lane snapper": ["game_fish", "reef_fish"],
    "Yellowtail snapper": ["game_fish", "reef_fish"],
    "Mutton snapper": ["game_fish", "reef_fish"],
    "Cubera snapper": ["game_fish", "reef_fish"],
    "Schoolmaster snapper": ["game_fish", "reef_fish"],
    "Silk snapper": ["reef_fish"],
    "Queen snapper": ["reef_fish"],
    "Gray snapper (juvenile)": ["reef_fish"],
    "Gag grouper": ["game_fish", "reef_fish"],
    "Red grouper": ["game_fish", "reef_fish"],
    "Scamp grouper": ["game_fish", "reef_fish"],
    "Black grouper": ["game_fish", "reef_fish"],
    "Snowy grouper": ["game_fish", "reef_fish"],
    "Wreckfish": ["reef_fish"],
    "Yellowedge grouper": ["reef_fish"],
    "Misty grouper": ["reef_fish"],
    "Nassau grouper": ["reef_fish"],
    "Goliath grouper (jewfish)": ["reef_fish"],
    "Coney": ["reef_fish"],
    "Rock hind": ["reef_fish"],
    "Red hind": ["reef_fish"],
    "Speckled hind": ["reef_fish"],
    "Warsaw grouper": ["reef_fish"],
    "Yellowmouth grouper": ["reef_fish"],
    "Kitty Mitchell (yellowfin grouper)": ["game_fish", "reef_fish"],
    "Greater soapfish": ["reef_fish"],
    "Hogfish": ["game_fish", "reef_fish"],
    "Red porgy": ["panfish", "reef_fish"],
    "Scup (porgy)": ["panfish"],
    "Knobbed porgy": ["panfish", "reef_fish"],
    "Whitebone porgy": ["panfish", "reef_fish"],
    "Jolthead porgy": ["panfish", "reef_fish"],
    "Grass porgy": ["panfish", "reef_fish"],
    "Saucereye porgy": ["panfish", "reef_fish"],
    "White grunt": ["panfish", "reef_fish"],
    "Blue-striped grunt": ["panfish", "reef_fish"],
    "Tomtate grunt": ["panfish", "reef_fish"],
    "Sailor's choice grunt": ["panfish", "reef_fish"],
    "Margate": ["panfish", "reef_fish"],
    "Porkfish": ["panfish", "reef_fish"],
    "Sand perch": ["panfish", "reef_fish"],
    "Sand tilefish": ["reef_fish"],
    "Blueline tilefish": ["game_fish", "reef_fish"],
    "Golden tilefish": ["game_fish", "reef_fish"],
    "Tilefish (blueline juvenile/grey)": ["reef_fish"],
    "Tilefish (golden juvenile)": ["reef_fish"],
    "Tilefish (golden)": ["game_fish", "reef_fish"],
    "Barrelfish": ["reef_fish"],
    "Blackbelly rosefish": ["reef_fish"],
    "Blue tang (surgeonfish)": ["reef_fish"],
    "Gray angelfish": ["reef_fish"],
    "Spotfin butterflyfish": ["reef_fish"],
    "Doctorfish (tang)": ["reef_fish"],
    "Squirrelfish": ["reef_fish"],
    "Bermuda chub (sea chub)": ["reef_fish"],
    "Sergeant major (damselfish)": ["reef_fish"],
    "Yellowtail damselfish": ["reef_fish"],
    "Slippery dick (wrasse)": ["reef_fish"],
    "Puddingwife (wrasse)": ["reef_fish"],
    "Bluehead wrasse": ["reef_fish"],
    "Creole wrasse": ["reef_fish"],
    "Yellowhead wrasse": ["reef_fish"],
    "Cunner (bergall)": ["reef_fish"],
    "Striped blenny": ["reef_fish"],
    "Feather blenny": ["reef_fish"],
    "Naked goby": ["reef_fish"],
    "Smooth trunkfish": ["reef_fish"],
    "Honeycomb cowfish": ["reef_fish"],
    "Scrawled cowfish": ["reef_fish"],
    "Striped burrfish (spiny boxfish)": ["reef_fish"],
    "Bandtail puffer": ["reef_fish"],
    "Checkered puffer": ["reef_fish"],
    "Northern puffer (blowfish)": ["panfish", "reef_fish"],
    "Bigeye (Priacanthus arenatus)": ["reef_fish"],
    "Short bigeye": ["reef_fish"],
    "Red lionfish (invasive)": ["reef_fish"],
    "Spotted scorpionfish": ["reef_fish"],
    "Barbfish (scorpionfish)": ["reef_fish"],
    "Plumed scorpionfish": ["reef_fish"],
    "Spotted moray eel": ["reef_fish"],
    "Green moray eel": ["reef_fish"],
    # ── East Coast — panfish / edible small fish ────────────────────────────
    "Spot": ["panfish"],
    "Atlantic croaker": ["panfish"],
    "Whiting (sea mullet, kingfish)": ["panfish"],
    "Southern kingfish (ground mullet)": ["panfish"],
    "Gulf kingfish (gulf whiting)": ["panfish"],
    "Northern kingfish": ["panfish"],
    "Star drum": ["panfish"],
    "Banded drum": ["panfish"],
    "Silver perch": ["panfish"],
    "White perch": ["panfish"],
    "Yellow perch": ["panfish"],
    "Bluegill": ["panfish"],
    "Redear sunfish (shellcracker)": ["panfish"],
    "Warmouth": ["panfish"],
    "Hickory shad": ["game_fish", "migratory"],
    "American shad": ["game_fish", "migratory"],
    # ── East Coast — bait fish ──────────────────────────────────────────────
    "Atlantic menhaden (bunker)": ["bait_fish", "migratory"],
    "Ballyhoo (balao)": ["bait_fish"],
    "Round scad (cigar minnow)": ["bait_fish"],
    "Bigeye scad": ["bait_fish"],
    "Spanish sardine": ["bait_fish"],
    "Atlantic thread herring (greenback)": ["bait_fish"],
    "Atlantic herring": ["bait_fish", "migratory"],
    "Blueback herring": ["bait_fish", "migratory"],
    "Alewife": ["bait_fish", "migratory"],
    "Butterfish": ["bait_fish"],
    "Harvestfish": ["bait_fish"],
    "Sheepshead minnow (killifish)": ["bait_fish"],
    "Atlantic bumper": ["bait_fish"],
    "Striped mullet": ["bait_fish", "migratory"],
    "Southern sennet": ["bait_fish"],
    # ── East Coast — sharks ─────────────────────────────────────────────────
    "Blacktip shark": ["shark", "game_fish"],
    "Spinner shark": ["shark"],
    "Atlantic sharpnose shark": ["shark"],
    "Bull shark": ["shark", "game_fish"],
    "Sandbar shark": ["shark"],
    "Bonnethead shark": ["shark"],
    "Lemon shark": ["shark"],
    "Dusky shark": ["shark"],
    "Smooth dogfish": ["shark"],
    "Spiny dogfish": ["shark"],
    "Sand tiger shark": ["shark"],
    "Tiger shark": ["shark", "game_fish"],
    "Scalloped hammerhead shark": ["shark"],
    "Shortfin mako shark": ["shark", "game_fish", "pelagic"],
    "Thresher shark": ["shark", "game_fish"],
    "Nurse shark": ["shark"],
    "Finetooth shark": ["shark"],
    "Atlantic angel shark": ["shark"],
    # ── East Coast — rays ───────────────────────────────────────────────────
    "Southern stingray": ["ray"],
    "Cownose ray": ["ray", "migratory"],
    "Clearnose skate": ["ray"],
    "Atlantic stingray": ["ray"],
    "Bluntnose stingray": ["ray"],
    "Butterfly ray": ["ray"],
    "Eagle ray (spotted)": ["ray"],
    "Winter skate": ["ray"],
    "Little skate": ["ray"],
    # ── East Coast — other ──────────────────────────────────────────────────
    "Sea robin": ["other"],
    "Striped sea robin": ["other"],
    "Northern sea robin": ["other"],
    "Oyster toadfish": ["other"],
    "Leopard toadfish": ["other"],
    "Atlantic needlefish": ["other"],
    "Lizardfish": ["other"],
    "American eel": ["other"],
    "Conger eel": ["other"],
    "Remora (sharksucker)": ["other"],
    "Sharksucker (whitefin)": ["other"],
    "Goosefish (monkfish)": ["other"],
    "Atlantic wolffish": ["other"],
    "Northern stargazer": ["other"],
    "Atlantic sturgeon": ["other"],
    "Shortnose sturgeon": ["other"],
    "Sargassumfish": ["other"],
    "Lined seahorse": ["other"],
    "Dusky pipefish": ["other"],
    "Shortnose batfish": ["other"],
    "Flying gurnard": ["other"],
    "Ocean sunfish (mola mola)": ["other"],
    "Ocean pout": ["other"],
    "Longnose gar": ["other"],
    "Kemp's ridley sea turtle": ["other"],
    "Hardhead catfish (sea catfish)": ["other"],
    "Gafftopsail catfish": ["other"],
    "Pigfish": ["other"],
    "Pinfish": ["other"],
    "Spottail pinfish": ["other"],
    "Ribbonfish (Atlantic cutlassfish)": ["other"],
    "Hogchoker": ["other"],
    "Spotted hake": ["other"],
    "Red hake (ling)": ["other"],
    "Longhorn sculpin": ["other"],
    "Pollock": ["game_fish", "reef_fish"],
    "Atlantic cod": ["game_fish", "reef_fish"],
    "Winter flounder": ["game_fish"],
    "Cunner": ["reef_fish"],
    "Fluke (summer flounder)": ["game_fish"],
    "Weakfish": ["game_fish"],
    "Mojarra (yellowfin mojarra)": ["other"],
    # ── Fresh / brackish water ──────────────────────────────────────────────
    "Largemouth bass": ["game_fish"],
    "Channel catfish": ["panfish"],
    "Blue catfish": ["panfish"],
    "Flathead catfish": ["game_fish"],
    # ── Gulf region variants ─────────────────────────────────────────────────
    "Sheepshead (Gulf)": ["game_fish", "reef_fish"],
    "Redfish (Gulf red drum)": ["game_fish"],
    # ── New species additions ────────────────────────────────────────────────
    "Haddock": ["game_fish", "reef_fish"],
    "Atlantic halibut": ["game_fish"],
    "Swordfish": ["game_fish", "pelagic"],
    "Black marlin": ["game_fish", "pelagic"],
    "Pacific halibut": ["game_fish"],
    "Chinook salmon (king salmon)": ["game_fish", "pelagic"],
    "Coho salmon (silver salmon)": ["game_fish", "pelagic"],
    "Chum salmon (dog salmon)": ["game_fish", "pelagic"],
    "Pink salmon (humpy)": ["game_fish", "pelagic"],
    "Sablefish (black cod)": ["game_fish", "reef_fish"],
    # ── West Coast ───────────────────────────────────────────────────────────
    "Barred surfperch": ["panfish"],
    "Redtail surfperch": ["panfish"],
    "Calico surfperch": ["panfish"],
    "Walleye surfperch": ["panfish"],
    "Rubberlip seaperch": ["panfish"],
    "Halfmoon (Catalina perch)": ["panfish"],
    "Opaleye": ["panfish"],
    "California halibut": ["game_fish"],
    "Starry flounder": ["game_fish"],
    "Lingcod": ["game_fish", "reef_fish"],
    "Vermilion rockfish": ["game_fish", "reef_fish"],
    "Blue rockfish": ["game_fish", "reef_fish"],
    "Copper rockfish": ["game_fish", "reef_fish"],
    "Grass rockfish": ["game_fish", "reef_fish"],
    "Cabezon": ["game_fish", "reef_fish"],
    "Kelp bass (calico bass)": ["game_fish", "reef_fish"],
    "Sand bass (barred sand bass)": ["game_fish", "reef_fish"],
    "California sheephead": ["game_fish", "reef_fish"],
    "White seabass": ["game_fish", "pelagic"],
    "Yellowtail (California yellowtail)": ["game_fish", "pelagic"],
    "Pacific bonito": ["game_fish", "pelagic"],
    "Pacific mackerel (chub mackerel)": ["panfish", "pelagic"],
    "Jack mackerel (Spanish jack)": ["bait_fish", "pelagic"],
    "Corbina": ["panfish"],
    "Spotfin croaker": ["panfish"],
    "Yellowfin croaker": ["panfish"],
    "White croaker (tomcod)": ["panfish"],
    "Leopard shark": ["shark"],
    "Shovelnose guitarfish": ["ray", "game_fish"],
    "Bat ray": ["ray"],
    "Thornback ray": ["ray"],
    "Kelp greenling": ["game_fish", "reef_fish"],
    "Rock greenling": ["game_fish", "reef_fish"],
    "Pacific staghorn sculpin": ["other"],
    "Pacific herring": ["bait_fish"],
    "Pacific sardine": ["bait_fish"],
    "Northern anchovy": ["bait_fish"],
    "Jacksmelt": ["bait_fish", "panfish"],
    # ── Hawaii ───────────────────────────────────────────────────────────────
    "Giant trevally (ulua)": ["game_fish", "pelagic"],
    "Bluefin trevally (omilu)": ["game_fish", "pelagic"],
    "Papio (juvenile jack)": ["game_fish", "pelagic"],
    "Bonefish (oio)": ["game_fish"],
    "Menpachi (soldierfish)": ["reef_fish"],
    "Moi (Pacific threadfin)": ["game_fish"],
    "Mu (bigeye emperor)": ["game_fish", "reef_fish"],
    "Kaku (barracuda)": ["game_fish", "pelagic"],
    "Aholehole (Hawaiian flagtail)": ["reef_fish"],
    # ── New species (batch 2+) ────────────────────────────────────────────────
    "Atlantic bluefin tuna": ["game_fish", "pelagic"],
    "Atlantic mackerel": ["game_fish", "pelagic", "bait_fish"],
    "Steelhead trout (sea-run rainbow)": ["game_fish"],
    "Pacific cod": ["game_fish", "reef_fish"],
    "Black rockfish": ["game_fish", "reef_fish"],
    "Yelloweye rockfish": ["game_fish", "reef_fish"],
    "Quillback rockfish": ["game_fish", "reef_fish"],
    "Olive rockfish": ["game_fish", "reef_fish"],
    "China rockfish": ["game_fish", "reef_fish"],
    "Black-and-yellow rockfish": ["game_fish", "reef_fish"],
    "Opakapaka (pink snapper)": ["game_fish", "reef_fish"],
    "Onaga (long-tail red snapper)": ["game_fish", "reef_fish"],
    "Uku (gray snapper / jobfish)": ["game_fish", "reef_fish"],
    "Hapu'upu'u (Hawaiian grouper)": ["game_fish", "reef_fish"],
    "Striped marlin": ["game_fish", "pelagic"],
    "Opah (moonfish)": ["game_fish", "pelagic"],
    "Porbeagle shark": ["shark"],
    "Acadian redfish (ocean perch)": ["game_fish", "reef_fish"],
    "Petrale sole": ["game_fish"],
    "Pacific sanddab": ["panfish"],
    "English sole": ["panfish"],
    "Arrowtooth flounder": ["game_fish"],
    "Cusk": ["game_fish", "reef_fish"],
    "Surf smelt (Pacific)": ["bait_fish"],
    "Gray triggerfish": ["game_fish", "reef_fish"],
    "Lehi (amberjack / silvermouth jack)": ["game_fish", "pelagic"],
    "Atlantic salmon": ["game_fish"],
    "Sockeye salmon (red salmon)": ["game_fish", "pelagic"],
    "Rock sole": ["panfish"],
    # ── Batch 3 additions ─────────────────────────────────────────────────────
    "Bocaccio": ["game_fish", "reef_fish"],
    "Widow rockfish": ["game_fish", "reef_fish"],
    "Tiger rockfish": ["game_fish", "reef_fish"],
    "Treefish": ["game_fish", "reef_fish"],
    "Gopher rockfish": ["game_fish", "reef_fish"],
    "Kelp rockfish": ["game_fish", "reef_fish"],
    "Brown rockfish": ["game_fish", "reef_fish"],
    "Rosy rockfish": ["game_fish", "reef_fish"],
    "Canary rockfish": ["game_fish", "reef_fish"],
    "Starry rockfish": ["game_fish", "reef_fish"],
    "Pacific spiny dogfish": ["shark"],
    "Sixgill shark": ["shark"],
    "Sevengill shark": ["shark"],
    "Tope shark (soupfin)": ["shark"],
    "Kumu (whitesaddle goatfish)": ["reef_fish"],
    "Nenue (Hawaiian chub)": ["reef_fish"],
    "Pacific ocean perch": ["game_fish", "reef_fish"],
    "Rougheye rockfish": ["game_fish", "reef_fish"],
    "Longspine thornyhead": ["game_fish", "reef_fish"],
    "Shortspine thornyhead": ["game_fish", "reef_fish"],
    "Brown rockfish": ["game_fish", "reef_fish"],
    "Dover sole": ["game_fish"],
    "Rex sole": ["panfish"],
    "Kala (unicornfish)": ["reef_fish"],
    "Manini (convict tang)": ["reef_fish"],
    "Kumu (whitesaddle goatfish)": ["reef_fish"],
    # New Hawaii species
    "Akule (bigeye scad)": ["baitfish", "panfish"],
    "Opelu (mackerel scad)": ["baitfish", "panfish"],
    "Uhu (parrotfish)": ["reef_fish"],
    "Taape (bluestripe snapper)": ["game_fish", "reef_fish"],
    "Moano (goatfish)": ["reef_fish", "panfish"],
    # New West Coast species
    "Pile perch": ["panfish"],
    "Shiner perch": ["panfish", "baitfish"],
    "Rainbow seaperch": ["panfish"],
    "Striped seaperch": ["panfish"],
    "Pink seaperch": ["panfish"],
    "Painted greenling": ["panfish"],
    "Monkeyface prickleback": ["panfish"],
    "Spotted sand bass": ["game_fish"],
    "Squarespot rockfish": ["game_fish", "reef_fish"],
    "Greenstriped rockfish": ["game_fish", "reef_fish"],
    "Splitnose rockfish": ["game_fish", "reef_fish"],
    "Shortbelly rockfish": ["game_fish", "reef_fish"],
    "Chilipepper rockfish": ["game_fish", "reef_fish"],
    "Pygmy rockfish": ["panfish", "reef_fish"],
    # New Gulf/SE species
    "Sand drum": ["game_fish"],
    "Southern puffer": ["panfish"],
    "Gulf toadfish": ["panfish"],
    "Scaled sardine (pilchard)": ["baitfish"],
    # New East Coast species
    "Atlantic silverside": ["baitfish", "panfish"],
    "Sea raven": ["panfish"],
    "Lumpfish": ["panfish"],
    "Atlantic wolffish": ["game_fish"],
    # New offshore/pelagic
    "Oceanic whitetip shark": ["game_fish", "catch_and_release"],
    "Silky shark": ["game_fish", "catch_and_release"],
    "Common thresher shark": ["game_fish"],
    "Shortbill spearfish": ["game_fish", "billfish"],
    # Additional species
    "Weakfish (seatrout)": ["game_fish"],
    "Spotted seatrout": ["game_fish"],
    "Fourspot flounder": ["game_fish", "panfish"],
    "Spotfin mojarra": ["panfish", "baitfish"],
    "Striped mojarra": ["panfish", "baitfish"],
    "Blue runner": ["game_fish", "baitfish"],
    "Papio (young ulua)": ["game_fish"],
    "Atlantic blue marlin": ["game_fish", "billfish"],
    "Longbill spearfish": ["game_fish", "billfish"],
    "Bay anchovy": ["baitfish"],
    "Mummichog": ["baitfish", "panfish"],
    "Striped killifish": ["baitfish", "panfish"],
    "Florida gar": ["game_fish"],
    "Chain pickerel": ["game_fish"],
    "Awa (milkfish)": ["game_fish"],
    "Halfbanded rockfish": ["game_fish", "reef_fish"],
    "Honeycomb rockfish": ["game_fish", "reef_fish"],
    "Speckled rockfish": ["game_fish", "reef_fish"],
    "Swordspine rockfish": ["game_fish", "reef_fish"],
    "Stripetail rockfish": ["game_fish", "reef_fish"],
    "Buffalo sculpin": ["panfish"],
    "Freshwater drum": ["game_fish"],
    "Kaku (barracuda, Hawaii)": ["game_fish"],
    "Gag grouper": ["game_fish", "reef_fish"],
    "Scamp grouper": ["game_fish", "reef_fish"],
    "Lane snapper": ["game_fish", "reef_fish"],
    "Vermilion snapper (beeliners)": ["game_fish", "reef_fish"],
    "Mutton snapper": ["game_fish", "reef_fish"],
    "Cubera snapper": ["game_fish", "reef_fish"],
    "Schoolmaster snapper": ["game_fish", "reef_fish"],
    "Vermilion rockfish": ["game_fish", "reef_fish"],
    "Quillback rockfish": ["game_fish", "reef_fish"],
    "Dusky rockfish": ["game_fish", "reef_fish"],
    "Yellowmouth grouper": ["game_fish", "reef_fish"],
    "Rock hind grouper": ["game_fish", "reef_fish"],
    "Little tunny (false albacore)": ["game_fish", "pelagic"],
    "Blackfin tuna": ["game_fish", "pelagic"],
    "Skipjack tuna": ["game_fish", "pelagic", "baitfish"],
    "Walleye (coastal)": ["game_fish"],
    "Olive flounder": ["game_fish"],
    "Windowpane flounder": ["game_fish", "panfish"],
    "Ocean pout": ["panfish"],
    "Cunner": ["panfish"],
    "Longhorn sculpin": ["panfish"],
    "Atlantic needlefish": ["panfish"],
    "Palometa (longfin pompano)": ["game_fish"],
    "Florida pompano": ["game_fish"],
    "Permit (nearshore)": ["game_fish"],
    "Lookdown": ["panfish"],
    "Gulf pipefish": ["panfish"],
    "Inshore lizardfish": ["panfish"],
    "Northern stargazer": ["panfish"],
    "Bowfin": ["game_fish"],
    "Rubberlip seaperch": ["panfish"],
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
            has_surf or has_pier or has_inshore or has_offshore
            or has_jetty or has_bridge or has_wade or has_kayak or has_fly
        )
        if only_charter and sp_name not in _CHARTER_SPECIES:
            return False

        # Fly-only gate: if the only type selected is fly, restrict to fly species
        only_fly = has_fly and not (
            has_surf or has_pier or has_inshore or has_offshore
            or has_jetty or has_bridge or has_wade or has_kayak or has_charter
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
            _SURF_SPECIES, _PIER_SPECIES, _INSHORE_SPECIES,
            _JETTY_SPECIES, _BRIDGE_SPECIES, _WADE_SPECIES,
            _KAYAK_SPECIES, _CHARTER_SPECIES, _FLY_SPECIES,
        )
        _accessible: set = set()
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
    # Fly fishing patterns / leader setups
    if (
        "fly pattern" in text
        or "fly leader" in text
        or "clouser" in text
        or "deceiver" in text
        or "fly line" in text
        or "tippet" in text
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
    ):
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
    if (
        "carolina" in text
        or "fishfinder" in text
        or "fish finder" in text
        or "sliding" in text
    ):
        return "fishfinder"
    return "fishfinder"


# Maps rig keys to their primary gear style ("bait", "lure", or "mixed").
# Used to filter recommendations based on user bait/lure preferences.
_RIG_GEAR_TYPE: Dict[str, str] = {
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
}

# Rigs that work well as a first introduction — simple setup, forgiving tackle.
_BEGINNER_FRIENDLY_RIGS = frozenset({"fishfinder", "hi-lo", "pompano", "float", "popping-cork"})


def build_rig_recommendations(
    species_ranking: List[Dict[str, Any]],
    fishing_types: Optional[List[str]] = None,
    experience: str = "",
    live_bait: str = "",
    cut_bait: str = "",
    lures: str = "",
) -> List[Dict[str, Any]]:
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
    wants_bait = not (live_bait == "no" and cut_bait == "no")
    lures_only = (lures == "yes" or lures == "sometimes") and live_bait == "no" and cut_bait == "no"

    rig_groups: Dict[str, List[Dict[str, Any]]] = {}
    rig_order: List[str] = []

    for sp in species_ranking:
        key = _classify_rig(sp["rig"])
        if key not in rig_groups:
            rig_groups[key] = []
            rig_order.append(key)
        rig_groups[key].append(sp)

    # Prepend fishing-type-specific rigs that may not emerge from species data
    type_rig_keys: List[str] = []
    if "fly" in ft and "fly_pattern" not in rig_order:
        type_rig_keys.append("fly_pattern")
    if ("bridge" in ft or "jetty" in ft) and "current_jig" not in rig_order:
        type_rig_keys.append("current_jig")
    if "wade" in ft and "wade_light" not in rig_order:
        type_rig_keys.append("wade_light")
    if "kayak" in ft and "kayak_live_bait" not in rig_order:
        type_rig_keys.append("kayak_live_bait")

    def _make_rec(key: str, group: Optional[List[Dict[str, Any]]] = None) -> Optional[Dict[str, Any]]:
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

    recommendations: List[Dict[str, Any]] = []

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
    def _gear_type_for_rec(rec: Dict[str, Any]) -> str:
        # Match rec name back to a rig key via RIG_CATEGORIES.
        for k, cat in RIG_CATEGORIES.items():
            if cat.get("name") == rec.get("name"):
                return _RIG_GEAR_TYPE.get(k, "mixed")
        return "mixed"

    if live_bait or cut_bait or lures:
        if not wants_lures:
            # User explicitly doesn't use lures — drop lure-only rigs.
            recommendations = [r for r in recommendations if _gear_type_for_rec(r) != "lure"]
        elif lures_only:
            # Lure-only angler: sort lure/mixed rigs first, bait rigs last.
            recommendations.sort(key=lambda r: 0 if _gear_type_for_rec(r) in ("lure", "mixed") else 1)

    # ── Experience-level filtering ──────────────────────────────────────────
    if experience == "beginner":
        beginner = [r for r in recommendations if any(
            RIG_CATEGORIES.get(k, {}).get("name") == r.get("name") and k in _BEGINNER_FRIENDLY_RIGS
            for k in RIG_CATEGORIES
        )]
        advanced = [r for r in recommendations if r not in beginner]
        # Beginners get friendly rigs first, capped at 3 total.
        recommendations = (beginner + advanced)[:3]

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


_MONTH_ABBREVS: Dict[str, int] = {
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
    closed: set = set()
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


def _retention_prohibited(regulation: Dict[str, str], month: int = 0) -> bool:
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


def build_species_ranking(
    month: int,
    water_temp: float,
    wind_dir: Optional[str] = None,
    wind_range: Optional[Tuple[float, float]] = None,
    wave_range: Optional[Tuple[float, float]] = None,
    hour: int = 12,
    coast: Optional[str] = None,
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

    ``coast`` must be one of ``"east"``, ``"west"``, or ``"hawaii"``.
    Passing ``None`` (or omitting the argument) returns an empty list —
    the coast must be known to avoid leaking wrong-region species.
    """
    # For wind scoring, Hawaii uses "east" wind patterns (NE trades)
    wind_coast = "west" if coast == "west" else "east"
    scored = []
    for sp in SPECIES_DB:
        # Skip species from a different coast/region; also skip all species
        # when coast is None (unknown location — do not show any species).
        if coast is None or sp.get("coast", "east") != coast:
            continue
        # Skip nuisance/bycatch species that aren't worth targeting
        if sp["name"] in _NUISANCE_SPECIES:
            continue
        # Skip species not found in this geographic region
        if fish_region and "regions" in sp and fish_region not in sp["regions"]:
            continue
        # Skip species that don't match user's fishing profile
        if not _species_matches_profile(sp["name"], fishing_types, targets):
            continue
        s = _score_species(
            sp,
            month,
            water_temp,
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

    # Max possible raw score: 50 (temp) + 30 (season) + 15 (conditions) = 95
    _MAX_RAW_SCORE = 95.0

    result: List[Dict[str, Any]] = []
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
                    continue
                regulation = reg

        # Normalize raw score (max ~95) to a clean 0-100 display percentage
        display_score = min(100, round(score / _MAX_RAW_SCORE * 100))

        # Look up display categories: JSON field takes precedence if present,
        # then the curated _SPECIES_CATEGORIES dict, then default to ["other"].
        categories: List[str] = (
            sp.get("categories") or _SPECIES_CATEGORIES.get(sp["name"]) or ["other"]
        )

        entry: Dict[str, Any] = {
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
        # Expose legality status so templates can show uncertainty / stale warnings
        # without needing to re-parse regulation text.  Only set when state was queried.
        if regulation_status is not None:
            entry["regulation_status"] = regulation_status

        result.append(entry)
        if len(result) >= 10:
            break

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

    def canonical_bait_name(name: str) -> str:
        """Return a canonical label for de-duplicating near-identical bait names."""
        cleaned = " ".join(name.lower().replace("-", " ").split())
        alias_map = {
            "cut squid strips": "squid strips",
        }
        return alias_map.get(cleaned, cleaned)

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

    deduped_rankings: List[Dict[str, str]] = []
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
LURE_DB: List[Dict[str, Any]] = [
    {
        "lure": "Soft plastic paddle-tail swimbait",
        "sizes": "3-5\"",
        "colors": "White, chartreuse, natural (match baitfish)",
        "retrieve": "Slow steady retrieve or bounce on bottom",
        "notes": "Versatile inshore lure; works for almost any predator species. Use on 1/4-3/8 oz jighead.",
        "image": "images/lures/swimbait-paddle.svg",
        "available_months": list(range(1, 13)),
        "targets": [
            "Red drum", "Speckled trout", "Flounder", "Gray trout", "Striped bass",
            "Snook", "Sand bass", "Kelp bass", "California halibut",
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
            "Spanish mackerel", "Bluefish", "Red drum", "Jack crevalle",
            "Pacific bonito", "False albacore",
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
            "False albacore", "Bluefish", "Spanish mackerel", "Atlantic bonito",
            "Blackfin tuna", "Striped bass", "Pacific bonito", "Yellowtail",
        ],
    },
    {
        "lure": "Topwater plug / walk-the-dog",
        "sizes": "3.5-5\"",
        "colors": "White, bone, mullet pattern",
        "retrieve": "Walk-the-dog with slack-line twitches at dawn and dusk",
        "notes": "Most productive during low-light periods and calm conditions. Surface explosions make this a favorite lure.",
        "image": "images/lures/topwater-plug.svg",
        "notes_seasonal": {
            "winter": "Topwater bite slows in cold water; switch to subsurface lures in water below 60°F.",
        },
        "available_months": [3, 4, 5, 6, 7, 8, 9, 10, 11],
        "targets": [
            "Speckled trout", "Red drum", "Striped bass", "Snook",
            "Jack crevalle", "Bluefish", "Tarpon",
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
            "Flounder", "Striped bass", "Weakfish", "Gray trout",
            "Fluke", "White seabass",
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
        "sizes": "2-3\"",
        "colors": "New penny, pink shine, natural",
        "retrieve": "Slow drag or short hops on bottom; extremely versatile",
        "notes": "Scented soft plastic that outperforms natural bait in many situations. Popular for surf perch and flatfish.",
        "image": "images/lures/gulp-soft.svg",
        "available_months": list(range(1, 13)),
        "targets": [
            "Barred surfperch", "Corbina", "California halibut", "Pompano",
            "Flounder", "Whiting",
        ],
    },
    {
        "lure": "Swimbaits (large, 5-9\")",
        "sizes": "5-9\"",
        "colors": "Mullet pattern, sardine pattern, white",
        "retrieve": "Slow to medium; allow natural swimming action near structure",
        "notes": "Best for big predators — lingcod, yellowtail, cobia, tarpon. Match local baitfish size.",
        "image": "images/lures/swimbait-large.svg",
        "notes_seasonal": {
            "fall": "Fall baitfish migrations bring large predators close to shore; large swimbaits shine during this period.",
        },
        "available_months": list(range(1, 13)),
        "targets": [
            "Lingcod", "Yellowtail", "Cobia", "Tarpon", "Greater amberjack",
            "White seabass", "Snook",
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
        "sizes": "3-4\"",
        "colors": "White, chartreuse, fire tiger",
        "retrieve": "Aggressive popping action; pause between pops",
        "notes": "Excellent for pier and jetty fishing when birds are working. Creates a commotion that calls fish from a distance.",
        "image": "images/lures/popper.svg",
        "notes_seasonal": {
            "winter": "Poppers slow down in cold water; fish them when water temp is above 60°F.",
        },
        "available_months": [4, 5, 6, 7, 8, 9, 10, 11],
        "targets": [
            "Bluefish", "Spanish mackerel", "Jack crevalle", "Giant trevally",
            "Bluefin trevally", "Snook",
        ],
    },
    {
        "lure": "Offshore trolling lure",
        "sizes": "6-12\"",
        "colors": "Blue/white, pink/white, black/red",
        "retrieve": "Trolled at 6-18 knots (speed depends on species)",
        "notes": "Skirted lures run behind the boat in spread formation. Ilanders for wahoo and marlin; smaller skirts for mahi and tuna.",
        "image": "images/lures/trolling-lure.svg",
        "available_months": [4, 5, 6, 7, 8, 9, 10, 11],
        "targets": [
            "Mahi-mahi", "Wahoo", "Yellowfin tuna", "Blackfin tuna",
            "Sailfish", "Blue marlin", "White marlin",
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
    species_ranking: List[Dict[str, Any]],
    month: int,
) -> List[Dict[str, Any]]:
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
    species_ranks: Dict[str, int] = {}
    species_display: Dict[str, str] = {}
    for sp in species_ranking:
        short = sp["name"].split("(")[0].strip()
        species_ranks[short] = sp.get("rank", 0)
        species_display[short] = sp["name"]

    scored_lures: List[tuple] = []
    for lure_entry in LURE_DB:
        score = 0.0
        active: List[str] = []
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

        scored_lures.append((score, {
            "lure": lure_entry["lure"],
            "sizes": lure_entry.get("sizes", ""),
            "colors": lure_entry.get("colors", ""),
            "retrieve": lure_entry.get("retrieve", ""),
            "notes": notes,
            "image": lure_entry.get("image", ""),
            "active_species": active,
            "score": round(score, 1),
        }))

    scored_lures.sort(key=lambda x: x[0], reverse=True)
    return [entry for _, entry in scored_lures]


# ---------------------------------------------------------------------------
# Natural bait / forage species availability
# ---------------------------------------------------------------------------

NATURAL_BAIT_DB: List[Dict[str, Any]] = [
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

SPAWNING_DATA: List[Dict] = [
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


def _format_spawn_window(spawn_months: List[int]) -> str:
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


def build_spawning_report(
    month: int,
    water_temp: float,
    coast: Optional[str] = None,
    state: str = "",
) -> List[Dict[str, Any]]:
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
    results: List[Dict[str, Any]] = []

    for entry in SPAWNING_DATA:
        # Also filters out all entries when coast is None (unknown location).
        if coast is None or entry["coast"] != coast:
            continue

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
        reg: Optional[Dict[str, str]] = None
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

        # Preserve legacy legal_status field for backward-compatibility with
        # templates that still reference it (e.g. the "Verify seasonal rules" badge).
        _LEGACY_MAP = {
            "legal": "open",
            "catch_and_release": "catch_release",
            "restricted": "restricted",
            "out_of_season": "catch_release",
            "prohibited": "catch_release",
            "unknown": "unknown",
        }
        legal_status = _LEGACY_MAP.get(regulation_status, "unknown")

        # Resolve display categories from the canonical dict (or JSON field).
        spawn_sp = next((s for s in SPECIES_DB if s["name"] == entry["name"]), None)
        sp_categories: List[str] = (
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

    # Sort priority: spawning → pre_spawn → temp_pending → post_spawn; alpha within group
    _STATUS_ORDER = {"spawning": 0, "pre_spawn": 1, "temp_pending": 2, "post_spawn": 3}
    results.sort(key=lambda x: (_STATUS_ORDER.get(x["status"], 9), x["name"]))
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

        calendar.append(
            {
                "name": ranked_sp["name"],
                "months": months,
            }
        )

    return calendar
