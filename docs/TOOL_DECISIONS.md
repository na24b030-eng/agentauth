# Tool decisions

The buildathon rewards AI judgment, not maximum AI usage. AgentAuth separates probabilistic
interpretation from deterministic authority.

| Concern | Selected tool | Reason | Explicitly not used |
|---|---|---|---|
| Natural-language goal and substitutions | One Gemini 3.7 Flash agent using the native Google GenAI SDK | The problem is semantic and benefits from tool selection, comparison and clarification. The free tier keeps the buildathon reproducible without billing, while representative evals decide low vs medium thinking. | Rules-only NLU would be brittle; an OpenAI paid model conflicts with the zero-cost constraint, and a multi-agent graph adds latency and failure surfaces without independent roles. |
| Price, tax, fees and totals | Python quote service + PostgreSQL snapshot | Money must be reproducible and independently checkable. | LLM arithmetic. |
| Delegated authority | Immutable grant digest + conditional SQL counter | Strict shared caps need atomic database truth. | Prompt policy, Redis counters or model memory. |
| Caller authenticity | ES256 P-256 PoP + JWK thumbprint | Proves possession of the registered agent key and binds the exact request. | Bearer grant IDs, blockchain and a claimed UAP implementation. |
| Replay defense | PostgreSQL UNLOGGED nonce table + bounded pruning | Survives API restarts and needs no new service; ten-minute records do not require WAL durability. | In-process cache, Redis. |
| Checkout retries | PostgreSQL `ON CONFLICT` + semantic request hash | Correct under concurrent retries and catches accidental key reuse without hashing volatile proof metadata. | `SELECT`-then-`INSERT`. |
| Durable work | PostgreSQL queue rows, `SKIP LOCKED`, worker | The state already lives in PostgreSQL and load is buildathon scale. | FastAPI background tasks, Celery/Redis. |
| Payment | Razorpay Orders + Standard Checkout + HMAC webhooks | Demonstrates a real Test Mode provider lifecycle. | A fake success webhook or a claim of autonomous UPI debit. |
| Ambiguous provider result | Stable receipt + reconciliation lease | Observes before retrying and prevents a stale worker from overwriting webhook truth. | Blind network retry. |
| Product retrieval | Indexed relational search over a seeded catalog | Small, structured and exact inventory dataset. | Vector database or RAG. |
| Inter-service agent call | Small signed HTTP client | Only one agent and one merchant surface; the protocol itself is part of the evidence. | MCP server, message broker. |

The central judgment boundary is enforceable in code: `place_order` has an empty argument schema and
is invisible until trusted runtime context contains an unexpired Quote. Even then, the merchant API
revalidates the Quote, grant and inventory; prompt compliance is never a security boundary.
