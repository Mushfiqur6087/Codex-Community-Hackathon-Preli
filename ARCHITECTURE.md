# QueueStorm Investigator — Architecture

This document describes the **current** architecture of the service as it
exists in the repository today. It is a faithful map of the code, not a
proposal for changes.

---

## 1. High-level pipeline

A single POST to `/analyze-ticket` flows through six stages:

```
HTTP request
   │
   ▼
1. Pydantic request validation  (app/schemas.py)
   │  strict types, enums, future-timestamp rejection
   ▼
2. Signal extraction             (app/extractors.py)
   │  deterministic regex + scoring → Signals dataclass
   ▼
3. Orchestrator                  (app/reasoning.py::decide)
   │  picks one of three paths (see §3)
   ▼
4. Response draft                (template | template | LLM JSON)
   │
   ▼
5. Rule-based verifier           (app/reasoning.py::_verifier)
   │  forces verdict, txn_id, safety invariants
   ▼
6. Pydantic response validation  (app/schemas.py)
   │
   ▼
HTTP 200 JSON response
```

Each stage is fail-safe: a failure at any stage falls through to a
controlled response (or a 4xx/5xx with no leaked internals).

---

## 2. Modules

```
app/
├── main.py          FastAPI app, HTTP handlers, error handlers
├── schemas.py       Pydantic v2 request/response models, all enums
├── extractors.py    Deterministic signal extraction + transaction scoring
├── reasoning.py     Orchestrator (decide) + rule-path templates + verifier
├── llm_client.py    OpenAI-compatible chat-completions HTTP client
└── prompts.py       System + user prompts, few-shot examples
```

There are no databases, queues, caches, or background workers. State lives
only for the duration of one HTTP request.

### Module responsibilities

| Module        | Owns                                           | Does NOT own                            |
|---------------|------------------------------------------------|------------------------------------------|
| `main.py`     | HTTP entry, error mapping, logging             | Business logic, LLM calls                |
| `schemas.py`  | The shape of request and response              | How fields get their values              |
| `extractors.py` | Turning free text + history into `Signals`   | Deciding case_type / verdict / reply     |
| `reasoning.py`| Choosing a path, drafting, verifying           | HTTP transport, regex extraction         |
| `llm_client.py`| One HTTP call to an LLM endpoint              | Prompt content, what to do with output   |
| `prompts.py`  | Prompt text and few-shot examples              | Calling the LLM, parsing                 |

---

## 3. The three paths

The orchestrator `decide()` in `app/reasoning.py` tries three paths in
order. The first one that produces a non-default response wins.

### Path 1 — Safety short-circuit

**Trigger:** `sig.phishing_request` or `sig.credential_leak` is true.

The extractor flags phishing when the complaint contains phrases like
"asked for my OTP", "account will be blocked", or "claiming to be from"
plus credential words. It flags credential leak when the customer says
they already shared credentials.

**Behavior:** Route directly to `fraud_risk` / `critical` severity /
`human_review_required=True` with a hard-coded safe reply. The LLM is
**never consulted** for these cases — even if it was unavailable or
jailbroken, the safety answer is unchanged.

This path exists because safety penalties (-15 / -10 / -10 points) and
the two-strike disqualification rule make phishing cases too risky to
delegate to a probabilistic LLM.

### Path 2 — Rule path

**Trigger:** The deterministic extractor recognized a clear-cut pattern.

`_rule_path_decide()` is a chain of `if` blocks that match on
`sig.evidence_verdict`, `sig.has_keyword_*` flags, `sig.is_duplicate_pair`,
and `txn_id`. Each branch constructs an `AnalyzeTicketResponse` with
hard-coded `agent_summary`, `recommended_next_action`, and
`customer_reply` templates. Covered cases:

