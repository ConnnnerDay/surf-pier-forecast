"""Tests for domain/forecast.py's build_spot_tips() fishing-type-specific
branches and build_gear_checklist() conditions/species/weather branches —
previously exercised only indirectly (monkeypatched away) elsewhere.
"""

from __future__ import annotations

from domain.forecast import build_gear_checklist, build_spot_tips


def _titles(tips):
    return [t["title"] for t in tips]


class TestBuildSpotTipsWaterQuality:
    def test_low_dissolved_oxygen_warning(self):
        wq = {"available": True, "do_mg_l": 3.2, "enterococcus_flag": "ok"}
        tips = build_spot_tips(water_quality=wq)
        assert "Low Dissolved Oxygen Warning" in _titles(tips)

    def test_enterococcus_advisory_warning(self):
        wq = {"available": True, "do_mg_l": 8.0, "enterococcus_flag": "advisory"}
        tips = build_spot_tips(water_quality=wq)
        assert "Beach Water Quality Advisory" in _titles(tips)

    def test_no_warning_when_unavailable(self):
        wq = {"available": False}
        tips = build_spot_tips(water_quality=wq)
        assert "Low Dissolved Oxygen Warning" not in _titles(tips)
        assert "Beach Water Quality Advisory" not in _titles(tips)


class TestBuildSpotTipsWind:
    def test_heavy_wind(self):
        tips = build_spot_tips(wind_range=(22, 25))
        assert "Heavy Wind Strategy" in _titles(tips)

    def test_moderate_wind(self):
        tips = build_spot_tips(wind_range=(13, 14))
        assert "Moderate Wind" in _titles(tips)

    def test_calm_wind(self):
        tips = build_spot_tips(wind_range=(2, 3))
        assert "Calm Conditions" in _titles(tips)


class TestBuildSpotTipsWave:
    def test_heavy_surf(self):
        tips = build_spot_tips(wave_range=(5, 6))
        assert "Heavy Surf" in _titles(tips)

    def test_moderate_surf(self):
        tips = build_spot_tips(wave_range=(2.5, 3))
        assert "Moderate Surf" in _titles(tips)

    def test_flat_surf(self):
        tips = build_spot_tips(wave_range=(0.5, 1))
        assert "Flat Surf" in _titles(tips)


class TestBuildSpotTipsTide:
    def test_rising_tide(self):
        tips = build_spot_tips(tide_state="Rising")
        assert "Rising Tide Tactics" in _titles(tips)

    def test_falling_tide(self):
        tips = build_spot_tips(tide_state="Falling")
        assert "Falling Tide Tactics" in _titles(tips)


class TestBuildSpotTipsTimeOfDay:
    def test_early_bird(self):
        tips = build_spot_tips(hour=6)
        assert "Early Bird Advantage" in _titles(tips)

    def test_evening_bite(self):
        tips = build_spot_tips(hour=18)
        assert "Evening Bite" in _titles(tips)

    def test_midday(self):
        tips = build_spot_tips(hour=12)
        assert "Midday Approach" in _titles(tips)


class TestBuildSpotTipsSeasonal:
    def test_cold_water(self):
        tips = build_spot_tips(month=1)
        assert "Cold Water Tips" in _titles(tips)

    def test_summer(self):
        tips = build_spot_tips(month=7)
        assert "Summer Patterns" in _titles(tips)


class TestBuildSpotTipsExperience:
    def test_beginner(self):
        tips = build_spot_tips(experience="beginner")
        assert "Getting Started" in _titles(tips)

    def test_experienced(self):
        tips = build_spot_tips(experience="experienced")
        assert "Read the Water" in _titles(tips)


class TestBuildSpotTipsFishingTypeBridge:
    def test_rising_tide_position(self):
        tips = build_spot_tips(fishing_types=["bridge"], tide_state="Rising")
        assert "Bridge: Incoming Tide Position" in _titles(tips)

    def test_falling_tide_position(self):
        tips = build_spot_tips(fishing_types=["bridge"], tide_state="Falling")
        assert "Bridge: Outgoing Tide Position" in _titles(tips)

    def test_slack_tide_tactics(self):
        tips = build_spot_tips(fishing_types=["bridge"], tide_state="")
        assert "Bridge: Slack Tide Tactics" in _titles(tips)

    def test_type_tips_prepended(self):
        tips = build_spot_tips(fishing_types=["bridge"], tide_state="Rising")
        assert tips[0]["title"] == "Bridge: Incoming Tide Position"


