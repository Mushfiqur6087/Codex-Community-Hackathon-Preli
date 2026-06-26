# QueueStorm Investigator

A support-copilot API for the SUST CSE Carnival 2026 — Codex Community
Hackathon (Online Preliminary). The service reads a customer complaint
plus a short transaction history, decides what actually happened, and
returns a structured, schema-valid analysis (case type, evidence verdict,
routing, severity, safe reply) for a support agent.

Positioned as an **internal copilot**, not an autonomous decision maker:
it never requests credentials, never promises refunds/reversals it cannot
authorize, and escalates anything ambiguous for human review.

---

## What it does

`POST /analyze-ticket` runs a six-stage pipeline:

1. **Pydantic validation** of the request (`app/schemas.py`).
2. **Deterministic signal extraction** — amounts, phones, time hints,
   phishing triggers, case-type keyword flags, transaction ranking, and
   an `evidence_verdict` (`app/extractors.py`).
3. **Orchestrator** picks one of three paths (`app/reasoning.py::decide`):
   - **Safety short-circuit** — phishing or credential-leak → `fraud_risk`
     + `critical`, no LLM consulted.
   - **Rule path** — clear-cut wrong-transfer / failed-payment / refund /
     duplicate / settlement / cash-in / vague / ambiguous cases produce
     templated output in <20 ms.
   - **LLM path** — only for ambiguous tickets the rule path can't classify.
     The LLM receives the already-extracted `Signals` (never raw text) and
     outputs JSON narrative.
4. **Rule-based verifier** forces evidence correctness, clamps severity,
   and scrubs unsafe phrases from `customer_reply`, `recommended_next_action`,
   and `agent_summary` (`app/reasoning.py::_verifier`).
5. **Pydantic validation** of the response (`app/schemas.py`).
6. **HTTP response** with controlled error bodies (no stack traces, no
   secrets).

Complaint text is treated as **data** — never executed, never interpolated
into customer-facing fields. Prompt-injection attempts are ignored.

---

## Tech stack

- **Python 3.11**, **FastAPI** 0.115, **Uvicorn** 0.30, **Pydantic v2** 2.9.
- **python-dotenv** for local `.env` loading.
- **No GPU, no local model weights.** Uses an external OpenAI-compatible
  LLM via HTTP (OpenRouter, OpenAI, Anthropic, etc.). The LLM is
  **required** — see Configuration below.
- **No databases, queues, caches, or background workers.** Each request
  is fully independent.

---

## Run locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Required: configure the LLM. The service refuses to start without these.
cp .env.example .env
# Edit .env and set ALL of: LLM_ENABLED=1, LLM_API_KEY, LLM_BASE_URL, LLM_MODEL.
# Without any of them, boot fails with LLMConfigError.

uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Smoke test in a second shell:

```bash
chmod +x scripts/smoke_test.sh
./scripts/smoke_test.sh
```

## Run with Docker

```bash
docker build -t queuestorm-team .
# .env must contain all four required LLM vars (see Configuration).
docker run -p 8000:8000 --env-file .env queuestorm-team
```

The image is `python:3.11-slim` + ~4 Python packages; well under the
500 MB preferred size, binds to `0.0.0.0`, no GPU, no large downloads.
If the env file is missing any required LLM var, the container exits at
boot with `LLMConfigError`.

---

## Endpoints

### `GET /health`

Readiness probe. Body is exactly `{"status":"ok"}` per the problem
statement §4 (operational metadata lives at `/info`).

### `GET /info`  *(operational, not part of the judged contract)*

Returns `{"service", "version", "llm_enabled"}` so operators can confirm
LLM configuration without touching the readiness contract.

### `POST /analyze-ticket`

Accepts a ticket per the request schema, returns the structured analysis.

**Sample request** (from `SUST_Preli_Sample_Cases.json` SAMPLE-01):

```bash
curl -X POST http://localhost:8000/analyze-ticket \
  -H "Content-Type: application/json" \
  -d '{
    "ticket_id": "TKT-001",
    "complaint": "I sent 5000 taka to a wrong number around 2pm today. The number was supposed to be 01712345678 but I think I typed it wrong. The person isn'"'"'t responding to my call. Please help me get my money back.",
    "language": "en",
    "channel": "in_app_chat",
    "user_type": "customer",
    "campaign_context": "boishakh_bonanza_day_1",
    "transaction_history": [
      {"transaction_id":"TXN-9101","timestamp":"2026-04-14T14:08:22Z",
       "type":"transfer","amount":5000,"counterparty":"+8801719876543","status":"completed"}
    ]
  }'
```

**Sample response** (actual output saved in `scripts/sample_output.json`):

