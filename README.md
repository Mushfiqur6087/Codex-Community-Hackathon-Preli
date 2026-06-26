# QueueStorm Investigator

bKash presents SUST CSE Carnival 2026 - Codex Community Hackathon (Preliminary).

## Step 1 - Project Skeleton

A FastAPI service exposing the two endpoints the judge will call:
- `GET /health` -> `{"status":"ok"}`
- `POST /analyze-ticket` -> schema-valid placeholder response

The reasoning, safety, and persistence layers come in Steps 3-6.

## Run locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## Smoke test

In a second shell:
```bash
chmod +x scripts/smoke_test.sh
./scripts/smoke_test.sh
```

## Run with Docker

```bash
docker build -t queuestorm-team .
docker run -p 8000:8000 queuestorm-team
```

## Tech stack

- Python 3.11, FastAPI, Uvicorn, Pydantic v2.
- No GPU, no external APIs required for Step 1.
