# Release checklist

## Repository and reproducibility

- [x] Fresh clone installs from README commands.
- [x] Docker images build and health checks pass.
- [x] Alembic upgrades an empty PostgreSQL 16 database.
- [x] Ruff, pytest, npm lint, npm build and Playwright pass.
- [x] GitHub Actions workflow is green.
- [x] `.env`, private PEM material and provider secrets are absent from Git.

## Agent and trust evidence

- [x] Real Gemini 40×low + 40×medium report is committed.
- [x] Selected thinking level is justified from that report.
- [x] Agent fingerprint and grant digest are visible.
- [x] PoP canonical string, nonce result and scope gates are visible.
- [x] Model trace proves price, identity, grant and idempotency values are excluded.
- [x] Browse-only and ambiguous prompts do not execute.

## Payment and recovery

- [x] Autonomous mode is permanently labelled simulated.
- [x] Default execution is explicitly labelled AgentAuth Sandbox.
- [x] Sandbox settlement uses the same grant, Quote, reservation and audit pipeline.
- [x] Optional Razorpay adapter is isolated and never represented as executed without credentials.
- [x] Lost provider-response recovery is covered by real PostgreSQL integration tests.
- [x] Duplicate/out-of-order facts have one ledger effect in integration tests.
- [x] `payment.failed` retains reservations; capture can still settle once in integration tests.
- [x] Late capture after release becomes a visible incident in integration tests.

## Delivery

- [x] Public GitHub Pages frontend matches the tested commit.
- [ ] Start zero-cost HTTPS tunnels immediately before a remote live demonstration, only if remote API access is required.
- [x] Five-minute 1280×720 H.264/AAC MP4 follows `DEMO_RUNBOOK.md` and displays the seeded user as Diksha.
- [x] Failure narrative follows `FAILURE_STORY.md`.
- [x] Final README states every simplification and external gate honestly.