```json
{
  "ticket_id": "TKT-001",
  "relevant_transaction_id": "TXN-9101",
  "evidence_verdict": "consistent",
  "case_type": "wrong_transfer",
  "severity": "high",
  "department": "dispute_resolution",
  "agent_summary": "Customer reports sending 5000.0 BDT via TXN-9101 to +8801719876543, which they now believe was the wrong recipient. Recipient is unresponsive.",
  "recommended_next_action": "Verify TXN-9101 details with the customer and initiate the wrong-transfer dispute workflow per policy.",
  "customer_reply": "We have noted your concern about transaction TXN-9101. Please do not share your PIN or OTP with anyone. Our dispute team will review the case and contact you through official support channels.",
  "human_review_required": true,
  "confidence": 0.9,
  "reason_codes": ["wrong_transfer", "transaction_match", "dispute_initiated"]
}
```

### HTTP status codes

| Code | When |
|------|------|
| 200  | Successful analysis |
| 400  | Malformed JSON or missing required fields |
| 422  | Schema-valid but semantically invalid (e.g. empty complaint, bad enum, negative amount) |
| 500  | Internal error (body is `{"detail": "internal error"}` — never leaks traces/secrets) |

---

## AI / model usage

The service is a **hybrid rule + AI** system, in line with the
recommendation in §9 of the Team Instructions Manual.

- **Rules own facts and safety.** Transaction matching, evidence verdict,
  case-type routing, severity, safety classification, and reply templates
  are deterministic. This is the dominant path: it covers every public
  sample in under 1 ms with zero LLM cost.
- **LLM owns narrative interpretation only.** When the rule path falls
  through to the default bucket (ambiguous / unusual / no clear pattern),
  the LLM is asked to write `agent_summary`, `recommended_next_action`,
  and `customer_reply` and pick the softer classifications. It receives
  the pre-extracted `Signals` JSON — never the raw complaint — so it
  cannot misread amounts, phones, or transactions.
- **Verifier forces invariants on all LLM output.** Evidence verdict,
  relevant transaction id, case_type, department, severity for phishing,
  reason-code shape, length caps, and a denylist scrubber (English +
  Bangla) on customer-facing text. The LLM cannot override these.

### MODELS

| Model | Where it runs | Why |
|-------|---------------|-----|
| Rule engine + regex extractors (`app/extractors.py`, `app/reasoning.py`) | In-process, no external call | Deterministic, free, fast (<1 ms typical). Owns evidence + safety. |
| OpenAI-compatible chat-completions model (default `openai/gpt-4o-mini`) | External HTTP via `LLM_BASE_URL` (OpenRouter, OpenAI, Anthropic, etc.) | Used for the ambiguous-ticket narrative path. **Required at boot** — the service refuses to start without it. |

Although the LLM is mandatory at boot, the rule path still handles every
clear-cut case (wrong-transfer, failed-payment, refund, duplicate,
settlement, cash-in, phishing, ambiguous-match) in under 1 ms without
calling the LLM. The LLM is invoked only when the rule path falls through
to the default bucket — i.e. genuinely unusual tickets beyond the public
sample distribution. So while cost per request is typically zero LLM
calls, the LLM **must** be reachable when an ambiguous case arrives.

### Cost reasoning

- Typical request: **0 LLM calls** (rule path).
- LLM path: 1 call, ≤700 max_tokens, ≤5 s timeout, single-shot (no retry).
- At OpenAI `gpt-4o-mini` pricing (~$0.15 / M input tokens, ~$0.60 / M
  output), 10,000 LLM-path requests cost well under $1.
- Free-tier models (e.g. `openai/gpt-oss-120b:free` on OpenRouter) work
  but are slower (8–12 s per call) — fine for testing, not for p95
  latency scoring.

---

## Safety logic

Defended at four layers (in order of authority):

1. **Extractor** — Phishing / credential-leak detection is deterministic
   and runs before any LLM call. Two-tier trigger (hard phrase, or
   credential word + context phrase) plus negation handling so
   "I have not shared my OTP" is reassurance, not a leak.
2. **Templates** — Rule-path replies are hard-coded safe English/Bangla.
   Customer complaint text is **never** interpolated into customer-facing
   fields.
3. **Verifier** — Even if the LLM produces an unsafe phrase, the scrubber
   rewrites it:
   - `we will refund you` → `any eligible amount will be returned through official channels`
   - `your account is unblocked` → `your account access will be reviewed by the appropriate team`
   - `call this number: <phone>` → `contact us only through official support channels`
   - `please share your OTP` → `we never ask for your PIN, OTP, or password`
   - Bangla equivalents (`টাকা ফেরত দেবো`, `আপনার ওটিপি দিন`, etc.)
4. **Pydantic** — Length caps on every text field, enum types catch
   illegal values.

The verifier scrubs all three text fields — `customer_reply`,
`recommended_next_action`, and `agent_summary` — because the spec §8
safety rule checks the first two for unauthorized refund/reversal/unblock
promises (-10 each) and the customer_reply for credential requests (-15)
and third-party-contact instructions (-10).