class TestBuildSpotTipsFishingTypeJetty:
    def test_work_the_wash_on_big_waves(self):
        tips = build_spot_tips(fishing_types=["jetty"], wave_range=(2, 3))
        assert "Jetty: Work the Wash" in _titles(tips)

    def test_tip_position_on_moving_tide(self):
        tips = build_spot_tips(
            fishing_types=["jetty"], wave_range=(0.5, 1), tide_state="Rising"
        )
        assert "Jetty Tip Position" in _titles(tips)

    def test_no_jetty_tip_when_calm_and_slack(self):
        tips = build_spot_tips(fishing_types=["jetty"], wave_range=(0.5, 1), tide_state="")
        assert "Jetty: Work the Wash" not in _titles(tips)
        assert "Jetty Tip Position" not in _titles(tips)


class TestBuildSpotTipsFishingTypeWade:
    def test_windy_clarity_tip(self):
        tips = build_spot_tips(fishing_types=["wade"], wind_range=(14, 16))
        assert "Wade: Wind and Clarity" in _titles(tips)

    def test_sight_fishing_tip(self):
        tips = build_spot_tips(fishing_types=["wade"], wind_range=(3, 4))
        assert "Wade: Sight-Fishing Conditions" in _titles(tips)


class TestBuildSpotTipsFishingTypeKayak:
    def test_wind_strategy_tip(self):
        tips = build_spot_tips(fishing_types=["kayak"], wind_range=(14, 16))
        assert "Kayak: Wind Strategy" in _titles(tips)

    def test_drift_tactics_tip(self):
        tips = build_spot_tips(fishing_types=["kayak"], wind_range=(3, 4))
        assert "Kayak: Drift Tactics" in _titles(tips)


class TestBuildSpotTipsFishingTypeFly:
    def test_high_wind_adjustments(self):
        tips = build_spot_tips(fishing_types=["fly"], wind_range=(18, 20))
        assert "Fly: High Wind Adjustments" in _titles(tips)

    def test_sight_casting_setup(self):
        tips = build_spot_tips(fishing_types=["fly"], wind_range=(3, 4))
        assert "Fly: Sight-Casting Setup" in _titles(tips)


class TestBuildSpotTipsFishingTypeCharter:
    def test_rough_weather_tips(self):
        tips = build_spot_tips(fishing_types=["charter"], wave_range=(5, 6))
        assert "Charter: Rough Weather Tips" in _titles(tips)

    def test_maximize_trip_tips(self):
        tips = build_spot_tips(fishing_types=["charter"], wave_range=(1, 2))
        assert "Charter: Maximize Your Trip" in _titles(tips)


class TestBuildSpotTipsMultipleTypes:
    def test_combines_tips_across_types(self):
        tips = build_spot_tips(
            fishing_types=["kayak", "fly"], wind_range=(3, 4)
        )
        titles = _titles(tips)
        assert "Kayak: Drift Tactics" in titles
        assert "Fly: Sight-Casting Setup" in titles


class TestBuildGearChecklistFishingTypes:
    def _items_for(self, items):
        return {(i["category"], i["item"]) for i in items}

    def test_kayak_items(self):
        items = build_gear_checklist([], fishing_types=["kayak"])
        pairs = self._items_for(items)
        assert ("Kayak", "PFD (life jacket)") in pairs

    def test_jetty_items(self):
        items = build_gear_checklist([], fishing_types=["jetty"])
        pairs = self._items_for(items)
        assert ("Jetty", "Wading staff or walking stick") in pairs

    def test_wade_items(self):
        items = build_gear_checklist([], fishing_types=["wade"])
        pairs = self._items_for(items)
        assert ("Wade", "Polarized sunglasses") in pairs

    def test_fly_items(self):
        items = build_gear_checklist([], fishing_types=["fly"])
        pairs = self._items_for(items)
        assert ("Fly", "Stripping basket") in pairs

    def test_bridge_items(self):
        items = build_gear_checklist([], fishing_types=["bridge"])
        pairs = self._items_for(items)
        assert ("Bridge", "Headlamp") in pairs

    def test_charter_items(self):
        items = build_gear_checklist([], fishing_types=["charter"])
        pairs = self._items_for(items)
        assert ("Charter", "Non-slip deck shoes") in pairs

    def test_offshore_items_when_not_charter(self):
        items = build_gear_checklist([], fishing_types=["offshore"])
        pairs = self._items_for(items)
        assert ("Offshore", "VHF radio and EPIRB / PLB") in pairs

    def test_offshore_items_skipped_when_charter_present(self):
        items = build_gear_checklist([], fishing_types=["offshore", "charter"])
        pairs = self._items_for(items)
        assert ("Offshore", "VHF radio and EPIRB / PLB") not in pairs

    def test_no_duplicate_items_across_overlapping_types(self):
        items = build_gear_checklist([], fishing_types=["wade", "fly"])
        pairs = [(i["category"], i["item"]) for i in items]
        # "Polarized sunglasses" appears under both Wade and Fly logic but with
        # different categories, so de-dup is keyed by category:item.
        assert len(pairs) == len(set(pairs))


