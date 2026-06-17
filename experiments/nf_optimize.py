"""Optimize the NF model with the validated density/Elec/concentration levers.

Levers (all proven tractable / SAT in brainstorming probes):
  - Elec budget rules added (un-forbids Elec; precondition for any Elec).
  - HARD max-2-consecutive-OFF for JR/SR (off = no call + no working bg rotation;
    Elec/Vac/MICU count as WORKING, so 7-day Elec weeks are NOT punished). Forces
    dense NCC blocks via a local per-window clause (tractable, unlike cardinality caps).
  - SOFT penalty per NCC-labeled week for JR/SR (concentrate call → frees Elec weeks).
  - SOFT penalty per CCM NCC week (concentrate CCM into as few 4-week blocks as
    coverage allows; some blocks need 1 CCM, some 2 — not every week filled).
  - HARD Elec floor (config rule) optional via --elec-floor.

Runs RoundingSat in native optimize mode with a wall-clock --time-limit.

Usage:
  PYTHONPATH=src .venv/bin/python experiments/nf_optimize.py \
      --time-limit 300 --ncc-weight 10 --ccm-weight 10 --elec-floor 2 \
      [--prefix output_nf_opt]   # if set, also writes a workbook
"""

from __future__ import annotations

import argparse
import collections
import time
from pathlib import Path

import yaml

from parafrost_scheduler.experiment import assemble_config
from parafrost_scheduler.schedule_encoder import (
    build_full_schedule_opb, decode_solution, _day_to_week, CALL_ROLES,
    _build_off_indicator,
)
from parafrost_scheduler.schedule_types import day_of_week
from parafrost_scheduler.roundingsat_runner import RoundingSatRunner

REPO = Path(__file__).resolve().parent.parent
RUNNER = RoundingSatRunner(REPO / "vendor/roundingsat/build/roundingsat")
BASE_ANNUAL = REPO / "config/annual/ncc-nf-model.yaml"
STANDING = REPO / "config/standing/ncc-nf-model.yaml"


