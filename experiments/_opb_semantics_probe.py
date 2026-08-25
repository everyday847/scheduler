"""Definitive: does this RoundingSatRunner honor negated literals (~x)? Build a tiny OPB
in-process and solve via the SAME runner the tests use (not a bare binary call)."""
from pathlib import Path
from parafrost_scheduler.opb_encoder import OpbBuilder
from parafrost_scheduler.roundingsat_runner import RoundingSatRunner
REPO=Path('.').resolve()
R=RoundingSatRunner(REPO/'vendor/roundingsat/build/roundingsat')

# Test 1: negated literals. x1=x2=x3=1 forced; sum(~x)>=1 must be UNSAT if ~x honored.
o=OpbBuilder(); a,b,c=o.new_var(),o.new_var(),o.new_var()
o.add_unit(a); o.add_unit(b); o.add_unit(c)
o.weighted_sum_at_least([(-a,1),(-b,1),(-c,1)],1)
print('T1 negated-literal at_least  :', 'UNSAT (~x honored)' if not R.solve(o,timeout=30).satisfiable else 'SAT (~x BROKEN)')

# Test 2: the off-cap shape. 4 of 7 'off' forced on; extra forced 0; sum(~off)+extra >= 5.
# 7 off-vars, force 4 to 1 (off) and 3 to 0 => sum(~off)=3; +extra(0) => 3 >= 5 UNSAT if honored.
o=OpbBuilder(); offs=[o.new_var() for _ in range(7)]; extra=o.new_var()
for i in range(4): o.add_unit(offs[i])         # 4 off
for i in range(4,7): o.add_unit(-offs[i])      # 3 not-off
o.add_unit(-extra)                              # extra=0
o.weighted_sum_at_least([(-x,1) for x in offs]+[(extra,1)],5)   # sum(~off)+extra>=5
print('T2 off-cap >= form (4 off,e=0):', 'UNSAT (cap binds)' if not R.solve(o,timeout=30).satisfiable else 'SAT (cap BROKEN)')

# Test 3: same but 2 off (legal) => sum(~off)=5 >= 5 SAT.
o=OpbBuilder(); offs=[o.new_var() for _ in range(7)]; extra=o.new_var()
for i in range(2): o.add_unit(offs[i])
for i in range(2,7): o.add_unit(-offs[i])
o.add_unit(-extra)
o.weighted_sum_at_least([(-x,1) for x in offs]+[(extra,1)],5)
print('T3 off-cap >= form (2 off,e=0):', 'SAT (correct)' if R.solve(o,timeout=30).satisfiable else 'UNSAT (over-tight)')
