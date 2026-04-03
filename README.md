# Masterschool Admissions API

This repository contains a step-by-step FastAPI implementation for the admissions exercise.

## Layout

- `app/admissions_config.py` — funnel steps, tasks, and rules
- `app/service.py` — in-memory users and progression logic
- `app/main.py` — FastAPI routes and request bodies (Pydantic)
- `tests/test_admissions_flow.py` — 32 tests covering all PDF requirements and edge cases
- `tests/test_pm_modifiability.py` — 8 tests proving the config-driven architecture handles PM-driven flow changes with no service changes
- **40 tests total across both suites**
- `DESIGN_DECISIONS.md` — key reasoning and tradeoffs behind implementation choices

## Setup

Install dependencies:

```bash
py -3 -m pip install -r requirements.txt
```

## Run

Start the server:

```bash
py -3 -m uvicorn app.main:app --reload
```

The API will be available at `http://127.0.0.1:8000`.
Interactive docs (Swagger UI) at `http://127.0.0.1:8000/docs`.

## Test

Run the full test suite:

```bash
py -3 -m pytest tests/ -v
```

All 40 tests should pass.
