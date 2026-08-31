# Verification evidence

This file is the evidence index, not a substitute for running the repository. It deliberately
separates locally verified facts from external-provider acceptance gates.

## Implemented

- One bounded Gemini commerce agent with structural tool availability.
- Demo user consent, versioned registered agent keys and immutable scoped grants.
- P-256 PoP canonicalization, raw-body binding and persistent replay prevention.
- Canonical expiring Quotes; database-owned price, fees and inventory facts.
- Atomic PostgreSQL idempotency, allowance and inventory reservations.
- Real Razorpay Test Mode adapter, Standard Checkout UI path and raw-body HMAC webhook inbox.
- Labelled delegated-debit simulator using the same authorization/reservation pipeline.
- Worker execution, abandoned Checkout sweeps and token/version reconciliation leases.
- Lost-response, model-timeout, nonce-replay and out-of-order-webhook fault controls.
- Trust Inspector, consent UI, countdown, payment timeline and configuration health.
- CI for Python/PostgreSQL and Node/browser checks.
- Narrated five-minute browser demonstration generated from a passing live Playwright tour, with
  short burned-in subtitles placed below the full recording in a dedicated caption-safe strip.

## Reproducible commands

```powershell
uv run --extra dev ruff check backend tests evals
uv run --extra dev pytest -q
$env:TEST_DATABASE_URL='postgresql+psycopg://trustcart:trustcart@localhost:5432/trustcart_test'
$env:TRUSTCART_TEST_DATABASE_IS_DISPOSABLE='yes'
uv run --extra dev pytest -q
npm audit --audit-level=low
npm run lint
npm run build
npm run test:e2e
npm run demo:record
```

The real-model report is generated only by actual Gemini calls:

```powershell
uv run --extra dev python evals/run_agent_evals.py --output evals/report.json
```

The harness checkpoints after every scenario, retries typed transient provider failures, and refuses
to fabricate results without a key. `evals/report.json` compares the same 40 fixed scenarios at low
and medium thinking, including completion, constraint behavior, clarification, tool count, latency
and token use.

### Preserved real-model result (2026-08-26)

| Gemini thinking | Passed | Clarification success | Constraint violations | Avg. tools | Avg. elapsed* | Tokens |
|---|---:|---:|---:|---:|---:|---:|
| Low | 39/40 (97.5%) | 8/8 (100%) | 0/40 | 2.05 | 10.23 s | 134,275 |
| Medium | 37/40 (92.5%) | 8/8 (100%) | 0/40 | 2.375 | 11.82 s | 175,680 |

Low is therefore the configured default: it completed more scenarios with 23.6% fewer tokens,
fewer tools and lower elapsed time. Its one miss was conservative—the model produced a valid Quote
but asked for confirmation instead of automatically executing. Medium had one conservative proposal,
one eight-turn limit and one provider-unavailable result after bounded retries.

\*Elapsed time includes evaluator retry/backoff after free-tier provider/network failures. Every
individual agent attempt still enforces its own 20-second wall-clock budget.

The report was regraded once from its preserved outputs after the evaluator correctly learned that
“please share/specify” is a clarification even without a question mark, and that a quoted answer which
explicitly says no order was placed is still browse-only. Regrading made no Gemini call and changed no
model output.

## Evidence map

| Quality dimension | Product evidence | Automated evidence |
|---|---|---|
| Problem taste | Agent-bound authority closes the last-mile gap between an AI recommendation and a trusted checkout | Architecture and threat review |
| Build quality | Short transactions, durable queue, state machine, exact-once reservations, typed errors | PostgreSQL, route, crypto, payment and browser suites |
| AI judgment | Model only interprets/selects; deterministic tools own exact facts; omitted infrastructure is explained | `TOOL_DECISIONS.md`, structural tool tests, real-model eval |
| Failure recovery | Lost provider response, stale worker/webhook race, duplicate/out-of-order events | Failure Lab, state machine, integration tests and Trust Inspector |

## External gates

These cannot be truthfully completed without the account-bound values named below:

| Gate | Required external input | Current behavior without it |
|---|---|---|
| Optional live Razorpay Test Order and capture | Organizer-provided Test Mode key ID + secret | Public Site exposes only the clearly labelled AgentAuth Sandbox |
| Authentic Razorpay webhook delivery | Organizer-provided webhook URL + secret | HMAC inbox and convergence remain integration-tested, not claimed live |
| Public Python/PostgreSQL backend | Local Docker plus temporary HTTPS tunnels started for a live session | Site remains an honest interactive preview when no demo tunnel is active |

The backend can be exposed from local Docker through temporary free HTTPS tunnels for a scheduled live
session. No always-on tunnel or uptime guarantee is claimed. In its normal public state, the hosted frontend
is explicitly labelled as a preview; the complete working system runs locally. Razorpay configuration health
remains false because personal KYC was intentionally avoided.

No source code change can manufacture those account credentials or turn a synthetic event into live
provider evidence. Once credentials are available, the same adapter, worker and UI paths are used;
no demo-only “success” branch is required.
