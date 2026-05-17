from __future__ import annotations

from copy import deepcopy
from io import BytesIO
from typing import Any, Dict, List

import openpyxl

try:
    from .date_to_week_index import date_to_week_index
    from .main import W, optimize_schedule
except ImportError:  # pragma: no cover - supports running from src/scheduler
    from date_to_week_index import date_to_week_index
    from main import W, optimize_schedule


DEFAULT_SHIFTS = [
    "SICU",
    "MICU",
    "NS",
    "Anaesthesia",
    "NCC1",
    "NCC2",
    "Swing",
    "Elec",
    "Vac",
    "Stroke",
    "Telestroke/Clinic",
    "Clinic/Elective",
    "SCVMC Rehab",
    "NIR",
    "ISC",
]


def get_default_schedule_request() -> Dict[str, Any]:
    return {
        "jr_fellows": ["NCC Raya", "NCC Joseph"],
        "sr_fellows": ["NCC David", "NCC Prash"],
        "stroke_fellows": ["Stroke Gabi", "Stroke Jeff", "Stroke Victoria", "Stroke Parshva"],
        "CCM_fellows": [
            "CCM Ariana",
            "CCM Bert",
            "CCM Chloe",
            "CCM Dennis",
            "CCM Edwina",
            "CCM Frank",
            "CCM George",
            "CCM Helen",
            "CCM Iago",
            "CCM Jake",
            "CCM Kyle",
            "CCM Liana",
            "CCM Mary",
            "CCM Ning",
            "CCM Oyo",
        ],
        "NH_fellows": ["NH Adam"],
        "lia": ["NCC Lia"],
        "shifts": list(DEFAULT_SHIFTS),
        "fellow_week_pairs": {
            "NCC Prash": [
                date_to_week_index((2025, 7, 10)),
                date_to_week_index((2025, 8, 20)),
                date_to_week_index((2025, 12, 25)),
                date_to_week_index((2026, 3, 30)),
                date_to_week_index((2026, 4, 6)),
                date_to_week_index((2026, 4, 13)),
                date_to_week_index((2026, 4, 20)),
            ],
            "NCC David": [
                date_to_week_index((2025, 8, 6)),
                date_to_week_index((2025, 8, 13)),
                date_to_week_index((2025, 12, 31)),
            ],
            "NCC Raya": [
                date_to_week_index((2025, 11, 25)),
                date_to_week_index((2026, 3, 18)),
            ],
            "NCC Joseph": [
                date_to_week_index((2025, 12, 25)),
                date_to_week_index((2026, 3, 9)),
                date_to_week_index((2025, 10, 20)),
                date_to_week_index((2026, 5, 18)),
                date_to_week_index((2025, 9, 16)),
            ],
            "Stroke Gabi": [
                date_to_week_index((2025, 10, 28)),
                date_to_week_index((2026, 3, 21)),
                date_to_week_index((2026, 5, 5)),
                date_to_week_index((2025, 9, 16)),
            ],
            "Stroke Jeff": [
                date_to_week_index((2025, 12, 30)),
                date_to_week_index((2026, 3, 27)),
                date_to_week_index((2025, 10, 12)),
            ],
            "Stroke Victoria": [
                date_to_week_index((2025, 10, 10)),
                date_to_week_index((2026, 5, 5)),
                date_to_week_index((2025, 9, 30)),
                date_to_week_index((2025, 9, 16)),
            ],
            "Stroke Parshva": [
                date_to_week_index((2026, 5, 5)),
                date_to_week_index((2025, 10, 14)),
                date_to_week_index((2025, 12, 30)),
                date_to_week_index((2025, 9, 16)),
            ],
        },
    }


def normalize_schedule_request(raw_request: Dict[str, Any] | None) -> Dict[str, Any]:
    if raw_request is None:
        raise ValueError("Request body must be JSON.")

    defaults = get_default_schedule_request()
    request = deepcopy(defaults)
    for key in [
        "jr_fellows",
        "sr_fellows",
        "stroke_fellows",
        "CCM_fellows",
        "NH_fellows",
        "lia",
        "shifts",
    ]:
        if key in raw_request:
            request[key] = _string_list(raw_request[key], key)

    if "fellow_week_pairs" in raw_request:
        request["fellow_week_pairs"] = _week_pairs(raw_request["fellow_week_pairs"])

    return request


