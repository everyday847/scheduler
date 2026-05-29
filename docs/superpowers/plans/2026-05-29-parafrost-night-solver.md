# ParaFROST Night Call Solver Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a standalone package in `new_approach/` that solves the night call scheduling problem using the ParaFROST GPU-accelerated SAT solver instead of Z3, providing the same interface and output format.

**Architecture:** Three-layer design — CNF builder (variable allocation + sequential counter encoding), ParaFROST subprocess runner (DIMACS I/O + output parsing), and night solver (domain-specific constraint encoding + binary search optimization). CLI mirrors the existing `night_call_solver_policy.py` interface.

**Tech Stack:** Python 3.12, ParaFROST (C++/CUDA SAT solver built from source), DIMACS CNF format, subprocess for solver invocation.

---

## File Map

```
new_approach/
├── pyproject.toml                          # Package config, depends on parent scheduler
├── src/
│   └── parafrost_scheduler/
│       ├── __init__.py                     # Package exports
│       ├── cnf_builder.py                  # CNF formula builder + sequential counter
│       ├── parafrost_runner.py             # Subprocess wrapper for parafrost binary
│       ├── night_solver.py                 # Scheduling domain → CNF → solve → solution
│       └── cli.py                          # CLI entry point
├── tests/
│   ├── test_cnf_builder.py                 # Unit tests for encoding correctness
│   ├── test_parafrost_runner.py            # Integration tests for subprocess interface
│   └── test_night_solver.py                # End-to-end scheduling tests
└── vendor/
    └── ParaFROST/                          # Cloned + built from github.com/muhos/ParaFROST
```

---

### Task 1: Project Scaffolding

**Files:**
- Create: `new_approach/pyproject.toml`
- Create: `new_approach/src/parafrost_scheduler/__init__.py`
- Create: `new_approach/tests/__init__.py` (empty, needed for test discovery)

- [ ] **Step 1: Create pyproject.toml**

```toml
[project]
name = "parafrost-scheduler"
version = "0.1.0"
description = "Night call scheduler using ParaFROST SAT solver"
requires-python = ">=3.12"
dependencies = [
    "scheduler",
]

[project.scripts]
parafrost-scheduler = "parafrost_scheduler.cli:main"

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

- [ ] **Step 2: Create __init__.py**

```python
from parafrost_scheduler.cnf_builder import CnfBuilder
from parafrost_scheduler.parafrost_runner import ParaFrostRunner, SolveResult
```

- [ ] **Step 3: Create empty tests/__init__.py**

Empty file.

- [ ] **Step 4: Install both packages in editable mode**

Run:
```bash
cd /cv/scratch/u/watkina6/scheduler && .venv/bin/pip install -e .
cd /cv/scratch/u/watkina6/scheduler/new_approach && /cv/scratch/u/watkina6/scheduler/.venv/bin/pip install -e .
```

Expected: Both install successfully. Verify with:
```bash
/cv/scratch/u/watkina6/scheduler/.venv/bin/python -c "from scheduler.call_schedule_common import ParsedCallScheduleCsv; print('OK')"
```

- [ ] **Step 5: Commit**

```bash
git add new_approach/pyproject.toml new_approach/src/parafrost_scheduler/__init__.py new_approach/tests/__init__.py
git commit -m "feat: scaffold parafrost-scheduler package"
```

---

### Task 2: Clone and Build ParaFROST

**Files:**
- Create: `new_approach/vendor/ParaFROST/` (git clone)

- [ ] **Step 1: Clone ParaFROST**

Run:
```bash
cd /cv/scratch/u/watkina6/scheduler/new_approach
mkdir -p vendor
git clone https://github.com/muhos/ParaFROST.git vendor/ParaFROST
```

- [ ] **Step 2: Build ParaFROST with GPU support**

Run:
```bash
cd /cv/scratch/u/watkina6/scheduler/new_approach/vendor/ParaFROST
./install.sh -g
```

If GPU build fails (e.g., CUDA version mismatch), fall back to CPU:
```bash
./install.sh -c
```

- [ ] **Step 3: Verify the binary works**

Run:
```bash
# Create a trivial CNF: (x1 OR x2) AND (NOT x1 OR x2)
cat > /tmp/test.cnf << 'EOF'
p cnf 2 2
1 2 0
-1 2 0
EOF
/cv/scratch/u/watkina6/scheduler/new_approach/vendor/ParaFROST/build/parafrost /tmp/test.cnf
```

Expected: Output contains `s SATISFIABLE` and a `v` line with variable assignments.

- [ ] **Step 4: Add vendor/ to .gitignore**

Create `new_approach/.gitignore`:
```
vendor/
```

- [ ] **Step 5: Commit**

```bash
git add new_approach/.gitignore
git commit -m "build: add .gitignore for ParaFROST vendor directory"
```

---

### Task 3: CNF Builder — Core Variable and Clause Management

**Files:**
- Create: `new_approach/src/parafrost_scheduler/cnf_builder.py`
- Create: `new_approach/tests/test_cnf_builder.py`

- [ ] **Step 1: Write failing test for variable allocation and clause output**

```python
# tests/test_cnf_builder.py
from parafrost_scheduler.cnf_builder import CnfBuilder


def test_new_var_starts_at_one_and_increments():
    cnf = CnfBuilder()
    assert cnf.new_var() == 1
    assert cnf.new_var() == 2
    assert cnf.new_var() == 3


def test_new_vars_allocates_batch():
    cnf = CnfBuilder()
    batch = cnf.new_vars(4)
    assert batch == [1, 2, 3, 4]
    assert cnf.new_var() == 5


def test_add_clause_and_to_dimacs():
    cnf = CnfBuilder()
    x1, x2, x3 = cnf.new_vars(3)
    cnf.add_clause([x1, -x2])
    cnf.add_clause([-x1, x3])
    output = cnf.to_dimacs()
    assert output == "p cnf 3 2\n1 -2 0\n-1 3 0\n"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/cv/scratch/u/watkina6/scheduler/.venv/bin/python -m pytest new_approach/tests/test_cnf_builder.py -v`

Expected: FAIL — `ModuleNotFoundError: No module named 'parafrost_scheduler.cnf_builder'`

- [ ] **Step 3: Implement CnfBuilder core**

```python
# src/parafrost_scheduler/cnf_builder.py
from __future__ import annotations

from pathlib import Path


class CnfBuilder:
    def __init__(self) -> None:
        self._next_var = 1
        self._clauses: list[list[int]] = []

    @property
    def num_vars(self) -> int:
        return self._next_var - 1

    @property
    def num_clauses(self) -> int:
        return len(self._clauses)

    def new_var(self) -> int:
        var_id = self._next_var
        self._next_var += 1
        return var_id

    def new_vars(self, n: int) -> list[int]:
        start = self._next_var
        self._next_var += n
        return list(range(start, start + n))

    def add_clause(self, literals: list[int]) -> None:
        self._clauses.append(list(literals))

    def to_dimacs(self) -> str:
        lines = [f"p cnf {self.num_vars} {self.num_clauses}"]
        for clause in self._clauses:
            lines.append(" ".join(str(lit) for lit in clause) + " 0")
        return "\n".join(lines) + "\n"

    def write_dimacs(self, path: Path) -> None:
        path.write_text(self.to_dimacs(), encoding="utf-8")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/cv/scratch/u/watkina6/scheduler/.venv/bin/python -m pytest new_approach/tests/test_cnf_builder.py -v`

Expected: 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add new_approach/src/parafrost_scheduler/cnf_builder.py new_approach/tests/test_cnf_builder.py
git commit -m "feat: CNF builder core — variable allocation and DIMACS output"
```

---

### Task 4: CNF Builder — Exactly-One Constraint

**Files:**
- Modify: `new_approach/src/parafrost_scheduler/cnf_builder.py`
- Modify: `new_approach/tests/test_cnf_builder.py`

