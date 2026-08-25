# Rule Editor & Generic Constraint Palette Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the flat rule tables with a full rule editor built on 8 generic constraint types, with feasibility checking and draft/publish persistence.

**Architecture:** New `palette_rules.py` module defines the canonical palette schema and converts palette-format rules into `SemanticConstraint` objects the solver already understands. The React app is restructured into a sidebar + main panel layout with per-group rule editing. A new `/api/feasibility/check` endpoint runs quick SAT probes for the two-pip system.

**Tech Stack:** Python 3.9, Flask, RoundingSat (PB solver), React 19, TypeScript

**Test commands:**
- Python: `cd /cv/scratch/u/watkina6/scheduler && PYTHONPATH=src:new_approach/src python -m pytest tests/ -v --tb=short`
- React build: `cd /cv/scratch/u/watkina6/scheduler/src/web && CI=true npx react-scripts build`
- Dev server: `PYTHONPATH=src:new_approach/src python -m scheduler.web_app` + `cd src/web && npm start`

---

## File Structure

### New Files
| File | Responsibility |
|------|---------------|
| `src/scheduler/palette_rules.py` | Palette schema definition, validation, conversion to `SemanticConstraint` |
| `src/scheduler/palette_derivations.py` | Derived behavior: compute forbidden shifts from Shift Total rules |
| `src/scheduler/draft_persistence.py` | Draft/publish CRUD for YAML configs |
| `tests/test_palette_rules.py` | Unit tests for palette→SemanticConstraint conversion |
| `tests/test_palette_derivations.py` | Tests for forbidden shift derivation |
| `tests/test_draft_persistence.py` | Tests for draft/publish file operations |
| `tests/test_feasibility_endpoint.py` | Integration tests for feasibility API |
| `config/standing/stanford-fellowship-v2.yaml` | Standing rules in palette format |
| `config/annual/my-2025-2026-v2.yaml` | Annual rules in palette format |
| `src/web/src/components/Sidebar.tsx` | Navigation sidebar component |
| `src/web/src/components/RuleCard.tsx` | Single rule display card with feasibility pips |
| `src/web/src/components/RuleEditor.tsx` | Inline edit form for a rule's parameters |
| `src/web/src/components/RulePalette.tsx` | "+ Add Rule" type picker modal |
| `src/web/src/components/CoverageTotalsTable.tsx` | Compact table for Shift Total rules |
| `src/web/src/components/TopBar.tsx` | Config selector, draft status, publish button |
| `src/web/src/components/FeasibilityPips.tsx` | Two-dot feasibility indicator |
| `src/web/src/types.ts` | Shared TypeScript types for palette rules |

### Modified Files
| File | Changes |
|------|---------|
| `src/scheduler/web_app.py` | Add draft/publish endpoints, feasibility endpoint |
| `src/scheduler/solver_bridge.py` | Accept palette-format rules, call `palette_rules.py` |
| `src/web/src/App.tsx` | Restructure to sidebar layout, delegate to new components |
| `src/web/src/App.css` | Sidebar + panel layout styles |

---

## Task 1: Palette Rule Schema and Shift Total Conversion

**Files:**
- Create: `src/scheduler/palette_rules.py`
- Create: `tests/test_palette_rules.py`

This task defines the palette rule data model and implements conversion for the most common type (shift_total) into `SemanticConstraint` objects.

- [ ] **Step 1: Write failing tests for shift_total conversion**

