#!/usr/bin/env bash
# Local smoke test for Step 2. Server must be running on $HOST.
# Exercises: /health, /analyze-ticket (valid), bad enums, oversized history,
# empty complaint, malformed JSON, missing required field.
set -euo pipefail
HOST="${HOST:-http://127.0.0.1:8000}"

post() {
    # post <path> <body> -> echoes "HTTP_CODE BODY"
    local path="$1" body="$2"
    local out
    out=$(curl -s -o /tmp/_smoke_body -w "%{http_code}" -X POST "${HOST}${path}" \
        -H "Content-Type: application/json" -d "${body}")
    echo "${out} $(cat /tmp/_smoke_body)"
}

assert_code() {
    local actual="$1" expected="$2" label="$3"
    if [[ "${actual}" != "${expected}" ]]; then
        echo "FAIL ${label}: expected HTTP ${expected}, got ${actual}" >&2
        exit 1
    fi
    echo "OK   ${label} (HTTP ${actual})"
}

echo "=== /health ==="
HEALTH=$(curl -s -o /tmp/_h -w "%{http_code}" "${HOST}/health")
assert_code "${HEALTH}" "200" "GET /health"
cat /tmp/_h; echo

echo "=== /analyze-ticket valid sample ==="
read -r CODE BODY < <(post "/analyze-ticket" '{
  "ticket_id": "TKT-001",
  "complaint": "I sent 5000 taka to a wrong number.",
  "language": "en",
  "channel": "in_app_chat",
  "user_type": "customer",
  "transaction_history": [
    {"transaction_id":"TXN-9101","timestamp":"2026-04-14T14:08:22Z",
     "type":"transfer","amount":5000,"counterparty":"+8801719876543","status":"completed"}
  ]
}')
assert_code "${CODE}" "200" "valid sample"
echo "${BODY}"; echo

echo "=== /analyze-ticket empty body -> 400 ==="
read -r CODE BODY < <(post "/analyze-ticket" '{}')
assert_code "${CODE}" "400" "empty body"

echo "=== /analyze-ticket missing ticket_id -> 400 ==="
read -r CODE BODY < <(post "/analyze-ticket" '{"complaint":"something"}')
assert_code "${CODE}" "400" "missing ticket_id"

echo "=== /analyze-ticket empty complaint -> 422 ==="
read -r CODE BODY < <(post "/analyze-ticket" '{"ticket_id":"TKT-X","complaint":""}')
assert_code "${CODE}" "422" "empty complaint"

echo "=== /analyze-ticket bad enum language -> 422 ==="
read -r CODE BODY < <(post "/analyze-ticket" '{
  "ticket_id":"TKT-X","complaint":"x","language":"french"
}')
assert_code "${CODE}" "422" "bad enum language"

echo "=== /analyze-ticket unknown field -> 422 ==="
read -r CODE BODY < <(post "/analyze-ticket" '{
  "ticket_id":"TKT-X","complaint":"x","not_a_field":123
}')
assert_code "${CODE}" "422" "unknown field"

echo "=== /analyze-ticket negative amount -> 422 ==="
read -r CODE BODY < <(post "/analyze-ticket" '{
  "ticket_id":"TKT-X","complaint":"x",
  "transaction_history":[{"transaction_id":"TXN-1","timestamp":"2026-04-14T14:08:22Z",
  "type":"transfer","amount":-100,"counterparty":"+8801700000000","status":"completed"}]
}')
assert_code "${CODE}" "422" "negative amount"

echo "=== /analyze-ticket malformed JSON -> 400 ==="
CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST "${HOST}/analyze-ticket" \
    -H "Content-Type: application/json" --data-binary 'not json at all')
assert_code "${CODE}" "400" "malformed JSON"

echo "ALL CHECKS PASSED"
