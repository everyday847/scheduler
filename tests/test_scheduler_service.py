import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "scheduler"))


def test_default_schedule_request_contains_seed_data():
    from scheduler.service import get_default_schedule_request

    request = get_default_schedule_request()

    assert request["jr_fellows"] == ["NCC Raya", "NCC Joseph"]
    assert request["sr_fellows"] == ["NCC David", "NCC Prash"]
    assert "Stroke Gabi" in request["stroke_fellows"]
    assert "NH Adam" in request["NH_fellows"]
    assert request["lia"] == ["NCC Lia"]
    assert request["fellow_week_pairs"]["NCC Prash"][:3] == [1, 7, 25]
    assert "Clinic/Elective" in request["shifts"]


def test_build_schedule_workbook_returns_xlsx_bytes():
    from scheduler.service import build_schedule_workbook

    result = {
        "request": {
            "jr_fellows": ["NCC Raya"],
            "sr_fellows": [],
            "stroke_fellows": ["Stroke Gabi"],
            "CCM_fellows": [],
            "NH_fellows": [],
            "lia": [],
        },
        "shifts_for_fellows": {
            "NCC Raya": ["MICU"] * 52,
            "Stroke Gabi": ["Stroke"] * 52,
        },
        "fellows_for_shifts": {
            "NCC1": ["NCC Raya"] * 52,
            "NCC2": [""] * 52,
            "Extra": [""] * 52,
            "Swing": [""] * 52,
            "Stroke": ["Stroke Gabi"] * 52,
            "Telestroke/Clinic": [""] * 52,
            "Stroke_Supervisory": [""] * 52,
        },
    }

    workbook = build_schedule_workbook(result)

    assert isinstance(workbook, bytes)
    assert io.BytesIO(workbook).read(2) == b"PK"


def test_flask_default_schedule_endpoint_returns_seed_data():
    from scheduler import app

    client = app.test_client()
    response = client.get("/api/default-schedule")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["jr_fellows"] == ["NCC Raya", "NCC Joseph"]
    assert payload["lia"] == ["NCC Lia"]


def test_solve_schedule_normalizes_and_returns_optimizer_result(monkeypatch):
    from scheduler import service

    captured = {}

    def fake_optimize_schedule(**kwargs):
        captured.update(kwargs)
        return (
            {"NCC Raya": ["MICU"] * 52},
            {"NCC1": ["NCC Raya"] * 52, "NCC2": [""] * 52},
        )

    monkeypatch.setattr(service, "optimize_schedule", fake_optimize_schedule)

    result = service.solve_schedule({
        "jr_fellows": ["NCC Raya"],
        "fellow_week_pairs": {"NCC Raya": [0, 1]},
    })

    assert captured["jr_fellows"] == ["NCC Raya"]
    assert captured["sr_fellows"] == []
    assert captured["stroke_fellows"] == []
    assert captured["CCM_fellows"] == []
    assert captured["NH_fellows"] == []
    assert captured["lia"] == []
    assert captured["fellow_week_pairs"] == {"NCC Raya": [0, 1]}
    assert result["shifts_for_fellows"]["NCC Raya"][0] == "MICU"


def test_normalize_schedule_request_does_not_backfill_default_fellows():
    from scheduler.service import normalize_schedule_request

    request = normalize_schedule_request({
        "jr_fellows": ["NCC New"],
        "sr_fellows": [],
        "stroke_fellows": [],
        "CCM_fellows": [],
        "fellow_week_pairs": {},
    })

    assert request["jr_fellows"] == ["NCC New"]
    assert request["sr_fellows"] == []
    assert request["stroke_fellows"] == []
    assert request["CCM_fellows"] == []
    assert request["NH_fellows"] == []
    assert request["lia"] == []
    assert request["fellow_week_pairs"] == {}
    assert "NCC Prash" not in request["fellow_week_pairs"]


def test_normalize_schedule_request_rejects_requests_for_missing_fellows():
    import pytest

    from scheduler.service import normalize_schedule_request

    with pytest.raises(ValueError, match="Vacation request references unknown fellow: NCC Prash"):
        normalize_schedule_request({
            "jr_fellows": ["NCC New"],
            "sr_fellows": [],
            "stroke_fellows": [],
            "CCM_fellows": [],
            "fellow_week_pairs": {"NCC Prash": [1]},
        })
