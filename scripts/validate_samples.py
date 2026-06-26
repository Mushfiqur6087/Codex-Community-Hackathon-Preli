#!/usr/bin/env python3
"""Step 2: drive the 10 sample cases through the service and validate.

For each sample:
  - POST its `input` to /analyze-ticket.
  - Verify the response parses as AnalyzeTicketResponse (schema validity).
  - Verify every enum value matches the expected output exactly.
  - Print a pass/fail table.

Usage:
    python scripts/validate_samples.py                 # uses http://127.0.0.1:8000
    HOST=http://localhost:8000 python scripts/validate_samples.py

Exits non-zero on any schema mismatch.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

import urllib.request
import urllib.error

ROOT = Path(__file__).resolve().parent.parent
SAMPLES_PATH = ROOT.parent / "SUST_Preli_Sample_Cases.json"
HOST = os.environ.get("HOST", "http://127.0.0.1:8000")

# Add project to sys.path so we can import the response schema.
sys.path.insert(0, str(ROOT))
from app.schemas import AnalyzeTicketResponse  # noqa: E402


def post_analyze(payload: Dict[str, Any]) -> tuple[int, Dict[str, Any] | str]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{HOST}/analyze-ticket",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        try:
            body = json.loads(body)
        except Exception:
            pass
        return e.code, body


def diff_vs_expected(actual: Dict[str, Any], expected: Dict[str, Any]) -> List[str]:
    """Compare the locked fields. Text fields are not byte-compared (Step 4).

    Step 2 enforces: schema validity + exact enum matches + ticket_id echo.
    """
    mismatches: List[str] = []

    # ticket_id must echo exactly.
    if actual.get("ticket_id") != expected.get("ticket_id"):
        mismatches.append(
            f"ticket_id: actual={actual.get('ticket_id')!r} "
            f"expected={expected.get('ticket_id')!r}"
        )

    # All other locked enum / exact fields.
    for field in (
        "relevant_transaction_id",
        "evidence_verdict",
        "case_type",
        "severity",
        "department",
        "human_review_required",
    ):
        if field in expected:
            if actual.get(field) != expected.get(field):
                mismatches.append(
                    f"{field}: actual={actual.get(field)!r} "
                    f"expected={expected.get(field)!r}"
                )

    return mismatches


def main() -> int:
    if not SAMPLES_PATH.exists():
        print(f"ERROR: sample file not found at {SAMPLES_PATH}", file=sys.stderr)
        return 2

    with SAMPLES_PATH.open("r", encoding="utf-8") as f:
        doc = json.load(f)

    cases = doc.get("cases", [])
    print(f"Validating {len(cases)} sample cases against {HOST}/analyze-ticket\n")

    all_pass = True
    rows: List[str] = []
    for case in cases:
        cid = case.get("id", "?")
        label = case.get("label", "")
        payload = case["input"]
        expected = case["expected_output"]

        status, body = post_analyze(payload)
        if status != 200:
            rows.append(f"  [{cid}] HTTP {status}  -- {label}")
            rows.append(f"        body: {body}")
            all_pass = False
            continue

        # Schema validation: must round-trip through the response model.
        try:
            parsed = AnalyzeTicketResponse.model_validate(body)
        except Exception as e:
            rows.append(f"  [{cid}] SCHEMA INVALID -- {label}")
            rows.append(f"        error: {e}")
            all_pass = False
            continue

        # Functional field-level diff vs expected_output (Step 2 scope).
        diffs = diff_vs_expected(parsed.model_dump(), expected)
        if diffs:
            rows.append(f"  [{cid}] FIELD MISMATCH -- {label}")
            for d in diffs:
                rows.append(f"        - {d}")
            all_pass = False
        else:
            rows.append(f"  [{cid}] OK   -- {label}")

    print("\n".join(rows))
    print()
    if all_pass:
        print(f"RESULT: {len(cases)}/{len(cases)} cases schema-valid and field-aligned.")
        return 0
    print("RESULT: FAILURES present (see above).")
    return 1


if __name__ == "__main__":
    sys.exit(main())