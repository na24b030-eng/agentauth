# Checkout state machine

`CANCEL_WINDOW` is the only state entered by checkout creation. The short database transaction has
already reserved allowance and inventory, but has made no provider call.

```text
CANCEL_WINDOW -> CANCELLED
CANCEL_WINDOW -> ORDER_CREATING
ORDER_CREATING -> PAYMENT_PENDING | SIMULATED_SETTLED | FAILED_TERMINAL | RECONCILING
PAYMENT_PENDING -> PAID | EXPIRING
EXPIRING -> RECONCILING
RECONCILING -> PAID | PAYMENT_PENDING | EXPIRED | RECONCILING
EXPIRED -> LATE_CAPTURE_INCIDENT
```

## Race ownership

Webhook and worker apply transactions both lock the Checkout row. The worker additionally checks its
random reconciliation token and expected version after the external Razorpay observation. If a
webhook won during that network gap, the stale observation has no effect.

`payment.failed` is an attempt fact, not an Order terminal state. Reservations remain held. Only a
capture/paid observation settles them, while expiry requires a provider-confirmed unpaid result after
the grace period. A later capture after release is visible as `LATE_CAPTURE_INCIDENT`; it never
silently consumes unavailable inventory.

## Resource effects

| Transition | Allowance | Inventory |
|---|---|---|
| Checkout created | `held += total` | `reserved += quantity` |
| Paid/simulated settled | `held -= total; spent += total` | `reserved -= quantity; on_hand -= quantity` |
| Cancelled/expired/terminal failure | `held -= total` | `reserved -= quantity` |
| Failed attempt | no change | no change |
| Late capture incident | no automatic change | no automatic fulfillment |

Reservation rows must still be `HELD` for either consume or release. This is the exactly-once ledger
guard under duplicate webhooks, retries and out-of-order delivery.