- [ ] **Step 1: Write failing test for exactly_one**

Append to `tests/test_cnf_builder.py`:

```python
def test_exactly_one_produces_at_least_one_and_pairwise():
    cnf = CnfBuilder()
    lits = cnf.new_vars(3)
    cnf.exactly_one(lits)
    # at-least-one: 1 clause [1, 2, 3]
    # at-most-one: C(3,2) = 3 clauses: [-1,-2], [-1,-3], [-2,-3]
    assert cnf.num_clauses == 4
    dimacs = cnf.to_dimacs()
    assert "1 2 3 0" in dimacs
    assert "-1 -2 0" in dimacs
    assert "-1 -3 0" in dimacs
    assert "-2 -3 0" in dimacs


def test_exactly_one_with_two_vars():
    cnf = CnfBuilder()
    lits = cnf.new_vars(2)
    cnf.exactly_one(lits)
    # at-least-one: [1, 2], at-most-one: [-1, -2]
    assert cnf.num_clauses == 2
```

- [ ] **Step 2: Run to verify failure**

Run: `/cv/scratch/u/watkina6/scheduler/.venv/bin/python -m pytest new_approach/tests/test_cnf_builder.py::test_exactly_one_produces_at_least_one_and_pairwise -v`

Expected: FAIL — `AttributeError: 'CnfBuilder' object has no attribute 'exactly_one'`

- [ ] **Step 3: Implement exactly_one**

Add to `cnf_builder.py`:

```python
    def exactly_one(self, literals: list[int]) -> None:
        self.add_clause(literals)
        for i in range(len(literals)):
            for j in range(i + 1, len(literals)):
                self.add_clause([-literals[i], -literals[j]])
```

- [ ] **Step 4: Run tests**

Run: `/cv/scratch/u/watkina6/scheduler/.venv/bin/python -m pytest new_approach/tests/test_cnf_builder.py -v`

Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
git add new_approach/src/parafrost_scheduler/cnf_builder.py new_approach/tests/test_cnf_builder.py
git commit -m "feat: CNF builder exactly_one constraint"
```

---

### Task 5: CNF Builder — Sequential Counter (at_most_k)

**Files:**
- Modify: `new_approach/src/parafrost_scheduler/cnf_builder.py`
- Modify: `new_approach/tests/test_cnf_builder.py`

- [ ] **Step 1: Write failing test for at_most_k**

Append to `tests/test_cnf_builder.py`:

```python
def test_at_most_k_trivial_case():
    """at_most_1 of 3 variables — any valid assignment has at most 1 true."""
    cnf = CnfBuilder()
    lits = cnf.new_vars(3)
    cnf.at_most_k(lits, 1)
    dimacs = cnf.to_dimacs()
    # Verify the DIMACS is parseable and non-empty
    lines = dimacs.strip().split("\n")
    header = lines[0]
    assert header.startswith("p cnf")
    # Sequential counter for n=3, k=1 produces (n-1)*k = 2 aux vars
    # and specific clauses. Total vars = 3 + 2 = 5
    assert cnf.num_vars == 5


def test_at_most_k_correctness_exhaustive():
    """Verify at_most_2 of 4 vars by checking all 16 assignments."""
    from itertools import product

    def evaluate_cnf(num_vars: int, clauses: list[list[int]], assignment: dict[int, bool]) -> bool:
        for clause in clauses:
            satisfied = False
            for lit in clause:
                var = abs(lit)
                val = assignment[var]
                if (lit > 0 and val) or (lit < 0 and not val):
                    satisfied = True
                    break
            if not satisfied:
                return False
        return True

    cnf = CnfBuilder()
    lits = cnf.new_vars(4)
    cnf.at_most_k(lits, 2)

    # Extract clauses
    clauses = cnf._clauses

    # For each of the 16 assignments to the 4 input variables,
    # check that the formula is SAT iff at most 2 inputs are true.
    for bits in product([False, True], repeat=4):
        count_true = sum(bits)
        # Try all assignments to aux variables
        num_aux = cnf.num_vars - 4
        found_satisfying = False
        for aux_bits in product([False, True], repeat=num_aux):
            assignment = {}
            for i, b in enumerate(bits):
                assignment[i + 1] = b
            for i, b in enumerate(aux_bits):
                assignment[4 + i + 1] = b
            if evaluate_cnf(cnf.num_vars, clauses, assignment):
                found_satisfying = True
                break
        if count_true <= 2:
            assert found_satisfying, f"Should be SAT for {bits} (count={count_true})"
        else:
            assert not found_satisfying, f"Should be UNSAT for {bits} (count={count_true})"
```

- [ ] **Step 2: Run to verify failure**

Run: `/cv/scratch/u/watkina6/scheduler/.venv/bin/python -m pytest new_approach/tests/test_cnf_builder.py::test_at_most_k_trivial_case -v`

Expected: FAIL — `AttributeError: 'CnfBuilder' object has no attribute 'at_most_k'`

- [ ] **Step 3: Implement at_most_k using Sinz sequential counter**

Add to `cnf_builder.py`:

```python
    def at_most_k(self, literals: list[int], k: int) -> None:
        n = len(literals)
        if k >= n:
            return
        if k == 0:
            for lit in literals:
                self.add_clause([-lit])
            return
        if k == 1 and n <= 20:
            for i in range(n):
                for j in range(i + 1, n):
                    self.add_clause([-literals[i], -literals[j]])
            return

        # Sinz sequential counter: register[i][j] for i in 0..n-2, j in 0..k-1
        # register[i][j] means "at least j+1 of literals[0..i+1] are true"
        register = [[self.new_var() for _ in range(k)] for _ in range(n - 1)]

        # Row 0: only literals[0] can set register[0][0]
        self.add_clause([-literals[0], register[0][0]])
        for j in range(1, k):
            self.add_clause([-register[0][j]])

        # Rows 1..n-2
        for i in range(1, n - 1):
            # If literals[i] is true, at least 1 is true
            self.add_clause([-literals[i], register[i][0]])
            # Propagate: if previous row had j+1, current row has j+1
            self.add_clause([-register[i - 1][0], register[i][0]])
            for j in range(1, k):
                # If literals[i] true and previous had j, current has j+1
                self.add_clause([-literals[i], -register[i - 1][j - 1], register[i][j]])
                # Propagate from previous row
                self.add_clause([-register[i - 1][j], register[i][j]])
            # Overflow: literals[i] true and previous already had k → contradiction
            self.add_clause([-literals[i], -register[i - 1][k - 1]])

        # Last literal: overflow check
        self.add_clause([-literals[n - 1], -register[n - 2][k - 1]])
```

- [ ] **Step 4: Run tests**

Run: `/cv/scratch/u/watkina6/scheduler/.venv/bin/python -m pytest new_approach/tests/test_cnf_builder.py -v`

Expected: All tests PASS including the exhaustive correctness check.

- [ ] **Step 5: Commit**

```bash
git add new_approach/src/parafrost_scheduler/cnf_builder.py new_approach/tests/test_cnf_builder.py
git commit -m "feat: CNF builder at_most_k with Sinz sequential counter"
```

---

### Task 6: CNF Builder — at_least_k and exactly_k

**Files:**
- Modify: `new_approach/src/parafrost_scheduler/cnf_builder.py`
- Modify: `new_approach/tests/test_cnf_builder.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_cnf_builder.py`:

```python
def test_at_least_k_correctness_exhaustive():
    """Verify at_least_2 of 4 vars by checking all 16 assignments."""
    from itertools import product

    def evaluate_cnf(num_vars: int, clauses: list[list[int]], assignment: dict[int, bool]) -> bool:
        for clause in clauses:
            satisfied = False
            for lit in clause:
                var = abs(lit)
                val = assignment[var]
                if (lit > 0 and val) or (lit < 0 and not val):
                    satisfied = True
                    break
            if not satisfied:
                return False
        return True

    cnf = CnfBuilder()
    lits = cnf.new_vars(4)
    cnf.at_least_k(lits, 2)
    clauses = cnf._clauses

    for bits in product([False, True], repeat=4):
        count_true = sum(bits)
        num_aux = cnf.num_vars - 4
        found_satisfying = False
        for aux_bits in product([False, True], repeat=num_aux):
            assignment = {}
            for i, b in enumerate(bits):
                assignment[i + 1] = b
            for i, b in enumerate(aux_bits):
                assignment[4 + i + 1] = b
            if evaluate_cnf(cnf.num_vars, clauses, assignment):
                found_satisfying = True
                break
        if count_true >= 2:
            assert found_satisfying, f"Should be SAT for {bits} (count={count_true})"
        else:
            assert not found_satisfying, f"Should be UNSAT for {bits} (count={count_true})"


