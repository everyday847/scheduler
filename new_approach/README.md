# parafrost-scheduler

Fast constraint-based fellowship schedule solver. Jointly optimizes weekly shift
assignments, weekend call, and night call using the RoundingSat pseudo-Boolean
solver.

## How it works

The solver encodes scheduling rules as pseudo-Boolean (PB) constraints in OPB
format. Each feasibility probe answers "is there a valid schedule with total
soft-penalty <= B?" in under a second for typical problem sizes (20K variables,
76K constraints). A linear scan over B finds the near-optimal schedule in
minutes rather than the hours required by Z3's `Optimize`.

Three layers of decision variables are solved simultaneously:

| Layer | Variables | Description |
|-------|-----------|-------------|
| Weekly shifts | `xs[fellow][week][shift]` | Who does what rotation each week |
| Weekend call | `wr[week][role][fellow]` | NCC1, NCC2, Stroke weekend assignments |
| Night call | `xn[day][fellow]` | Nightly on-call assignments (Mon-Sun) |

Cross-layer constraints link them: night blocking depends on the weekly service,
weekend eligibility depends on the weekday rotation, and the Friday/weekend NCC1
penalty is jointly optimized.

## Quick start

```bash
# From the repository root
cd new_approach

# Solve a full schedule (weekly + weekend + night)
PYTHONPATH=../src:src uv run python -m parafrost_scheduler.cli \
  --solver schedule \
  --annual-config ../config/annual/small-2025-2026.yaml \
  --standing-config ../config/standing/stanford-fellowship.yaml \
  --hard anaesthesia,friday_weekend_ncc1,sunday_following \
  schedule_output.csv
```

## Requirements

- Python 3.12+
- The `scheduler` package (parent directory) on PYTHONPATH
- RoundingSat binary at `vendor/roundingsat/build/roundingsat`

Build RoundingSat (one-time):
```bash
cd vendor/roundingsat
mkdir -p build && cd build
cmake .. && make -j$(nproc)
```

## Solver backends

| Mode | Flag | Input | What it solves |
|------|------|-------|----------------|
| Full schedule | `--solver schedule` | YAML configs | Weekly + weekend + night jointly |
| Joint | `--solver joint` | CSV (weekday matrix) | Weekend + night jointly |
| RoundingSat | `--solver roundingsat` | CSV (with weekends) | Night only (PB format) |
| ParaFROST | `--solver parafrost` | CSV (with weekends) | Night only (CNF format) |

## Project structure

```
src/parafrost_scheduler/
  schedule_solver.py    # Full joint solver (weekly + weekend + night)
  joint_solver.py       # Weekend + night solver (fixed weekday input)
  night_solver_pb.py    # Night-only PB solver
  night_solver.py       # Night-only CNF solver
  opb_encoder.py        # OPB formula builder
  roundingsat_runner.py # RoundingSat subprocess wrapper
  parafrost_runner.py   # ParaFROST subprocess wrapper
  cnf_builder.py        # DIMACS CNF formula builder
  workbook.py           # Excel workbook output
  cli.py                # Command-line interface

vendor/
  roundingsat/          # PB solver (recommended)
  ParaFROST/            # GPU-capable SAT solver
```
