# Security review

## Assets and likely attackers

Protected assets are user consent, grant allowance, inventory, the agent private key, demo-auth
signing key, provider credentials, webhook secret and the truth of payment state. Relevant attackers
are a browser script, a caller holding only a grant ID, a replaying network client, a compromised
model/tool output, duplicate or stale provider events, and concurrent retrying clients.

## Controls

| Threat | Control | Verification |
|---|---|---|
| Stolen grant ID | Agent-bound P-256 PoP and stored JWK thumbprint | Request signed by another key fails |
| Replay | Timestamp tolerance + unique `(agent_id, nonce)` persisted before policy checks | Same proof fails after restart and after rejected business operation |
| Request mutation | Method, canonical path/query and raw-body SHA-256 are signed | Changed body/path/query/method fails |
| Model fabricates price/identity | Empty execution schema, trusted RunContext and merchant revalidation | Tool-structure tests; unknown checkout fields rejected |
| Double-click/retry | Scoped idempotency uniqueness + semantic hash + consumed Quote | PostgreSQL concurrency tests |
| Shared cap race | Conditional `held + spent + amount <= cumulative cap` update | Real PostgreSQL concurrent Checkout test |
| Oversold inventory/deadlock | Sorted row locks and reservation status guard | Real PostgreSQL concurrent inventory test |
| Webhook spoofing | Raw-body HMAC, separate route dependency, current/previous secret | HMAC and route-boundary tests |
| Duplicate/out-of-order webhook | Durable inbox uniqueness, Checkout lock, monotonic transition rules | Fixture and payment tests |
| Worker overwrites webhook | Lease token + version + shared Checkout row lock | Stale lease is ignored |
| Abandoned Checkout | Scheduled provider reconciliation, grace, exact-once release | Worker integration path |
| Secret disclosure | Service-scoped environment values; none in browser or PostgreSQL | Configuration health returns booleans only |

## Deliberate limitations

Demo authentication is application consent, not bank identity. Secrets are environment-managed, not
production KMS/HSM-backed. PoP nonces are in an UNLOGGED table: a database crash can lose up to ten
minutes of replay memory, which is an explicit demo trade-off and would need durable or distributed
replay storage for a regulated deployment. Audit hashes are tamper-evident evidence, not immutable
regulatory storage. The autonomous provider is a labelled deterministic simulator; no real UPI debit
is claimed.

## Release gates

- Production environment disables all synthetic failure fixtures.
- Public deployment must use HTTPS and private backend origins with explicit CORS.
- Razorpay secrets must be Test Mode for the buildathon and rotated after recording.
- A real autonomous debit integration requires provider authorization, bank-grade user issuance,
  consent revocation semantics, KMS/HSM keys, incident response and regulatory review beyond v1.