def test_exactly_k_correctness_exhaustive():
    """Verify exactly_2 of 4 vars by checking all 16 assignments."""
    from itertools import product

    def evaluate_cnf(num_vars: int, clauses: list[list[int]], assignment: dict[int, bool]) -> bool:
        for clause in clauses:
            satisfied = False
            for lit in clause:
                var = abs(lit)
                val = assignment[var]
                if (lit > 0 and val) or (lit < 0 and not val):
                    satisfied = True
                    break
            if not satisfied:
                return False
        return True

    cnf = CnfBuilder()
    lits = cnf.new_vars(4)
    cnf.exactly_k(lits, 2)
    clauses = cnf._clauses

    for bits in product([False, True], repeat=4):
        count_true = sum(bits)
        num_aux = cnf.num_vars - 4
        found_satisfying = False
        for aux_bits in product([False, True], repeat=num_aux):
            assignment = {}
            for i, b in enumerate(bits):
                assignment[i + 1] = b
            for i, b in enumerate(aux_bits):
                assignment[4 + i + 1] = b
            if evaluate_cnf(cnf.num_vars, clauses, assignment):
                found_satisfying = True
                break
        if count_true == 2:
            assert found_satisfying, f"Should be SAT for {bits} (count={count_true})"
        else:
            assert not found_satisfying, f"Should be UNSAT for {bits} (count={count_true})"
```

- [ ] **Step 2: Run to verify failure**

Run: `/cv/scratch/u/watkina6/scheduler/.venv/bin/python -m pytest new_approach/tests/test_cnf_builder.py::test_at_least_k_correctness_exhaustive -v`

Expected: FAIL — `AttributeError: 'CnfBuilder' object has no attribute 'at_least_k'`

- [ ] **Step 3: Implement at_least_k and exactly_k**

Add to `cnf_builder.py`:

```python
    def at_least_k(self, literals: list[int], k: int) -> None:
        n = len(literals)
        if k <= 0:
            return
        if k == n:
            for lit in literals:
                self.add_clause([lit])
            return
        # at_least_k(x1..xn, k) ≡ at_most_(n-k)(¬x1..¬xn)
        self.at_most_k([-lit for lit in literals], n - k)

    def exactly_k(self, literals: list[int], k: int) -> None:
        self.at_most_k(literals, k)
        self.at_least_k(literals, k)
```

- [ ] **Step 4: Run tests**

Run: `/cv/scratch/u/watkina6/scheduler/.venv/bin/python -m pytest new_approach/tests/test_cnf_builder.py -v`

Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
git add new_approach/src/parafrost_scheduler/cnf_builder.py new_approach/tests/test_cnf_builder.py
git commit -m "feat: CNF builder at_least_k and exactly_k"
```

---

### Task 7: CNF Builder — Weighted Sum Bound

**Files:**
- Modify: `new_approach/src/parafrost_scheduler/cnf_builder.py`
- Modify: `new_approach/tests/test_cnf_builder.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_cnf_builder.py`:

```python
def test_weighted_sum_at_most_correctness():
    """weighted_sum_at_most with weights [2, 3] and bound 3.

    x1 has weight 2, x2 has weight 3.
    Possible weighted sums: 0 (neither), 2 (x1 only), 3 (x2 only), 5 (both).
    Bound 3 allows: (F,F), (T,F), (F,T) — not (T,T).
    """
    from itertools import product

    def evaluate_cnf(num_vars: int, clauses: list[list[int]], assignment: dict[int, bool]) -> bool:
        for clause in clauses:
            satisfied = False
            for lit in clause:
                var = abs(lit)
                val = assignment[var]
                if (lit > 0 and val) or (lit < 0 and not val):
                    satisfied = True
                    break
            if not satisfied:
                return False
        return True

    cnf = CnfBuilder()
    x1, x2 = cnf.new_vars(2)
    cnf.weighted_sum_at_most([(x1, 2), (x2, 3)], 3)
    clauses = cnf._clauses

    for bits in product([False, True], repeat=2):
        weighted_sum = bits[0] * 2 + bits[1] * 3
        num_aux = cnf.num_vars - 2
        found_satisfying = False
        for aux_bits in product([False, True], repeat=num_aux):
            assignment = {}
            for i, b in enumerate(bits):
                assignment[i + 1] = b
            for i, b in enumerate(aux_bits):
                assignment[2 + i + 1] = b
            if evaluate_cnf(cnf.num_vars, clauses, assignment):
                found_satisfying = True
                break
        if weighted_sum <= 3:
            assert found_satisfying, f"Should be SAT for {bits} (sum={weighted_sum})"
        else:
            assert not found_satisfying, f"Should be UNSAT for {bits} (sum={weighted_sum})"
```

- [ ] **Step 2: Run to verify failure**

Run: `/cv/scratch/u/watkina6/scheduler/.venv/bin/python -m pytest new_approach/tests/test_cnf_builder.py::test_weighted_sum_at_most_correctness -v`

Expected: FAIL — `AttributeError`

- [ ] **Step 3: Implement weighted_sum_at_most**

Add to `cnf_builder.py`:

```python
    def weighted_sum_at_most(self, weighted_lits: list[tuple[int, int]], bound: int) -> None:
        if not weighted_lits:
            return
        # Expand: for each (literal, weight), create 'weight' auxiliary variables
        # that are implied by the literal. Then at_most_k over all auxiliaries.
        expanded = []
        for lit, weight in weighted_lits:
            for _ in range(weight):
                aux = self.new_var()
                # lit → aux (if lit is true, aux must be true)
                self.add_clause([-lit, aux])
                expanded.append(aux)
        self.at_most_k(expanded, bound)
```

- [ ] **Step 4: Run tests**

Run: `/cv/scratch/u/watkina6/scheduler/.venv/bin/python -m pytest new_approach/tests/test_cnf_builder.py -v`

Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
git add new_approach/src/parafrost_scheduler/cnf_builder.py new_approach/tests/test_cnf_builder.py
git commit -m "feat: CNF builder weighted_sum_at_most for soft constraint optimization"
```

---

### Task 8: ParaFROST Runner

**Files:**
- Create: `new_approach/src/parafrost_scheduler/parafrost_runner.py`
- Create: `new_approach/tests/test_parafrost_runner.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_parafrost_runner.py
from pathlib import Path

import pytest

from parafrost_scheduler.cnf_builder import CnfBuilder
from parafrost_scheduler.parafrost_runner import ParaFrostRunner, SolveResult


PARAFROST_BINARY = Path(__file__).resolve().parent.parent / "vendor" / "ParaFROST" / "build" / "parafrost"


@pytest.fixture
def runner():
    if not PARAFROST_BINARY.exists():
        pytest.skip("ParaFROST binary not built")
    return ParaFrostRunner(PARAFROST_BINARY)


