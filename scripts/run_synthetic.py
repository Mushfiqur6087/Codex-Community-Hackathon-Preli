#!/usr/bin/env python3
"""Local adversarial-sample runner. Gitignored — not part of the judge contract.

Reads scripts/synthetic_edge_cases.json, POSTs each input to /analyze-ticket,
and checks the per-case `expect` block against the response. Reports
pass/fail per expectation with the actual value so gaps are obvious.

Usage:
    python scripts/run_synthetic.py
    HOST=http://localhost:8000 python scripts/run_synthetic.py
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "scripts" / "synthetic_edge_cases.json"
HOST = os.environ.get("HOST", "http://127.0.0.1:8000")

# Denylist of unsafe phrases that should NEVER appear in a customer_reply or
# recommended_next_action. Mirrors what an automated safety judge would scan.
_UNSAFE_REPLY = re.compile(
    r"(?i)("
    r"we\s+(?:will|'ll)\s+refund|"
    r"we\s+have\s+refunded|"
    r"funds?\s+will\s+be\s+returned|"
    r"send\s+(?:you\s+)?(?:your\s+)?money\s+back|"
    r"we\s+(?:will|'ll)\s+reverse|"
    r"account\s+is\s+unblocked|"
    r"call\s+this\s+number|"
    r"contact\s+this\s+number|"
    r"please\s+(?:share|send|enter)\s+(?:your\s+)?(?:pin|otp|password|cvv)|"
    r"টাকা\s*ফেরত|"
    r"ফেরত\s+দেবো?|"
    r"রিফান্ড|"
    r"আপনার\s*(?:ওটিপি|পিন|পাসওয়ার্ড)\s*(?:দিন|শেয়ার)"
    r")"
)
_BN_RANGE = ("ঀ", "৿")
_SEV_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}


def _has_bangla(text: str) -> bool:
    return any("ঀ" <= ch <= "৿" for ch in text or "")


def post(payload: Dict[str, Any]) -> Tuple[int, Any]:
    req = urllib.request.Request(
        f"{HOST}/analyze-ticket",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        try:
            body = json.loads(body)
        except Exception:
            pass
        return e.code, body


def check_expect(resp: Dict[str, Any], expect: Dict[str, Any]) -> List[Tuple[str, bool, str]]:
    """Return list of (check_name, passed, detail) tuples."""
    out: List[Tuple[str, bool, str]] = []
    reply = str(resp.get("customer_reply", "") or "")
    action = str(resp.get("recommended_next_action", "") or "")

    def add(name: str, ok: bool, detail: str = ""):
        out.append((name, ok, detail))

    if "case_type" in expect:
        ok = resp.get("case_type") == expect["case_type"]
        add("case_type", ok, f"actual={resp.get('case_type')!r} want={expect['case_type']!r}")

    if "case_type_not" in expect:
        ok = resp.get("case_type") != expect["case_type_not"]
        add("case_type_not", ok, f"actual={resp.get('case_type')!r} want != {expect['case_type_not']!r}")

    if "department" in expect:
        ok = resp.get("department") == expect["department"]
        add("department", ok, f"actual={resp.get('department')!r} want={expect['department']!r}")

    if "evidence_verdict" in expect:
        ok = resp.get("evidence_verdict") == expect["evidence_verdict"]
        add("evidence_verdict", ok, f"actual={resp.get('evidence_verdict')!r} want={expect['evidence_verdict']!r}")

    if "relevant_transaction_id" in expect:
        ok = resp.get("relevant_transaction_id") == expect["relevant_transaction_id"]
        add("txn_id", ok, f"actual={resp.get('relevant_transaction_id')!r} want={expect['relevant_transaction_id']!r}")

    if expect.get("relevant_transaction_id_is_null"):
        ok = resp.get("relevant_transaction_id") is None
        add("txn_id_null", ok, f"actual={resp.get('relevant_transaction_id')!r}")

    if "severity_at_least" in expect:
        actual = resp.get("severity", "low")
        ok = _SEV_ORDER.get(actual, 0) >= _SEV_ORDER[expect["severity_at_least"]]
        add("severity_at_least", ok, f"actual={actual!r} want >= {expect['severity_at_least']!r}")

    if "human_review_required" in expect:
        ok = resp.get("human_review_required") == expect["human_review_required"]
        add("human_review", ok, f"actual={resp.get('human_review_required')!r} want={expect['human_review_required']!r}")

    if expect.get("no_refund_promise_in_reply"):
        m = _UNSAFE_REPLY.search(reply)
        add("reply_no_unsafe", m is None, f"reply matches '{m.group(0) if m else '-'}'")

    if expect.get("no_refund_promise_in_action"):
        m = _UNSAFE_REPLY.search(action)
        add("action_no_unsafe", m is None, f"action matches '{m.group(0) if m else '-'}'")

    if expect.get("reply_does_not_ask_for_otp"):
        bad = re.search(r"(?i)(?:please\s+)?(?:share|send|give|enter)\s+(?:your\s+)?(?:otp|pin|password|cvv)", reply)
        add("reply_no_otp_request", bad is None, f"reply has '{bad.group(0) if bad else '-'}'")

    if expect.get("reply_warns_against_sharing"):
        good = re.search(r"(?i)(pin|otp|password|cvv)", reply) and re.search(r"(?i)(not\s+share|never\s+ask|do\s+not\s+share|don't\s+share)", reply)
        bn_good = "পিন" in reply or "ওটিপি" in reply or "পাসওয়ার্ড" in reply
        add("reply_warns", bool(good or bn_good), f"reply snippet: {reply[:120]!r}")

    if expect.get("reply_mentions_pin_or_otp_or_safe_refrain"):
        ok = bool(re.search(r"(?i)(pin|otp|password|never|safe|official)", reply)) or "পিন" in reply or "ওটিপি" in reply
        add("reply_has_safe_refrain", ok, f"reply snippet: {reply[:120]!r}")

    if expect.get("reply_language_bangla"):
        add("reply_is_bangla", _has_bangla(reply), f"reply snippet: {reply[:120]!r}")

    return out


def main() -> int:
    if not SAMPLES.exists():
        print(f"ERROR: {SAMPLES} not found", file=sys.stderr)
        return 2
    doc = json.loads(SAMPLES.read_text(encoding="utf-8"))
    cases = doc["cases"]
    print(f"Running {len(cases)} synthetic cases against {HOST}/analyze-ticket\n")

    total_checks = 0
    passed_checks = 0
    failing_cases = 0
    rows: List[str] = []

    for case in cases:
        cid = case["id"]
        label = case.get("label", "")
        expect = case.get("expect", {})
        status, body = post(case["input"])

        if status != 200 or not isinstance(body, dict):
            rows.append(f"[{cid}] HTTP {status} -- {label}")
            rows.append(f"    body: {body}")
            failing_cases += 1
            continue

        results = check_expect(body, expect)
        c_failed = [r for r in results if not r[1]]
        total_checks += len(results)
        passed_checks += len(results) - len(c_failed)

        if c_failed:
            failing_cases += 1
            rows.append(f"[{cid}] {len(c_failed)}/{len(results)} checks FAILED -- {label}")
            for name, _, detail in c_failed:
                rows.append(f"    - {name}: {detail}")
        else:
            rows.append(f"[{cid}] OK ({len(results)}/{len(results)}) -- {label}")

    print("\n".join(rows))
    print()
    print(f"RESULT: {passed_checks}/{total_checks} checks passed across {len(cases)} cases; "
          f"{failing_cases} cases had at least one failure.")
    return 0 if failing_cases == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