Adversarial complaints are treated as data. The system prompt tells the
LLM to ignore embedded instructions, but the verifier enforces
correctness regardless of LLM cooperation.

---

## Local testing

```bash
# 10 public sample cases — schema + field-level conformance
python scripts/validate_samples.py

# curl-based smoke test (HTTP codes, error bodies, malformed input)
./scripts/smoke_test.sh
```

Sample output reference: `scripts/sample_output.json` (generated from
SAMPLE-01).

---

## Configuration

All configuration is via environment variables (see `.env.example`).
The LLM is **mandatory** — if any of the four required vars below is
missing or `LLM_ENABLED != "1"`, the service raises `LLMConfigError` at
boot and exits. `/health` would never report ready in that state.

| Variable | Required? | Default | Purpose |
|----------|-----------|---------|---------|
| `LLM_ENABLED` | **yes** | — | Must be exactly `"1"` to start |
| `LLM_API_KEY` | **yes** | — | Bearer token; non-empty, not `"dummy"` |
| `LLM_BASE_URL` | **yes** | — | OpenAI-compatible endpoint (e.g. `https://openrouter.ai/api/v1`) |
| `LLM_MODEL` | **yes** | — | Model slug (e.g. `openai/gpt-4o-mini`) |
| `LLM_TIMEOUT_S` | no | `5` | Per-call hard timeout (single-shot, no retry) |
| `OPENROUTER_APP_NAME` | no | — | Sent as `X-Title` (attribution, harmless elsewhere) |
| `OPENROUTER_APP_URL` | no | — | Sent as `HTTP-Referer` (attribution) |
| `PORT` | no | `8000` | Bind port (informational; uvicorn flag still wins) |

---

## Project layout

```
app/
├── main.py          FastAPI app, HTTP handlers, /health + /info + /analyze-ticket
├── schemas.py       Pydantic v2 request/response models, all enums
├── extractors.py    Deterministic signal extraction + transaction scoring
├── reasoning.py     Orchestrator (decide) + rule-path templates + verifier
├── llm_client.py    OpenAI-compatible chat-completions HTTP client
└── prompts.py       System + user prompts, few-shot examples
scripts/
├── smoke_test.sh         curl-based HTTP smoke test
├── validate_samples.py   drives the 10 public sample cases
└── sample_output.json    one real response from SAMPLE-01
```

See `ARCHITECTURE.md` for the full pipeline diagram and design notes.

---

## Assumptions

- Transaction `amount` is in BDT (per spec §5.2).
- Transaction history is "typically 2–5 entries"; the service accepts any
  length and processes all entries.
- Customer may write in English, Bangla, or mixed Banglish. The reply
  language is chosen by sniffing Bangla Unicode in the complaint, so
  `language="mixed"` and missing-language Bangla complaints still get a
  Bangla reply.
- "Ambiguous evidence" means multiple transactions of similar score with
  no disambiguator (phone, recipient pattern). In that case the service
  returns `evidence_verdict: insufficient_data` + `relevant_transaction_id: null`
  rather than guessing.

## Known limitations

- The denylist scrubber is regex-based. Paraphrased unsafe phrases the
  regex does not cover (English or Bangla) could slip through. The
  rule-path templates are safe by construction; this only matters for
  LLM-output text.
- Bangla keyword coverage in the extractor is best-effort. The common
  case-type keywords are covered, but rare phrasings may fall through
  to the LLM/default path.
- The LLM is consulted only when the rule path can't classify — i.e. for
  genuinely unusual tickets. Latency on those is bounded by
  `LLM_TIMEOUT_S` (default 5 s, single-shot).
- No persistent state. Every request is independent; operator feedback
  is not learned.
- The architecture targets the public sample distribution and the
  problem statement. Hidden test cases may include scenarios not
  covered; the rule path + verifier fallback keeps the service safe
  even when it can't be precise.

---

## Submission

All three paths **require the four mandatory LLM env vars** to be set
(`LLM_ENABLED`, `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL`). Without
them the service exits at boot with `LLMConfigError`.

- **Live URL**: deploy to Render/Railway/Fly/Vercel/Poridhi Lab/AWS, set
  the four LLM env vars in the hosting platform's dashboard, expose
  port 8000. `/health` returns ready within ~1 s of process start.
- **Docker fallback**: `docker build -t queuestorm-team . && docker run -p 8000:8000 --env-file .env queuestorm-team` (`.env` must contain all four LLM vars).
- **Code-only**: clone, `pip install -r requirements.txt`, `cp .env.example .env && # fill in the 4 LLM vars`, `uvicorn app.main:app --host 0.0.0.0 --port 8000`.

Repository contains **no real secrets**. `.env` is gitignored; only
`.env.example` (variable names + placeholders) is committed. Real keys
go in the hosting platform's env vars (for the Live URL path) or in the
submission form's private field (for Docker/code fallback).
