"""Phase-1 feasibility probe for the NF model (full year). objective=False +
solve() => first model / proven UNSAT (the correct feasibility method)."""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

from parafrost_scheduler.experiment import assemble_config
from parafrost_scheduler.schedule_encoder import build_full_schedule_opb
from parafrost_scheduler.roundingsat_runner import RoundingSatRunner

REPO = Path(__file__).resolve().parent.parent
ROUNDINGSAT = REPO / "vendor/roundingsat/build/roundingsat"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--annual", default=str(REPO / "config/annual/ncc-nf-model.yaml"))
    ap.add_argument("--standing", default=str(REPO / "config/standing/ncc-nf-model.yaml"))
    ap.add_argument("--timeout", type=float, default=3600.0)
    args = ap.parse_args()

    res_cfg = assemble_config(None, annual_path=Path(args.annual),
                              standing_path=Path(args.standing), verbose=True)
    config = res_cfg[0] if isinstance(res_cfg, tuple) else res_cfg
    opb, vm = build_full_schedule_opb(config, objective=False)
    runner = RoundingSatRunner(ROUNDINGSAT)
    print(f"[nf-probe] constraints={opb.num_constraints} call_days={len(vm.call)} "
          f"timeout={args.timeout}s", flush=True)
    t0 = time.perf_counter()
    try:
        r = runner.solve(opb, timeout=args.timeout)
    except subprocess.TimeoutExpired:
        print(f"RESULT UNKNOWN(timeout) elapsed={time.perf_counter()-t0:.1f}s", flush=True)
        return 0
    print(f"RESULT {'SAT' if r.satisfiable else 'UNSAT'} elapsed={time.perf_counter()-t0:.1f}s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
