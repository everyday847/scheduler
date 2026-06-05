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


@dataclass(frozen=True)
class OptimizeResult:
    """Result of a native (objective) optimization run.

    satisfiable:
        True if any feasible solution was found.
    assignment:
        The best (incumbent) solution found, or None when UNSAT/none found.
    optimal:
        True if RoundingSat proved optimality (``s OPTIMUM FOUND``); False when
        the run stopped at the time limit with a best-so-far incumbent.
    objective:
        The incumbent's objective value parsed from the last ``c bounds`` line,
        or None if not reported.
    runtime_seconds:
        Wall-clock time.
    """
    satisfiable: bool
    assignment: dict[int, bool] | None
    optimal: bool
    objective: int | None
    runtime_seconds: float
    proven_unsat: bool = False
    """True only when RoundingSat *proved* UNSAT (``s UNSATISFIABLE``). A
    time-limited run that simply found no solution is NOT proven_unsat — it is
    unknown, and must not be treated as a proof of optimality."""
    lower_bound: int | None = None
    """Best lower bound on the objective parsed from the last ``c bounds`` line.
    With *objective* (the incumbent/upper bound) this gives the optimality gap;
    lower_bound == objective means proven optimal."""

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

    def optimize(
        self,
        opb: OpbBuilder,
        *,
        time_limit: float | None = None,
        opt_mode: str = "hybrid",
        echo_progress: bool = False,
    ) -> OptimizeResult:
        """Run RoundingSat in native optimization mode on an OPB with an objective.

        The formula must carry a ``min:`` objective (see
        :meth:`OpbBuilder.set_objective`). RoundingSat minimizes it; with a
        *time_limit* it stops early and reports the best incumbent found, which
        is what enables an anytime/streaming workflow.

        When *echo_progress* is True, RoundingSat's ``c bounds`` progress lines
        (incumbent >= lower bound @ time) are echoed to stdout AS THEY ARRIVE —
        a non-interrupting heartbeat for long single B&B runs. The solver is not
        disturbed; we only read its output stream.

        Returns an :class:`OptimizeResult`. ``optimal=True`` means provably
        optimal; otherwise ``assignment`` is the best-so-far incumbent.
        """
        if not opb.has_objective:
            raise ValueError("optimize() requires an objective; call opb.set_objective(...)")

        opb_fd, opb_name = tempfile.mkstemp(suffix=".opb")
        err_fd, err_name = tempfile.mkstemp(suffix=".err")
        opb_path = Path(opb_name)
        with os.fdopen(opb_fd, "w") as f:
            f.write(opb.to_opb())

        # RoundingSat's --time-limit is unreliable on this build (it can overshoot
        # badly), but it catches SIGTERM and flushes its best incumbent + "s ..."
        # line before exiting. So we stop it ourselves with SIGTERM at the deadline.
        # We read stdout via a PIPE on a background thread so we can echo progress
        # lines live (the file-then-read approach hid all progress until exit).
        args = [str(self._binary), "--print-sol=1", f"--opt-mode={opt_mode}"]
        if time_limit is not None:
            args.append(f"--time-limit={time_limit}")
        args += [str(opb_path), *self._extra_args]

        import threading

        stdout_lines: list[str] = []

        def _drain(pipe):
            # Accumulate every line; echo only the cheap progress lines live.
            for raw in iter(pipe.readline, ""):
                stdout_lines.append(raw)
                if echo_progress and raw.startswith("c bounds "):
                    print(f"    [roundingsat] {raw.rstrip()}", flush=True)
            pipe.close()

        start = time.perf_counter()
        try:
            with os.fdopen(err_fd, "w") as err_f:
                proc = subprocess.Popen(
                    args, stdout=subprocess.PIPE, stderr=err_f,
                    env=self._env, text=True, bufsize=1,
                )
                reader = threading.Thread(target=_drain, args=(proc.stdout,), daemon=True)
                reader.start()
                try:
                    proc.wait(timeout=time_limit)
                except subprocess.TimeoutExpired:
                    # Deadline reached: ask RoundingSat to stop and print incumbent.
                    proc.terminate()  # SIGTERM
                    try:
                        proc.wait(timeout=30.0)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait()
                reader.join(timeout=5.0)
            elapsed = time.perf_counter() - start
            stdout = "".join(stdout_lines)
            stderr = Path(err_name).read_text()
        finally:
            for p in (opb_name, err_name):
                Path(p).unlink(missing_ok=True)

        return self._parse_optimize_output(stdout, stderr, elapsed)

    def _parse_optimize_output(
        self, stdout: str, stderr: str, elapsed: float
    ) -> OptimizeResult:
        """Parse optimization output: ``s`` status, incumbent ``v`` line, and the
        last ``c bounds <incumbent> >= <lower>`` objective value."""
        optimal = False
        proven_unsat = False
        satisfiable: bool | None = None
        assignment: dict[int, bool] | None = None
        objective: int | None = None
        lower_bound: int | None = None

        for line in stdout.splitlines():
            line = line.strip()
            if line.startswith("s "):
                status = line[2:].strip()
                if status == "UNSATISFIABLE":
                    satisfiable = False
                    proven_unsat = True
                elif status == "OPTIMUM FOUND":
                    satisfiable = True
                    optimal = True
                elif status == "SATISFIABLE":
                    satisfiable = True
            elif line.startswith("c bounds "):
                # "c bounds <incumbent> >= <lower> @ <time>"
                parts = line.split()
                if len(parts) >= 3 and parts[2] != "-":
                    try:
                        objective = int(parts[2])
                    except ValueError:
                        pass
                if len(parts) >= 5 and parts[3] == ">=" and parts[4] != "-":
                    try:
                        lower_bound = int(parts[4])
                    except ValueError:
                        pass
            elif line.startswith("v "):
                if assignment is None:
                    assignment = {}
                for token in line[2:].split():
                    token = token.strip()
                    if not token:
                        continue
                    if token.startswith("-x") or token.startswith("~x"):
                        assignment[int(token[2:])] = False
                    elif token.startswith("x"):
                        assignment[int(token[1:])] = True

        if satisfiable is None:
            # Time limit hit before any status line but an incumbent may exist.
            satisfiable = assignment is not None
        if not satisfiable:
            assignment = None
        return OptimizeResult(
            satisfiable=satisfiable,
            assignment=assignment,
            optimal=optimal,
            objective=objective,
            runtime_seconds=elapsed,
            proven_unsat=proven_unsat,
            lower_bound=lower_bound,
        )

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
