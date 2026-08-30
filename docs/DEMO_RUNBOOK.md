# Five-minute demonstration runbook

Use seeded fictional data and keep the Trust Inspector open in a second tab. The primary walkthrough
uses the deterministic AgentAuth Sandbox: no PAN, provider credentials, real money or UPI transfer.

| Time | Action | Evidence / narration |
|---|---|---|
| 0:00–0:25 | Open Developer Console and registered agent | “This P-256 fingerprint identifies the software agent. Its private key is confined to Agent API.” |
| 0:25–0:55 | Open consent and approve grocery-only grant, ₹900/order, cumulative cap, expiry, auto-execute | “The model cannot widen this mandate. Editing a cap creates a new immutable grant.” |
| 0:55–1:15 | Send: “Order my usual groceries under ₹900 for delivery tonight.” | “One Gemini agent interprets intent and chooses merchant tools; deterministic services own every trusted fact.” |
| 1:15–1:55 | Expand tool trace | Show history, exact catalog/delivery facts and canonical Quote. Point out that model tool output contains SKUs/quantities but no trusted amount, grant or user ID. |
| 1:55–2:25 | Open Quote and authority view | Show exact server total, category gate, remaining cap, PoP canonical string, signature verification and nonce acceptance. |
| 2:25–2:50 | Let three-second cancellation window finish | “PostgreSQL reserved allowance and inventory atomically before execution. The simulator uses the same policy pipeline and is permanently labelled simulated.” |
| 2:50–3:15 | Show `SIMULATED_SETTLED` ledger effects | Held decreases once, spent and on-hand change once, audit sequence is hash-linked. State explicitly: no UPI transfer occurred. |
| 3:15–3:35 | In Developer Console, arm **Force model timeout** and submit a purchase | Show a typed model failure with no Quote, Checkout, allowance hold or inventory mutation. |
| 3:35–4:05 | Retry the same request normally | A fresh run succeeds; the previous failure cannot leak authority or create a duplicate purchase. |
| 4:05–4:30 | Run **Replay PoP nonce** | The first signed request is accepted and the identical proof is rejected as `PROOF_REPLAYED`. |
| 4:30–5:00 | End on Trust Inspector and test evidence | “AI chose products. Cryptography proved the caller. Deterministic services authorized, priced and settled. Failures defaulted to no money movement.” |

## Demo safety checks

Before recording:

```powershell
docker compose ps
Invoke-RestMethod http://localhost:8000/health
Invoke-RestMethod http://localhost:8001/health
npm run test:e2e
```

- Confirm the page says **Test environment**.
- Confirm the execution policy says **AgentAuth Sandbox** and **no real money or personal KYC**.
- Never reveal `.env`, terminal secrets or Razorpay key values.
- Do not call a Razorpay Order or `payment.failed` a successful payment.
- Reset seeded data before each take.
- Keep the exact failure injection label visible so synthetic fault control is not mistaken for a
  provider event.

## Optional provider-adapter disclosure

The repository retains the Razorpay Orders, HMAC webhook and receipt-reconciliation adapter behind
optional configuration. Do not demonstrate or claim a live provider result without an organizer-
supplied sandbox. The public product intentionally exposes only the self-contained AgentAuth Sandbox.
