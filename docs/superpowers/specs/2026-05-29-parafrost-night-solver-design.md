# ParaFROST Night Call Solver

## Summary

Replace Z3 with ParaFROST (a GPU-accelerated parallel SAT solver) for the night call scheduling problem. The new solver lives in `new_approach/` as a standalone package, reusing the existing `scheduler` package's data structures (`ParsedCallScheduleCsv`, `NightSolverConfig`, `NightScheduleSolution`, etc.).

ParaFROST is a pure SAT solver accepting DIMACS CNF input. The existing Z3 solver uses SMT-level features (integer arithmetic, pseudo-boolean sums). This design encodes all constraints as CNF clauses using Sinz's sequential counter for cardinality constraints, and drives optimization via iterative SAT calls with binary search.

## Architecture

Three layers, each independently testable:

```
cnf_builder.py          General-purpose CNF formula builder + sequential counter
        |
parafrost_runner.py     Subprocess wrapper: write DIMACS, invoke parafrost, parse result
        |
night_solver.py         Domain encoder: scheduling constraints → CNF → solve → NightScheduleSolution
        |
cli.py                  CLI matching night_call_solver_policy.py's interface
```

## Project Structure

```
new_approach/
├── pyproject.toml
├── src/
│   └── parafrost_scheduler/
│       ├── __init__.py
│       ├── cnf_builder.py
│       ├── parafrost_runner.py
│       ├── night_solver.py
│       └── cli.py
├── tests/
│   ├── test_cnf_builder.py
│   ├── test_parafrost_runner.py
│   └── test_night_solver.py
└── vendor/
    └── ParaFROST/                   # git clone of muhos/ParaFROST
```

The package depends on the parent `scheduler` package (installed editable) for `call_schedule_common`, `night_call_solver` data structures, and CSV I/O.

## Layer 1: CNF Builder (`cnf_builder.py`)

### Variable Allocation

- DIMACS uses 1-indexed positive integers for variables.
- `new_var() -> int` allocates a fresh variable ID.
- `new_vars(n: int) -> list[int]` allocates a batch.
- Internal counter starts at 1.

### Clause Management

- `add_clause(literals: list[int])` stores a disjunctive clause. Positive literal = variable true, negative = variable false.
- `clauses: list[list[int]]` stores all clauses.

### Exactly-One Constraint

`exactly_one(lits: list[int])`:
- At-least-one: single clause `[l1, l2, ..., ln]`.
- At-most-one: pairwise binary clauses `[-li, -lj]` for all i < j.
- For n=11 (typical fellow count), this produces 1 + C(11,2) = 56 clauses per day. With ~350 days, that's ~19,600 clauses — trivial.

### Sequential Counter Encoding

`at_most_k(lits: list[int], k: int)`:

Sinz's sequential counter introduces a register matrix `r[i][j]` for i in 1..n-1, j in 1..k, where `r[i][j]` means "at least j of the first i+1 input literals are true." Clauses:

1. `[-x[0], r[0][0]]` — if first input is true, register[0][0] is true
2. `[-r[0][j]]` for j > 0 — first row can't exceed 1
3. For i > 0:
   - `[-x[i], r[i][0]]` — if x[i] is true, at least 1 is true
   - `[-r[i-1][j-1], r[i][j]]` — propagate from previous row
   - `[-x[i], -r[i-1][j-1], r[i][j]]` — combine input + previous
4. `[-x[i], -r[i-1][k-1]]` — overflow prevention (the "at most k" part)

Auxiliary variables: `(n-1) * k`. Clauses: `O(n * k)`.

`at_least_k(lits, k)`: encode `at_most_(n-k)` over negated literals.

`exactly_k(lits, k)`: `at_most_k(lits, k)` + `at_least_k(lits, k)`.

### Weighted Sum Bound

`weighted_sum_at_most(weighted_lits: list[tuple[int, int]], bound: int)`:

For soft constraint optimization. Each tuple is `(literal, weight)`. Expands to unweighted: for a literal with weight `w`, create `w` auxiliary variables `a[1]..a[w]` and add implication clauses `[-lit, a[j]]` for each j. Then apply `at_most_k` over all auxiliary variables with the given bound.

