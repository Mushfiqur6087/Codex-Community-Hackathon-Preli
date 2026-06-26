"""Response and request schemas for QueueStorm Investigator.

These are the locked-in shapes judges will validate against. Field names,
types, and enum spellings are exactly as defined in the problem statement.

Step 1: only the response model is wired in; the request model is a stub.
Step 2 will tighten request validation; later steps will fill the values.
"""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field


# --- Enums (locked taxonomy from problem statement Section 7) ---

EvidenceVerdict = Literal["consistent", "inconsistent", "insufficient_data"]
Severity = Literal["low", "medium", "high", "critical"]

CaseType = Literal[
    "wrong_transfer",
    "payment_failed",
    "refund_request",
    "duplicate_payment",
    "merchant_settlement_delay",
    "agent_cash_in_issue",
    "phishing_or_social_engineering",
    "other",
]

Department = Literal[
    "customer_support",
    "dispute_resolution",
    "payments_ops",
    "merchant_operations",
    "agent_operations",
    "fraud_risk",
]


# --- Request schema (minimal stub for Step 1; tightened in Step 2) ---


class TransactionHistoryEntry(BaseModel):
    transaction_id: str
    timestamp: str
    type: str
    amount: float
    counterparty: str
    status: str


class AnalyzeTicketRequest(BaseModel):
    ticket_id: str
    complaint: str
    language: Optional[str] = None
    channel: Optional[str] = None
    user_type: Optional[str] = None
    campaign_context: Optional[str] = None
    transaction_history: Optional[List[TransactionHistoryEntry]] = None
    metadata: Optional[dict] = None


# --- Response schema (locked for Step 1) ---


class AnalyzeTicketResponse(BaseModel):
    ticket_id: str = Field(..., description="Must match the request ticket_id.")
    relevant_transaction_id: Optional[str] = Field(
        None, description="Transaction ID from history, or null if none matches."
    )
    evidence_verdict: EvidenceVerdict
    case_type: CaseType
    severity: Severity
    department: Department
    agent_summary: str
    recommended_next_action: str
    customer_reply: str
    human_review_required: bool
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    reason_codes: List[str] = Field(default_factory=list)