def _annual_with_elec(elec_floor: int) -> Path:
    base = yaml.safe_load(open(BASE_ANNUAL))
    for g in ("NCC_JR", "NCC_SR"):
        rule = {"type": "shift_total", "name": f"{g} Elec budget",
                "groups": [g], "shifts": ["Elec"], "relation": "at_least",
                "count": max(elec_floor, 0), "strength": "hard"}
        base["rules"].append(rule)
    out = Path("/tmp/nf-optimize-annual.yaml")
    yaml.safe_dump(base, open(out, "w"), sort_keys=False)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--time-limit", type=float, default=300.0)
    ap.add_argument("--ncc-weight", type=int, default=10)
    ap.add_argument("--ccm-weight", type=int, default=10)
    ap.add_argument("--max-off", type=int, default=0,
                    help="LEGACY max-consecutive-off lever (0=off; superseded by --rule-a)")
    ap.add_argument("--elec-floor", type=int, default=2)
    ap.add_argument("--ncc1-fullweek", action="store_true",
                    help="HARD: NCC1 constant across all 7 days/week per fellow (clean weekly block)")
    ap.add_argument("--ncc1-weekday", action="store_true",
                    help="HARD: NCC1 constant across the 5 weekdays only; weekend NCC1 free")
    ap.add_argument("--rule-a", type=int, default=0, metavar="CAP",
                    help="Rule A: <=CAP off days/week, exempt a full in-week NF rest (0=off; use 2)")
    ap.add_argument("--rule-b", type=int, default=0, metavar="MAXCALL",
                    help="Rule B: max MAXCALL consecutive call days (0=off; use 14)")
    ap.add_argument("--rule-c", action="store_true",
                    help="Rule C: CCM NF runs may not bridge a 4-week block boundary")
    ap.add_argument("--prefix", default=None)
    args = ap.parse_args()

    annual = _annual_with_elec(args.elec_floor)
    res = assemble_config(None, annual_path=annual, standing_path=STANDING, verbose=False)
    cfg = res[0] if isinstance(res, tuple) else res
    sd = cfg.start_dow

    opb, vm = build_full_schedule_opb(cfg, objective=False)
    si = {s: i for i, s in enumerate(vm.shifts)}
    nccsi = vm.shifts.index("NCC")
    jr = set(cfg.fellow_groups["NCC_JR"])
    sr = set(cfg.fellow_groups["NCC_SR"])
    ccm = set(cfg.fellow_groups.get("CCM", []))

    obj: list[tuple[int, int]] = []

    # HARD (optional): NCC1 constant within each week per fellow.
    #  --ncc1-fullweek : constant across ALL 7 days (one fellow holds NCC1 all week).
    #  --ncc1-weekday  : constant across the 5 WEEKDAYS only; weekend NCC1 free. This
    #     lets a fellow finish a weekday NCC1 block, take the weekend off, then start an
    #     NF run Monday (the NCC1→weekend-off→NF transition the user wants).
    if args.ncc1_fullweek or args.ncc1_weekday:
        diw = collections.defaultdict(list)
        for d in range(vm.num_days):
            diw[_day_to_week(d, sd)].append(d)
        for f in range(len(vm.fellow_names)):
            for w, days in diw.items():
                days = sorted(days)
                if args.ncc1_weekday:
                    days = [d for d in days if day_of_week(d, sd) < 5]  # Mon..Fri
                for a, b in zip(days, days[1:]):
                    # only chain consecutive calendar days (weekday filter may leave gaps)
                    if b - a != 1:
                        continue
                    va, vb = vm.call[a][f]["NCC1"], vm.call[b][f]["NCC1"]
                    opb.weighted_sum_at_least([(-va, 1), (vb, 1)], 1)
                    opb.weighted_sum_at_least([(-vb, 1), (va, 1)], 1)

    # --- CCM/rest model rules A/B/C (corrected encodings; default off) ---
    diw_all = collections.defaultdict(list)
    for d in range(vm.num_days):
        diw_all[_day_to_week(d, sd)].append(d)

    def _and_upper(lits):
        v = opb.new_var()
        for lit in lits:
            opb.weighted_sum_at_least([(-v, 1), (lit, 1)], 1)
        return v

    if args.rule_a or args.rule_b:
        for f, name in enumerate(vm.fellow_names):
            nf = [vm.call[d][f]["NF"] for d in range(vm.num_days)]
            off = [_build_off_indicator(opb, vm.call, vm.xs, si, cfg, f, d, sd)
                   for d in range(vm.num_days)]
            if args.rule_a:
                cap = args.rule_a
                rest = [None] * vm.num_days
                for d in range(vm.num_days):
                    terms = []
                    if d + 1 < vm.num_days:
                        terms.append(_and_upper([off[d], nf[d + 1]]))
                    if d - 1 >= 0:
                        terms.append(_and_upper([off[d], nf[d - 1]]))
                    if d - 2 >= 0:
                        nnf = opb.new_var()
                        opb.weighted_sum_at_least([(-nnf, 1), (-nf[d - 1], 1)], 1)
                        terms.append(_and_upper([off[d], nnf, nf[d - 2]]))
                    rv = opb.new_var()
                    if terms:
                        opb.weighted_sum_at_least([(t, 1) for t in terms] + [(-rv, 1)], 0)
                    else:
                        opb.add_unit(-rv)
                    rest[d] = rv
                for w, days in diw_all.items():
                    offs = [off[d] for d in days]
                    rests = [rest[d] for d in days]
                    extra = opb.new_var()
                    # (extra, -(cap+1)): sum(rests) - (cap+1)*extra >= 0  [NOT (-extra,..), vacuous]
                    opb.weighted_sum_at_least([(r, 1) for r in rests] + [(extra, -(cap + 1))], 0)
                    opb.weighted_sum_at_most([(o, 1) for o in offs] + [(extra, -1)], cap)
            if args.rule_b:
                cday = []
                for d in range(vm.num_days):
                    roles = [vm.call[d][f][r] for r in CALL_ROLES]
                    c = opb.new_var()
                    for rv in roles:
                        opb.weighted_sum_at_least([(-rv, 1), (c, 1)], 1)
                    opb.weighted_sum_at_least([(-c, 1)] + [(rv, 1) for rv in roles], 1)
                    cday.append(c)
                bwin = args.rule_b + 1
                for d in range(vm.num_days - bwin + 1):
                    opb.weighted_sum_at_least([(-cday[d + o], 1) for o in range(bwin)], 1)

    if args.rule_c:
        from parafrost_scheduler.schedule_encoder import _block_starts_grid
        starts = [bs for bs, _ in _block_starts_grid(vm.num_weeks, 4, 1)][1:]
        for f, name in enumerate(vm.fellow_names):
            if name not in ccm:
                continue
            for wk in starts:
                ds = diw_all.get(wk, [])
                if ds and min(ds) - 1 >= 0:
                    b = min(ds)
                    opb.at_most_k([vm.call[b - 1][f]["NF"], vm.call[b][f]["NF"]], 1)

    win = args.max_off + 1
    for f, name in enumerate(vm.fellow_names):
        if name in jr or name in sr:
            # HARD: no more than max_off consecutive off days (LEGACY lever; default off)
            if args.max_off:
                offv = [_build_off_indicator(opb, vm.call, vm.xs, si, cfg, f, d, sd)
                        for d in range(vm.num_days)]
                for d in range(vm.num_days - win + 1):
                    opb.weighted_sum_at_least([(-offv[d + o], 1) for o in range(win)], 1)
            # SOFT: penalize each NCC week (concentrate → Elec)
            for w in range(vm.num_weeks):
                wk = vm.xs[f][w][nccsi]
                if wk != 0:
                    obj.append((wk, args.ncc_weight))
        elif name in ccm:
            # SOFT block-level: penalize each ACTIVE 4-week CCM block (not each week),
            # so the optimizer minimizes the NUMBER of blocks used and packs each
            # active block densely. blk_active = OR(block's NCC week vars).
            # Block grid mirrors the config CCM block_rotation (size 4, offset 1).
            from parafrost_scheduler.schedule_encoder import _block_starts_grid
            for bs, be in _block_starts_grid(vm.num_weeks, 4, 1):
                bvars = [vm.xs[f][w][nccsi] for w in range(bs, be)
                         if vm.xs[f][w][nccsi] != 0]
                if not bvars:
                    continue
                blk = opb.new_var()
                # blk_active => OR: blk >= each week? No — blk=1 iff ANY week on.
                # week => blk:  (-wkvar,1)+(blk,1) >= 1
                for bv in bvars:
                    opb.weighted_sum_at_least([(-bv, 1), (blk, 1)], 1)
                # blk => some week:  (-blk,1)+sum(bvars) >= 1  (forces blk off when empty)
                opb.weighted_sum_at_least([(-blk, 1)] + [(bv, 1) for bv in bvars], 1)
                obj.append((blk, args.ccm_weight))

    # Keep the framework's own soft violations in the objective too.
    obj.extend(vm.soft_violations)
    opb.set_objective(obj)

    print(f"optimizing: time_limit={args.time_limit}s ncc_w={args.ncc_weight} "
          f"ccm_w={args.ccm_weight} max_off={args.max_off} elec_floor={args.elec_floor}")
    t = time.time()
    r = RUNNER.optimize(opb, time_limit=args.time_limit, echo_progress=True)
    dt = round(time.time() - t, 1)
    if r.assignment is None:
        print(f"NO INCUMBENT after {dt}s (optimal={getattr(r,'optimal',None)})")
        return 1
    sol = decode_solution(r.assignment, vm)

    perweek = collections.defaultdict(lambda: collections.defaultdict(int))
    for d, h in enumerate(sol.call_assignments_by_day):
        w = _day_to_week(d, sd)
        for role in CALL_ROLES:
            if h.get(role):
                perweek[w][h[role]] += 1

    print(f"\noptimize {dt}s optimal={r.optimal}")
    order = ([x for g in ("NCC_JR", "NCC_SR") for x in cfg.fellow_groups[g]]
             + list(cfg.fellow_groups.get("CCM", [])))
    for name in order:
        labels = sol.weekly_assignments[name]
        ncc = sum(1 for l in labels if l == "NCC")
        elec = sum(1 for l in labels if l == "Elec")
        blank = sum(1 for l in labels if l == "")
        nccdays = sum(perweek[w][name] for w in range(len(labels)) if labels[w] == "NCC")
        svc = sum(perweek[w][name] for w in range(len(labels)))
        dens = f"{nccdays/ncc:.2f}" if ncc else "-"
        print(f"    {name:16s} NCC={ncc:2d} Elec={elec:2d} blank={blank:2d} "
              f"dens={dens:>5s} svc-days={svc:3d}")

    if args.prefix:
        from parafrost_scheduler.workbook import write_nf_workbook
        out = Path(args.prefix + ".xlsx")
        write_nf_workbook(sol, cfg, out)
        print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