class TestBuildGearChecklistConditions:
    def test_high_wind_adds_heavy_sinkers(self):
        items = build_gear_checklist([], wind_range=(16, 18))
        pairs = {(i["category"], i["item"]) for i in items}
        assert ("Conditions", "Heavy sinkers (4-6 oz)") in pairs

    def test_high_wave_adds_waders(self):
        items = build_gear_checklist([], wave_range=(5, 6))
        pairs = {(i["category"], i["item"]) for i in items}
        assert ("Conditions", "Waders or waterproof boots") in pairs

    def test_early_hour_adds_headlamp(self):
        items = build_gear_checklist([], hour=4)
        pairs = {(i["category"], i["item"]) for i in items}
        assert ("Conditions", "Headlamp (red light mode)") in pairs

    def test_late_hour_adds_headlamp(self):
        items = build_gear_checklist([], hour=21)
        pairs = {(i["category"], i["item"]) for i in items}
        assert ("Conditions", "Headlamp (red light mode)") in pairs

    def test_midday_hour_adds_sun_protection(self):
        items = build_gear_checklist([], hour=13)
        pairs = {(i["category"], i["item"]) for i in items}
        assert ("Conditions", "Sunscreen SPF 50+") in pairs


class TestBuildGearChecklistWeather:
    def test_cold_weather_adds_layers(self):
        items = build_gear_checklist([], weather={"air_temp_f": 38})
        pairs = {(i["category"], i["item"]) for i in items}
        assert ("Weather", "Hand warmers") in pairs

    def test_hot_weather_adds_extra_water(self):
        items = build_gear_checklist([], weather={"air_temp_f": 92})
        pairs = {(i["category"], i["item"]) for i in items}
        assert ("Weather", "Extra water (1 gal minimum)") in pairs

    def test_mild_weather_adds_nothing_extra(self):
        items = build_gear_checklist([], weather={"air_temp_f": 70})
        categories = {i["category"] for i in items}
        assert "Weather" not in categories

    def test_no_weather_dict_skips_weather_section(self):
        items = build_gear_checklist([])
        categories = {i["category"] for i in items}
        assert "Weather" not in categories


class TestBuildGearChecklistSpecies:
    def test_shark_adds_wire_leader(self):
        species = [{"name": "Bull Shark"}]
        items = build_gear_checklist(species)
        pairs = {(i["category"], i["item"]) for i in items}
        assert ("Species", "Wire leader (single-strand)") in pairs

    def test_king_adds_stinger_rig(self):
        species = [{"name": "King Mackerel"}]
        items = build_gear_checklist(species)
        pairs = {(i["category"], i["item"]) for i in items}
        assert ("Species", "Stinger rig components") in pairs

    def test_flounder_adds_bucktail_jig(self):
        species = [{"name": "Summer Flounder"}]
        items = build_gear_checklist(species)
        pairs = {(i["category"], i["item"]) for i in items}
        assert ("Species", "Bucktail jig (white/chartreuse)") in pairs

    def test_only_checks_top_ten_species(self):
        species = [{"name": "Spot"} for _ in range(10)] + [{"name": "Bull Shark"}]
        items = build_gear_checklist(species)
        pairs = {(i["category"], i["item"]) for i in items}
        assert ("Species", "Wire leader (single-strand)") not in pairs

    def test_always_includes_essentials_and_convenience(self):
        items = build_gear_checklist([])
        categories = {i["category"] for i in items}
        assert "Essentials" in categories
        assert "Convenience" in categories
