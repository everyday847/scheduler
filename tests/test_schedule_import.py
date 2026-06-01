from __future__ import annotations

import pytest
from scheduler.schedule_import import parse_schedule_file, ImportResult


CSV_SIMPLE = b"""NCC Alaric,NCC Bertram,Stroke Gabi
MICU,NCC1,Stroke
MICU,NCC1,Telestroke/Clinic
NCC1,MICU,Stroke
NCC1,MICU,Elec
"""

CSV_WITH_WEEK_COL = b"""Week,NCC Alaric,NCC Bertram,Weekend NCC1,Night Mon
1,MICU,NCC1,NCC Alaric,NCC Bertram
2,MICU,NCC1,NCC Bertram,NCC Alaric
3,NCC1,MICU,NCC Alaric,NCC Bertram
"""


def test_parse_csv_basic():
    result = parse_schedule_file(CSV_SIMPLE, "schedule.csv")
    assert result.fellow_names == ["NCC Alaric", "NCC Bertram", "Stroke Gabi"]
    assert result.num_weeks == 4
    assert "MICU" in result.shifts_found
    assert "NCC1" in result.shifts_found
    assert "Stroke" in result.shifts_found
    assert "Telestroke/Clinic" in result.shifts_found
    assert "Elec" in result.shifts_found
    assert result.assignments["NCC Alaric"] == ["MICU", "MICU", "NCC1", "NCC1"]
    assert result.assignments["NCC Bertram"] == ["NCC1", "NCC1", "MICU", "MICU"]


def test_parse_csv_skips_weekend_night_columns():
    result = parse_schedule_file(CSV_WITH_WEEK_COL, "schedule.csv")
    assert result.fellow_names == ["NCC Alaric", "NCC Bertram"]
    assert "Weekend NCC1" not in result.fellow_names
    assert "Night Mon" not in result.fellow_names
    assert result.num_weeks == 3


def test_parse_csv_empty_raises():
    with pytest.raises(ValueError, match="Empty CSV"):
        parse_schedule_file(b"", "schedule.csv")


def test_parse_csv_no_fellows_raises():
    with pytest.raises(ValueError, match="No fellow columns"):
        parse_schedule_file(b"Week,Weekend NCC1\n1,Someone\n", "schedule.csv")


def test_parse_csv_with_blanks():
    csv_data = b"Fellow A,Fellow B\nMICU,NCC1\n,NCC1\nMICU,\n"
    result = parse_schedule_file(csv_data, "test.csv")
    assert result.assignments["Fellow A"] == ["MICU", "", "MICU"]
    assert result.assignments["Fellow B"] == ["NCC1", "NCC1", ""]
    assert "" not in result.shifts_found


def test_parse_xlsx():
    import openpyxl
    from io import BytesIO

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["NCC Alaric", "NCC Bertram"])
    ws.append(["MICU", "NCC1"])
    ws.append(["NCC1", "MICU"])

    buf = BytesIO()
    wb.save(buf)

    result = parse_schedule_file(buf.getvalue(), "schedule.xlsx")
    assert result.fellow_names == ["NCC Alaric", "NCC Bertram"]
    assert result.num_weeks == 2
    assert result.assignments["NCC Alaric"] == ["MICU", "NCC1"]
