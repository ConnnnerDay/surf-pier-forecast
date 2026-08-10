from app.core.catch_calculator import evaluate_catch, parse_size_bounds


def test_parse_simple_min_size() -> None:
    assert parse_size_bounds({"min_size": "18 in TL"}) == (18.0, None, True)


def test_parse_decimal_min_size() -> None:
    assert parse_size_bounds({"min_size": "12.5 in"}) == (12.5, None, True)


def test_parse_slot_limit_takes_priority() -> None:
    # slot (from the enriched "18-27 in" field) wins over a plain min_size,
    # since it's the more specific/authoritative parse
    assert parse_size_bounds({"min_size": "18 in TL", "slot": "18-27 in"}) == (18.0, 27.0, True)


def test_parse_no_minimum_text() -> None:
    assert parse_size_bounds({"min_size": "No minimum size"}) == (0.0, None, True)
    assert parse_size_bounds({"min_size": "None (FL: no minimum)"}) == (0.0, None, True)


def test_parse_ambiguous_multi_region_text_is_not_confident() -> None:
    # real data: "12 in TL in Gulf; 14 in TL in Atlantic" — two numbers,
    # genuinely ambiguous without a sub-area input we don't collect
    min_in, max_in, confident = parse_size_bounds(
        {"min_size": "12 in TL in Gulf; 14 in TL in Atlantic"}
    )
    assert confident is False
    assert min_in is None


def test_parse_empty_or_missing_min_size() -> None:
    assert parse_size_bounds({}) == (None, None, False)
    assert parse_size_bounds({"min_size": ""}) == (None, None, False)


def test_parse_prose_only_text_is_not_confident() -> None:
    min_in, _, confident = parse_size_bounds({"min_size": "Varies by river system"})
    assert confident is False
    assert min_in is None


def test_evaluate_catch_too_small() -> None:
    result = evaluate_catch({"min_size": "18 in TL", "slot": "18-27 in"}, "legal", 15.0)
    assert result["verdict"] == "too_small"
    assert result["legal"] is False
    assert result["min_size_in"] == 18.0


def test_evaluate_catch_too_large_slot() -> None:
    result = evaluate_catch({"min_size": "18 in TL", "slot": "18-27 in"}, "legal", 30.0)
    assert result["verdict"] == "too_large"
    assert result["legal"] is False
    assert result["max_size_in"] == 27.0


def test_evaluate_catch_legal_within_slot() -> None:
    result = evaluate_catch({"min_size": "18 in TL", "slot": "18-27 in"}, "legal", 22.0)
    assert result["verdict"] == "legal"
    assert result["legal"] is True


def test_evaluate_catch_legal_no_upper_bound() -> None:
    result = evaluate_catch({"min_size": "10 in TL"}, "legal", 25.0)
    assert result["verdict"] == "legal"
    assert result["legal"] is True
    assert result["max_size_in"] is None


def test_evaluate_catch_boundary_values_are_legal() -> None:
    # exactly at the min and exactly at the max should both be legal (>=, <=)
    assert evaluate_catch({"min_size": "18 in", "slot": "18-27 in"}, "legal", 18.0)["legal"] is True
    assert evaluate_catch({"min_size": "18 in", "slot": "18-27 in"}, "legal", 27.0)["legal"] is True


def test_evaluate_catch_prohibited_short_circuits_size_check() -> None:
    # even a huge fish can't be kept if the species is fully protected
    result = evaluate_catch({"min_size": "18 in TL"}, "prohibited", 40.0)
    assert result["verdict"] == "cannot_target"
    assert result["legal"] is False


def test_evaluate_catch_out_of_season() -> None:
    result = evaluate_catch({"min_size": "14 in TL"}, "out_of_season", 20.0)
    assert result["verdict"] == "cannot_target"
    assert result["legal"] is False
    assert "closed" in result["reason"].lower()


def test_evaluate_catch_catch_and_release() -> None:
    result = evaluate_catch({"min_size": "14 in TL"}, "catch_and_release", 20.0)
    assert result["verdict"] == "cannot_target"
    assert result["legal"] is False


def test_evaluate_catch_unknown_status() -> None:
    result = evaluate_catch({}, "unknown", 20.0)
    assert result["verdict"] == "unknown"
    assert result["legal"] is None


def test_evaluate_catch_unparseable_size_is_unknown_not_legal() -> None:
    # this is the important fail-safe case: ambiguous data must never
    # resolve to a confident "legal" verdict
    result = evaluate_catch({"min_size": "Varies by area (IPHC)"}, "legal", 20.0)
    assert result["verdict"] == "unknown"
    assert result["legal"] is None