```python
# tests/test_palette_rules.py
from __future__ import annotations
import pytest
from scheduler.palette_rules import palette_rule_to_constraints
from scheduler.semantic_constraints import ConstraintStrength, ConstraintLifecycle


def test_shift_total_exactly():
    rule = {
        "name": "NCC Junior MICU Total",
        "type": "shift_total",
        "groups": ["NCC_JR"],
        "shifts": ["MICU"],
        "relation": "exactly",
        "count": 20,
        "strength": "hard",
        "active": True,
    }
    constraints = palette_rule_to_constraints(rule)
    assert len(constraints) == 1
    c = constraints[0]
    assert c.kind == "shift_total"
    assert c.strength == ConstraintStrength.HARD
    assert c.lifecycle == ConstraintLifecycle.ANNUAL_RULE
    assert c.fellows.groups == ("NCC_JR",)
    assert c.shifts.shifts == ("MICU",)
    assert c.params["relation"] == "exactly"
    assert c.params["count"] == 20
    assert c.params["name"] == "NCC Junior MICU Total"


def test_shift_total_with_window():
    rule = {
        "name": "NCC Junior Orientation MICU",
        "type": "shift_total",
        "groups": ["NCC_JR"],
        "shifts": ["MICU"],
        "relation": "exactly",
        "count": 4,
        "strength": "hard",
        "active": True,
        "window": [0, 4],
    }
    constraints = palette_rule_to_constraints(rule)
    assert len(constraints) == 1
    c = constraints[0]
    assert c.weeks is not None
    assert c.weeks.start == 0
    assert c.weeks.end == 4


def test_shift_total_at_least():
    rule = {
        "name": "Stroke Elective Min",
        "type": "shift_total",
        "groups": ["STROKE"],
        "shifts": ["Elec"],
        "relation": "at_least",
        "count": 2,
        "strength": "soft",
        "active": True,
    }
    constraints = palette_rule_to_constraints(rule)
    c = constraints[0]
    assert c.strength == ConstraintStrength.SOFT
    assert c.params["relation"] == "at_least"
    assert c.params["count"] == 2


def test_inactive_rule_returns_empty():
    rule = {
        "name": "Disabled Rule",
        "type": "shift_total",
        "groups": ["NCC_JR"],
        "shifts": ["MICU"],
        "relation": "exactly",
        "count": 20,
        "strength": "hard",
        "active": False,
    }
    constraints = palette_rule_to_constraints(rule)
    assert constraints == []


def test_unknown_type_raises():
    rule = {
        "name": "Bad Rule",
        "type": "nonexistent_type",
        "groups": ["NCC_JR"],
        "shifts": ["MICU"],
        "strength": "hard",
        "active": True,
    }
    with pytest.raises(ValueError, match="Unknown palette rule type"):
        palette_rule_to_constraints(rule)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /cv/scratch/u/watkina6/scheduler && PYTHONPATH=src:new_approach/src python -m pytest tests/test_palette_rules.py -v --tb=short`
Expected: FAIL with `ModuleNotFoundError: No module named 'scheduler.palette_rules'`

- [ ] **Step 3: Implement palette_rules.py with shift_total support**

