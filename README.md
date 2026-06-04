# scheduler
Stanford fellowship scheduling

## Schedule Solver (RoundingSat)

Generate an optimized schedule from an annual YAML config + standing rules:

```bash
PYTHONPATH=src:new_approach/src python run_v3_optimize.py
```

Output: `output_v3_workbook.xlsx` with weekly shifts, weekend call, and night call.

## Feasibility Check

Check whether a set of standing + annual rules is satisfiable:

```bash
curl -X POST http://localhost:5000/api/feasibility/check \
  -H 'Content-Type: application/json' \
  -d '{"config": {...}, "rule": {...}}'
```

## Web UI (React)

```bash
cd src/web && npm start        # start frontend
PYTHONPATH=src:new_approach/src python -m scheduler.web_app  # start backend
```