This correctly counts: if the literal is true, all `w` auxiliaries are forced true, contributing `w` to the count. If false, the auxiliaries are unconstrained (but the at-most-k constraint may force some false).

### DIMACS Output

- `to_dimacs() -> str`: `p cnf <num_vars> <num_clauses>` header, one clause per line terminated with `0`.
- `write_dimacs(path: Path)`: write to file.

## Layer 2: ParaFROST Runner (`parafrost_runner.py`)

### `ParaFrostRunner`

```python
@dataclass(frozen=True)
class SolveResult:
    satisfiable: bool
    assignment: dict[int, bool] | None
    runtime_seconds: float
    stdout: str
    stderr: str
```

- `__init__(binary_path: str | Path)` — path to the `parafrost` executable.
- `solve(cnf: CnfBuilder, timeout: float | None = None) -> SolveResult`:
  1. Write CNF to a temporary `.cnf` file.
  2. Run `parafrost <file.cnf>` via `subprocess.run`, capture stdout/stderr.
  3. Parse output: `s SATISFIABLE` / `s UNSATISFIABLE` from stdout. On SAT, parse `v` lines for variable truth values.
  4. Return `SolveResult`.

### Output Parsing

ParaFROST follows the standard SAT competition output format:
- `s SATISFIABLE` or `s UNSATISFIABLE`
- `v <lit1> <lit2> ... 0` lines where positive = true, negative = false

### Binary Search

Each binary search iteration builds a fresh `CnfBuilder` with the current bound baked in as a hard constraint. No incremental solving across iterations (subprocess model). The base constraints (assignment, blocking, cardinality) are deterministic given the input, so reconstruction is cheap — the overhead is in the SAT solving, not the encoding.

## Layer 3: Night Solver (`night_solver.py`)

### Decision Variables

For each `(day_index, fellow_index)` pair, allocate one boolean variable:
```
x[d][f] = cnf.new_var()
```
where `d` ranges over `num_weeks * 7` days and `f` over `len(fellow_names)` fellows.

### Constraint Encoding

Maps directly from `_build_policy_solver` in `night_call_solver_policy.py`:

1. **Exactly-one per day:** `cnf.exactly_one([x[d][f] for f in range(num_fellows)])` for each day `d`.

2. **Blocking:** If fellow `f` is CCM, night-blocked, or holiday-ineligible on day `d`: `cnf.add_clause([-x[d][f]])` (unit clause forcing false).

3. **Total night counts:** `cnf.exactly_k([x[d][f] for d in range(num_days)], total)` for each fellow with a configured total.

4. **Friday night counts:** Same, but selecting only Friday indices (`d % 7 == 4`).

5. **No-3-consecutive:** For each fellow `f` and each window start `s`: `cnf.at_most_k([x[s][f], x[s+1][f], x[s+2][f]], 1)`. Since n=3, k=1, this is 3 binary clauses (pairwise).

6. **Multiset constraints:** For a multiset constraint over fellows `[A, B]` with possible value assignments `{(15,16), (16,15)}`:
   - Allocate a selector variable `sel` for each permutation.
   - `exactly_one([sel_1, sel_2])` — exactly one permutation holds.
   - For each permutation `p` with selector `sel_p` and target count `k` for fellow `f`:
     - Build conditional cardinality: add `sel_p` as a relaxation literal to the overflow/underflow clauses in the sequential counter for `exactly_k`. When `sel_p` is false, the cardinality constraint is trivially satisfied (relaxed). When true, it's enforced.
   - For the common case of 2 fellows with 2 values differing by 1 (e.g., 15/16): unconditionally enforce `at_least(min)` and `at_most(max)` for each fellow, then use the selector to tighten.

7. **Hard policy criteria:** If a criterion (anaesthesia, stroke, etc.) is hard for a given `(day, fellow)` pair: `cnf.add_clause([-x[d][f]])`.

8. **Soft policy criteria:** For each soft violation `(day, fellow, weight)`:
   - The indicator variable `x[d][f]` is the violation trigger.
   - Collect all `(x[d][f], weight)` pairs.
   - `cnf.weighted_sum_at_most(weighted_pairs, bound)` encodes the optimization bound.

