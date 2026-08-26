# What broke at 2 AM

## Incident

The worker sent `POST /orders` to Razorpay. Razorpay created the Test Mode Order, but the connection
dropped before AgentAuth received the successful response. The local Checkout remained
`ORDER_CREATING`, and a blind retry could have created a second provider Order for the same basket.

This is deliberately injectable from the Developer Console as **Discard next successful Order
response**. It models an ambiguous network outcome, not a provider rejection.

## Why the obvious retry is unsafe

An HTTP timeout does not mean the provider did nothing. Retrying Order creation immediately would
create two independent external side effects even though PostgreSQL still contains one allowance
reservation and one inventory reservation. A local idempotency key cannot by itself deduplicate an
external provider that did not receive that key as its own uniqueness constraint.

## Recovery design

1. Checkout reservation commits before any provider request.
2. Every Checkout has a stable receipt, `tc_` plus the Checkout UUID hex, below Razorpay's limit.
3. The lost response moves the Checkout to `RECONCILING`; it is not labelled failed or paid.
4. A worker claim transaction writes a random reconciliation token and increments the Checkout
   version, then commits.
5. Outside the database transaction, the worker searches Razorpay Orders by the stable receipt.
6. The apply transaction locks the Checkout and accepts the observation only when token and version
   are still current and no webhook has already won.
7. The discovered Order is attached to the existing Checkout. No second allowance or inventory
   reservation is created.
8. Later capture is applied once by webhook or reconciliation; duplicate/out-of-order facts are
   audited and have no second ledger effect.

```text
ORDER_CREATING
  └─ create response lost
       └─ RECONCILING
            ├─ receipt finds one Order ─→ PAYMENT_PENDING
            ├─ capture already visible ─→ PAID
            └─ no trustworthy answer ──→ RECONCILING with backoff
```

## Race that was explicitly patched

A worker can observe “unpaid” while a capture webhook is in flight. Network observation therefore
never directly mutates state. Webhook and worker apply transactions compete for the same Checkout
row lock. If the webhook wins, its version change invalidates the worker lease. If expiry wins and a
capture arrives later, AgentAuth enters `LATE_CAPTURE_INCIDENT` and demands compensation/manual
review rather than silently fulfilling inventory that was already released.

## Evidence to show a judge

- Checkout timeline: `ORDER_CREATING → RECONCILING → PAYMENT_PENDING`.
- One stable receipt and one Razorpay Order ID.
- One `HELD` allowance reservation and one inventory reservation throughout recovery.
- Reconciliation token/version in Trust Inspector.
- Hash-linked audit facts for claim, provider observation and apply.
- Duplicate webhook fixture has no second monetary effect.

This is the failure-recovery story because it is a real distributed-systems ambiguity, visible in
the product, and directly relevant to agent-initiated payments.
