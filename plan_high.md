# QueueStorm Investigator — High-Level Implementation Plan

> Goal: Build, deploy, and submit a safe, evidence-driven AI/API service in 4.5 hours.
> Scoring weights: Evidence Reasoning 35 · Safety 20 · API/Schema 15 · Performance 10 · Response Quality 10 · Deployment 5 · Docs 5.
> Rule of thumb: **schema first, reasoning second, safety third, reliability fourth, deploy last**.

---

## Step 1 — Project Skeleton & Health Endpoint
**Problem:** Nothing exists yet. We need a runnable service with the exact two endpoints the judge will call (`GET /health`, `POST /analyze-ticket`) on the correct path/port, bound to `0.0.0.0`, returning valid JSON so the harness can confirm readiness before hidden tests fire.

**Deliverable:** A minimal FastAPI (or Flask) service with `/health` → `{"status":"ok"}` and a stub `/analyze-ticket` that returns 200 with a placeholder JSON body matching the response schema. Runs locally with one command. Verified end-to-end with `curl`.

**Why first:** Without reachable, schema-valid endpoints, every other step is unscoreable. The harness gates everything on `/health`.

---

## Step 2 — Request Validation & Response Schema Lock-in
**Problem:** Hidden tests will send malformed JSON, missing fields, unknown enum values, empty `transaction_history`, and non-English/Banglish text. We need Pydantic models (or equivalent) for request/response so we return controlled `400`/`422`/`500` errors instead of crashing — and a **fixed** set of enum values for `case_type`, `department`, `evidence_verdict`, `severity`, plus typed `relevant_transaction_id`.

**Deliverable:** Strict request validation, typed response model, a small list of fixtures (sample cases from `SUST_Preli_Sample_Cases.json`) that the service handles cleanly. Error responses never leak stack traces or secrets.

**Why second:** Schema validity is 15% of the score. Crashes are zero. Getting types and enums right once prevents drift across every later step.

---

## Step 3 — Evidence Reasoning Engine (the 35-point core)
**Problem:** "The Investigator Twist" — the complaint and the transaction history may disagree. We must read both, pick `relevant_transaction_id`, set `evidence_verdict` (`consistent` / `inconsistent` / `insufficient_data`), infer `case_type`, and route to the right `department` with a sensible `severity` and `human_review_required` flag.

**Deliverable:** A rule-based scoring module that:
- Matches a complaint to 0, 1, or N transactions via signals: amount, counterparty (phone number), type, time-of-day, recency, status.
- Returns `null` + `insufficient_data` when matches are zero or ambiguous (e.g., SAMPLE-06, SAMPLE-08). Never guess.
- Picks exactly one transaction when there's a unique, high-confidence match (e.g., SAMPLE-01 wrong-transfer).
- Flags `phishing_or_social_engineering` patterns (OTP/PIN/password requests) regardless of transaction history → `fraud_risk` / `critical`.
- Sets `human_review_required = true` for disputes, suspicious patterns, high-value cases, ambiguous evidence.

**Why third:** This is the single largest scoring block. A correct investigator beats a fancy LLM wrapper. Pure rules here are faster, safer, and deterministic.

---

## Step 4 — Safety Guardrails & Customer Reply Generation
**Problem:** The `customer_reply` field is auto-scanned for safety violations that cost **−15 / −10 / −10 points** and **disqualify after 2 critical hits**. Hidden adversarial inputs will try to make the reply promise refunds, ask for OTPs, or instruct customers to call suspicious numbers — possibly by injecting instructions *into* the complaint text itself.

**Deliverable:** A post-processing layer that:
- Sanitizes every `customer_reply` against banned patterns ("share your OTP", "we will refund", "call this number", "your PIN is...").
- Forces safe phrasings: "any eligible amount will be returned through official channels", "we never ask for PIN/OTP", "please contact us only through official support channels".
- Ignores prompt-injection attempts embedded in complaint text — system rules win.
- Produces a short, polite, agent-readable `agent_summary` and a concrete `recommended_next_action`.

**Why fourth:** Safety is 20% of the score and the fastest way to *lose* points. Locking this in *after* reasoning but *before* deployment prevents a high-evidence answer from being wiped out by one bad phrase.

---