def solve_schedule(raw_request: Dict[str, Any] | None) -> Dict[str, Any]:
    request = normalize_schedule_request(raw_request)
    shifts_for_fellows, fellows_for_shifts = optimize_schedule(
        jr_fellows=request["jr_fellows"],
        sr_fellows=request["sr_fellows"],
        stroke_fellows=request["stroke_fellows"],
        CCM_fellows=request["CCM_fellows"],
        NH_fellows=request["NH_fellows"],
        lia=request["lia"],
        shifts=request["shifts"],
        fellow_week_pairs=request["fellow_week_pairs"],
    )
    return {
        "request": request,
        "shifts_for_fellows": shifts_for_fellows,
        "fellows_for_shifts": fellows_for_shifts,
    }


def build_schedule_workbook(result: Dict[str, Any]) -> bytes:
    request = result["request"]
    shifts_for_fellows = result["shifts_for_fellows"]
    fellows_for_shifts = result["fellows_for_shifts"]

    wb = openpyxl.Workbook()
    _build_per_fellow_sheet(wb.active, request, shifts_for_fellows)
    ws = wb.create_sheet("NCC+Stroke Shift Schedule")
    _build_per_shift_sheet(ws, fellows_for_shifts)

    output = BytesIO()
    wb.save(output)
    return output.getvalue()


def _string_list(value: Any, key: str) -> List[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"{key} must be a list of strings.")
    return [item.strip() for item in value if item.strip()]


def _week_pairs(value: Any) -> Dict[str, List[int]]:
    if not isinstance(value, dict):
        raise ValueError("fellow_week_pairs must be an object.")

    pairs: Dict[str, List[int]] = {}
    for fellow, weeks in value.items():
        if not isinstance(fellow, str) or not isinstance(weeks, list):
            raise ValueError("fellow_week_pairs must map fellow names to week lists.")
        parsed_weeks = []
        for week in weeks:
            if not isinstance(week, int) or week < 0 or week >= W:
                raise ValueError("Vacation weeks must be integers from 0 through 51.")
            parsed_weeks.append(week)
        pairs[fellow] = parsed_weeks
    return pairs


def _build_per_fellow_sheet(ws, request: Dict[str, Any], shifts_for_fellows: Dict[str, List[str]]) -> None:
    ws.title = "Per-Fellow Schedule"
    fellows = (
        request["jr_fellows"]
        + request["sr_fellows"]
        + request["stroke_fellows"]
        + request["NH_fellows"]
        + request["lia"]
    )
    _write_table(ws, ["Week"] + fellows, [
        [week] + [shifts_for_fellows.get(fellow, [""] * W)[week] for fellow in fellows]
        for week in range(W)
    ])


def _build_per_shift_sheet(ws, fellows_for_shifts: Dict[str, List[str]]) -> None:
    shifts = ["NCC1", "NCC2", "Extra", "Swing", "Stroke", "Telestroke/Clinic", "Stroke_Supervisory"]
    _write_table(ws, ["Week"] + shifts, [
        [week] + [_cell_value(fellows_for_shifts.get(shift, [""] * W)[week]) for shift in shifts]
        for week in range(W)
    ])


def _write_table(ws, headers: List[str], rows: List[List[Any]]) -> None:
    fills = _fills()
    for column, header in enumerate(headers, start=1):
        ws.cell(row=1, column=column, value=header)
    for row_index, row in enumerate(rows, start=2):
        for column, value in enumerate(row, start=1):
            cell = ws.cell(row=row_index, column=column, value=_cell_value(value))
            if isinstance(value, str) and value in fills:
                cell.fill = fills[value]
    ws.freeze_panes = "B2"


def _cell_value(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(value)
    return value


def _fills() -> Dict[str, Any]:
    colors = {
        "MICU": "B4C6E7",
        "SICU": "FFE699",
        "NCC1": "C6E0B4",
        "NCC2": "C6E0B4",
        "Swing": "A9D08E",
        "Stroke": "F8CBAD",
        "Telestroke/Clinic": "DCF4D6",
        "NS": "FFF3CC",
        "Anaesthesia": "F8CBAD",
        "Clinic/Elective": "C4D677",
        "NIR": "DDB644",
        "Vac": "D9E2F3",
        "Elec": "E7E6E6",
    }
    return {
        key: openpyxl.styles.PatternFill(start_color=color, end_color=color, fill_type="solid")
        for key, color in colors.items()
    }
