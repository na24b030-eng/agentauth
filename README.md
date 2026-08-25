# TrustCart

TrustCart is a merchant-side, UAP-inspired authorization and checkout gateway for AI buyers. A
single OpenAI commerce agent may discover and propose products, while deterministic services own
identity, prices, inventory, grants, money arithmetic, checkout state, Razorpay state and recovery.

This is a Razorpay Buildathon project. It is not NPCI UAP compliance, a bank identity system, or a
real autonomous UPI debit. `RAZORPAY_PAYMENT_LAB` creates real Test Mode Orders;
`DELEGATED_DEBIT_SIMULATOR` is permanently labelled simulation.

## What runs

- `agent-api`: the only service with the OpenAI key and P-256 agent private key.
- `merchant-api`: verifies PoP, owns commerce policy, demo login and Razorpay webhook HMAC.
- `worker`: executes cancel-window work and reconciles ambiguous provider outcomes.
- PostgreSQL 16: durable state, nonce replay protection, strict shared counters and row locks.
- React/TypeScript: buyer experience, consent, Trust Inspector and failure lab.

The same backend image runs each Python service via `TRUSTCART_SERVICE_ROLE`. Razorpay webhooks use
their own raw-body HMAC path and never pass through agent PoP.

## Local setup

Requirements: Python 3.12, Node 22+, Docker with Compose, an OpenAI API key, and optional Razorpay
Test Mode credentials.

```bash
python -m pip install -e ".[dev]"
trustcart init-secrets --output .env
```

Add `TRUSTCART_OPENAI_API_KEY` and optional Razorpay Test Mode values to `.env`, then run:

```bash
docker compose up --build -d postgres migrate
docker compose run --rm agent-api trustcart seed
docker compose up -d merchant-api agent-api worker
npm install
npm run dev
```

Open `http://localhost:3000`. Service health endpoints are at ports 8000 and 8001. The seeded login
is `demo@trustcart.local` / `trustcart-demo`.

Migrations against an empty database:

```bash
alembic upgrade head
```

## Verification

Fast tests contain no SQLite concurrency claims:

```bash
python -m ruff check backend tests
python -m pytest -q
npm run lint
npm run build
```

Run the fixed 40-scenario real-model comparison (40 low + 40 medium calls) only when you intend to
spend API credits:

```bash
python evals/run_agent_evals.py --output evals/report.json
```

The harness uses the real model and production tool definitions against a deterministic, no-money
merchant transport. It records completion, tool count, latency and token use; it refuses to create a
synthetic report when no OpenAI key is present.

The concurrency and provider suites require real infrastructure. Set `TEST_DATABASE_URL` to a
disposable PostgreSQL 16 database and supply Razorpay Test Mode credentials before running marked
tests. A live provider result and a real-model eval report must be produced before claiming the full
acceptance criteria; the repository never fabricates either artifact.

## Security invariants

- Agent signatures bind method, canonical path/query, raw body digest, timestamp, nonce, grant ID
  and immutable grant digest.
- A valid proof nonce is consumed even when subsequent business policy rejects the request.
- Checkout idempotency is an atomic PostgreSQL `INSERT .. ON CONFLICT DO NOTHING RETURNING` with a
  semantic hash that excludes timestamps and tracing metadata.
- The cumulative allowance is a conditional update on `spent + held + amount <= cap`.
- Inventory rows are locked in ascending product-ID order.
- No Razorpay request occurs inside a database transaction.
- Worker observations use a versioned reconciliation token. Webhooks lock the same Checkout row.
- A failed payment attempt does not release an Order-level reservation.
- Settlement and release mutate only `HELD` reservations, so duplicate events have no second effect.
- Audit facts are committed with the transitions they explain and form a per-Checkout hash chain.

## Repository map

- `backend/trustcart/agent_runtime.py` — one bounded Agents SDK agent and structural tool gating.
- `backend/trustcart/pop.py` — P-256 request proof and durable replay defense.
- `backend/trustcart/commerce.py` — quote, idempotency and atomic reservations.
- `backend/trustcart/payments.py` — Razorpay HMAC inbox and convergent webhook transitions.
- `backend/trustcart/worker.py` — execution, expiry and two-transaction reconciliation lease.
- `backend/trustcart/models.py` — PostgreSQL system of record and constraints.
- `docs/TOOL_DECISIONS.md` — why every selected and omitted tool belongs where it does.
- `docs/STATE_MACHINE.md` — lifecycle rules and race ownership.

## Honest v1 exclusions

Single fictional merchant, INR only, demo login, no device attestation/KMS, no real autonomous UPI,
no refunds automation, settlement accounting or fulfillment logistics, and no automatic allowance
restoration after a refund.