- Phishing / credential leak (also reachable from Path 1)
- Wrong transfer, consistent verdict
- Wrong transfer, inconsistent verdict (established recipient pattern)
- Duplicate payment pair
- Failed payment with balance deduction claim
- Refund request (merchant-policy-dependent)
- Merchant settlement delay
- Agent cash-in issue
- Vague / insufficient-data complaint
- Ambiguous match (multiple plausible transactions)
- Default fallback bucket (`no_clear_pattern`)

Templates compose safe chunks (`_SAFETY_REMINDER_EN`, `_SAFETY_REMINDER_BN`,
clarification heads, etc.). User text is **never** interpolated into
`customer_reply`.

**Rule path is the bottom of the funnel for the LLM:** the orchestrator
checks whether the rule path returned the default bucket. Only that
default bucket falls through to Path 3.

### Path 3 — LLM path

**Trigger:** Rule path returned the default fallback bucket
(`case_type="other"` AND `reason_codes` contains `no_clear_pattern` or
`vague_complaint`).

**Behavior:**

1. Build a JSON payload for the LLM: original complaint, transaction
   history, and the pre-extracted `Signals` object (amounts, phones,
   keywords, top_txn, etc.).
2. Build prompts: a system prompt with the schema, safety rules, and
   three diverse few-shot examples; a user prompt with the per-ticket
   payload.
3. Call `LLMClient.complete()`. Currently implemented as a raw
   `urllib.request.urlopen` POST to `<base_url>/chat/completions` with
   OpenAI-compatible request body. One retry on JSON parse failure with
   a strict "respond ONLY with valid JSON" repair message.
4. If the call fails or returns nothing, fall back to the rule-path
   default response.
5. If the call returns JSON, the draft goes through `_verifier` (see §4)
   and then Pydantic validation. If Pydantic rejects it, fall back to
   the rule-path default.

The LLM is **never asked to re-extract facts**. It only interprets
already-extracted signals and writes narrative text. This eliminates the
largest class of LLM bugs on financial tickets (wrong transaction,
misread amount, phone-vs-OTP confusion).

### Path selection summary

```
                ┌──────────────────────────┐
                │ phishing/credential_leak?│
                └────────────┬─────────────┘
                       yes   │   no
              ┌──────────────┴──────────────┐
              ▼                             ▼
     ┌─────────────────┐         ┌────────────────────┐
     │ Path 1: Safety  │         │ Run rule path      │
     │ (rule template, │         │ (_rule_path_decide)│
     │ no LLM)         │         └─────────┬──────────┘
     └─────────────────┘                   │
                              returned default bucket?
                              ┌────────────┴────────────┐
                            yes                         no
                              ▼                         ▼
                    ┌─────────────────┐        ┌──────────────────┐
                    │ Path 3: LLM     │        │ Path 2: Rule     │
                    │ (if configured) │        │ (use template)   │
                    │ + verifier      │        └──────────────────┘
                    └─────────────────┘
                              │
                  verifier + Pydantic validation
                              │
                          (response)
```

---

## 4. The verifier (`_verifier`)

Any LLM output goes through a deterministic post-processing layer before
Pydantic validation. The verifier applies forced overrides, each one
logged into an `overrides` list (surfaced in server logs):

| Override | Rule |
|----------|------|
| O1 — verdict | Force `evidence_verdict` to match the extractor's deterministic verdict |
| O2 — txn_id | Force `relevant_transaction_id=null` when verdict is `insufficient_data`; otherwise force it to the extractor's `top_txn.transaction_id` |
| O3 — phishing | Force `case_type="phishing_or_social_engineering"` + `department="fraud_risk"` when phishing signals were detected |
| O4 — severity | Clamp to ≥ `high` (or `critical` for credential leak) when phishing |
| O5 — reply scrub | Replace unsafe phrases (`"we will refund you"` → `"any eligible amount will be returned through official channels"`, etc.); append safety reminder if missing |
| O6 — reason_codes | Sanitize to short `snake_case`, dedupe, cap at 6 |
| O7 — clamp | Truncate text fields to 2000 chars, clamp `confidence` to `[0, 1]`, coerce `human_review_required` to bool |

