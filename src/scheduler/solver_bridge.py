"""Bridge between the web API / annual YAML and the RoundingSat schedule solver."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict

import yaml

from .annual_rules import constraints_from_config as annual_constraints_from_config
from .palette_rules import palette_rule_to_constraints
from .palette_derivations import derive_forbidden_shifts
from .shift_palette import ShiftPalette
from .standing_rules import constraints_from_config as standing_constraints_from_config

from parafrost_scheduler.orchestrator import (
    build_night_config_from_request,
    build_weekend_config_from_request,
    get_runner as _orchestrator_runner,
)
from parafrost_scheduler.schedule_types import ScheduleSolverConfig

_REPO = Path(__file__).resolve().parents[2]
CONFIG_DIR = _REPO / "config"
STANDING_RULE_CONFIG = CONFIG_DIR / "standing" / "stanford-fellowship-v3.yaml"
DEFAULT_ANNUAL_CONFIG = CONFIG_DIR / "annual" / "my-2026-2027-v3.yaml"


def build_solver_config_from_request(
    raw_request: Dict[str, Any] | None,
    *,
    annual_path: Path | None = None,
    standing_path: Path | None = None,
) -> ScheduleSolverConfig:
    """Build a ScheduleSolverConfig from web request JSON.

    The request can be:
    - A full annual config (loaded from disk via the API, then edited by the user)
    - Or a minimal request with just fellow_groups + shifts + fellow_week_pairs

    Standing rules are always loaded from disk.
    """
    if raw_request is None:
        raise ValueError("Request body must be JSON.")

    fellow_groups = _require_dict(raw_request, "fellow_groups")
    shifts = raw_request.get("shifts", [])
    if not shifts:
        raise ValueError("shifts must be a non-empty list.")
    fellow_week_pairs = raw_request.get("fellow_week_pairs", {})
    annual_rules = raw_request.get("annual_rules")

    # Standing rules: use overrides from request if provided, else load from disk
    standing_rules_override = raw_request.get("standing_rules")
    if standing_rules_override is not None:
        standing_config = {"rules": standing_rules_override}
    else:
        standing_path = standing_path or STANDING_RULE_CONFIG
        standing_config = yaml.safe_load(standing_path.read_text())

    # Detect palette v2 format: rules have "type" key instead of "kind"
    standing_rules_list = standing_config.get("rules", [])
    is_palette_format = (
        standing_rules_list
        and isinstance(standing_rules_list[0], dict)
        and "type" in standing_rules_list[0]
    )

    if is_palette_format:
        constraints = _build_palette_constraints(
            standing_config, raw_request, fellow_groups, shifts, fellow_week_pairs,
        )
    else:
        constraints = [
            *standing_constraints_from_config(standing_config),
            *annual_constraints_from_config(annual_rules, fellow_week_pairs=fellow_week_pairs),
        ]

    all_fellows = {name: group for group, names in fellow_groups.items() for name in names}

    # Merge night_rules and weekend_rules from standing config and raw_request
    # (annual overrides standing, same pattern as regular rules)
    if is_palette_format:
        standing_night_rules = standing_config.get("night_rules", [])
        annual_night_rules = raw_request.get("night_rules", [])
        merged_night_rules = annual_night_rules if annual_night_rules else standing_night_rules
        if merged_night_rules:
            raw_request = {**raw_request, "night_rules": merged_night_rules}

        standing_weekend_rules = standing_config.get("weekend_rules", [])
        annual_weekend_rules = raw_request.get("weekend_rules", [])
        merged_weekend_rules = annual_weekend_rules if annual_weekend_rules else standing_weekend_rules
        if merged_weekend_rules:
            raw_request = {**raw_request, "weekend_rules": merged_weekend_rules}

    night_config = build_night_config_from_request(raw_request, fellow_groups)
    weekend_config = build_weekend_config_from_request(raw_request, fellow_groups)

    # night_gating / weekend_gating rules → typed SemanticConstraints. Targets
    # are resolved against the palette HERE so the schedule_rules leaf stays
    # palette-free (one resolved source for encode + evaluate). These are Standing
    # structural rules, so they are sourced from the standing config and always
    # apply even when the annual config overrides the tunable night/weekend rules;
    # an annual night_gating entry overrides a standing one by `criterion`.
    if is_palette_format:
        palette_for_gating = ShiftPalette.from_config(
            raw_request.get("shift_palette") or standing_config.get("shift_palette")
        )
        # Standing night_gating rules survive the standing_rules override path via
        # the request's `standing_night_rules` copy (set by experiment.assemble_*),
        # falling back to the on-disk standing config when present.
        standing_night_rules = (
            raw_request.get("standing_night_rules")
            or standing_config.get("night_rules", [])
        )
        gating_night_rules = _merge_gating_rules(
            standing_night_rules,
            raw_request.get("night_rules", []),
            kind="night_gating",
        )
        standing_weekend_rules = (
            raw_request.get("standing_weekend_rules")
            or standing_config.get("weekend_rules", [])
        )
        gating_weekend_rules = _merge_gating_rules(
            standing_weekend_rules,
            raw_request.get("weekend_rules", []),
            kind="weekend_gating",
        )
        # weekend_night rules (the WeekendNightCriterion archetype) live in the
        # night_rules block (they gate a weekend night) → ride standing_night_rules.
        weekend_night_rules = _merge_gating_rules(
            standing_night_rules,
            raw_request.get("night_rules", []),
            kind="weekend_night",
        )
        constraints.extend(_build_gating_constraints(
            gating_night_rules, gating_weekend_rules, weekend_night_rules,
            palette_for_gating))

    locked_assignments = raw_request.get("locked_assignments", {})
    # The `call_rules` channel was fully dissolved into the typed `rules:`
    # pipeline. A request that still carries call_rules is stale config — reject
    # it loudly (active entries only) rather than silently dropping it, so a
    # caller is told to move the rule to `rules:` (the field is retained, always
    # empty, only so existing ScheduleSolverConfig constructors keep their kwarg).
    stale_call_rules = [r for r in raw_request.get("call_rules", [])
                        if isinstance(r, dict) and r.get("active", True)]
    if stale_call_rules:
        types = sorted({r.get("type") for r in stale_call_rules})
        raise ValueError(
            f"`call_rules` is no longer a supported channel (types {types}); these "
            f"rules were dissolved into the typed `rules:` pipeline. Move them to "
            f"`rules:` (per-fellow counts → shift_total with a fellow: selector; "
            f"pins/blocks/prerequisites/dual_stroke_window keep their type string)."
        )
    call_rules: list[dict] = []

    # Compute calendar model from horizon_start
    horizon_start_str = raw_request.get("horizon_start", "2026-07-01")
    if isinstance(horizon_start_str, str):
        horizon_date = date.fromisoformat(horizon_start_str)
    else:
        horizon_date = date(2026, 7, 1)

    start_dow = horizon_date.weekday()  # 0=Mon, 6=Sun

    # Academic year: from horizon_start to one year later minus one day
    horizon_end = date(horizon_date.year + 1, horizon_date.month, horizon_date.day) - timedelta(days=1)
    num_days = (horizon_end - horizon_date).days + 1  # 365 or 366

    # Shift Attributes (ADR-0003): the shift_palette block drives the historical
    # night/weekend shift-name frozensets. Prefer the request's copy (it survives
    # the standing_rules override path used by experiment.assemble_config); else
    # take it from the standing config loaded from disk.
    palette_block = raw_request.get("shift_palette")
    if palette_block is None:
        palette_block = standing_config.get("shift_palette")
    shift_palette = ShiftPalette.from_config(palette_block)

    return ScheduleSolverConfig(
        fellow_groups=fellow_groups,
        shifts=shifts,
        constraints=constraints,
        night_config=night_config,
        weekend_config=weekend_config,
        locked_assignments=locked_assignments,
        call_rules=call_rules,
        start_dow=start_dow,
        num_days=num_days,
        shift_palette=shift_palette,
    )


def load_annual_config(path: Path | None = None) -> Dict[str, Any]:
    """Load an annual config YAML file and return as a dict."""
    path = path or DEFAULT_ANNUAL_CONFIG
    data = yaml.safe_load(path.read_text())
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a YAML mapping.")
    return data


def list_configs() -> Dict[str, list[str]]:
    """List available config files."""
    annual_dir = CONFIG_DIR / "annual"
    standing_dir = CONFIG_DIR / "standing"
    return {
        "annual": sorted(f.name for f in annual_dir.glob("*.yaml") if not f.name.startswith(".")),
        "standing": sorted(f.name for f in standing_dir.glob("*.yaml") if not f.name.startswith(".")),
    }


def get_runner():
    """Get a RoundingSatRunner instance (delegates to orchestrator)."""
    return _orchestrator_runner()


# ---------------------------------------------------------------------------
# Palette v2 constraint builder
# ---------------------------------------------------------------------------

def _build_palette_constraints(
    standing_config: Dict[str, Any],
    request: Dict[str, Any],
    fellow_groups: Dict[str, list[str]],
    shifts: list[str],
    fellow_week_pairs: Dict[str, list[int]],
) -> list:
    """Build constraints from palette-format (v2) configs."""
    from .semantic_constraints import ConstraintLifecycle, ConstraintStrength, FellowSelector, SemanticConstraint, ShiftSet, WeekSpan
    from .annual_rules import vacation_request_constraints

    constraints = []

    # Standing rules (palette format)
    for rule in standing_config.get("rules", []):
        if not rule.get("active", True):
            continue
        if rule.get("type") == "full_assignment":
            constraints.append(SemanticConstraint(
                kind="full_assignment",
                lifecycle=ConstraintLifecycle.STANDING_RULE,
                strength=ConstraintStrength.HARD,
                fellows=FellowSelector.by_groups(*rule["groups"]),
                params={"name": rule["name"]},
            ))
            continue
        constraints.extend(palette_rule_to_constraints(
            rule, lifecycle=ConstraintLifecycle.STANDING_RULE,
        ))

    # Annual rules (palette format)
    annual_rules = request.get("rules", [])
    if not annual_rules:
        annual_section = request.get("annual_rules")
        if annual_section and isinstance(annual_section, dict):
            annual_rules = annual_section.get("rules", [])

    for rule in annual_rules:
        if not rule.get("active", True):
            continue
        if rule.get("type") == "vacation_request_policy":
            constraints.extend(vacation_request_constraints(
                fellow_week_pairs,
                hard_request_count=rule.get("hard_request_count", 3),
            ))
            continue
        if rule.get("type") == "specific_assignment" and rule.get("fellow"):
            from .annual_rules import named_assignment
            constraints.append(named_assignment(
                rule["fellow"],
                week=rule["week"],
                shift=rule["shift"],
                hard=rule.get("strength", "hard") == "hard",
                params={"name": rule.get("name", "specific_assignment")},
            ))
            continue
        migrated = _migrated_call_rule_to_constraint(rule)
        if migrated is not None:
            constraints.append(migrated)
            continue
        constraints.extend(palette_rule_to_constraints(
            rule, lifecycle=ConstraintLifecycle.ANNUAL_RULE,
        ))

    # Derive forbidden shifts from shift_total rules (+ per_fellow call_rules)
    all_rules = standing_config.get("rules", []) + annual_rules
    constraints.extend(derive_forbidden_shifts(all_rules, shifts, fellow_groups,
                                               call_rules=request.get("call_rules", [])))

    return constraints


def _merge_gating_rules(standing: list, annual: list, *, kind: str) -> list:
    """Merge standing + annual gating rules of the given `kind` by `criterion`:
    an annual entry overrides the standing one for the same criterion; standing
    entries with no annual override survive (unlike the all-or-nothing override
    the tunable night/weekend rules use). Non-gating types are dropped here —
    they ride the separate NightSolverConfig/WeekendSolverConfig path."""
    def gating_only(rules):
        return [r for r in rules if r.get("type") == kind]

    merged = {r["criterion"]: r for r in gating_only(standing)}
    for r in gating_only(annual):
        merged[r["criterion"]] = r
    return list(merged.values())


def _build_gating_constraints(
    night_rules: list, weekend_rules: list, weekend_night_rules: list, palette,
) -> list:
    """Convert night_gating + weekend_gating + weekend_night rules into typed
    SemanticConstraints, resolving targets against the palette."""
    from .palette_rules import (
        night_gating_rule_to_constraint,
        weekend_gating_rule_to_constraint,
        weekend_night_rule_to_constraint,
    )
    from .semantic_constraints import ConstraintLifecycle

    out = []
    for rules, convert in (
        (night_rules, night_gating_rule_to_constraint),
        (weekend_rules, weekend_gating_rule_to_constraint),
        (weekend_night_rules, weekend_night_rule_to_constraint),
    ):
        for rule in rules:
            con = convert(rule, palette, lifecycle=ConstraintLifecycle.STANDING_RULE)
            if con is not None:
                out.append(con)
    return out


def _migrated_call_rule_to_constraint(rule: Dict[str, Any]):
    """Convert an annual call-rule entry (migrated out of the legacy `call_rules:`
    channel into `rules:`) into a typed SemanticConstraint routed to a co-located
    Rule Shape. Returns None for any other rule type (the caller falls through to
    the palette converter).

    Each kind keeps its original type string; coordinates the typed schema has no
    first-class field for (dates, weeks, role, groups, exempt lists) ride in
    params, which is exactly what the registered encode handlers read. Strength
    mirrors the rule (default hard — the legacy call_rules path was always hard)."""
    from .semantic_constraints import (
        ConstraintLifecycle, ConstraintStrength, FellowSelector, SemanticConstraint, ShiftSet,
    )

    rule_type = rule.get("type")
    name = rule.get("name", rule_type)
    strength = ConstraintStrength(rule.get("strength", "hard"))
    ANNUAL = ConstraintLifecycle.ANNUAL_RULE
    STANDING = ConstraintLifecycle.STANDING_RULE

    if rule_type == "per_fellow_shift_total":
        # Single-fellow count band → the shift_total archetype (kind "shift_total").
        return SemanticConstraint(
            kind="shift_total",
            lifecycle=ANNUAL,
            strength=strength,
            fellows=FellowSelector.by_names(rule["fellow"]),
            shifts=ShiftSet(name, tuple(rule["shifts"])),
            params={"name": name, "relation": rule["relation"], "count": rule["count"]},
        )

    if rule_type == "group_night_requirement":
        return SemanticConstraint(
            kind="group_night_requirement",
            lifecycle=ANNUAL,
            strength=strength,
            fellows=None,
            params={"name": name, "groups": rule.get("groups", []),
                    "dates": rule.get("dates", [])},
        )

    if rule_type in ("specific_night_assignment", "blocked_night"):
        return SemanticConstraint(
            kind=rule_type,
            lifecycle=ANNUAL,
            strength=strength,
            fellows=FellowSelector.by_names(rule["fellow"]),
            params={"name": name, "dates": rule.get("dates", [])},
        )

    if rule_type == "friday_call_assignment":
        return SemanticConstraint(
            kind="friday_call_assignment",
            lifecycle=ANNUAL,
            strength=strength,
            fellows=FellowSelector.by_names(rule["fellow"]),
            params={"name": name, "weeks": rule.get("weeks", [])},
        )

    if rule_type == "specific_weekend_assignment":
        return SemanticConstraint(
            kind="specific_weekend_assignment",
            lifecycle=ANNUAL,
            strength=strength,
            fellows=FellowSelector.by_names(rule["fellow"]),
            params={"name": name, "role": rule["role"], "weeks": rule.get("weeks", [])},
        )

    if rule_type == "blocked_weekend":
        return SemanticConstraint(
            kind="blocked_weekend",
            lifecycle=ANNUAL,
            strength=strength,
            fellows=FellowSelector.by_names(rule["fellow"]),
            params={"name": name, "weeks": rule.get("weeks", [])},
        )

    if rule_type in ("weekend_stroke_prerequisite", "weekend_ncc_prerequisite"):
        # Program-structural → Standing tier. Encode handler reads exempt_* from
        # params and branches role-set/prereq-shift-set on the kind.
        return SemanticConstraint(
            kind=rule_type,
            lifecycle=STANDING,
            strength=strength,
            fellows=None,
            params={"name": name,
                    "exempt_fellows": rule.get("exempt_fellows", []),
                    "exempt_groups": rule.get("exempt_groups", [])},
        )

    if rule_type == "dual_stroke_window":
        # Windowed opportunistic supervision on Stroke (a weekly-layer xs rule).
        # Encode handler reads window/supervisors from params and splits the
        # week's Stroke pool by the supervisors list.
        return SemanticConstraint(
            kind="dual_stroke_window",
            lifecycle=STANDING,
            strength=strength,
            fellows=None,
            params={"name": name,
                    "window": rule.get("window", [0, 0]),
                    "supervisors": rule.get("supervisors", [])},
        )

    return None


def _require_dict(request: Dict[str, Any], key: str) -> Dict[str, list[str]]:
    value = request.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"{key} must be an object.")
    return {k: list(v) for k, v in value.items()}