```python
# src/scheduler/palette_rules.py
from __future__ import annotations

from typing import Any

from .semantic_constraints import (
    ConstraintLifecycle,
    ConstraintStrength,
    FellowSelector,
    SemanticConstraint,
    ShiftSet,
    WeekSpan,
)


PALETTE_TYPES = frozenset({
    "shift_total",
    "staffing_per_week",
    "coverage_target",
    "max_consecutive",
    "block_rotation",
    "rotation_continuity",
    "prerequisite",
    "windowed_balance",
})


def palette_rule_to_constraints(
    rule: dict[str, Any],
    *,
    lifecycle: ConstraintLifecycle = ConstraintLifecycle.ANNUAL_RULE,
) -> list[SemanticConstraint]:
    if not rule.get("active", True):
        return []

    rule_type = rule["type"]
    if rule_type not in PALETTE_TYPES:
        raise ValueError(f"Unknown palette rule type: {rule_type!r}")

    converter = _CONVERTERS[rule_type]
    return converter(rule, lifecycle)


def _convert_shift_total(
    rule: dict[str, Any], lifecycle: ConstraintLifecycle
) -> list[SemanticConstraint]:
    name = rule["name"]
    groups = rule["groups"]
    shifts = rule["shifts"]
    relation = rule["relation"]
    count = rule["count"]
    strength = _parse_strength(rule["strength"])
    window = rule.get("window")

    weeks = WeekSpan(window[0], window[1]) if window else None

    return [SemanticConstraint(
        kind="shift_total",
        lifecycle=lifecycle,
        strength=strength,
        fellows=FellowSelector.by_groups(*groups),
        weeks=weeks,
        shifts=ShiftSet(name, tuple(shifts)),
        params={
            "name": name,
            "relation": relation,
            "count": count,
        },
    )]


def _parse_strength(value: str) -> ConstraintStrength:
    try:
        return ConstraintStrength(value)
    except ValueError as exc:
        raise ValueError(f"Unknown constraint strength: {value!r}") from exc


_CONVERTERS: dict[str, Any] = {
    "shift_total": _convert_shift_total,
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /cv/scratch/u/watkina6/scheduler && PYTHONPATH=src:new_approach/src python -m pytest tests/test_palette_rules.py -v --tb=short`
Expected: All 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/scheduler/palette_rules.py tests/test_palette_rules.py
git commit -m "feat: palette rule schema with shift_total conversion"
```

---

## Task 2: Remaining Palette Type Converters

**Files:**
- Modify: `src/scheduler/palette_rules.py`
- Modify: `tests/test_palette_rules.py`

Add converters for the remaining 7 palette types: staffing_per_week, coverage_target, max_consecutive, block_rotation, rotation_continuity, prerequisite, windowed_balance.

- [ ] **Step 1: Write failing tests for all remaining types**

Append to `tests/test_palette_rules.py`:

```python
def test_staffing_per_week():
    rule = {
        "name": "NCC1 Coverage",
        "type": "staffing_per_week",
        "groups": ["NCC_JR", "NCC_SR", "STROKE", "CCM", "NH"],
        "shifts": ["NCC1"],
        "relation": "at_least",
        "count": 1,
        "strength": "hard",
        "active": True,
    }
    constraints = palette_rule_to_constraints(rule)
    assert len(constraints) == 1
    c = constraints[0]
    assert c.kind == "staffing_per_week"
    assert c.params["relation"] == "at_least"
    assert c.params["count"] == 1


def test_staffing_per_week_with_window():
    rule = {
        "name": "Fourth Block MICU Staffing",
        "type": "staffing_per_week",
        "groups": ["NCC_JR", "NCC_SR"],
        "shifts": ["MICU"],
        "relation": "exactly",
        "count": 2,
        "strength": "hard",
        "active": True,
        "window": [12, 16],
    }
    constraints = palette_rule_to_constraints(rule)
    c = constraints[0]
    assert c.weeks.start == 12
    assert c.weeks.end == 16


def test_coverage_target():
    rule = {
        "name": "Swing Coverage Target",
        "type": "coverage_target",
        "groups": ["NCC_JR", "NCC_SR", "STROKE", "CCM", "NH"],
        "shifts": ["Swing"],
        "max_uncovered_weeks": 2,
        "strength": "soft",
        "active": True,
    }
    constraints = palette_rule_to_constraints(rule)
    assert len(constraints) == 1
    c = constraints[0]
    assert c.kind == "coverage_target"
    assert c.params["max_uncovered_weeks"] == 2


def test_max_consecutive():
    rule = {
        "name": "Core ICU Max Consecutive",
        "type": "max_consecutive",
        "groups": ["NCC_JR", "NCC_SR", "STROKE", "NH"],
        "shifts": ["NCC1", "NCC2", "Swing", "SICU", "MICU", "Stroke"],
        "max_weeks": 8,
        "strength": "hard",
        "active": True,
    }
    constraints = palette_rule_to_constraints(rule)
    assert len(constraints) == 1
    c = constraints[0]
    assert c.kind == "max_consecutive"
    assert c.params["weeks"] == 8


def test_block_rotation():
    rule = {
        "name": "MICU 4-Week Blocks",
        "type": "block_rotation",
        "groups": ["NCC_JR", "NCC_SR"],
        "shifts": ["MICU"],
        "block_size": 4,
        "strength": "hard",
        "active": True,
    }
    constraints = palette_rule_to_constraints(rule)
    assert len(constraints) == 1
    c = constraints[0]
    assert c.kind == "all_or_none_block"
    assert c.params["block_size"] == 4


