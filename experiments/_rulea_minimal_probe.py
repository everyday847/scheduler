"""Minimal isolation of the Rule A off-cap. Build the FULL model with nf_week_off_cap=2,
pin JR1 wk13 to 4 call days + 3 off + 0 NF, write the OPB, and ALSO dump the exact off-cap
constraints touching JR1's week-13 off vars. Run the solve on Slurm. If SAT, the off-cap
encoding does not bind and we print WHY (which constraint should have fired)."""
from pathlib import Path
import yaml, collections
from parafrost_scheduler.experiment import assemble_config
from parafrost_scheduler.schedule_encoder import build_full_schedule_opb, _day_to_week, CALL_ROLES, _build_off_indicator
from parafrost_scheduler.roundingsat_runner import RoundingSatRunner
REPO=Path('.').resolve()
base=yaml.safe_load(open('config/annual/ncc-nf-model.yaml')); base.setdefault('solver_options',{})['nf_week_off_cap']=2
yaml.safe_dump(base,open('results_slurm/_ra_min.yaml','w'),sort_keys=False)
cfg=assemble_config(None,annual_path=Path('results_slurm/_ra_min.yaml'),standing_path=Path('config/standing/ncc-nf-model.yaml'),verbose=False)
cfg=cfg[0] if isinstance(cfg,tuple) else cfg
opb,vm=build_full_schedule_opb(cfg,objective=False)
f=vm.fellow_names.index('JR1')
on={89:'NCC1',91:'NCC2',92:'NCC1',93:'NCC1'}
for d in range(89,96):
    for role in CALL_ROLES:
        v=vm.call[d][f][role]
        opb.add_unit(v if on.get(d)==role else -v)
# find off-cap <=3 lines mentioning JR1 wk13 off vars — we can't easily know var ids, but
# we can re-derive: rebuild off indicators for those days on a SEPARATE opb won't match ids.
# Instead just count off-cap-shaped lines and show those ending '<= 3 ;' with a ~x.
ups=[c for c in opb._constraints if c.strip().endswith('<= 3 ;')]
print('TOTAL <=3 lines:',len(ups))
print('sample <=3 lines (first 2):')
for c in ups[:2]: print('  ',c)
r=RoundingSatRunner(REPO/'vendor/roundingsat/build/roundingsat').solve(opb,timeout=600)
print('PINNED lone-3off result:', 'SAT (BUG: off-cap not binding)' if r.satisfiable else 'UNSAT (off-cap binds)')