def test_solve_satisfiable(runner):
    cnf = CnfBuilder()
    x1, x2 = cnf.new_vars(2)
    cnf.add_clause([x1, x2])  # x1 OR x2
    cnf.add_clause([-x1, x2])  # NOT x1 OR x2 (forces x2=true)

    result = runner.solve(cnf)

    assert result.satisfiable is True
    assert result.assignment is not None
    assert result.assignment[2] is True  # x2 must be true


def test_solve_unsatisfiable(runner):
    cnf = CnfBuilder()
    x1 = cnf.new_var()
    cnf.add_clause([x1])   # x1
    cnf.add_clause([-x1])  # NOT x1

    result = runner.solve(cnf)

    assert result.satisfiable is False
    assert result.assignment is None
```

- [ ] **Step 2: Run to verify failure**

Run: `/cv/scratch/u/watkina6/scheduler/.venv/bin/python -m pytest new_approach/tests/test_parafrost_runner.py -v`

Expected: FAIL — `ImportError`

- [ ] **Step 3: Implement ParaFrostRunner**

```python
# src/parafrost_scheduler/parafrost_runner.py
from __future__ import annotations

import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from parafrost_scheduler.cnf_builder import CnfBuilder


@dataclass(frozen=True)
class SolveResult:
    satisfiable: bool
    assignment: dict[int, bool] | None
    runtime_seconds: float
    stdout: str
    stderr: str


class ParaFrostRunner:
    def __init__(self, binary_path: str | Path) -> None:
        self._binary = Path(binary_path)
        if not self._binary.exists():
            raise FileNotFoundError(f"ParaFROST binary not found: {self._binary}")

    def solve(self, cnf: CnfBuilder, *, timeout: float | None = None) -> SolveResult:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".cnf", delete=False) as f:
            f.write(cnf.to_dimacs())
            cnf_path = Path(f.name)

        try:
            start = time.perf_counter()
            proc = subprocess.run(
                [str(self._binary), str(cnf_path)],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            elapsed = time.perf_counter() - start
        finally:
            cnf_path.unlink(missing_ok=True)

        return self._parse_output(proc.stdout, proc.stderr, elapsed)

    def _parse_output(self, stdout: str, stderr: str, elapsed: float) -> SolveResult:
        satisfiable = None
        assignment: dict[int, bool] | None = None

        for line in stdout.splitlines():
            line = line.strip()
            if line.startswith("s "):
                if "SATISFIABLE" in line and "UNSATISFIABLE" not in line:
                    satisfiable = True
                elif "UNSATISFIABLE" in line:
                    satisfiable = False
            elif line.startswith("v "):
                if assignment is None:
                    assignment = {}
                tokens = line[2:].split()
                for token in tokens:
                    val = int(token)
                    if val == 0:
                        break
                    assignment[abs(val)] = val > 0

        if satisfiable is None:
            raise RuntimeError(f"Could not parse ParaFROST output:\n{stdout}\n{stderr}")

        if not satisfiable:
            assignment = None

        return SolveResult(
            satisfiable=satisfiable,
            assignment=assignment,
            runtime_seconds=elapsed,
            stdout=stdout,
            stderr=stderr,
        )
```

- [ ] **Step 4: Run tests**

Run: `/cv/scratch/u/watkina6/scheduler/.venv/bin/python -m pytest new_approach/tests/test_parafrost_runner.py -v`

Expected: Both tests PASS (or skip if binary not built yet).

- [ ] **Step 5: Commit**

```bash
git add new_approach/src/parafrost_scheduler/parafrost_runner.py new_approach/tests/test_parafrost_runner.py
git commit -m "feat: ParaFROST subprocess runner with DIMACS I/O"
```

---

### Task 9: Night Solver — Base Constraint Encoding

**Files:**
- Create: `new_approach/src/parafrost_scheduler/night_solver.py`
- Create: `new_approach/tests/test_night_solver.py`

- [ ] **Step 1: Write failing test for constraint encoding**

```python
# tests/test_night_solver.py
from pathlib import Path

import pytest

from scheduler.call_schedule_common import ParsedCallScheduleCsv, WEEKEND_ROLES, WeekRow
from scheduler.night_call_solver import NightSolverConfig
from scheduler.night_call_solver_policy import (
    CRITERION_STROKE,
    NightPolicyWeights,
    criteria_counts_for_solution,
)
from parafrost_scheduler.night_solver import (
    build_night_cnf,
    extract_solution,
)
from parafrost_scheduler.cnf_builder import CnfBuilder


def _fixture() -> ParsedCallScheduleCsv:
    fellow_names = ["A", "B", "C", "D", "E", "F", "G"]
    return ParsedCallScheduleCsv(
        fellow_names=fellow_names,
        existing_schedule_columns=WEEKEND_ROLES,
        week_rows=[
            WeekRow(
                weekday_assignments={
                    "A": "NCC1",
                    "B": "NCC2",
                    "C": "Elective",
                    "D": "Elective",
                    "E": "Elective",
                    "F": "Elective",
                    "G": "Elective",
                },
                schedule_assignments={"Weekend NCC1": "A", "Weekend NCC2": "B", "Weekend Stroke": "C"},
                raw_row=["NCC1", "NCC2", "Elective", "Elective", "Elective", "Elective", "Elective", "A", "B", "C"],
            ),
        ],
        trailing_rows=[],
    )


def test_build_night_cnf_produces_valid_dimacs():
    parsed = _fixture()
    config = NightSolverConfig(
        total_nights={"A": 1, "B": 1, "C": 1, "D": 1, "E": 1, "F": 1, "G": 1},
        friday_nights={"A": 0, "B": 0, "C": 0, "D": 1, "E": 0, "F": 0, "G": 0},
        total_night_multisets=(),
        friday_night_multisets=(),
        ccm_fellows=frozenset(),
        holiday_dates=(),
    )

    cnf, var_map = build_night_cnf(
        parsed,
        config=config,
        hard_criteria=frozenset(),
        weights=NightPolicyWeights(),
        soft_bound=None,
    )

    dimacs = cnf.to_dimacs()
    assert dimacs.startswith("p cnf")
    # 7 days * 7 fellows = 49 decision variables minimum
    assert cnf.num_vars >= 49
    assert cnf.num_clauses > 0
```

- [ ] **Step 2: Run to verify failure**

Run: `/cv/scratch/u/watkina6/scheduler/.venv/bin/python -m pytest new_approach/tests/test_night_solver.py::test_build_night_cnf_produces_valid_dimacs -v`

Expected: FAIL — `ImportError: cannot import name 'build_night_cnf'`

- [ ] **Step 3: Implement build_night_cnf**

```python
# src/parafrost_scheduler/night_solver.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from scheduler.call_schedule_common import (
    NIGHT_ROLES,
    ParsedCallScheduleCsv,
    is_anaesthesia_service,
    is_clinic_service,
    is_night_blocked,
    is_night_holiday_eligible,
    is_preferred_sunday_following_service,
)
from scheduler.night_call_solver import (
    NightScheduleSolution,
    NightSolverConfig,
    absolute_day_index,
    holiday_indices_for_config,
)
from scheduler.night_call_solver_policy import (
    ALL_POLICY_CRITERIA,
    CRITERION_ANAESTHESIA,
    CRITERION_CLINIC,
    CRITERION_FRIDAY_WEEKEND_NCC1,
    CRITERION_STROKE,
    CRITERION_SUNDAY_FOLLOWING,
    NightPolicyCounts,
    NightPolicySolveResult,
    NightPolicyWeights,
    criteria_counts_for_solution,
)

from parafrost_scheduler.cnf_builder import CnfBuilder


@dataclass(frozen=True)
class NightCnfVarMap:
    x: list[list[int]]  # x[day_index][fellow_index] = variable ID
    num_days: int
    num_fellows: int