def test_rotation_continuity():
    rule = {
        "name": "NCC 2-Week Team Continuity",
        "type": "rotation_continuity",
        "groups": ["NCC_JR", "NCC_SR", "CCM"],
        "block_size": 2,
        "choices": [["NCC1", "Swing"], ["NCC2", "Swing"]],
        "allow_none": True,
        "strength": "hard",
        "active": True,
    }
    constraints = palette_rule_to_constraints(rule)
    assert len(constraints) == 1
    c = constraints[0]
    assert c.kind == "block_shift_set_choice"
    assert c.params["block_size"] == 2
    assert c.params["choices"] == [["NCC1", "Swing"], ["NCC2", "Swing"]]
    assert c.params["allow_none"] is True


def test_prerequisite():
    rule = {
        "name": "NCC Junior: NCC Before Swing",
        "type": "prerequisite",
        "groups": ["NCC_JR"],
        "prerequisite_shifts": ["NCC1", "NCC2"],
        "target_shifts": ["Swing"],
        "min_prerequisite_weeks": 4,
        "strength": "hard",
        "active": True,
    }
    constraints = palette_rule_to_constraints(rule)
    assert len(constraints) == 1
    c = constraints[0]
    assert c.kind == "prerequisite"
    assert c.params["prerequisite_shifts"] == ["NCC1", "NCC2"]
    assert c.params["target_shifts"] == ["Swing"]
    assert c.params["min_prerequisite_weeks"] == 4


def test_windowed_balance():
    rule = {
        "name": "Half-Year Balance",
        "type": "windowed_balance",
        "groups": ["NCC_JR", "NCC_SR"],
        "shifts": ["MICU"],
        "window_a": [0, 26],
        "window_b": [26, 52],
        "max_difference": 4,
        "strength": "soft",
        "active": True,
    }
    constraints = palette_rule_to_constraints(rule)
    assert len(constraints) == 1
    c = constraints[0]
    assert c.kind == "windowed_balance"
    assert c.params["window_a"] == [0, 26]
    assert c.params["window_b"] == [26, 52]
    assert c.params["max_difference"] == 4
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /cv/scratch/u/watkina6/scheduler && PYTHONPATH=src:new_approach/src python -m pytest tests/test_palette_rules.py -v --tb=short`
Expected: 7 new tests FAIL with `KeyError` (converters not registered)

- [ ] **Step 3: Implement remaining converters in palette_rules.py**

Add to `src/scheduler/palette_rules.py`:

```python
def _convert_staffing_per_week(
    rule: dict[str, Any], lifecycle: ConstraintLifecycle
) -> list[SemanticConstraint]:
    name = rule["name"]
    strength = _parse_strength(rule["strength"])
    window = rule.get("window")
    weeks = WeekSpan(window[0], window[1]) if window else None

    return [SemanticConstraint(
        kind="staffing_per_week",
        lifecycle=lifecycle,
        strength=strength,
        fellows=FellowSelector.by_groups(*rule["groups"]),
        weeks=weeks,
        shifts=ShiftSet(name, tuple(rule["shifts"])),
        params={
            "name": name,
            "relation": rule["relation"],
            "count": rule["count"],
        },
    )]


def _convert_coverage_target(
    rule: dict[str, Any], lifecycle: ConstraintLifecycle
) -> list[SemanticConstraint]:
    name = rule["name"]
    strength = _parse_strength(rule["strength"])

    return [SemanticConstraint(
        kind="coverage_target",
        lifecycle=lifecycle,
        strength=strength,
        fellows=FellowSelector.by_groups(*rule["groups"]),
        shifts=ShiftSet(name, tuple(rule["shifts"])),
        params={
            "name": name,
            "max_uncovered_weeks": rule["max_uncovered_weeks"],
        },
    )]


