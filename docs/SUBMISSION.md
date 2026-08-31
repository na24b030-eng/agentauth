# AgentAuth — bounded authority for agentic commerce

## Product summary

AgentAuth is a merchant-side authorization and checkout control plane for AI buyers. It lets a user
delegate narrow purchasing authority to one registered software agent while deterministic services retain
control of identity, prices, inventory, spending limits, payment state and recovery.

An AI assistant can understand “order my usual groceries under ₹900 tonight,” but language understanding
does not prove who is calling or whether the caller may spend. AgentAuth closes that gap with agent-bound
cryptographic proof, user-approved mandates, canonical merchant quotes and atomic reservations.

## What the product does

1. A registered commerce agent owns a P-256 private key; the merchant stores only its public JWK and
   fingerprint.
2. The user approves an immutable grant containing the agent fingerprint, merchant, product categories,
   per-order cap, cumulative cap, validity window and automatic-execution permission.
3. A bounded Gemini agent interprets the shopping request and calls factual merchant tools for history,
   catalog, delivery and quoting.
4. The merchant service independently computes the Quote and verifies the grant and signed
   Proof-of-Possession request.
5. PostgreSQL atomically reserves allowance and inventory before execution.
6. The worker executes the selected provider path, reconciles ambiguous outcomes and records every
   money-relevant transition in a hash-linked audit sequence.

## Why this is an agentic system, not an AI wrapper

Gemini is used only where probabilistic judgment is useful: interpreting language, selecting tools,
comparing factual candidates, choosing substitutions, requesting clarification and explaining a proposal.

The model cannot supply or override:

- buyer, agent, merchant or grant identity;
- prices, fees, tax or final totals;
- quote IDs or business idempotency keys;
- allowance or inventory arithmetic;
- checkout and provider state; or
- reconciliation decisions.

Execution is structurally unavailable until trusted run context contains a current merchant Quote bound to
the authenticated user, registered agent and approved grant. Every security property is revalidated by the
merchant API regardless of the model's output.

## Engineering decisions

| Concern | Selected mechanism | Reason |
|---|---|---|
| Natural-language intent | One bounded Gemini tool loop | Language and substitution require judgment; a multi-agent graph adds no trust benefit |
| Caller authenticity | ES256 Proof of Possession | A grant alone is bearer data; the caller must also prove control of the registered key |
| Replay resistance | Timestamped nonce with PostgreSQL uniqueness | Replays remain rejected across API restarts |
| Spending authority | Immutable grant plus conditional database update | Shared cumulative limits require atomic enforcement outside the model |
| Price and availability | Canonical expiring merchant Quote | Exact commerce facts must come from the merchant system of record |
| Concurrency | PostgreSQL row locks, sorted inventory order and idempotency constraints | Prevents overspend, oversell and duplicate checkout effects |
| Durable work | Worker queue and reconciliation leases | External calls cannot safely run inside database transactions |
| Provider ambiguity | Stable receipt and observation-before-apply reconciliation | A lost response is not proof that an external write failed |
| Explainability | Transactional, hash-linked audit events | Every state change is accompanied by a machine reason and human explanation |

## Failure recovery

The primary failure story is a lost payment-provider Order-creation response. The provider may have created
the Order even though the worker never received the response, so a blind retry could create a duplicate
external effect. AgentAuth assigns one stable receipt to the Checkout, claims reconciliation with a random
token and version, queries provider state outside the transaction, then applies the observation only if the
lease is still current. Webhooks and reconciliation compete on the same Checkout lock.

Additional recovery evidence covers duplicate and out-of-order events, failed-payment attempts followed by
capture, stale worker observations, abandoned unpaid Checkouts, nonce replay and model timeout. Failure
defaults to no new money movement; ambiguous provider outcomes remain reserved until reconciled.

## Demonstration modes

**AgentAuth Sandbox** runs the complete grant, proof, Quote, allowance, inventory, cancellation and audit
pipeline with a deterministic provider. It ends as `SIMULATED_SETTLED`, never `PAID`, and moves no money.

The repository also contains an optional Razorpay Test Mode Orders, Standard Checkout, raw-body HMAC
webhook and receipt-reconciliation adapter. No live Razorpay or UPI result is claimed without account-bound
provider credentials.

## Verification evidence

- 40 fixed real-model scenarios evaluated at both low and medium Gemini thinking levels.
- Low thinking selected from measured results: 97.5% completion, zero constraint violations and 100%
  clarification success, with fewer tools, tokens and elapsed time than medium.
- Cryptography, policy, route-boundary and payment suites.
- Real PostgreSQL concurrency tests for allowance, inventory, idempotency and exactly-once transitions.
- Browser coverage for consent, commerce, evidence inspection, recovery controls and responsive layout.
- Reproducible narrated five-minute walkthrough from a passing live browser test.

## Project links

- Product: https://na24b030-eng.github.io/agentauth/
- Five-minute walkthrough: https://na24b030-eng.github.io/agentauth/downloads/agentauth-five-minute-demo.mp4
- Alternate frontend: https://agentauth-orpin.vercel.app
- Source: https://github.com/na24b030-eng/agentauth

The public frontends are explicitly labelled previews when the local Python and PostgreSQL services are not
connected. The complete working MVP runs through Docker with the same frontend source.

## Scope

The demonstration uses fictional buyer, catalog and merchant data, INR and application-level demo identity.
It does not claim NPCI UAP compliance, bank identity, device attestation, biometric consent, production KMS
or autonomous UPI debit. These boundaries are visible in the product and documentation.
