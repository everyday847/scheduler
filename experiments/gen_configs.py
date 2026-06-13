"""Generate alternate annual config files for the constraint experiments.

Loads the shipped annual YAML, applies each experiment's DELTA (additive rules /
toggles), and writes a variant file under config/annual/exp/. The shipped
config/annual/my-2026-2027-v3.yaml is never mutated.

Experiments (composable):
  S  Sunday-night-before-Vac HARD, Vac-ONLY. Adds a `sunday_following_vac`
     night_gating rule (Sunday dow6, week_offset 1, target Vac) and hardens it via
     solver_options.night_hard_criteria. The broader 7-shift `sunday_following`
     rule (standing) stays soft.
  B  SCVMC Rehab as a contiguous 2-week BLOCK. Adds a `no_isolated_week` rule for
     SCVMC Rehab over the STROKE group: a SCVMC week must be adjacent to another
     SCVMC week. The existing "exactly 2 SCVMC weeks" rules fix the count; B forces
     those two weeks ADJACENT, ANYWHERE (no fixed even-week alignment — the
     block_rotation alignment proved very hard; consecutivity-only is SAT @600s).
  W  Weekend-OFF forbids (blocked_weekend, hard): Aditya wks {5,38,47},
     Helena wk 43, Harneet wk 16.

Week indices were derived from the wb7 horizon (2026-07-01, start_dow 2): see
memory project_wb7_horizon_calendar. Run: PYTHONPATH=src python experiments/gen_configs.py
"""

from __future__ import annotations

import copy
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
SHIPPED_ANNUAL = REPO / "config/annual/my-2026-2027-v3.yaml"
OUT_DIR = REPO / "config/annual/exp"

# Full fellow names (must match the workbook import exactly).
ADITYA = "Aditya Srivatsan"
HELENA = "Helena Xeros"
HARNEET = "Harneet Dhillon"


def _sunday_vac_night_rule() -> dict:
    """A Vac-only split of the sunday_following night_gating rule."""
    return {
        "name": "Sunday night before Vac (hard, Vac-only)",
        "type": "night_gating",
        "criterion": "sunday_following_vac",
        "description": (
            "Sunday night rolling into next week's Vacation. Split out of the broader "
            "soft sunday_following rule so it can be hardened independently."),
        "terms": [
            {"dows": [6], "week_offset": 1, "target_key": "sunday_vac",
             "target_shifts": ["Vac"]},
        ],
        "active": True,
    }


def _scvmc_block_rule() -> dict:
    """SCVMC Rehab contiguous: no isolated SCVMC week (block >=2, no even-week
    alignment). With the existing exactly-2 total this makes a 2-week block."""
    return {
        "name": "SCVMC contiguous (no isolated week)",
        "type": "no_isolated_week",
        "groups": ["STROKE"],
        "shifts": ["SCVMC Rehab"],
        "strength": "hard",
        "active": True,
    }


def _blocked_weekend(fellow: str, weeks: list[int], label: str) -> dict:
    return {
        "name": f"{label} weekend off (wks {','.join(map(str, weeks))})",
        "type": "blocked_weekend",
        "fellow": fellow,
        "weeks": weeks,
        "strength": "hard",
        "active": True,
    }


def _weekend_off_rules() -> list[dict]:
    return [
        _blocked_weekend(ADITYA, [5, 38, 47], "Aditya"),
        _blocked_weekend(HELENA, [43], "Helena"),
        _blocked_weekend(HARNEET, [16], "Harneet"),
    ]


def apply_S(cfg: dict) -> None:
    cfg.setdefault("night_rules", []).append(_sunday_vac_night_rule())
    so = cfg.setdefault("solver_options", {}) or {}
    # Default hard criteria are anaesthesia + friday_weekend_ncc1; preserve them and
    # add the Vac-only criterion.
    hard = set(so.get("night_hard_criteria", ["anaesthesia", "friday_weekend_ncc1"]))
    hard.add("sunday_following_vac")
    so["night_hard_criteria"] = sorted(hard)
    cfg["solver_options"] = so


def apply_B(cfg: dict) -> None:
    cfg.setdefault("rules", []).append(_scvmc_block_rule())


def apply_W(cfg: dict) -> None:
    cfg.setdefault("rules", []).extend(_weekend_off_rules())


APPLY = {"S": apply_S, "B": apply_B, "W": apply_W}

# The variants to generate: each is a subset of {S,B,W} applied in S,B,W order.
VARIANTS = ["S", "B", "W", "SB", "SW", "BW", "SBW"]


def main() -> None:
    base = yaml.safe_load(SHIPPED_ANNUAL.read_text())
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for variant in VARIANTS:
        cfg = copy.deepcopy(base)
        for letter in variant:
            APPLY[letter](cfg)
        out = OUT_DIR / f"exp-{variant}.yaml"
        out.write_text(yaml.safe_dump(cfg, sort_keys=False, width=120))
        print(f"wrote {out.relative_to(REPO)}")


if __name__ == "__main__":
    main()