def build_night_cnf(
    parsed: ParsedCallScheduleCsv,
    *,
    config: NightSolverConfig,
    hard_criteria: frozenset[str],
    weights: NightPolicyWeights,
    soft_bound: int | None,
) -> tuple[CnfBuilder, NightCnfVarMap]:
    cnf = CnfBuilder()
    num_days = len(parsed.week_rows) * len(NIGHT_ROLES)
    num_fellows = len(parsed.fellow_names)

    # Decision variables
    x = [[cnf.new_var() for _ in range(num_fellows)] for _ in range(num_days)]

    # Exactly-one per day
    for day_index in range(num_days):
        cnf.exactly_one(x[day_index])

    # Blocking constraints
    holiday_indices = set(holiday_indices_for_config(config))
    for day_index in range(num_days):
        week_index, _ = divmod(day_index, len(NIGHT_ROLES))
        week_row = parsed.week_rows[week_index]
        for fellow_index, fellow_name in enumerate(parsed.fellow_names):
            weekday_service = week_row.weekday_assignments[fellow_name]
            if fellow_name in config.ccm_fellows or is_night_blocked(weekday_service):
                cnf.add_clause([-x[day_index][fellow_index]])
            if day_index in holiday_indices and not is_night_holiday_eligible(weekday_service):
                cnf.add_clause([-x[day_index][fellow_index]])

    # No-3-consecutive
    for fellow_index in range(num_fellows):
        for start in range(num_days - 2):
            cnf.at_most_k(
                [x[start][fellow_index], x[start + 1][fellow_index], x[start + 2][fellow_index]],
                1,
            )

    # Total night counts
    for fellow_name, total in config.total_nights.items():
        fellow_index = parsed.fellow_names.index(fellow_name)
        all_days = [x[d][fellow_index] for d in range(num_days)]
        cnf.exactly_k(all_days, total)

    # Friday night counts
    friday_indices = list(range(4, num_days, len(NIGHT_ROLES)))
    for fellow_name, total in config.friday_nights.items():
        fellow_index = parsed.fellow_names.index(fellow_name)
        friday_lits = [x[d][fellow_index] for d in friday_indices]
        cnf.exactly_k(friday_lits, total)

    # Multiset constraints
    for multiset_constraint in config.total_night_multisets:
        _add_multiset_cnf(cnf, x, parsed.fellow_names, multiset_constraint, num_days, friday_only=False)
    for multiset_constraint in config.friday_night_multisets:
        _add_multiset_cnf(cnf, x, parsed.fellow_names, multiset_constraint, num_days, friday_only=True)

    # Hard policy criteria: block the assignment
    # Soft policy criteria: collect weighted violations
    weighted_soft_pairs: list[tuple[int, int]] = []
    for day_index in range(num_days):
        week_index, day_of_week = divmod(day_index, len(NIGHT_ROLES))
        for fellow_index, fellow_name in enumerate(parsed.fellow_names):
            criteria = _criteria_for_assignment(parsed, week_index, day_of_week, fellow_name)
            for criterion in criteria:
                if criterion in hard_criteria:
                    cnf.add_clause([-x[day_index][fellow_index]])
                else:
                    weighted_soft_pairs.append((x[day_index][fellow_index], weights.for_criterion(criterion)))

    # Soft bound
    if soft_bound is not None and weighted_soft_pairs:
        cnf.weighted_sum_at_most(weighted_soft_pairs, soft_bound)

    var_map = NightCnfVarMap(x=x, num_days=num_days, num_fellows=num_fellows)
    return cnf, var_map


def extract_solution(
    parsed: ParsedCallScheduleCsv,
    var_map: NightCnfVarMap,
    assignment: dict[int, bool],
) -> NightScheduleSolution:
    assignments_by_week = []
    for week_index in range(len(parsed.week_rows)):
        week_assignments = {}
        for day_of_week, role in enumerate(NIGHT_ROLES):
            day_index = absolute_day_index(week_index, day_of_week)
            assigned_index = next(
                fellow_index
                for fellow_index in range(var_map.num_fellows)
                if assignment.get(var_map.x[day_index][fellow_index], False)
            )
            week_assignments[role] = parsed.fellow_names[assigned_index]
        assignments_by_week.append(week_assignments)
    return NightScheduleSolution(assignments_by_week=assignments_by_week)


def _criteria_for_assignment(
    parsed: ParsedCallScheduleCsv,
    week_index: int,
    day_of_week: int,
    fellow_name: str,
) -> tuple[str, ...]:
    week_row = parsed.week_rows[week_index]
    weekday_service = week_row.weekday_assignments[fellow_name]
    criteria: list[str] = []
    if is_anaesthesia_service(weekday_service):
        criteria.append(CRITERION_ANAESTHESIA)
    if is_clinic_service(weekday_service):
        criteria.append(CRITERION_CLINIC)
    if "Stroke" in weekday_service:
        criteria.append(CRITERION_STROKE)
    if day_of_week == 4 and week_row.schedule_assignments.get("Weekend NCC1") == fellow_name:
        criteria.append(CRITERION_FRIDAY_WEEKEND_NCC1)
    if day_of_week == 6 and week_index + 1 < len(parsed.week_rows):
        following_service = parsed.week_rows[week_index + 1].weekday_assignments[fellow_name]
        if not is_preferred_sunday_following_service(following_service):
            criteria.append(CRITERION_SUNDAY_FOLLOWING)
    return tuple(criteria)


def _add_multiset_cnf(
    cnf: CnfBuilder,
    x: list[list[int]],
    fellow_names: list[str],
    multiset_constraint,
    num_days: int,
    *,
    friday_only: bool,
) -> None:
    from itertools import permutations as _permutations

    unique_orderings = {tuple(o) for o in _permutations(multiset_constraint.values)}

    if len(unique_orderings) == 1:
        # Only one permutation — unconditional exactly_k for each fellow
        ordering = next(iter(unique_orderings))
        for fellow_name, total in zip(multiset_constraint.names, ordering, strict=True):
            fellow_index = fellow_names.index(fellow_name)
            lits = _fellow_lits(x, fellow_index, num_days, friday_only)
            cnf.exactly_k(lits, total)
        return

    # Multiple permutations: use selector variables
    selectors = cnf.new_vars(len(unique_orderings))
    cnf.exactly_one(selectors)

    for sel, ordering in zip(selectors, unique_orderings):
        for fellow_name, total in zip(multiset_constraint.names, ordering, strict=True):
            fellow_index = fellow_names.index(fellow_name)
            lits = _fellow_lits(x, fellow_index, num_days, friday_only)
            # Conditional exactly_k: if sel is true, enforce exactly_k
            # Encode as: sel → at_most_k AND sel → at_least_k
            # Use relaxation: add -sel to overflow/underflow clauses
            _conditional_exactly_k(cnf, lits, total, sel)


def _fellow_lits(x: list[list[int]], fellow_index: int, num_days: int, friday_only: bool) -> list[int]:
    if friday_only:
        return [x[d][fellow_index] for d in range(4, num_days, len(NIGHT_ROLES))]
    return [x[d][fellow_index] for d in range(num_days)]


def _conditional_exactly_k(cnf: CnfBuilder, literals: list[int], k: int, selector: int) -> None:
    """Encode: if selector is true, then exactly k of literals are true.

    Uses activation literal: adds -selector to all constraining clauses,
    making them trivially satisfied when selector is false.
    """
    n = len(literals)
    if k > n or k < 0:
        # Impossible — force selector false
        cnf.add_clause([-selector])
        return

    # Conditional at_most_k
    _conditional_at_most_k(cnf, literals, k, selector)
    # Conditional at_least_k: at_most_(n-k) over negated literals
    _conditional_at_most_k(cnf, [-lit for lit in literals], n - k, selector)


