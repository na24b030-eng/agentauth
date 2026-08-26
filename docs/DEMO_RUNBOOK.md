# Five-minute demonstration runbook

Use seeded data and keep the Trust Inspector open in a second tab. Real Razorpay steps require Test
Mode keys and a webhook endpoint; without them, demonstrate the autonomous simulator and state the
external credential gap plainly.

| Time | Action | Evidence / narration |
|---|---|---|
| 0:00–0:25 | Open Developer Console and registered agent | “This P-256 fingerprint identifies the software agent. Its private key is confined to Agent API.” |
| 0:25–0:55 | Open consent and approve grocery-only grant, ₹900/order, cumulative cap, expiry, auto-execute | “The model cannot widen this mandate. Editing a cap creates a new immutable grant.” |
| 0:55–1:15 | Select **Autonomous demo** and send: “Order my usual groceries under ₹900 for delivery tonight.” | “One Gemini agent interprets intent; the label says it may call five merchant capabilities, not five prompts.” |
| 1:15–1:55 | Expand tool trace | Show history, exact catalog/delivery facts and canonical Quote. Point out that model tool output contains SKUs/quantities but no trusted amount, grant or user ID. |
| 1:55–2:25 | Open Quote and authority view | Show exact server total, category gate, remaining cap, PoP canonical string, signature verification and nonce acceptance. |
| 2:25–2:50 | Let three-second cancellation window finish | “PostgreSQL reserved allowance and inventory atomically before execution. The simulator uses the same policy pipeline and is permanently labelled simulated.” |
| 2:50–3:15 | Show `SIMULATED_SETTLED` ledger effects | Held decreases once, spent and on-hand change once, audit sequence is hash-linked. State explicitly: no UPI transfer occurred. |
| 3:15–3:35 | Switch to **Razorpay Payment Lab** | “This path creates a real Test Mode Order and only says paid after capture. It never calls simulated settlement.” |
| 3:35–4:05 | Arm **Discard next successful Order response**, start a purchase | Show `ORDER_CREATING → RECONCILING`; explain why blindly retrying an ambiguous external write is unsafe. |
| 4:05–4:30 | Show receipt lookup recovery | One stable receipt, one recovered provider Order, one local reservation. Show lease token/version and no duplicate ledger effect. |
| 4:30–4:50 | Complete Standard Checkout Test payment | Show raw-body HMAC webhook inbox, deduplication and webhook-driven `PAID`. Creating an Order was never labelled paid. |
| 4:50–5:00 | End on Trust Inspector | “AI chose products. Cryptography proved the caller. Deterministic services authorized and priced. Durable state recovered the network failure.” |

## Demo safety checks

Before recording:

```powershell
docker compose ps
Invoke-RestMethod http://localhost:8000/health
Invoke-RestMethod http://localhost:8001/health
npm run test:e2e
```

- Confirm the page says **Test environment**.
- Confirm real and simulated mode controls are visually distinct.
- Never reveal `.env`, terminal secrets or Razorpay key values.
- Do not call a Razorpay Order or `payment.failed` a successful payment.
- Reset seeded data before each take.
- Keep the exact failure injection label visible so synthetic fault control is not mistaken for a
  provider event.

## Short fallback when Razorpay is unavailable

Run through 0:00–3:15, show Payment Lab disabled with the explicit Test Mode credential message,
then use the state-machine and Failure Story evidence. Say: “The provider adapter and recovery path
are implemented and tested; this recording does not claim a live provider result without account
credentials.”