## Step 5 — Local Test Harness & Sample-Case Conformance
**Problem:** The 10 public cases in `SUST_Preli_Sample_Cases.json` are reference examples, not the full test set, but they're the only feedback loop we have. We need to drive the service with all 10 and check field-by-field conformance against `expected_output` — and we need to *design for the full problem*, not hard-code these 10.

**Deliverable:** A `scripts/run_samples.py` (or similar) that POSTs every sample `input` to `/analyze-ticket`, diffs the response against `expected_output`, and reports conformance per field. We tune rules until all 10 cases are functionally equivalent (same `relevant_transaction_id`, `evidence_verdict`, `case_type`, `department`, comparable `severity`, safe `customer_reply`).

**Why fifth:** This is our pre-deployment test gate. Catching schema or reasoning regressions here is cheap; catching them on a deployed URL is expensive.

---

## Step 6 — Reliability, Latency & Edge-Case Hardening
**Problem:** The judge enforces a **30-second per-request timeout**, **60-second `/health` readiness**, and penalizes crashes / 5xx / repeated slowness. Edge cases include: empty complaint, very long Bangla complaint, transaction history with 0 or 50+ entries, future-dated timestamps, missing optional fields, non-UTF-8 characters.

**Deliverable:**
- Bounded processing time (no unbounded LLM loops; rule engine returns instantly; LLM calls have hard timeouts).
- Try/except wrappers around the whole `/analyze-ticket` handler that return safe 500s.
- Caching of any expensive calls where safe.
- Confirmed stable under rapid-fire requests via a small load script.

**Why sixth:** Performance + Reliability is 10%. A correct but flaky service loses to a slightly-less-correct but bulletproof one. Hidden tests will probe edges.

---

## Step 7 — Deployment & Public URL
**Problem:** Judges call `https://<our-url>/health` and `/analyze-ticket` directly with no login. We need a public HTTPS URL that stays reachable during the evaluation window — Render / Railway / Fly.io / Vercel / AWS EC2 / Poridhi Lab / Puku are all options. As a fallback we need a Dockerfile that binds `0.0.0.0`, responds on the documented port, and stays under 500 MB (hard limit 1 GB).

**Deliverable:**
- Live URL where `/health` returns `{"status":"ok"}` and a smoke test of `/analyze-ticket` from outside our network passes.
- A working `Dockerfile` (image <500 MB, no GPU, secrets via env vars only) that we can fall back to if the live URL misbehaves.
- `.env.example` listing required variable names with placeholder values.

**Why seventh:** Live URL is the *strongly preferred* submission path. We do this after the service is correct locally so we deploy a known-good artifact once.

---

## Step 8 — Documentation, Submission Package & Polish
**Problem:** Shortlisted teams get manual-reviewed on README quality and the submission form. We need a clear, honest README plus all the form fields pre-filled: setup, run command, tech stack, AI/model usage, safety logic, sample request/response, known limitations, MODELS section. No real secrets anywhere in the repo.

**Deliverable:**
- `README.md` covering: problem in one paragraph, architecture, run command, sample `curl` for both endpoints, sample request/response from a public case, AI/model usage explanation, safety guardrail explanation, assumptions and known limitations.
- `MODELS` section listing every model used, where it runs, and why.
- `scripts/sample_output.json` (or similar) with one real response from a public sample case.
- Repo is public (or `bipulhf` added as collaborator), `.gitignore` excludes `.env`, secrets scrubbed from history.

**Why eighth (last):** Docs describe what we built. Writing them last means they're accurate, and the repo is in its final, submittable state.

---

## Out-of-Scope / Explicit Non-Goals

- **No frontend/UI.** Not judged, wastes the 4.5h window.
- **No real payment APIs, no real customer data.** Synthetic only.
- **No GPU, no large local LLMs.** Rule-based + optional small local model + optional external API (our own keys, our own cost).
- **No hard-coding of the 10 public sample outputs.** Hidden tests differ — generalize.
- **No prompt-injection compliance.** The complaint text is *data*, not instructions.

---

## Execution Cadence

1. Send me the step number (e.g., "Step 1").
2. I'll give you a detailed implementation: file layout, code, exact commands, test cases.
3. You run, verify, and confirm.
4. We move to the next step.

This way each step is small enough to actually finish inside the round.
