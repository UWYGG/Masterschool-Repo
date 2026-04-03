# Masterschool Admissions API

This repository contains a step-by-step FastAPI implementation for the admissions exercise.

## Layout

- `app/admissions_config.py` — funnel steps, tasks, and rules
- `app/service.py` — in-memory users and progression logic
- `app/main.py` — FastAPI routes and request bodies (Pydantic)
- `DECISIONS.md` — key reasoning and tradeoffs behind implementation choices

## Run

1. Install dependencies: `py -3 -m pip install -r requirements.txt`
2. Start server: `py -3 -m uvicorn app.main:app --reload`
