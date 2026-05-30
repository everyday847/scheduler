"""RoundingSat pseudo-Boolean solver runner.

Wraps the RoundingSat binary to solve OPB-format pseudo-Boolean problems.
Parses the standard PB-competition output format:

    s SATISFIABLE
    v x1 -x2 x3 ...

    s UNSATISFIABLE

    s OPTIMUM FOUND
    v x1 -x2 x3 ...
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from parafrost_scheduler.opb_encoder import OpbBuilder
from parafrost_scheduler.parafrost_runner import SolveResult  # reuse the same dataclass

# Library directories required when RoundingSat was built against GCC 13 /
# Boost 1.85 modules on this cluster.  These are prepended to LD_LIBRARY_PATH
# only when the binary exists at the expected vendor location and the libs are
# present.  No-op on systems where the binary is self-contained (static build).
_CLUSTER_LIB_HINTS = [
    "/apps/rocs/2024.04/common/x86-64-v4/software/GCCcore/13.3.0/lib64",
    "/apps/rocs/2024.04/common/x86-64-v3/software/Boost/1.85.0-GCC-13.3.0/lib",
]


def _build_env() -> dict[str, str]:
    """Return an environment dict with any needed library paths prepended."""
    env = dict(os.environ)
    hints = [p for p in _CLUSTER_LIB_HINTS if os.path.isdir(p)]
    if hints:
        existing = env.get("LD_LIBRARY_PATH", "")
        env["LD_LIBRARY_PATH"] = ":".join(hints + ([existing] if existing else []))
    return env


class RoundingSatRunner:
    """Run RoundingSat on OPB formulae and return parsed results.

    Parameters
    ----------
    binary_path:
        Path to the ``roundingsat`` executable.
    extra_args:
        Optional list of additional command-line flags to pass.
    """

    def __init__(
        self,
        binary_path: str | Path,
        *,
        extra_args: list[str] | None = None,
    ) -> None:
        self._binary = Path(binary_path)
        if not self._binary.exists():
            raise FileNotFoundError(f"RoundingSat binary not found: {self._binary}")
        self._extra_args = extra_args or []
        self._env = _build_env()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def solve(
        self,
        opb: OpbBuilder,
        *,
        timeout: float | None = None,
    ) -> SolveResult:
        """Solve an OPB formula and return a :class:`SolveResult`.

        Writes the formula to a temporary file, invokes RoundingSat, and
        parses its output.

        Parameters
        ----------
        opb:
            A fully-constructed :class:`~parafrost_scheduler.opb_encoder.OpbBuilder`.
        timeout:
            Optional wall-clock timeout in seconds.  If exceeded,
            ``subprocess.TimeoutExpired`` is propagated.

        Returns
        -------
        SolveResult
            ``satisfiable=True`` and a populated ``assignment`` dict when
            the problem is SAT or an optimum is found; ``satisfiable=False``
            when UNSAT.
        """
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".opb", delete=False
        ) as f:
            f.write(opb.to_opb())
            opb_path = Path(f.name)

        try:
            start = time.perf_counter()
            proc = subprocess.run(
                # --print-sol=1 is required to emit the "v ..." solution line
                [str(self._binary), "--print-sol=1", str(opb_path), *self._extra_args],
                capture_output=True,
                text=True,
                timeout=timeout,
                env=self._env,
            )
            elapsed = time.perf_counter() - start
        finally:
            opb_path.unlink(missing_ok=True)

        return self._parse_output(proc.stdout, proc.stderr, elapsed)

    # ------------------------------------------------------------------
    # Output parsing
    # ------------------------------------------------------------------

    def _parse_output(
        self, stdout: str, stderr: str, elapsed: float
    ) -> SolveResult:
        """Parse PB-competition-format output from RoundingSat."""
        satisfiable: bool | None = None
        assignment: dict[int, bool] | None = None

        for line in stdout.splitlines():
            line = line.strip()

            if line.startswith("s "):
                status = line[2:].strip()
                if status == "UNSATISFIABLE":
                    satisfiable = False
                elif status in ("SATISFIABLE", "OPTIMUM FOUND"):
                    satisfiable = True

            elif line.startswith("v "):
                # Solution line: ``v x1 -x2 x3 ...``
                # Positive token ``x3``  → variable 3 is True
                # Negative token ``-x3`` → variable 3 is False
                # RoundingSat also uses ``~x3`` as another negation form.
                if assignment is None:
                    assignment = {}
                tokens = line[2:].split()
                for token in tokens:
                    token = token.strip()
                    if not token:
                        continue
                    if token.startswith("-x"):
                        var = int(token[2:])
                        assignment[var] = False
                    elif token.startswith("~x"):
                        var = int(token[2:])
                        assignment[var] = False
                    elif token.startswith("x"):
                        var = int(token[1:])
                        assignment[var] = True
                    # Ignore other tokens (e.g. objective value lines)

        if satisfiable is None:
            raise RuntimeError(
                f"Could not determine SAT result from RoundingSat output:\n"
                f"STDOUT:\n{stdout}\nSTDERR:\n{stderr}"
            )

        if not satisfiable:
            assignment = None

        return SolveResult(
            satisfiable=satisfiable,
            assignment=assignment,
            runtime_seconds=elapsed,
            stdout=stdout,
            stderr=stderr,
        )
