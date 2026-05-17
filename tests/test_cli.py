from pathlib import Path

import yaml

from scheduler import cli


def test_default_request_writes_yaml(tmp_path):
    output = tmp_path / "annual-request.yaml"

    exit_code = cli.main(["default-request", "--output", str(output)])

    assert exit_code == 0
    request = yaml.safe_load(output.read_text())
    assert request["jr_fellows"] == ["NCC Raya", "NCC Joseph"]
    assert request["fellow_week_pairs"]["NCC Prash"][:3] == [1, 7, 25]


def test_solve_reads_yaml_and_writes_workbook(tmp_path, monkeypatch):
    request_path = tmp_path / "annual-request.yaml"
    output_path = tmp_path / "optimized_schedule.xlsx"
    request_path.write_text(yaml.safe_dump({
        "jr_fellows": ["NCC Raya"],
        "fellow_week_pairs": {"NCC Raya": [0, 1]},
    }))
    captured = {}

    def fake_solve_schedule(raw_request):
        captured["raw_request"] = raw_request
        return {"request": raw_request, "shifts_for_fellows": {}, "fellows_for_shifts": {}}

    def fake_build_schedule_workbook(result):
        captured["workbook_result"] = result
        return b"fake-xlsx"

    monkeypatch.setattr(cli.service, "solve_schedule", fake_solve_schedule)
    monkeypatch.setattr(cli.service, "build_schedule_workbook", fake_build_schedule_workbook)

    exit_code = cli.main([
        "solve",
        "--request",
        str(request_path),
        "--output",
        str(output_path),
    ])

    assert exit_code == 0
    assert captured["raw_request"]["jr_fellows"] == ["NCC Raya"]
    assert captured["raw_request"]["fellow_week_pairs"] == {"NCC Raya": [0, 1]}
    assert captured["workbook_result"]["request"] == captured["raw_request"]
    assert output_path.read_bytes() == b"fake-xlsx"


def test_solve_rejects_empty_yaml_request(tmp_path, capsys):
    request_path = tmp_path / "empty.yaml"
    request_path.write_text("")

    exit_code = cli.main(["solve", "--request", str(request_path)])

    assert exit_code == 2
    assert "must contain a YAML mapping" in capsys.readouterr().err