def _conditional_at_most_k(cnf: CnfBuilder, literals: list[int], k: int, selector: int) -> None:
    """Encode: if selector is true, then at most k of literals are true."""
    n = len(literals)
    if k >= n:
        return
    if k == 0:
        for lit in literals:
            cnf.add_clause([-selector, -lit])
        return
    if k == 1 and n <= 20:
        for i in range(n):
            for j in range(i + 1, n):
                cnf.add_clause([-selector, -literals[i], -literals[j]])
        return

    # Sequential counter with selector relaxation
    register = [[cnf.new_var() for _ in range(k)] for _ in range(n - 1)]

    # Row 0
    cnf.add_clause([-selector, -literals[0], register[0][0]])
    for j in range(1, k):
        cnf.add_clause([-selector, -register[0][j]])

    # Rows 1..n-2
    for i in range(1, n - 1):
        cnf.add_clause([-selector, -literals[i], register[i][0]])
        cnf.add_clause([-selector, -register[i - 1][0], register[i][0]])
        for j in range(1, k):
            cnf.add_clause([-selector, -literals[i], -register[i - 1][j - 1], register[i][j]])
            cnf.add_clause([-selector, -register[i - 1][j], register[i][j]])
        # Overflow
        cnf.add_clause([-selector, -literals[i], -register[i - 1][k - 1]])

    # Last literal overflow
    cnf.add_clause([-selector, -literals[n - 1], -register[n - 2][k - 1]])
```

- [ ] **Step 4: Run tests**

Run: `/cv/scratch/u/watkina6/scheduler/.venv/bin/python -m pytest new_approach/tests/test_night_solver.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add new_approach/src/parafrost_scheduler/night_solver.py new_approach/tests/test_night_solver.py
git commit -m "feat: night solver CNF encoding — base constraints and policy criteria"
```

---

### Task 10: Night Solver — Binary Search Optimization and Full Solve Interface

**Files:**
- Modify: `new_approach/src/parafrost_scheduler/night_solver.py`
- Modify: `new_approach/tests/test_night_solver.py`

- [ ] **Step 1: Write failing test for the full solve**

Append to `tests/test_night_solver.py`:

```python
from parafrost_scheduler.night_solver import (
    solve_night_schedule_parafrost_incremental,
    solve_night_schedule_parafrost_at_limit,
)
from parafrost_scheduler.parafrost_runner import ParaFrostRunner


PARAFROST_BINARY = Path(__file__).resolve().parent.parent / "vendor" / "ParaFROST" / "build" / "parafrost"


@pytest.fixture
def runner():
    if not PARAFROST_BINARY.exists():
        pytest.skip("ParaFROST binary not built")
    return ParaFrostRunner(PARAFROST_BINARY)


def test_solve_at_limit_finds_valid_solution(runner):
    parsed = _fixture()
    config = NightSolverConfig(
        total_nights={"A": 1, "B": 1, "C": 1, "D": 1, "E": 1, "F": 1, "G": 1},
        friday_nights={"D": 1},
        total_night_multisets=(),
        friday_night_multisets=(),
        ccm_fellows=frozenset(),
        holiday_dates=(),
    )

    result = solve_night_schedule_parafrost_at_limit(
        parsed,
        config=config,
        hard_criteria=frozenset(),
        weights=NightPolicyWeights(),
        max_violation_limit=100,
        runner=runner,
        emit_summary=False,
    )

    assert result.solution is not None
    # Each fellow should appear exactly once across the 7 days
    all_assigned = [name for week in result.solution.assignments_by_week for name in week.values()]
    assert len(all_assigned) == 7
    for fellow in ["A", "B", "C", "D", "E", "F", "G"]:
        assert all_assigned.count(fellow) == 1


def test_solve_incremental_optimizes(runner):
    parsed = _fixture()
    config = NightSolverConfig(
        total_nights={"A": 1, "B": 1, "C": 1, "D": 1, "E": 1, "F": 1, "G": 1},
        friday_nights={"D": 1},
        total_night_multisets=(),
        friday_night_multisets=(),
        ccm_fellows=frozenset(),
        holiday_dates=(),
    )

    result = solve_night_schedule_parafrost_incremental(
        parsed,
        config=config,
        hard_criteria=frozenset(),
        weights=NightPolicyWeights(),
        max_violation_limit=100,
        runner=runner,
        emit_summary=False,
    )

    assert result.optimized is True
    assert result.solution is not None
    assert result.counts.weighted_total >= 0
```

- [ ] **Step 2: Run to verify failure**

Run: `/cv/scratch/u/watkina6/scheduler/.venv/bin/python -m pytest new_approach/tests/test_night_solver.py::test_solve_at_limit_finds_valid_solution -v`

Expected: FAIL — `ImportError: cannot import name 'solve_night_schedule_parafrost_at_limit'`

- [ ] **Step 3: Implement solve functions**

Add to `night_solver.py`:

```python
from parafrost_scheduler.parafrost_runner import ParaFrostRunner, SolveResult


def weighted_upper_bound(
    parsed: ParsedCallScheduleCsv,
    weights: NightPolicyWeights,
    hard_criteria: frozenset[str],
) -> int:
    total = 0
    for week_index in range(len(parsed.week_rows)):
        for day_of_week in range(len(NIGHT_ROLES)):
            for fellow_name in parsed.fellow_names:
                for criterion in _criteria_for_assignment(parsed, week_index, day_of_week, fellow_name):
                    if criterion not in hard_criteria:
                        total += weights.for_criterion(criterion)
    return total


def solve_night_schedule_parafrost_at_limit(
    parsed: ParsedCallScheduleCsv,
    *,
    config: NightSolverConfig = NightSolverConfig(),
    hard_criteria: frozenset[str] = frozenset(),
    weights: NightPolicyWeights = NightPolicyWeights(),
    max_violation_limit: int | None = None,
    runner: ParaFrostRunner,
    emit_summary: bool = True,
) -> NightPolicySolveResult:
    limit = weighted_upper_bound(parsed, weights, hard_criteria) if max_violation_limit is None else max_violation_limit
    cnf, var_map = build_night_cnf(
        parsed,
        config=config,
        hard_criteria=hard_criteria,
        weights=weights,
        soft_bound=limit,
    )

    result = runner.solve(cnf)
    if not result.satisfiable:
        raise ValueError(f"Night call schedule is unsatisfiable with <= {limit} weighted soft violations")

    solution = extract_solution(parsed, var_map, result.assignment)
    counts = criteria_counts_for_solution(parsed, solution, weights=weights)
    solve_result = NightPolicySolveResult(
        tier=f"parafrost-unoptimized-soft<={limit}",
        solution=solution,
        counts=counts,
        hard_criteria=hard_criteria,
        optimized=False,
    )
    if emit_summary:
        from scheduler.night_call_solver_policy import print_policy_summary
        print_policy_summary(parsed, solve_result)
    return solve_result


def solve_night_schedule_parafrost_incremental(
    parsed: ParsedCallScheduleCsv,
    *,
    config: NightSolverConfig = NightSolverConfig(),
    hard_criteria: frozenset[str] = frozenset(),
    weights: NightPolicyWeights = NightPolicyWeights(),
    max_violation_limit: int | None = None,
    runner: ParaFrostRunner,
    emit_summary: bool = True,
) -> NightPolicySolveResult:
    upper = weighted_upper_bound(parsed, weights, hard_criteria) if max_violation_limit is None else max_violation_limit

    best_solution = None
    best_counts = None
    best_limit = None
    low = 0
    high = upper

    while low <= high:
        mid = (low + high) // 2
        cnf, var_map = build_night_cnf(
            parsed,
            config=config,
            hard_criteria=hard_criteria,
            weights=weights,
            soft_bound=mid,
        )
        result = runner.solve(cnf)
        if result.satisfiable:
            best_solution = extract_solution(parsed, var_map, result.assignment)
            best_counts = criteria_counts_for_solution(parsed, best_solution, weights=weights)
            best_limit = mid
            high = mid - 1
        else:
            low = mid + 1

    if best_solution is None or best_counts is None or best_limit is None:
        raise ValueError(f"Night call schedule is unsatisfiable with <= {upper} weighted soft violations")

    solve_result = NightPolicySolveResult(
        tier=f"parafrost-incremental-soft<={best_limit}",
        solution=best_solution,
        counts=best_counts,
        hard_criteria=hard_criteria,
        optimized=True,
    )
    if emit_summary:
        from scheduler.night_call_solver_policy import print_policy_summary
        print_policy_summary(parsed, solve_result)
    return solve_result
