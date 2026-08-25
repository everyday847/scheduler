# Repository Guidelines

## Project Structure & Module Organization

This repo contains a Stanford fellowship scheduling prototype with two main areas:

- `src/scheduler/`: Python scheduling logic and Streamlit UI. Core constraint code lives in `main.py`, `constraints.py`, `fellow_mapping.py`, and helper modules such as `date_to_week_index.py`.
- `src/web/`: Create React App frontend in TypeScript. App code is under `src/web/src/`; static assets are in `src/web/public/`.
- `README.md`: short project summary. There is no packaging metadata or Python test directory yet.

## Build, Test, and Development Commands

Python scheduler:

- `cd src/scheduler && python -m venv .venv && source .venv/bin/activate`: create and activate a local environment.
- `pip install -r requirements.txt`: install Z3, Streamlit, OpenPyXL, and related UI dependencies.
- `streamlit run app.py`: run the scheduler UI locally.

React frontend:

- `cd src/web && npm install`: install frontend dependencies from `package-lock.json`.
- `npm start`: start the local React development server.
- `npm run build`: create a production build.
- `npm test -- --watchAll=false`: run the Jest/React Testing Library suite once.

## Coding Style & Naming Conventions

Python code uses 4-space indentation, snake_case function and variable names, and module-level scheduling constants such as `W = 52`. Keep constraint helpers small and named for the rule they enforce, for example `maximum_consecutive_icu_shifts`.

TypeScript React code uses functional components, local state hooks, and `.tsx` for UI files. Prefer descriptive handler names such as `handleClick` or `setStrokeFellows`. Keep imports grouped by package, then local CSS/assets.

## Testing Guidelines

Frontend tests use Jest with React Testing Library; place tests next to components as `*.test.tsx`. The current `App.test.tsx` is still the Create React App starter test, so update it when changing rendered behavior.

No Python test suite is currently present. Add focused `pytest` tests for pure scheduling helpers before changing constraint logic. If `pytest` fails in the sandbox, especially with a SIGSEGV, retry with output capture disabled (`pytest -s`) or add a readline shim.

## Commit & Pull Request Guidelines

Recent history uses short, informal commit subjects such as `Update first week of year`. Keep new commits concise but make them more specific when possible, for example `Fix CCM block constraints`.

Pull requests should include a short description, the scheduling behavior affected, commands run, and screenshots for UI changes. Link any issue or scheduling request that motivated the change.

## Agent skills

### Issue tracker

Issues and PRDs are tracked in GitHub Issues for `everyday847/scheduler`. See `docs/agents/issue-tracker.md`.

### Triage labels

Use the default five-label triage vocabulary. See `docs/agents/triage-labels.md`.

### Domain docs

This is a single-context repo. See `docs/agents/domain.md`.
