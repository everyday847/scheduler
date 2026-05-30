# scheduler
Stanford fellowship scheduling

## Weekend Call Solver

Generate weekend assignments from the weekday matrix in `weekend_call.csv` with:

```bash
PYTHONPATH=src uv run python -m scheduler.weekend_call_solver weekend_call.csv /tmp/weekend_call_with_weekends.csv
```

The output CSV preserves the original weekday columns and appends `Weekend NCC1`, `Weekend NCC2`, and `Weekend Stroke`.