```

- [ ] **Step 4: Run tests**

Run: `/cv/scratch/u/watkina6/scheduler/.venv/bin/python -m pytest new_approach/tests/test_night_solver.py -v`

Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
git add new_approach/src/parafrost_scheduler/night_solver.py new_approach/tests/test_night_solver.py
git commit -m "feat: night solver binary search optimization and full solve interface"
```

---

### Task 11: Night Solver — Hard Criteria Enforcement Test

**Files:**
- Modify: `new_approach/tests/test_night_solver.py`

- [ ] **Step 1: Write test that verifies hard criteria block assignments**

Append to `tests/test_night_solver.py`:

```python
def test_hard_stroke_criteria_blocks_stroke_fellow(runner):
    """Same test as test_policy_incremental_solver_can_make_stroke_hard from the Z3 suite."""
    fellow_names = ["A", "B", "Clinic", "Stroke", "FridayWeekend", "SundayBad", "D", "E", "F", "G", "H"]
    parsed = ParsedCallScheduleCsv(
        fellow_names=fellow_names,
        existing_schedule_columns=WEEKEND_ROLES,
        week_rows=[
            WeekRow(
                weekday_assignments={
                    "A": "NCC1",
                    "B": "NCC2",
                    "Clinic": "Clinic/Elective",
                    "Stroke": "Stroke",
                    "FridayWeekend": "Elective",
                    "SundayBad": "Elective",
                    "D": "Elective",
                    "E": "Elective",
                    "F": "Elective",
                    "G": "Elective",
                    "H": "Elective",
                },
                schedule_assignments={"Weekend NCC1": "FridayWeekend", "Weekend NCC2": "A", "Weekend Stroke": "B"},
                raw_row=["NCC1", "NCC2", "Clinic/Elective", "Stroke", "Elective", "Elective", "Elective", "Elective", "Elective", "Elective", "Elective", "FridayWeekend", "A", "B"],
            ),
            WeekRow(
                weekday_assignments={
                    "A": "Elective",
                    "B": "Elective",
                    "Clinic": "Elective",
                    "Stroke": "Elective",
                    "FridayWeekend": "Elective",
                    "SundayBad": "MSICU",
                    "D": "Elective",
                    "E": "Elective",
                    "F": "Elective",
                    "G": "Elective",
                    "H": "Elective",
                },
                schedule_assignments={"Weekend NCC1": "A", "Weekend NCC2": "B", "Weekend Stroke": "D"},
                raw_row=["Elective", "Elective", "Elective", "Elective", "Elective", "MSICU", "Elective", "Elective", "Elective", "Elective", "Elective", "A", "B", "D"],
            ),
        ],
        trailing_rows=[],
    )
    config = NightSolverConfig(
        total_nights={
            "A": 2, "B": 2, "Clinic": 2, "Stroke": 0,
            "FridayWeekend": 2, "SundayBad": 2, "D": 1, "E": 1, "F": 1, "G": 1, "H": 0,
        },
        friday_nights={
            "A": 0, "B": 0, "Clinic": 0, "Stroke": 0,
            "FridayWeekend": 0, "SundayBad": 0, "D": 1, "E": 1, "F": 0, "G": 0, "H": 0,
        },
        total_night_multisets=(),
        friday_night_multisets=(),
        ccm_fellows=frozenset(),
        holiday_dates=(),
    )

    result = solve_night_schedule_parafrost_incremental(
        parsed,
        config=config,
        hard_criteria=frozenset({CRITERION_STROKE}),
        weights=NightPolicyWeights(stroke=5),
        runner=runner,
        emit_summary=False,
    )

    assigned = {fellow for week in result.solution.assignments_by_week for fellow in week.values()}
    assert "Stroke" not in assigned
    assert result.counts.by_criterion["stroke"] == 0
```

- [ ] **Step 2: Run test**

Run: `/cv/scratch/u/watkina6/scheduler/.venv/bin/python -m pytest new_approach/tests/test_night_solver.py::test_hard_stroke_criteria_blocks_stroke_fellow -v`

Expected: PASS (this exercises the full pipeline: encoding + ParaFROST solve + extraction).

- [ ] **Step 3: Commit**

```bash
git add new_approach/tests/test_night_solver.py
git commit -m "test: hard criteria enforcement via ParaFROST solver"
```

---

### Task 12: CLI

**Files:**
- Create: `new_approach/src/parafrost_scheduler/cli.py`

- [ ] **Step 1: Write failing test for CLI**

Append to `tests/test_night_solver.py`:

```python
from parafrost_scheduler.cli import main


def test_cli_reports_progress(tmp_path, capsys, monkeypatch):
    input_path = tmp_path / "input.csv"
    output_path = tmp_path / "output.csv"
    input_path.write_text("placeholder\n", encoding="utf-8")

    parsed = _fixture()
    solution = NightScheduleSolution(
        assignments_by_week=[{
            "Night Mon": "A", "Night Tue": "B", "Night Wed": "C",
            "Night Thu": "D", "Night Fri": "E", "Night Sat": "F", "Night Sun": "G",
        }]
    )
    mock_result = NightPolicySolveResult(
        tier="parafrost-unoptimized-soft<=100",
        solution=solution,
        counts=NightPolicyCounts(
            by_criterion={c: 0 for c in ALL_POLICY_CRITERIA},
            weighted_total=0,
        ),
        hard_criteria=frozenset(),
        optimized=False,
    )

    monkeypatch.setattr("parafrost_scheduler.cli.parse_night_call_csv", lambda path: parsed)
    monkeypatch.setattr("parafrost_scheduler.cli.ParaFrostRunner", lambda path: None)
    monkeypatch.setattr("parafrost_scheduler.cli.solve_night_schedule_parafrost_at_limit", lambda *a, **kw: mock_result)
    monkeypatch.setattr("parafrost_scheduler.cli.write_night_schedule_csv", lambda *a: None)

    exit_code = main([str(input_path), str(output_path), "--no-optimize", "--max-soft", "100"])

    assert exit_code == 0
    stdout = capsys.readouterr().out
    assert "Parsed 1 schedule weeks" in stdout
    assert f"Wrote {output_path}" in stdout
```

- [ ] **Step 2: Run to verify failure**

Run: `/cv/scratch/u/watkina6/scheduler/.venv/bin/python -m pytest new_approach/tests/test_night_solver.py::test_cli_reports_progress -v`

Expected: FAIL — `ImportError`

- [ ] **Step 3: Implement CLI**

