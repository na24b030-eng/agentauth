# Submission checklist

## Repository and reproducibility

- [x] Fresh clone installs from README commands.
- [x] Docker images build and health checks pass.
- [x] Alembic upgrades an empty PostgreSQL 16 database.
- [x] Ruff, pytest, npm lint, npm build and Playwright pass.
- [ ] GitHub Actions workflow is green.
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
- [ ] Razorpay Payment Lab creates a real Test Mode Order.
- [ ] Real Test payment reaches `PAID` only after capture.
- [ ] Real webhook is stored and HMAC-verified.
- [ ] Lost create response recovers by stable receipt to one provider Order.
- [x] Duplicate/out-of-order facts have one ledger effect in integration tests.
- [x] `payment.failed` retains reservations; capture can still settle once in integration tests.
- [x] Late capture after release becomes a visible incident in integration tests.

## Delivery

- [x] Private hosted preview matches the tested commit.
- [ ] Public backend URLs are configured if live hosted behavior is claimed.
- [x] Five-minute video follows `DEMO_RUNBOOK.md`.
- [x] Failure narrative follows `FAILURE_STORY.md`.
- [x] Final README states every simplification and external gate honestly.