def _convert_max_consecutive(
    rule: dict[str, Any], lifecycle: ConstraintLifecycle
) -> list[SemanticConstraint]:
    name = rule["name"]
    strength = _parse_strength(rule["strength"])

    return [SemanticConstraint(
        kind="max_consecutive",
        lifecycle=lifecycle,
        strength=strength,
        fellows=FellowSelector.by_groups(*rule["groups"]),
        shifts=ShiftSet(name, tuple(rule["shifts"])),
        params={
            "name": name,
            "weeks": rule["max_weeks"],
        },
    )]


def _convert_block_rotation(
    rule: dict[str, Any], lifecycle: ConstraintLifecycle
) -> list[SemanticConstraint]:
    name = rule["name"]
    strength = _parse_strength(rule["strength"])

    return [SemanticConstraint(
        kind="all_or_none_block",
        lifecycle=lifecycle,
        strength=strength,
        fellows=FellowSelector.by_groups(*rule["groups"]),
        shifts=ShiftSet(name, tuple(rule["shifts"])),
        params={
            "name": name,
            "block_size": rule["block_size"],
        },
    )]


def _convert_rotation_continuity(
    rule: dict[str, Any], lifecycle: ConstraintLifecycle
) -> list[SemanticConstraint]:
    name = rule["name"]
    strength = _parse_strength(rule["strength"])
    all_shifts = sorted({s for choice in rule["choices"] for s in choice})

    return [SemanticConstraint(
        kind="block_shift_set_choice",
        lifecycle=lifecycle,
        strength=strength,
        fellows=FellowSelector.by_groups(*rule["groups"]),
        shifts=ShiftSet(name, tuple(all_shifts)),
        params={
            "name": name,
            "block_size": rule["block_size"],
            "choices": rule["choices"],
            "allow_none": rule.get("allow_none", False),
        },
    )]


def _convert_prerequisite(
    rule: dict[str, Any], lifecycle: ConstraintLifecycle
) -> list[SemanticConstraint]:
    name = rule["name"]
    strength = _parse_strength(rule["strength"])
    all_shifts = rule["prerequisite_shifts"] + rule["target_shifts"]

    return [SemanticConstraint(
        kind="prerequisite",
        lifecycle=lifecycle,
        strength=strength,
        fellows=FellowSelector.by_groups(*rule["groups"]),
        shifts=ShiftSet(name, tuple(all_shifts)),
        params={
            "name": name,
            "prerequisite_shifts": rule["prerequisite_shifts"],
            "target_shifts": rule["target_shifts"],
            "min_prerequisite_weeks": rule["min_prerequisite_weeks"],
        },
    )]


def _convert_windowed_balance(
    rule: dict[str, Any], lifecycle: ConstraintLifecycle
) -> list[SemanticConstraint]:
    name = rule["name"]
    strength = _parse_strength(rule["strength"])

    return [SemanticConstraint(
        kind="windowed_balance",
        lifecycle=lifecycle,
        strength=strength,
        fellows=FellowSelector.by_groups(*rule["groups"]),
        shifts=ShiftSet(name, tuple(rule["shifts"])),
        params={
            "name": name,
            "window_a": rule["window_a"],
            "window_b": rule["window_b"],
            "max_difference": rule["max_difference"],
        },
    )]
```

Update the `_CONVERTERS` dict:

```python
_CONVERTERS: dict[str, Any] = {
    "shift_total": _convert_shift_total,
    "staffing_per_week": _convert_staffing_per_week,
    "coverage_target": _convert_coverage_target,
    "max_consecutive": _convert_max_consecutive,
    "block_rotation": _convert_block_rotation,
    "rotation_continuity": _convert_rotation_continuity,
    "prerequisite": _convert_prerequisite,
    "windowed_balance": _convert_windowed_balance,
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /cv/scratch/u/watkina6/scheduler && PYTHONPATH=src:new_approach/src python -m pytest tests/test_palette_rules.py -v --tb=short`
Expected: All 12 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/scheduler/palette_rules.py tests/test_palette_rules.py
git commit -m "feat: all 8 palette type converters"
```
