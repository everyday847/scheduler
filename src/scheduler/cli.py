from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Sequence

import yaml

from . import service


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ValueError as exc:
        parser.print_usage(sys.stderr)
        sys.stderr.write(f"{parser.prog}: error: {exc}\n")
        return 2


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="scheduler")
    subcommands = parser.add_subparsers(dest="command", required=True)

    default_request = subcommands.add_parser(
        "default-request",
        help="Write the default annual schedule request as YAML.",
    )
    default_request.add_argument("--output", "-o", type=Path, help="YAML file to write. Defaults to stdout.")
    default_request.set_defaults(func=_default_request)

    solve = subcommands.add_parser(
        "solve",
        help="Solve an annual YAML request and write an XLSX workbook.",
    )
    solve.add_argument("--request", "-r", type=Path, required=True, help="Annual request YAML file.")
    solve.add_argument(
        "--output",
        "-o",
        type=Path,
        default=Path("optimized_schedule.xlsx"),
        help="Workbook path to write. Defaults to optimized_schedule.xlsx.",
    )
    solve.set_defaults(func=_solve)

    return parser


def _default_request(args: argparse.Namespace) -> int:
    _write_yaml(service.get_default_schedule_request(), args.output)
    return 0


def _solve(args: argparse.Namespace) -> int:
    request = _read_yaml_mapping(args.request)
    result = service.solve_schedule(request)
    print("Schedule solved successfully.")

    workbook = service.build_schedule_workbook(result)
    print("Workbook built.")

    args.output.write_bytes(workbook)
    
    return 0


def _read_yaml_mapping(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text())
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a YAML mapping.")
    return data


def _write_yaml(data: dict[str, Any], output: Path | None) -> None:
    text = yaml.safe_dump(data, sort_keys=False)
    if output is None:
        sys.stdout.write(text)
    else:
        output.write_text(text)


if __name__ == "__main__":
    raise SystemExit(main())