```python
# src/parafrost_scheduler/cli.py
from __future__ import annotations

import argparse
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from scheduler.night_call_solver import NightSolverConfig, write_night_schedule_csv
from scheduler.night_call_solver_policy import (
    NightPolicyWeights,
    NightPolicySpec,
    NightPolicySolveResult,
    print_policy_summary,
    staged_policy_specs,
)

from parafrost_scheduler.night_solver import (
    solve_night_schedule_parafrost_at_limit,
    solve_night_schedule_parafrost_incremental,
)
from parafrost_scheduler.parafrost_runner import ParaFrostRunner


def parse_night_call_csv(path):
    from scheduler.night_call_solver_policy import parse_night_call_csv as _parse
    return _parse(path)


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Solve night call schedules using ParaFROST SAT solver.")
    parser.add_argument("input_csv")
    parser.add_argument("output")
    parser.add_argument("--staged", action="store_true", help="Run the five staged hardening policies in parallel.")
    parser.add_argument("--workers", type=int, default=None, help="Worker count for --staged.")
    parser.add_argument("--hard", default="none", help="Comma-separated hard criteria for a single run.")
    parser.add_argument("--max-soft", type=int, default=None, help="Maximum weighted soft score.")
    parser.add_argument("--no-optimize", action="store_true", help="Skip binary search.")
    parser.add_argument("--stroke-weight", type=int, default=5)
    parser.add_argument("--clinic-weight", type=int, default=1)
    parser.add_argument("--anaesthesia-weight", type=int, default=1)
    parser.add_argument("--friday-weekend-ncc1-weight", type=int, default=1)
    parser.add_argument("--sunday-following-weight", type=int, default=1)
    parser.add_argument(
        "--parafrost-path",
        type=Path,
        default=Path(__file__).resolve().parent.parent.parent / "vendor" / "ParaFROST" / "build" / "parafrost",
        help="Path to the parafrost binary.",
    )
    return parser


def _parse_hard_criteria(value: str) -> frozenset[str]:
    from scheduler.night_call_solver_policy import ALL_POLICY_CRITERIA
    if not value or value == "none":
        return frozenset()
    criteria = frozenset(part.strip() for part in value.split(",") if part.strip())
    invalid = criteria - ALL_POLICY_CRITERIA
    if invalid:
        raise ValueError(f"Unknown policy criteria: {', '.join(sorted(invalid))}")
    return criteria


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(sys.argv[1:] if argv is None else argv)
    parsed = parse_night_call_csv(args.input_csv)
    weights = NightPolicyWeights(
        anaesthesia=args.anaesthesia_weight,
        clinic=args.clinic_weight,
        stroke=args.stroke_weight,
        friday_weekend_ncc1=args.friday_weekend_ncc1_weight,
        sunday_following=args.sunday_following_weight,
    )
    runner = ParaFrostRunner(args.parafrost_path)

    mode = "unoptimized bounded solve" if args.no_optimize else "optimized binary search"
    print(f"Parsed {len(parsed.week_rows)} schedule weeks from {args.input_csv}.", flush=True)
    print(
        "Policy weights: "
        f"anaesthesia={weights.anaesthesia}, clinic={weights.clinic}, stroke={weights.stroke}, "
        f"friday_weekend_ncc1={weights.friday_weekend_ncc1}, sunday_following={weights.sunday_following}.",
        flush=True,
    )

    if args.staged:
        specs = staged_policy_specs()
        print(f"Launching {len(specs)} staged policy runs with {mode}.", flush=True)
        results = []
        for spec in specs:
            solve_fn = solve_night_schedule_parafrost_incremental if not args.no_optimize else solve_night_schedule_parafrost_at_limit
            result = solve_fn(
                parsed,
                hard_criteria=spec.hard_criteria,
                weights=weights,
                max_violation_limit=args.max_soft,
                runner=runner,
                emit_summary=False,
            )
            output_prefix = Path(args.output)
            suffix = "optimized" if not args.no_optimize else "unoptimized"
            if output_prefix.suffix:
                out_path = output_prefix.with_name(f"{output_prefix.stem}.{spec.name}.{suffix}{output_prefix.suffix}")
            else:
                out_path = output_prefix.parent / f"{output_prefix.name}.{spec.name}.{suffix}.csv"
            write_night_schedule_csv(parsed, result.solution, out_path)
            print(f"  {spec.name}: weighted={result.counts.weighted_total} output={out_path}", flush=True)
            results.append((spec, result, out_path))
        return 0

    solve_fn = solve_night_schedule_parafrost_at_limit if args.no_optimize else solve_night_schedule_parafrost_incremental
    hard_criteria = _parse_hard_criteria(args.hard)
    hard_display = ", ".join(sorted(hard_criteria)) if hard_criteria else "none"
    print(f"Launching single policy run with {mode}; hard={hard_display}.", flush=True)

    result = solve_fn(
        parsed,
        hard_criteria=hard_criteria,
        weights=weights,
        max_violation_limit=args.max_soft,
        runner=runner,
    )
    print(f"Completed single policy run: weighted={result.counts.weighted_total}.", flush=True)
    print(f"Writing {args.output}.", flush=True)
    write_night_schedule_csv(parsed, result.solution, args.output)
    print(f"Wrote {args.output}.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run tests**

Run: `/cv/scratch/u/watkina6/scheduler/.venv/bin/python -m pytest new_approach/tests/test_night_solver.py::test_cli_reports_progress -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add new_approach/src/parafrost_scheduler/cli.py new_approach/tests/test_night_solver.py
git commit -m "feat: CLI matching night_call_solver_policy interface"
```

---

### Task 13: Update __init__.py and End-to-End Integration Test

**Files:**
- Modify: `new_approach/src/parafrost_scheduler/__init__.py`
- Modify: `new_approach/tests/test_night_solver.py`

- [ ] **Step 1: Update __init__.py with full exports**

```python
# src/parafrost_scheduler/__init__.py
from parafrost_scheduler.cnf_builder import CnfBuilder
from parafrost_scheduler.parafrost_runner import ParaFrostRunner, SolveResult
from parafrost_scheduler.night_solver import (
    NightCnfVarMap,
    build_night_cnf,
    extract_solution,
    solve_night_schedule_parafrost_at_limit,
    solve_night_schedule_parafrost_incremental,
    weighted_upper_bound,
)
```

- [ ] **Step 2: Write end-to-end test using the real CSV fixture**

Append to `tests/test_night_solver.py`:

```python
def test_end_to_end_with_real_fixture_matches_z3_constraints(runner):
    """Verify that the ParaFROST solution satisfies the same hard constraints
    that the Z3 solver would enforce."""
    parsed = _fixture()
    config = NightSolverConfig(
        total_nights={"A": 1, "B": 1, "C": 1, "D": 1, "E": 1, "F": 1, "G": 1},
        friday_nights={"D": 1},
        total_night_multisets=(),
        friday_night_multisets=(),
        ccm_fellows=frozenset(),
        holiday_dates=(),
    )

    result = solve_night_schedule_parafrost_incremental(
        parsed,
        config=config,
        hard_criteria=frozenset(),
        weights=NightPolicyWeights(),
        runner=runner,
        emit_summary=False,
    )

    # Verify hard constraints
    solution = result.solution
    assert len(solution.assignments_by_week) == 1

    week = solution.assignments_by_week[0]
    assigned = list(week.values())
    # Exactly one fellow per day (7 unique assignments)
    assert len(assigned) == 7
    # Total nights: each fellow has exactly 1
    from collections import Counter
    counts = Counter(assigned)
    for fellow in ["A", "B", "C", "D", "E", "F", "G"]:
        assert counts[fellow] == 1
    # Friday night goes to D
    assert week["Night Fri"] == "D"
```

- [ ] **Step 3: Run all tests**

Run: `/cv/scratch/u/watkina6/scheduler/.venv/bin/python -m pytest new_approach/tests/ -v`

Expected: All tests PASS.

- [ ] **Step 4: Commit**

```bash
git add new_approach/src/parafrost_scheduler/__init__.py new_approach/tests/test_night_solver.py
git commit -m "feat: complete parafrost-scheduler package with integration tests"
```

---

## Verification

After all tasks are complete, run the full test suite and verify the binary integration:

```bash
cd /cv/scratch/u/watkina6/scheduler/new_approach
/cv/scratch/u/watkina6/scheduler/.venv/bin/python -m pytest tests/ -v
```

All tests should pass. The package is ready for use with real schedule CSVs via:

```bash
/cv/scratch/u/watkina6/scheduler/.venv/bin/python -m parafrost_scheduler.cli \
    ../weekend_call_with_weekends_and_nights_relaxed_nosoftlimit.csv \
    output.csv \
    --hard sunday_following,anaesthesia
```
