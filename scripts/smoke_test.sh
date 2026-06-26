#!/usr/bin/env bash
# Local smoke test for Step 1. Server must be running on $HOST.
set -euo pipefail
HOST="${HOST:-http://127.0.0.1:8000}"

echo ">> GET /health"
curl -fsS "${HOST}/health"
echo

echo ">> POST /analyze-ticket (sample)"
curl -fsS -X POST "${HOST}/analyze-ticket" \
    -H "Content-Type: application/json" \
    -d '{
      "ticket_id": "TKT-001",
      "complaint": "I sent 5000 taka to a wrong number around 2pm today.",
      "language": "en",
      "channel": "in_app_chat",
      "user_type": "customer",
      "transaction_history": [
        {"transaction_id": "TXN-9101", "timestamp": "2026-04-14T14:08:22Z",
         "type": "transfer", "amount": 5000, "counterparty": "+8801719876543",
         "status": "completed"}
      ]
    }'
echo

echo ">> POST /analyze-ticket (empty body -> 400)"
http_code=$(curl -s -o /dev/null -w "%{http_code}" -X POST "${HOST}/analyze-ticket" \
    -H "Content-Type: application/json" -d '{}')
echo "HTTP ${http_code}"
[[ "${http_code}" == "400" || "${http_code}" == "422" ]] || {
    echo "expected 400 or 422, got ${http_code}" >&2; exit 1;
}

echo "ALL CHECKS PASSED"
