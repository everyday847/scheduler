from __future__ import annotations

import re
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")

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
                [str(self._binary), str(cnf_path), "-model", "-modelprint"],
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
            line = _ANSI_ESCAPE.sub("", line).strip()
            if line.startswith("s "):
                if "UNSATISFIABLE" in line:
                    satisfiable = False
                elif "SATISFIABLE" in line:
                    satisfiable = True
            elif line.startswith("v "):
                if assignment is None:
                    assignment = {}
                # ParaFROST v lines: space-separated literals, NO trailing 0
                tokens = line[2:].split()
                for token in tokens:
                    val = int(token)
                    if val == 0:
                        break  # Just in case
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