### Solver Interface

```python
def solve_night_schedule_parafrost_incremental(
    parsed: ParsedCallScheduleCsv,
    *,
    config: NightSolverConfig = NightSolverConfig(),
    hard_criteria: frozenset[str] = frozenset(),
    weights: NightPolicyWeights = NightPolicyWeights(),
    max_violation_limit: int | None = None,
    parafrost_path: str | Path = "vendor/ParaFROST/build/parafrost",
    emit_summary: bool = True,
) -> NightPolicySolveResult:
```

Binary search loop:
1. Compute upper bound (same `_weighted_upper_bound` logic).
2. `low = 0`, `high = upper_bound`.
3. While `low <= high`: build CNF with `mid = (low+high)//2`, solve. If SAT, record best and `high = mid - 1`. Else `low = mid + 1`.
4. Extract assignment from best model, build `NightScheduleSolution`.

Also provides `solve_night_schedule_parafrost_at_limit` (single solve without optimization) and `run_staged_policy_schedules` (parallel staged runs via ProcessPoolExecutor).

### Result Extraction

Given a `SolveResult` with `assignment: dict[int, bool]`, map back to fellow names:
```python
for day_index in range(num_days):
    for fellow_index in range(num_fellows):
        if assignment[x[day_index][fellow_index]]:
            week_assignments[role] = fellow_names[fellow_index]
```

Returns `NightPolicySolveResult` with the same structure as the Z3 solver.

## Layer 4: CLI (`cli.py`)

Mirrors `night_call_solver_policy.py`'s CLI exactly:

```
python -m parafrost_scheduler input.csv output.csv [options]
```

Arguments:
- `input_csv`, `output` — same as existing
- `--staged` — run five staged hardening policies in parallel
- `--workers N` — worker count for `--staged`
- `--hard CRITERIA` — comma-separated hard criteria
- `--max-soft N` — maximum weighted soft score
- `--no-optimize` — skip binary search
- `--stroke-weight`, `--clinic-weight`, etc. — weight flags
- `--parafrost-path PATH` — path to parafrost binary (default: `vendor/ParaFROST/build/parafrost`)

Output: same CSV format via `write_night_schedule_csv`, same progress reporting.

## Building ParaFROST

Clone into `vendor/ParaFROST/`:
```bash
git clone https://github.com/muhos/ParaFROST.git vendor/ParaFROST
cd vendor/ParaFROST
./install.sh -c   # CPU-only build (or -g for GPU if CUDA available)
```

The binary lands at `build/parafrost` (or similar, confirmed at build time).

## Testing Strategy

1. **`test_cnf_builder.py`:** Unit tests for sequential counter correctness. Encode small cardinality problems, verify with a known-good solver (or exhaustive enumeration for tiny cases). Test `exactly_k`, `at_most_k`, `weighted_sum_at_most`.

2. **`test_parafrost_runner.py`:** Integration test — requires the built `parafrost` binary. Solve a trivial CNF, verify SAT/UNSAT detection and assignment parsing.

3. **`test_night_solver.py`:** End-to-end tests using the same test fixtures from `test_night_call_solver_policy.py`. Verify that the ParaFROST solver produces solutions satisfying the same constraints as Z3. Compare `criteria_counts_for_solution` outputs.

## Performance Expectations

- Problem size: ~3,850 decision variables + auxiliary variables from sequential counters.
- Sequential counter for total night counts (e.g., exactly 60 of 350): adds ~350×60 = 21,000 auxiliary vars and ~21,000 clauses per fellow. With 8 fellows constrained, that's ~168,000 aux vars.
- Total formula: estimated ~200,000 variables, ~500,000 clauses. Well within ParaFROST's capacity.
- Binary search: ~log2(upper_bound) SAT calls, each rebuilding the formula. Upper bound is typically <100 weighted violations, so ~7 iterations.
- Expected wall-clock per SAT call: seconds (CPU) to sub-second (GPU), vs. Z3's minutes for the optimization version.

## Dependencies

- `scheduler` package (editable install from parent directory)
- ParaFROST binary (built from source in `vendor/`)
- Python standard library only (no additional pip dependencies)
