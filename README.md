# AgentAuth

AgentAuth is an authorization and checkout control plane for AI-driven commerce. It lets a user
delegate narrowly scoped purchasing authority to a registered software agent while keeping identity,
pricing, inventory, spending limits, payment state and recovery outside the language model.

The application combines a tool-using Gemini commerce agent with deterministic merchant services,
PostgreSQL-backed state and cryptographic proof of possession. The included payment sandbox executes
the complete authorization and reservation lifecycle without moving real money. A Razorpay Test Mode
adapter is available as an optional provider integration.

## Live interfaces

- [AgentAuth on GitHub Pages](https://na24b030-eng.github.io/agentauth/)
- [Five-minute product walkthrough](https://na24b030-eng.github.io/agentauth/downloads/agentauth-five-minute-demo.mp4)
- [Alternate Vercel frontend](https://agentauth-orpin.vercel.app/)

The deployments serve the same frontend. Live agent execution requires the local PostgreSQL and
Python services described below; without those services the interface remains in its explicitly
labelled preview state.

## Core guarantees

- Every protected agent request is signed with a registered P-256 key.
- Grants bind a user, merchant, agent key, category scope, time window and spending limits.
- The model cannot supply trusted identity, prices, grant IDs, totals or idempotency keys.
- Quotes are immutable, server-calculated and expire after two minutes.
- Allowance and inventory are reserved atomically in PostgreSQL.
- Replayed proofs and duplicate checkout requests are rejected or resolved idempotently.
- Payment and reconciliation work happens outside database transactions.
- Money-relevant transitions append hash-linked audit events in the same transaction.

## Architecture

```text
React buyer interface
        │
        ├── Agent API ── Gemini tool orchestration + agent private key
        │       │
        │       └── ES256 proof-of-possession
        │
        └── Merchant API ── identity, grants, catalog, quotes and checkout policy
                    │
               PostgreSQL 16
                    │
              Worker service
                    │
          deterministic payment sandbox
          optional Razorpay Test adapter
```

The system uses one bounded commerce agent. Gemini interprets natural-language intent, selects
merchant tools, compares factual candidates and explains the result. Deterministic code owns all
authorization and financial decisions.

## Services

- `agent-api` — runs the Gemini tool loop and signs merchant requests with the agent key.
- `merchant-api` — authenticates users, verifies proofs, manages grants and owns commerce policy.
- `worker` — executes delayed work and reconciles ambiguous provider outcomes.
- PostgreSQL 16 — stores durable state, replay nonces, reservations and audit events.
- React/TypeScript frontend — provides commerce, consent, trust inspection and failure controls.

The agent service has no payment-provider credentials. The merchant and worker services have no
Gemini key or agent private key. Webhooks use their own raw-body HMAC authentication path and never
pass through agent proof-of-possession middleware.

## Local setup

Requirements:

- Docker Desktop with Compose
- Node.js 22 or newer
- Python 3.12 or newer
- A Gemini API key from [Google AI Studio](https://aistudio.google.com/app/apikey)

Create the local secret file and install dependencies:

```bash
python -m pip install -e ".[dev]"
trustcart init-secrets --output .env
npm install
```

Set `TRUSTCART_GEMINI_API_KEY` in `.env`. Do not commit `.env`, private PEM material or provider
credentials.

Start and seed the system:

```bash
docker compose up --build -d postgres migrate
docker compose run --rm agent-api trustcart seed
docker compose up -d merchant-api agent-api worker
npm run dev
```

Open `http://localhost:3000`. The seeded demo login is:

```text
Email: demo@trustcart.local
Passcode: trustcart-demo
```

Service health endpoints are available at `http://localhost:8000/health` and
`http://localhost:8001/health`.

## Deployment

The repository has two independent deployment targets:

- GitHub Pages publishes the static frontend from `.github/workflows/pages.yml` at the repository
  Pages URL and includes the downloadable MP4 walkthrough.
- The frontend can be deployed to Vercel from the repository root. `vercel.json` selects the
  Next.js framework and runs `npm run build:vercel`, which produces Vercel's `.next` output.
- `merchant-api`, `agent-api`, `worker` and PostgreSQL must run on a container host or local Docker
  environment. The worker is a durable polling process and is intentionally not implemented as a
  Vercel Function.

Set these public frontend variables in the frontend host when it should use a remote backend:

```text
NEXT_PUBLIC_MERCHANT_API_URL=https://merchant-api.example.com
NEXT_PUBLIC_AGENT_API_URL=https://agent-api.example.com
```

If they are absent, the interface remains usable in its explicitly labelled preview state. Secrets
such as the Gemini key, signing keys and database URL belong only in the relevant backend service;
they must never be configured as `NEXT_PUBLIC_*` values.

## Configuration

The main environment variables are documented in `.env.example`:

- `TRUSTCART_GEMINI_API_KEY`
- `TRUSTCART_AGENT_PRIVATE_KEY_PEM`
- `TRUSTCART_DEMO_AUTH_PRIVATE_KEY_PEM`
- `TRUSTCART_DEMO_AUTH_PUBLIC_KEY_PEM`
- `TRUSTCART_DATABASE_URL`
- `TRUSTCART_FRONTEND_ORIGIN`
- `NEXT_PUBLIC_MERCHANT_API_URL`
- `NEXT_PUBLIC_AGENT_API_URL`

Razorpay Test Mode values are optional. When they are absent, AgentAuth uses only the clearly labelled
deterministic sandbox and never represents a sandbox settlement as a provider payment.

## Verification

```bash
python -m ruff check backend tests evals alembic
python -m pytest -q
npm audit --audit-level=low
npm run lint
npm run build
npm run test:e2e
```

Concurrency tests require a disposable PostgreSQL database:

```bash
TEST_DATABASE_URL=postgresql+psycopg://trustcart:trustcart@localhost:5432/trustcart_test \
TRUSTCART_TEST_DATABASE_IS_DISPOSABLE=yes \
python -m pytest -q
```

The fixed real-model evaluation compares low and medium Gemini reasoning across 40 scenarios per
configuration. The committed report records completion, constraint violations, clarification quality,
tool count, latency and token use in `evals/report.json`.

The published walkthrough can be regenerated from the real browser tour with `npm run demo:record`.
That command uses Playwright at 1280×720, the free `edge-tts` package through `uv`, and FFmpeg to
produce an exactly five-minute H.264/AAC MP4 with normalized narration and burned-in captions. The
final 1280×756 frame preserves the complete 1280×720 recording and reserves a 36-pixel bottom strip
for small subtitles, so captions do not cover the interface. It does not require a paid text-to-speech
API key. Run `npm run demo:subtitles` only when reapplying captions to a clean narrated recording.

## Failure handling

AgentAuth treats ambiguous external writes as reconciliation problems rather than retry opportunities.
A stable receipt identifies the semantic operation, workers claim reconciliation with a versioned
lease, and webhook transitions compete on the same checkout row lock. Duplicate and out-of-order
events converge without applying a second ledger effect.

The developer console also exposes safe failure exercises for model timeout and proof replay. A model
failure creates no quote, checkout or reservation; an identical proof nonce is rejected even after an
API restart.

## Repository map

- `backend/trustcart/agent_runtime.py` — bounded Gemini loop and structural tool gating.
- `backend/trustcart/pop.py` — canonical ES256 proofs and durable replay protection.
- `backend/trustcart/commerce.py` — quotes, idempotency and atomic reservations.
- `backend/trustcart/worker.py` — execution, leases, expiry and reconciliation.
- `backend/trustcart/payments.py` — optional Razorpay adapter and HMAC webhook inbox.
- `app/` — buyer interface, consent, Trust Inspector and developer console.
- `alembic/` — PostgreSQL schema migrations.
- `tests/` — cryptography, policy, concurrency, payment and agent-structure tests.
- `evals/` — fixed real-model evaluation harness and report.
- `docs/` — architecture, state-machine, security and tool-decision records.
- `docs/SUBMISSION.md` — concise product, engineering and verification overview.

## Scope

AgentAuth uses fictional catalog and buyer data, supports INR and models a single merchant. Demo login
is application authentication, not bank-grade identity. The project does not claim NPCI UAP compliance,
device attestation, biometric authorization or autonomous UPI debit.
