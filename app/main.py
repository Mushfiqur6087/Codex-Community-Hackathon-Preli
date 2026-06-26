"""FastAPI entry point for QueueStorm Investigator.

Step 1: /health returns readiness, /analyze-ticket returns a schema-valid
placeholder. Steps 3-6 will replace the stub with real reasoning + safety.
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.schemas import AnalyzeTicketResponse

app = FastAPI(title="QueueStorm Investigator", version="0.1.0-step1")


@app.get("/health")
def health() -> dict:
    """Readiness probe. Judge harness calls this before hidden tests."""
    return {"status": "ok"}


@app.post("/analyze-ticket")
def analyze_ticket(payload: dict) -> AnalyzeTicketResponse:
    """Step-1 stub: echo the ticket_id with placeholder fields.

    Steps 3-6 will replace the body with the evidence engine + safety layer.
    """
    ticket_id = payload.get("ticket_id")
    if not isinstance(ticket_id, str) or not ticket_id.strip():
        raise HTTPException(status_code=400, detail="ticket_id is required")

    complaint = payload.get("complaint", "")
    if not isinstance(complaint, str) or not complaint.strip():
        raise HTTPException(status_code=422, detail="complaint must be a non-empty string")

    return AnalyzeTicketResponse(
        ticket_id=ticket_id,
        relevant_transaction_id=None,
        evidence_verdict="insufficient_data",
        case_type="other",
        severity="low",
        department="customer_support",
        agent_summary="Step 1 placeholder - reasoning engine not yet wired.",
        recommended_next_action="Implement Step 3 evidence reasoning.",
        customer_reply="Thank you for contacting support. We are reviewing your case.",
        human_review_required=True,
        confidence=0.0,
        reason_codes=["step1_stub"],
    )


@app.exception_handler(RequestValidationError)
async def _validation_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
    """Return 400 for malformed JSON/missing required fields. No stack trace."""
    return JSONResponse(status_code=400, content={"detail": "malformed request body"})


@app.exception_handler(Exception)
async def _unhandled(_request: Request, _exc: Exception) -> JSONResponse:
    """Return 500 with a non-sensitive message. Never leak stack traces."""
    return JSONResponse(status_code=500, content={"detail": "internal error"})