The verifier is also run on rule-path output in some branches for
defensive consistency, even though rule-path output is already trusted.

---

## 5. Signal extraction (`extractors.py`)

The extractor turns the raw complaint + `transaction_history` into a
single `Signals` dataclass. This object is ground truth — every
downstream stage consumes it and never re-reads the raw text.

### What gets extracted

- **Amounts** — regex over English numerals with currency markers, plus
  best-effort Bangla-numeral conversion. De-duped by value with
  tolerance.
- **Phones** — BD/international phone patterns, canonicalized to
  `01XXXXXXXXX` or `+880XXXXXXXXX`.
- **Time hints** — `today` / `yesterday` (English + Bangla), and
  `time_of_day` (e.g., "2pm", "সকাল").
- **Phishing triggers** — list of phrases ("otp", "asked for my pin",
  "account will be blocked", "claiming to be from", …) plus a regex
  that detects "I already shared my OTP/PIN".
- **Keyword flags** — six booleans for the case-type categories
  (`has_keyword_wrong_transfer`, `_failed_payment`, `_refund`,
  `_duplicate`, `_settlement`, `_cash_in`). Each is matched against a
  curated phrase list, including Bangla phrases for cash-in.
- **Transaction ranking** — every transaction in the history gets a
  deterministic score:
  - `+2.0` exact amount match, `+1.0` within 5%, `+0.25` within 20%
  - `+1.5` counterparty phone matches a phone in the complaint
  - `+1.0` transaction type matches an implied case-type
  - `+0.5` pending status (for settlement/cash-in)
  - `+1.0` recent within 24h, `+0.5` within 72h, `-1.0` older than 14d
  - `+1.0` agent counterparty when cash-in implied
- **Top transaction** — highest-scoring transaction, with reasons.
- **Duplicate-pair detection** — two same-amount same-counterparty
  completed transactions within 10 minutes → flag the later one as the
  suspected duplicate.
- **Evidence verdict** — derived deterministically:
  - No money-movement intent and no amounts/phones → `insufficient_data`
  - No top transaction → `insufficient_data`
  - Wrong-transfer claim against a recipient paid 2+ times → `inconsistent`
  - Duplicate pair → `consistent`
  - Multiple plausible matches within 0.5 score and no disambiguator → `insufficient_data` or `inconsistent`
  - Top score ≤ 0 and no amounts → `insufficient_data`
  - Otherwise → `consistent`

### Important invariant

When `evidence_verdict == "insufficient_data"` and not a duplicate pair,
`top_txn` is forcibly cleared. This guarantees the response will emit
`relevant_transaction_id=null` per the spec.

---

## 6. Schemas (`schemas.py`)

Strict Pydantic v2 models. Both request and response use
`ConfigDict(extra="forbid")` so unknown fields are rejected (422).

### Request validation

- `ticket_id` and `complaint` are required, max lengths enforced.
- All enums (`language`, `channel`, `user_type`, transaction `type` /
  `status`) are `Literal` types — invalid values raise 422.
- `amount` must be `>= 0`.
- `timestamp` must not be more than 5 minutes in the future.
- `transaction_history` capped at 50 entries.
- `metadata` capped at 20 keys.

### Response shape

All required fields per the problem statement. `relevant_transaction_id`
is `Optional[str]` (serializes as `null` when None, satisfying the
spec). `confidence` defaults to 0.0 and is clamped to `[0, 1]`.

---

## 7. HTTP layer (`main.py`)

Two endpoints:

- `GET /health` → `{"status": "ok", "llm_enabled": <bool>}`
- `POST /analyze-ticket` → `AnalyzeTicketResponse`

### Error handling

Three exception handlers map errors to controlled bodies:

| Exception                  | Status | Body                              |
|----------------------------|--------|-----------------------------------|
| `RequestValidationError`   | 400 or 422 | `{"detail": ..., "errors": [...]}` — `loc`, `msg`, `type` only, never input values |
| `HTTPException`            | (from raise) | `{"detail": ...}`             |
| Any other `Exception`      | 500    | `{"detail": "internal error"}` — no stack traces, no tokens, no secrets |

A semantic guard at the top of `analyze_ticket` rejects empty
`complaint` with 422 (Pydantic alone would let `"   "` through).

A `try` wrapper at the orchestrator level ensures malformed input or
internal errors never crash the process.

---

## 8. Configuration & environment

`LLMClient` reads environment variables once at boot:

| Variable         | Default                       | Purpose                              |
|------------------|-------------------------------|--------------------------------------|
| `LLM_ENABLED`    | `"1"`                         | Set to `"0"` to force-disable        |
| `LLM_API_KEY`    | (none)                        | Bearer token; empty → disabled       |
| `LLM_BASE_URL`   | `https://api.openai.com/v1`   | Any OpenAI-compatible endpoint       |
| `LLM_MODEL`      | `gpt-4o-mini`                 | Model name passed through            |
| `LLM_TIMEOUT_S`  | `8`                           | Per-call hard timeout (seconds)      |

When `LLM_ENABLED=0` or no key is set, the LLM path is skipped and the
rule-path default is returned for any case the rule path doesn't handle.

`/health` reports `llm_enabled` so operators can confirm at a glance
whether the LLM is configured.

---

## 9. Safety mechanisms (defense in depth)

Safety is enforced at four layers, in order of authority:

1. **Extractor** — Phishing/credential-leak detection is deterministic.
   The safety path runs before any LLM call.
2. **Templates** — Rule-path `customer_reply` strings are hard-coded
   safe English/Bangla. No complaint text is ever interpolated into a
   customer-facing field.
3. **Verifier (O5)** — Even if the LLM produces an unsafe phrase, the
   scrubber rewrites it (`"we will refund you"` → `"any eligible amount
   will be returned through official channels"`). The safety reminder is
   re-appended if missing.
4. **Pydantic** — Length caps on every text field prevent overflow
   attacks. Enum types catch illegal values.

Adversarial complaints are treated as **data**, not instructions. The
extractor never executes complaint text; the LLM prompt explicitly says
to ignore embedded instructions; the verifier forces invariants
regardless of LLM output.

---

## 10. Latency profile

- **Path 1 (safety):** < 10 ms — regex + template lookup
- **Path 2 (rule):** < 20 ms — regex + transaction scoring + template
- **Path 3 (LLM):** bounded by `LLM_TIMEOUT_S` (default 8 s), plus one
  retry on parse failure → worst case ~16 s. Well under the 30 s
  per-request judge timeout.

`/health` returns within milliseconds of process start (no model loads,
no warmup).

---

## 11. What this architecture does NOT do

- No prompt-injection execution. Complaint text is data only.
- No real payment APIs, no PII, no production integrations.
- No GPU, no large local models. The whole service runs in < 100 MB
  with no LLM, or with the openai-compatible client pointed at any
  external provider.
- No persistent state. Every request is independent.
- No frontend / UI. The contract is API-only.

---

## 12. Key design decisions (and where they live)

| Decision | Where | Why |
|----------|-------|-----|
| Three-tier path selection | `reasoning.py::decide` | Safety and evidence reasoning must be deterministic; LLM is for interpretation only |
| LLM gets signals, not raw text | `prompts.py::build_user_prompt` | Eliminates re-extraction bugs |
| Rule-based verifier on LLM output | `reasoning.py::_verifier` | Defense-in-depth against hallucination |
| Hard-coded reply templates | `reasoning.py` top | No string interpolation of user input into customer-facing text |
| Strict Pydantic on both ends | `schemas.py` | Catch malformed input before logic; catch malformed output before HTTP |
| Catch-all 500 handler | `main.py::_unhandled` | Never leak stack traces / tokens / secrets |
