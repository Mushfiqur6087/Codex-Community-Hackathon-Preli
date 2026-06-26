"""FastAPI entry point for QueueStorm Investigator.

Step 3: /health returns readiness; /analyze-ticket now runs the hybrid
Evidence Reasoning Engine (deterministic extractors -> rule path OR LLM path
-> rule-based verifier -> Pydantic). Steps 4-6 will layer in the safety
finisher and the test harness.

HTTP semantics (Problem Statement section 4.1):
    200 -> success
    400 -> malformed JSON / missing required fields
    422 -> semantically invalid (empty complaint, bad enum, etc.)
    500 -> internal error; never leak stack traces, tokens, or secrets
"""

from __future__ import annotations

import logging
import time

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.llm_client import LLMClient
from app.reasoning import decide
from app.schemas import AnalyzeTicketRequest, AnalyzeTicketResponse

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("app.main")

# Shared LLM client (reads env vars once at boot). When unconfigured, the
# orchestrator falls back to the rule path automatically.
llm_client = LLMClient()

app = FastAPI(
    title="QueueStorm Investigator",
    version="0.3.0-step3",
    description=(
        "SUST CSE Carnival 2026 Codex Community Hackathon preliminary. "
        "Provides /health and /analyze-ticket. See Problem Statement for "
        "the full contract."
    ),
)


# --- Readiness ---


@app.get("/health")
def health() -> dict:
    """Readiness probe. Judge harness calls this before hidden tests."""
    return {"status": "ok", "llm_enabled": llm_client.enabled}


# --- Main endpoint ---


_OPENAPI_REQUEST_EXAMPLE = {
    "summary": "Wrong transfer with matching evidence (SAMPLE-01)",
    "value": {
        "ticket_id": "TKT-001",
        "complaint": (
            "I sent 5000 taka to a wrong number around 2pm today. The number "
            "was supposed to be 01712345678 but I think I typed it wrong."
        ),
        "language": "en",
        "channel": "in_app_chat",
        "user_type": "customer",
        "campaign_context": "boishakh_bonanza_day_1",
        "transaction_history": [
            {
                "transaction_id": "TXN-9101",
                "timestamp": "2026-04-14T14:08:22Z",
                "type": "transfer",
                "amount": 5000,
                "counterparty": "+8801719876543",
                "status": "completed",
            }
        ],
    },
}


@app.post(
    "/analyze-ticket",
    response_model=AnalyzeTicketResponse,
    status_code=status.HTTP_200_OK,
    responses={
        400: {"description": "Malformed JSON or missing required fields."},
        422: {"description": "Schema-valid but semantically invalid input."},
        500: {"description": "Internal error."},
    },
    openapi_extra={
        "requestBody": {
            "content": {
                "application/json": {
                    "examples": {"sample_01": _OPENAPI_REQUEST_EXAMPLE}
                }
            }
        }
    },
)
def analyze_ticket(payload: AnalyzeTicketRequest) -> AnalyzeTicketResponse:
    """Run the Evidence Reasoning Engine and return the structured verdict.

    Pipeline: extractors -> (safety short-circuit | rule path | LLM path)
    -> rule-based verifier -> Pydantic final validation.
    """
    # Extra semantic guards beyond Pydantic (return 422, not 500).
    if not payload.complaint.strip():
        raise HTTPException(
            status_code=422, detail="complaint must contain non-whitespace text"
        )

    t0 = time.perf_counter()
    result = decide(payload, client=llm_client)
    latency_ms = (time.perf_counter() - t0) * 1000.0
    logger.info(
        "ticket=%s path=%s overrides=%d latency=%.1fms",
        payload.ticket_id,
        result.path,
        len(result.verifier_overrides),
        latency_ms,
    )
    return result.response


# --- Error handlers (controlled bodies, no leaks) ---


@app.exception_handler(RequestValidationError)
async def _validation_handler(
    _request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Pydantic validation failed.

    If the body itself is missing or not JSON -> 400 (malformed).
    If fields are present but invalid (bad enum, out-of-range, etc.) -> 422.
    """
    errors = exc.errors()
    is_malformed = any(
        e.get("type") in {"json_invalid", "missing", "model_attributes_type"}
        or e.get("loc", [None])[0] in ("body",)
        and e.get("type") == "value_error.jsondecode"
        for e in errors
    ) or not errors

    code = 400 if is_malformed else 422
    # Sanitize: only return safe fields (loc, msg, type) — no input values.
    safe_errors = [
        {
            "loc": [str(p) for p in e.get("loc", [])],
            "msg": e.get("msg", ""),
            "type": e.get("type", ""),
        }
        for e in errors
    ]
    return JSONResponse(
        status_code=code,
        content={"detail": "malformed request body" if is_malformed else "invalid input", "errors": safe_errors},
    )


@app.exception_handler(HTTPException)
async def _http_exception_handler(_request: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.exception_handler(Exception)
async def _unhandled(_request: Request, _exc: Exception) -> JSONResponse:
    """Catch-all: never leak stack traces, tokens, or secrets."""
    return JSONResponse(status_code=500, content={"detail": "internal error"})
