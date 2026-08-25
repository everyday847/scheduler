from scheduler.palette_rules import palette_rule_to_constraints

def test_windowed_forbid_converts_to_zero_cap_windowed_constraint():
    rule = {
        "type": "staffing_per_week", "name": "No CCM coverage (holiday)",
        "groups": ["CCM"], "shifts": ["NCC"],
        "relation": "at_most", "count": 0, "window": [25, 27], "strength": "hard",
    }
    cons = palette_rule_to_constraints(rule)
    assert cons, "rule must produce at least one constraint"
    c = cons[0]
    # windowed forbid: at_most 0 over weeks 25-26 (window [25,27), end-exclusive)
    assert getattr(c.weeks, "start", None) == 25
    assert getattr(c.weeks, "end", None) == 27
    # assert zero-cap forbid property
    assert c.params.get("relation") == "at_most"
    assert c.params.get("count") == 0
