"use client";

import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
  AlertTriangle,
  ArrowRight,
  ArrowUpRight,
  BadgeCheck,
  Bot,
  Check,
  ChevronRight,
  CircleDollarSign,
  Clock3,
  Code2,
  Command,
  FileKey2,
  KeyRound,
  LockKeyhole,
  ReceiptText,
  RotateCcw,
  ScanLine,
  Search,
  ShieldCheck,
  ShoppingCart,
  SlidersHorizontal,
  UserRound,
  WalletCards,
  Wrench,
  X,
} from "lucide-react";

type View = "commerce" | "inspector" | "delegations" | "developer";
type Mode = "DELEGATED_DEBIT_SIMULATOR" | "RAZORPAY_PAYMENT_LAB";
type Connection = "checking" | "preview" | "login" | "ready";
type ApiError = { code?: string; message?: string };
type SessionUser = {
  user_id: string;
  display_name: string;
  access_token: string;
};
type AgentIdentity = {
  id: string;
  name: string;
  jwk_thumbprint: string;
  key_version: number;
  status: string;
};
type Grant = {
  id: string;
  allowed_categories: string[];
  per_order_limit_paise: number;
  cumulative_limit_paise: number;
  held_paise: number;
  spent_paise: number;
  expires_at: string;
  auto_execute: boolean;
  immutable_digest: string;
  status: string;
};
type QuoteItem = {
  sku: string;
  product_name: string;
  category: string;
  quantity: number;
  unit_price_paise: number;
  line_total_paise: number;
};
type Quote = {
  id: string;
  status: string;
  total_paise: number;
  subtotal_paise: number;
  delivery_fee_paise: number;
  tax_paise: number;
  expires_at: string;
  canonical_hash: string;
  remaining_grant_paise: number;
  items: QuoteItem[];
};
type AgentRun = {
  id: string;
  status: string;
  final_response: string | null;
  active_quote_id: string | null;
  checkout_id: string | null;
  tool_call_count: number;
  turn_count: number;
  error_code: string | null;
};
type Checkout = {
  id: string;
  status: string;
  payment_mode: Mode;
  amount_paise: number;
  currency: string;
  receipt: string;
  execute_after: string;
  payment_deadline_at: string;
  version: number;
  razorpay_order_id: string | null;
  test_fixture_applied: boolean;
};
type AuditEvent = {
  id: number;
  sequence: number;
  layer: string;
  action: string;
  reason_code: string;
  explanation: string;
  amount_delta_paise: number;
  event_hash: string;
  previous_hash: string | null;
};
type ToolEvent = {
  sequence: number;
  tool: string;
  status: string;
  summary: string;
};

const envMerchant = process.env.NEXT_PUBLIC_MERCHANT_API_URL ?? "";
const envAgent = process.env.NEXT_PUBLIC_AGENT_API_URL ?? "";
const isLocalBrowser = () =>
  typeof window !== "undefined" &&
  ["localhost", "127.0.0.1"].includes(window.location.hostname);
const merchantBase = () =>
  envMerchant || (isLocalBrowser() ? "http://localhost:8000" : "");
const agentBase = () =>
  envAgent || (isLocalBrowser() ? "http://localhost:8001" : "");

const previewQuote: Quote = {
  id: "preview-quote",
  status: "OPEN",
  subtotal_paise: 34600,
  delivery_fee_paise: 1200,
  tax_paise: 0,
  total_paise: 35800,
  expires_at: new Date(Date.now() + 120_000).toISOString(),
  canonical_hash: "preview-only-not-a-server-hash",
  remaining_grant_paise: 264200,
  items: [
    {
      sku: "MILK-1L",
      product_name: "FarmFresh Toned Milk",
      category: "dairy",
      quantity: 2,
      unit_price_paise: 6400,
      line_total_paise: 12800,
    },
    {
      sku: "BREAD-WW",
      product_name: "Whole Wheat Bread",
      category: "bakery",
      quantity: 1,
      unit_price_paise: 5200,
      line_total_paise: 5200,
    },
    {
      sku: "EGGS-12",
      product_name: "Free Range Eggs · 12",
      category: "breakfast",
      quantity: 1,
      unit_price_paise: 11800,
      line_total_paise: 11800,
    },
    {
      sku: "BANANA-6",
      product_name: "Robusta Bananas · 6",
      category: "produce",
      quantity: 1,
      unit_price_paise: 4800,
      line_total_paise: 4800,
    },
  ],
};

const toolLabels: Record<string, string> = {
  get_usual_basket: "Purchase history",
  search_catalog: "Merchant catalog",
  get_delivery_options: "Delivery options",
  quote_cart: "Canonical quote",
  place_order: "Authorization + reservation",
  request_purchase_approval: "Purchase approval",
};
const promptIdeas = [
  "Restock my breakfast essentials under ₹650",
  "Find a high-protein basket for tomorrow morning",
  "Order my usual groceries under ₹900 for delivery tonight",
];
const terminalCheckoutStates = new Set([
  "PAID",
  "SIMULATED_SETTLED",
  "CANCELLED",
  "EXPIRED",
  "FAILED_TERMINAL",
  "LATE_CAPTURE_INCIDENT",
]);
const money = (paise: number) =>
  new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    maximumFractionDigits: 0,
  }).format(paise / 100);
const shortFingerprint = (value?: string) =>
  value ? `${value.slice(0, 10)}···${value.slice(-8)}` : "Unavailable";

async function requestJson<T>(
  base: string,
  path: string,
  token?: string,
  init?: RequestInit,
): Promise<T> {
  const response = await fetch(`${base}${path}`, {
    ...init,
    headers: {
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...init?.headers,
    },
  });
  const data = (await response.json().catch(() => ({}))) as ApiError & T;
  if (!response.ok)
    throw new Error(
      data.message || data.code || `Request failed (${response.status})`,
    );
  return data;
}

export default function Home() {
  const [connection, setConnection] = useState<Connection>("checking");
  const [sessionUser, setSessionUser] = useState<SessionUser | null>(null);
  const [email, setEmail] = useState("demo@trustcart.local");
  const [passcode, setPasscode] = useState("trustcart-demo");
  const [agent, setAgent] = useState<AgentIdentity | null>(null);
  const [grant, setGrant] = useState<Grant | null>(null);
  const [view, setView] = useState<View>("commerce");
  const [policyOpen, setPolicyOpen] = useState(false);
  const mode: Mode = "DELEGATED_DEBIT_SIMULATOR";
  const [draft, setDraft] = useState(
    "Order my usual groceries under ₹900 for delivery tonight",
  );
  const [submittedMessage, setSubmittedMessage] = useState("");
  const [run, setRun] = useState<AgentRun | null>(null);
  const [toolEvents, setToolEvents] = useState<ToolEvent[]>([]);
  const [quote, setQuote] = useState<Quote | null>(null);
  const [checkout, setCheckout] = useState<Checkout | null>(null);
  const [audit, setAudit] = useState<AuditEvent[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [armedFaults, setArmedFaults] = useState<string[]>([]);
  const [now, setNow] = useState(0);
  const runGeneration = useRef(0);

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    if (!policyOpen) return;
    const previousOverflow = document.body.style.overflow;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setPolicyOpen(false);
    };
    document.body.style.overflow = "hidden";
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.body.style.overflow = previousOverflow;
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [policyOpen]);

  useEffect(() => {
    const merchant = merchantBase();
    const agentApi = agentBase();
    if (!merchant || !agentApi) {
      const previewTimer = window.setTimeout(() => {
        setConnection("preview");
      }, 0);
      return () => window.clearTimeout(previewTimer);
    }
    Promise.all([fetch(`${merchant}/health`), fetch(`${agentApi}/health`)])
      .then((responses) =>
        setConnection(
          responses.every((response) => response.ok) ? "login" : "preview",
        ),
      )
      .catch(() => setConnection("preview"));
  }, []);

  const token = sessionUser?.access_token;
  const displayName =
    sessionUser?.display_name ||
    (connection === "preview" ? "Diksha" : "there");
  const quoteSeconds = quote
    ? now
      ? Math.max(
          0,
          Math.floor((new Date(quote.expires_at).getTime() - now) / 1000),
        )
      : 120
    : 0;
  const allowanceUsed = grant
    ? grant.spent_paise + grant.held_paise
    : quote?.total_paise || 0;
  const allowanceTotal = grant?.cumulative_limit_paise || 300000;
  const allowancePercent = Math.min(
    100,
    (allowanceUsed / allowanceTotal) * 100,
  );
  const traceSteps = useMemo(() => {
    const events = toolEvents.map((event) => ({
      label: toolLabels[event.tool] || event.tool,
      detail: event.summary,
      done: event.status === "SUCCEEDED",
    }));
    if (checkout)
      events.push({
        label: checkout.status.replaceAll("_", " "),
        detail: `Checkout ${checkout.receipt}`,
        done: terminalCheckoutStates.has(checkout.status),
      });
    return events;
  }, [checkout, toolEvents]);

  async function loadIdentityAndGrants(activeToken: string) {
    const [identity, grants] = await Promise.all([
      requestJson<AgentIdentity>(
        merchantBase(),
        "/v1/agents/current",
        activeToken,
      ),
      requestJson<Grant[]>(merchantBase(), "/v1/grants", activeToken),
    ]);
    setAgent(identity);
    const active =
      grants.find(
        (item) =>
          item.status === "ACTIVE" && new Date(item.expires_at) > new Date(),
      ) || null;
    setGrant(active);
    if (!active) setView("delegations");
  }

  async function login(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const result = await requestJson<SessionUser>(
        merchantBase(),
        "/v1/demo/login",
        undefined,
        { method: "POST", body: JSON.stringify({ email, passcode }) },
      );
      setSessionUser(result);
      await loadIdentityAndGrants(result.access_token);
      setConnection("ready");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Login failed");
    } finally {
      setBusy(false);
    }
  }

  async function createGrant() {
    if (!token || !sessionUser) return;
    setBusy(true);
    setError("");
    try {
      const requested = await requestJson<{ id: string }>(
        agentBase(),
        "/v1/grant-requests",
        token,
        {
          method: "POST",
          body: JSON.stringify({
            user_id: sessionUser.user_id,
            allowed_categories: [
              "bakery",
              "breakfast",
              "dairy",
              "produce",
              "staples",
            ],
            per_order_limit_paise: 100000,
            cumulative_limit_paise: 300000,
            expires_at: new Date(Date.now() + 7 * 86400000).toISOString(),
            auto_execute: true,
          }),
        },
      );
      const approved = await requestJson<Grant>(
        merchantBase(),
        `/v1/grant-requests/${requested.id}/approve`,
        token,
        {
          method: "POST",
          body: JSON.stringify({ acknowledge_demo_identity: true }),
        },
      );
      setGrant(approved);
      setView("commerce");
      setNotice("Bounded authority approved for this registered agent key.");
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "Grant approval failed",
      );
    } finally {
      setBusy(false);
    }
  }

  async function revokeGrant() {
    if (!token || !grant) return;
    setBusy(true);
    setError("");
    try {
      const revoked = await requestJson<Grant>(
        merchantBase(),
        `/v1/grants/${grant.id}/revoke`,
        token,
        { method: "POST" },
      );
      setGrant(revoked);
      setNotice(
        "Grant revoked. Existing reservations continue through reconciliation.",
      );
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Revocation failed");
    } finally {
      setBusy(false);
    }
  }

  async function consumeEvents(
    runId: string,
    activeToken: string,
    generation: number,
  ) {
    const response = await fetch(
      `${agentBase()}/v1/agent-runs/${runId}/events`,
      { headers: { Authorization: `Bearer ${activeToken}` } },
    );
    if (!response.ok || !response.body)
      throw new Error("Could not open the agent event stream");
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const chunk = await reader.read();
      if (chunk.done || runGeneration.current !== generation) break;
      buffer += decoder
        .decode(chunk.value, { stream: true })
        .replaceAll("\r\n", "\n");
      let boundary = buffer.indexOf("\n\n");
      while (boundary >= 0) {
        const block = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        const event = block.match(/^event: (.+)$/m)?.[1];
        const raw = block.match(/^data: (.+)$/m)?.[1];
        if (raw) {
          const data = JSON.parse(raw);
          if (event === "tool")
            setToolEvents((current) =>
              current.some((item) => item.sequence === data.sequence)
                ? current
                : [...current, data],
            );
          if (event === "state")
            setRun((current) =>
              current
                ? {
                    ...current,
                    ...data,
                    active_quote_id: data.quote_id ?? current.active_quote_id,
                  }
                : current,
            );
          if (event === "error")
            throw new Error(data.code || "Agent event stream failed");
        }
        boundary = buffer.indexOf("\n\n");
      }
    }
  }

  async function hydrateRun(runId: string, activeToken: string) {
    const latest = await requestJson<AgentRun>(
      agentBase(),
      `/v1/agent-runs/${runId}`,
      activeToken,
    );
    setRun(latest);
    if (latest.active_quote_id)
      setQuote(
        await requestJson<Quote>(
          agentBase(),
          `/v1/agent-runs/${runId}/quote`,
          activeToken,
        ),
      );
    return latest;
  }

  async function pollCheckout(
    runId: string,
    activeToken: string,
    generation: number,
    grantId: string,
  ) {
    for (
      let attempt = 0;
      attempt < 900 && runGeneration.current === generation;
      attempt += 1
    ) {
      try {
        const latest = await requestJson<Checkout>(
          agentBase(),
          `/v1/agent-runs/${runId}/checkout`,
          activeToken,
        );
        setCheckout(latest);
        if (terminalCheckoutStates.has(latest.status)) {
          const [events, refreshedGrant] = await Promise.all([
            requestJson<AuditEvent[]>(
              merchantBase(),
              `/v1/audit-events?checkout_id=${latest.id}`,
              activeToken,
            ),
            requestJson<Grant>(
              merchantBase(),
              `/v1/grants/${grantId}`,
              activeToken,
            ),
          ]);
          setAudit(events);
          setGrant(refreshedGrant);
          return;
        }
      } catch (cause) {
        if (attempt > 8) throw cause;
      }
      await new Promise((resolve) => window.setTimeout(resolve, 1000));
    }
  }

  async function startRun(event?: FormEvent) {
    event?.preventDefault();
    if (!draft.trim()) return;
    if (connection === "preview") {
      const previewMessage = draft.trim();
      setSubmittedMessage(previewMessage);
      setDraft("");
      setBusy(true);
      setToolEvents([]);
      setQuote(null);
      const previewTools = Object.keys(toolLabels).slice(0, 4);
      for (let index = 0; index < previewTools.length; index += 1) {
        await new Promise((resolve) => window.setTimeout(resolve, 240));
        const tool = previewTools[index];
        setToolEvents((current) => [
          ...current,
          {
            sequence: index + 1,
            tool,
            status: "SUCCEEDED",
            summary: "Verified against the preview merchant fixture",
          },
        ]);
      }
      setQuote({
        ...previewQuote,
        expires_at: new Date(Date.now() + 120_000).toISOString(),
      });
      setBusy(false);
      setNotice(
        "Labelled preview complete. Connect the APIs to execute a signed agent run.",
      );
      return;
    }
    if (!token || !grant || grant.status !== "ACTIVE") {
      setView("delegations");
      setError("Approve an active delegation before ordering.");
      return;
    }
    const generation = ++runGeneration.current;
    setBusy(true);
    setError("");
    setNotice("");
    setView("commerce");
    setSubmittedMessage(draft.trim());
    setRun(null);
    setToolEvents([]);
    setQuote(null);
    setCheckout(null);
    setAudit([]);
    try {
      const created = await requestJson<AgentRun>(
        agentBase(),
        "/v1/agent-runs",
        token,
        {
          method: "POST",
          body: JSON.stringify({
            message: draft.trim(),
            payment_mode: mode,
            grant_id: grant.id,
          }),
        },
      );
      setRun(created);
      setDraft("");
      await consumeEvents(created.id, token, generation);
      const terminal = await hydrateRun(created.id, token);
      if (terminal.error_code) throw new Error(terminal.error_code);
      if (terminal.checkout_id)
        await pollCheckout(created.id, token, generation, grant.id);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Agent run failed");
    } finally {
      if (runGeneration.current === generation) setBusy(false);
    }
  }

  async function cancelCurrent() {
    if (!token || !run) return;
    setError("");
    try {
      setRun(
        await requestJson<AgentRun>(
          agentBase(),
          `/v1/agent-runs/${run.id}/cancel`,
          token,
          { method: "POST" },
        ),
      );
      setNotice(
        "Cancellation requested through the same signed agent boundary.",
      );
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Cancellation failed");
    }
  }

  async function armFault(key: "FORCE_MODEL_TIMEOUT") {
    if (!token || connection !== "ready") return;
    setError("");
    try {
      await requestJson(merchantBase(), `/v1/developer/faults/${key}`, token, {
        method: "POST",
        body: JSON.stringify({ armed: true }),
      });
      setArmedFaults((current) =>
        current.includes(key) ? current : [...current, key],
      );
      setNotice(
        "One-shot typed model-timeout fixture armed for the next agent run.",
      );
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "Could not arm failure fixture",
      );
    }
  }

  async function replayNonce() {
    if (!token || !grant || connection !== "ready") return;
    setError("");
    try {
      const result = await requestJson<{
        first_status: number;
        second_status: number;
        second_code: string | null;
        proof_replayed: boolean;
      }>(agentBase(), "/v1/developer/replay-nonce", token, {
        method: "POST",
        body: JSON.stringify({ grant_id: grant.id }),
      });
      if (!result.proof_replayed)
        throw new Error(
          `Unexpected replay result: ${result.first_status} then ${result.second_status}`,
        );
      setNotice(
        `Replay proof passed: first request ${result.first_status}; identical nonce rejected ${result.second_status} ${result.second_code}.`,
      );
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "Nonce replay fixture failed",
      );
    }
  }

  async function runWebhookFixture() {
    if (!token || !checkout || connection !== "ready") return;
    setError("");
    try {
      const result = await requestJson<{
        checkout: Checkout;
        created_events: number;
        duplicate_deduplicated: boolean;
        disclosure: string;
      }>(merchantBase(), "/v1/developer/webhook-fixture", token, {
        method: "POST",
        body: JSON.stringify({ checkout_id: checkout.id }),
      });
      setCheckout(result.checkout);
      setAudit(
        await requestJson<AuditEvent[]>(
          merchantBase(),
          `/v1/audit-events?checkout_id=${checkout.id}`,
          token,
        ),
      );
      setNotice(
        `${result.disclosure} ${result.created_events} unique events; duplicate deduplicated: ${result.duplicate_deduplicated}.`,
      );
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "Webhook fixture failed",
      );
    }
  }

  async function resetDemo() {
    if (!token || connection !== "ready") return;
    setBusy(true);
    setError("");
    try {
      await requestJson(merchantBase(), "/v1/developer/reset-demo", token, {
        method: "POST",
      });
      runGeneration.current += 1;
      setGrant(null);
      setRun(null);
      setToolEvents([]);
      setQuote(null);
      setCheckout(null);
      setAudit([]);
      setSubmittedMessage("");
      setDraft("Order my usual groceries under ₹900 for delivery tonight");
      setArmedFaults([]);
      setView("delegations");
      setNotice(
        "Local fictional state reset. Approve a fresh bounded grant to continue.",
      );
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "Could not reset demo state",
      );
    } finally {
      setBusy(false);
    }
  }

  if (connection === "checking")
    return (
      <main className="boot-screen">
        <div className="brand-mark">
          <ShieldCheck size={18} />
        </div>
        <p>Connecting to authorization services…</p>
      </main>
    );
  if (connection === "login")
    return (
      <main className="login-screen">
        <div className="login-shell">
          <div className="login-context">
            <span className="brand-mark">
              <ShieldCheck size={18} />
            </span>
            <div>
              <b>AgentAuth</b>
              <span>Delegated commerce control plane</span>
            </div>
          </div>
          <div>
            <div className="section-kicker">SECURE TEST WORKSPACE</div>
            <h1>Sign in to review delegated purchases.</h1>
            <p>
              AgentAuth separates an AI buyer’s proposal from the authority and
              payment controls that decide whether it may execute.
            </p>
            <ul>
              <li>
                <ShieldCheck size={15} /> Agent-bound proof of possession
              </li>
              <li>
                <CircleDollarSign size={15} /> Hard spending and category limits
              </li>
              <li>
                <ReceiptText size={15} /> Durable checkout and audit state
              </li>
            </ul>
          </div>
        </div>
        <form className="login-card" onSubmit={login}>
          <div className="login-card-head">
            <LockKeyhole size={19} />
            <div>
              <b>Demo identity</b>
              <span>Application consent only</span>
            </div>
          </div>
          <label>
            Email
            <input
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              type="email"
            />
          </label>
          <label>
            Passcode
            <input
              value={passcode}
              onChange={(e) => setPasscode(e.target.value)}
              type="password"
            />
          </label>
          {error && <div className="error-banner">{error}</div>}
          <button disabled={busy}>
            {busy ? "Verifying…" : "Enter test environment"}
            <ArrowRight size={15} />
          </button>
          <p>This is not bank, biometric, or device identity.</p>
        </form>
      </main>
    );

  const navItems = [
    ["commerce", ShoppingCart, "Commerce"],
    ["inspector", ShieldCheck, "Trust Inspector"],
    ["delegations", FileKey2, "Delegations"],
    ["developer", Wrench, "Developer"],
  ] as const;

  return (
    <main className="app-shell">
      <header className="topbar">
        <div className="topbar-inner">
          <div className="brand-lockup">
            <span className="brand-mark">
              <ShieldCheck size={18} />
            </span>
            <div>
              <strong>AgentAuth</strong>
              <span>Delegated commerce control plane</span>
            </div>
          </div>
          <div className="topbar-meta">
            <div
              className={`environment ${connection === "preview" ? "preview" : ""}`}
            >
              <i />
              {connection === "preview"
                ? "Preview fixture"
                : "Test services online"}
            </div>
            <div className="account" aria-label="Current demo user">
              <span>{displayName.slice(0, 2).toUpperCase()}</span>
              <div>
                <b>{displayName}</b>
                <small>Demo account</small>
              </div>
            </div>
          </div>
        </div>
      </header>
      <nav className="product-nav" aria-label="Primary navigation">
        <div>
          {navItems.map(([key, Icon, label]) => (
            <button
              type="button"
              key={key}
              className={view === key ? "active" : ""}
              aria-current={view === key ? "page" : undefined}
              onClick={() => setView(key)}
            >
              <Icon size={15} />
              {label}
            </button>
          ))}
        </div>
      </nav>
      {(error || notice) && (
        <div className={error ? "global-banner error" : "global-banner"}>
          <span>
            {error ? <AlertTriangle size={16} /> : <BadgeCheck size={16} />}
            {error || notice}
          </span>
          <button
            aria-label="Dismiss notification"
            onClick={() => {
              setError("");
              setNotice("");
            }}
          >
            <X size={15} />
          </button>
        </div>
      )}
      <div className="workspace">
        <section className="conversation">
          {view === "commerce" ? (
            <>
              <div className="page-heading">
                <div className="hero-copy">
                  <div className="section-kicker live-kicker">
                    <span />
                    {connection === "preview"
                      ? "INTERACTIVE PRODUCT PREVIEW"
                      : "LIVE DELEGATED COMMERCE"}
                  </div>
                  <h1>
                    <small>Good evening, {displayName}.</small>
                    Commerce that acts.
                    <em>Authority that holds.</em>
                  </h1>
                  <p>
                    Ask naturally. Your agent finds the basket; AgentAuth proves
                    who called, checks the mandate, and controls every rupee.
                  </p>
                </div>
                <div className="mode-control">
                  <span>
                    <SlidersHorizontal size={13} /> Execution policy
                  </span>
                  <div className="mode-switch sandbox-mode">
                    <button
                      type="button"
                      className="selected"
                      aria-haspopup="dialog"
                      aria-expanded={policyOpen}
                      onClick={() => setPolicyOpen(true)}
                    >
                      <Bot size={14} />
                      <span>AgentAuth Sandbox</span>
                      <ChevronRight className="policy-chevron" size={14} />
                    </button>
                  </div>
                  <small className="mode-help">
                    Deterministic settlement · no real money or personal KYC
                  </small>
                </div>
              </div>
              {policyOpen && (
                <div
                  className="policy-backdrop"
                  role="presentation"
                  onMouseDown={(event) => {
                    if (event.currentTarget === event.target) {
                      setPolicyOpen(false);
                    }
                  }}
                >
                  <section
                    className="policy-dialog"
                    role="dialog"
                    aria-modal="true"
                    aria-labelledby="policy-dialog-title"
                  >
                    <div className="policy-dialog-head">
                      <div className="policy-dialog-icon">
                        <Bot size={19} />
                      </div>
                      <div>
                        <span>Execution policy</span>
                        <h2 id="policy-dialog-title">AgentAuth Sandbox</h2>
                      </div>
                      <button
                        type="button"
                        aria-label="Close execution policy"
                        autoFocus
                        onClick={() => setPolicyOpen(false)}
                      >
                        <X size={17} />
                      </button>
                    </div>
                    <p className="policy-dialog-intro">
                      A deterministic provider simulator behind the same signed
                      grant, quote, allowance, inventory and audit controls used
                      by the optional payment adapter.
                    </p>
                    <div className="policy-facts">
                      <article>
                        <ShieldCheck size={17} />
                        <div>
                          <b>Enforced for real</b>
                          <p>
                            Agent proof, grant scope, exact merchant pricing,
                            PostgreSQL reservations and idempotency.
                          </p>
                        </div>
                      </article>
                      <article>
                        <CircleDollarSign size={17} />
                        <div>
                          <b>Simulated by design</b>
                          <p>
                            Only provider settlement. No bank account, UPI
                            transfer, personal KYC or real money is involved.
                          </p>
                        </div>
                      </article>
                      <article>
                        <ReceiptText size={17} />
                        <div>
                          <b>Explicit outcome</b>
                          <p>
                            Successful runs end as SIMULATED_SETTLED and are
                            never presented as Razorpay PAID.
                          </p>
                        </div>
                      </article>
                    </div>
                    <div className="policy-dialog-actions">
                      <button
                        type="button"
                        className="secondary-action"
                        onClick={() => setPolicyOpen(false)}
                      >
                        Close
                      </button>
                      <button
                        type="button"
                        className="primary-action"
                        onClick={() => {
                          setPolicyOpen(false);
                          setView("inspector");
                        }}
                      >
                        Inspect controls <ArrowRight size={15} />
                      </button>
                    </div>
                  </section>
                </div>
              )}
              <div className="session-card">
                <form className="composer" onSubmit={startRun}>
                  <div className="composer-title">
                    <span>
                      <Command size={14} /> Purchase brief
                    </span>
                    <small>Natural language → deterministic checkout</small>
                  </div>
                  <textarea
                    id="purchase-request"
                    aria-label="Message your commerce agent"
                    placeholder="For example: Order my usual groceries under ₹900 for delivery tonight"
                    value={draft}
                    onChange={(event) => setDraft(event.target.value)}
                    disabled={busy}
                  />
                  <div className="composer-bar">
                    <span title="The model can read history, search catalog, check delivery and request a quote. Checkout appears only after a valid quote and matching grant.">
                      <LockKeyhole size={13} /> Checkout stays locked until all
                      controls pass
                    </span>
                    <button
                      type="submit"
                      aria-label="Send message"
                      disabled={busy}
                    >
                      {busy ? (
                        <Activity size={16} />
                      ) : (
                        <>
                          <span>Run request</span>
                          <ArrowUpRight size={15} />
                        </>
                      )}
                    </button>
                  </div>
                </form>
                {!submittedMessage && !busy && !run && (
                  <div className="quick-starts">
                    <span>Try a brief</span>
                    <div>
                      {promptIdeas.map((idea) => (
                        <button
                          type="button"
                          key={idea}
                          onClick={() => setDraft(idea)}
                        >
                          {idea}
                          <ArrowUpRight size={12} />
                        </button>
                      ))}
                    </div>
                  </div>
                )}
                {!submittedMessage && !busy && !run && (
                  <div className="session-empty">
                    <div>
                      <ShieldCheck size={19} />
                    </div>
                    <section>
                      <b>
                        Four independent controls stand between intent and money
                      </b>
                      <p>
                        {connection === "preview"
                          ? "This is a labelled preview fixture. Run the sample to see the agent and policy engine move together."
                          : "No checkout can execute without a current grant, canonical quote, allowance reservation and inventory reservation."}
                      </p>
                    </section>
                    <span>4 deterministic gates</span>
                  </div>
                )}
                <div className="chat-stream">
                  {submittedMessage && (
                    <div className="request-entry">
                      <div>
                        <UserRound size={15} />
                        <span>Your instruction</span>
                      </div>
                      <p className="user-message">{submittedMessage}</p>
                    </div>
                  )}
                  {(submittedMessage || busy || run) && (
                    <div className="agent-response">
                      <div className="response-heading">
                        <div className="agent-orb">
                          <Search size={15} />
                        </div>
                        <div>
                          <span>Commerce agent</span>
                          <small>
                            {busy
                              ? "Working with merchant facts"
                              : "Proposal complete"}
                          </small>
                        </div>
                      </div>
                      <div className="response-copy">
                        <p>
                          {run?.final_response ||
                            (busy
                              ? "Reading merchant facts. Authority and reservations will be evaluated independently."
                              : connection === "preview"
                                ? "Preview: the usual basket fits the sample grant and delivery constraint."
                                : "Ready.")}
                        </p>
                        <div className="live-tools" aria-live="polite">
                          {traceSteps.map((step, index) => (
                            <span
                              key={`${step.label}-${index}`}
                              className={step.done ? "complete" : "running"}
                            >
                              {step.done ? (
                                <Check size={12} />
                              ) : (
                                <Activity size={12} />
                              )}
                              {step.label}
                            </span>
                          ))}
                        </div>
                        {quote && (
                          <div className="basket-card">
                            <div className="basket-head">
                              <div>
                                <span>
                                  MERCHANT-SIGNED QUOTE{" "}
                                  {connection === "preview" && "· PREVIEW"}
                                </span>
                                <h2>Tonight’s basket</h2>
                              </div>
                              <div className="quote-total">
                                <strong>{money(quote.total_paise)}</strong>
                                <small>
                                  Includes {money(quote.delivery_fee_paise)}{" "}
                                  delivery
                                </small>
                              </div>
                            </div>
                            <div className="basket-items">
                              {quote.items.map((item) => (
                                <div className="basket-row" key={item.sku}>
                                  <span className="item-glyph">
                                    {item.product_name.slice(0, 1)}
                                  </span>
                                  <div className="basket-product">
                                    <div>
                                      <b>{item.product_name}</b>
                                      <span>{item.category}</span>
                                    </div>
                                    <small>
                                      {item.quantity} ×{" "}
                                      {money(item.unit_price_paise)}
                                    </small>
                                  </div>
                                  <strong>
                                    {money(item.line_total_paise)}
                                  </strong>
                                </div>
                              ))}
                            </div>
                            <div className="basket-footer">
                              <span>
                                <Clock3 size={13} />
                                {quoteSeconds
                                  ? `Valid for ${Math.floor(quoteSeconds / 60)}:${String(quoteSeconds % 60).padStart(2, "0")}`
                                  : quote.status}
                              </span>
                              <button onClick={() => setView("inspector")}>
                                Inspect authorization
                                <ChevronRight size={14} />
                              </button>
                            </div>
                          </div>
                        )}
                        {checkout && (
                          <div
                            className={`checkout-card ${checkout.test_fixture_applied ? "fixture" : ""}`}
                          >
                            <div className="checkout-icon">
                              <ReceiptText size={17} />
                            </div>
                            <div>
                              <span>
                                CHECKOUT STATE{" "}
                                {checkout.test_fixture_applied &&
                                  "· DEVELOPER FIXTURE"}
                              </span>
                              <strong>
                                {checkout.status.replaceAll("_", " ")}
                              </strong>
                              <small>
                                {checkout.receipt} · version {checkout.version}
                                {checkout.test_fixture_applied &&
                                  " · not a provider payment"}
                              </small>
                            </div>
                            {checkout.status === "CANCEL_WINDOW" && (
                              <button onClick={cancelCurrent}>
                                Cancel before execution
                              </button>
                            )}
                          </div>
                        )}
                      </div>
                    </div>
                  )}
                </div>
              </div>
            </>
          ) : view === "delegations" ? (
            <div className="feature-view">
              <div className="page-heading compact">
                <div>
                  <div className="section-kicker">USER-ISSUED AUTHORITY</div>
                  <h1>Delegation consent</h1>
                  <p>
                    Review the exact purchasing authority bound to this
                    registered agent key.
                  </p>
                </div>
              </div>
              <div className="consent-card">
                <div className="consent-agent">
                  <span>
                    <KeyRound size={18} />
                  </span>
                  <div>
                    <b>{agent?.name || "AgentAuth Commerce Agent v1"}</b>
                    <code>
                      SHA256 {shortFingerprint(agent?.jwk_thumbprint)}
                    </code>
                  </div>
                  <i>{grant?.status || "NOT APPROVED"}</i>
                </div>
                <dl>
                  <div>
                    <dt>Merchant</dt>
                    <dd>AgentAuth Daily</dd>
                  </div>
                  <div>
                    <dt>Categories</dt>
                    <dd>
                      {grant?.allowed_categories.join(", ") ||
                        "Bakery, breakfast, dairy, produce, staples"}
                    </dd>
                  </div>
                  <div>
                    <dt>Per order</dt>
                    <dd>{money(grant?.per_order_limit_paise || 100000)}</dd>
                  </div>
                  <div>
                    <dt>Cumulative</dt>
                    <dd>{money(grant?.cumulative_limit_paise || 300000)}</dd>
                  </div>
                  <div>
                    <dt>Expires</dt>
                    <dd>
                      {grant
                        ? new Date(grant.expires_at).toLocaleDateString("en-IN")
                        : "7 days after approval"}
                    </dd>
                  </div>
                  <div>
                    <dt>Auto-execute</dt>
                    <dd>
                      {grant?.auto_execute === false ? "Disabled" : "Enabled"}
                    </dd>
                  </div>
                </dl>
                <div className="demo-disclosure">
                  <ShieldCheck size={16} />
                  <div>
                    <b>Application-level demo identity</b>
                    <p>
                      This consent is not bank, biometric, or device identity.
                    </p>
                  </div>
                </div>
                {connection === "preview" ? (
                  <button disabled>Connect backend to approve</button>
                ) : grant?.status === "ACTIVE" ? (
                  <button
                    className="danger-button"
                    disabled={busy}
                    onClick={revokeGrant}
                  >
                    Revoke for new purchases
                  </button>
                ) : (
                  <button disabled={busy} onClick={createGrant}>
                    {busy ? "Approving…" : "Approve bounded authority"}
                    <ArrowRight size={15} />
                  </button>
                )}
              </div>
            </div>
          ) : view === "developer" ? (
            <div className="feature-view">
              <div className="page-heading compact">
                <div>
                  <div className="section-kicker">
                    CONTROLLED FAILURE TESTING
                  </div>
                  <h1>Recovery lab</h1>
                  <p>
                    Reproduce the incidents that usually appear only under
                    retries, timeouts and reordered events.
                  </p>
                </div>
              </div>
              <div className="developer-grid">
                <button disabled>
                  <span className="lab-icon">
                    <Activity size={17} />
                  </span>
                  <div>
                    <span>Provider-response recovery</span>
                    <b>OPTIONAL ADAPTER</b>
                    <small>
                      Receipt lookup is implemented and integration-tested; a
                      provider sandbox is required for a live replay
                    </small>
                  </div>
                  <ChevronRight size={16} />
                </button>
                <button
                  disabled={connection !== "ready" || !grant}
                  onClick={replayNonce}
                >
                  <span className="lab-icon">
                    <RotateCcw size={17} />
                  </span>
                  <div>
                    <span>Replay PoP nonce</span>
                    <b>RUN SIGNED REPLAY</b>
                    <small>
                      First request succeeds; identical proof returns
                      PROOF_REPLAYED
                    </small>
                  </div>
                  <ChevronRight size={16} />
                </button>
                <button
                  disabled={
                    connection !== "ready" ||
                    !checkout?.razorpay_order_id ||
                    checkout.payment_mode !== "RAZORPAY_PAYMENT_LAB"
                  }
                  onClick={runWebhookFixture}
                >
                  <span className="lab-icon">
                    <ReceiptText size={17} />
                  </span>
                  <div>
                    <span>Out-of-order webhook</span>
                    <b>RUN DISCLOSED FIXTURE</b>
                    <small>
                      Capture, stale failure and duplicate delivery produce one
                      ledger effect
                    </small>
                  </div>
                  <ChevronRight size={16} />
                </button>
                <button
                  disabled={connection !== "ready"}
                  onClick={() => armFault("FORCE_MODEL_TIMEOUT")}
                >
                  <span className="lab-icon">
                    <Clock3 size={17} />
                  </span>
                  <div>
                    <span>Model timeout</span>
                    <b>
                      {armedFaults.includes("FORCE_MODEL_TIMEOUT")
                        ? "ARMED ONCE"
                        : "ARM AGENT"}
                    </b>
                    <small>
                      The next run returns a typed recoverable outcome
                    </small>
                  </div>
                  <ChevronRight size={16} />
                </button>
                <button
                  disabled={connection !== "ready" || busy}
                  onClick={resetDemo}
                >
                  <span className="lab-icon">
                    <RotateCcw size={17} />
                  </span>
                  <div>
                    <span>Reset local demo</span>
                    <b>RESET FICTIONAL STATE</b>
                    <small>
                      Available only when Razorpay credentials are absent
                    </small>
                  </div>
                  <ChevronRight size={16} />
                </button>
              </div>
              <div
                className={`config-health ${connection === "preview" ? "warning" : ""}`}
              >
                <i />
                {connection === "preview"
                  ? "Frontend preview only — no backend or provider claim is being made."
                  : "Agent and merchant APIs are reachable. Provider secrets remain server-side."}
              </div>
            </div>
          ) : (
            <div className="feature-view inspector-focus">
              <div className="page-heading compact">
                <div>
                  <div className="section-kicker">TAMPER-EVIDENT EVIDENCE</div>
                  <h1>Trust Inspector</h1>
                  <p>
                    Follow the signed request from agent proof through every
                    money-relevant state transition.
                  </p>
                </div>
              </div>
              <div className="canonical-block">
                <div>
                  <Code2 size={17} />
                  <span>SIGNED CANONICAL REQUEST</span>
                </div>
                <code>
                  TC-POP-V1{`\n`}POST{`\n`}/v1/checkouts{`\n`}raw_body_sha256 ·
                  timestamp · nonce{`\n`}
                  {grant?.immutable_digest || "grant_immutable_digest"}
                </code>
              </div>
              <div className="hash-chain">
                {(audit.length
                  ? audit
                  : [
                      { sequence: 1, action: "quote.preview" },
                      { sequence: 2, action: "checkout.reserved" },
                    ]
                ).map((item, index) => (
                  <div key={`${item.sequence}-${item.action}`}>
                    <span>
                      {String(item.sequence).padStart(2, "0")} {item.action}
                    </span>
                    {index < (audit.length ? audit.length : 2) - 1 && <i />}
                  </div>
                ))}
              </div>
              {audit.length > 0 && (
                <div className="audit-list">
                  {audit.map((event) => (
                    <div key={event.id}>
                      <span>{String(event.sequence).padStart(2, "0")}</span>
                      <section>
                        <b>{event.reason_code}</b>
                        <p>{event.explanation}</p>
                        <code>{event.event_hash.slice(0, 18)}…</code>
                      </section>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </section>
        <aside className="trust-panel">
          <div className="authority-card-glow" />
          <div className="trust-head">
            <div>
              <span className="pulse">
                <WalletCards size={18} />
              </span>
              <div>
                <h2>Delegation passport</h2>
                <p>Live authority for this purchase</p>
              </div>
            </div>
            <span
              className={`status-badge ${grant?.status === "ACTIVE" ? "active" : ""}`}
            >
              {grant?.status ||
                (connection === "preview" ? "PREVIEW" : "MISSING")}
            </span>
          </div>
          <div className="trust-section">
            <div className="trust-label">
              REGISTERED AGENT{" "}
              <span>{agent ? "KEY VERIFIED" : "DEMO KEY"}</span>
            </div>
            <div className="fingerprint">
              <div className="fingerprint-icon">
                <KeyRound size={16} />
              </div>
              <div>
                <b>{agent?.name || "AgentAuth Buyer v1"}</b>
                <code>{shortFingerprint(agent?.jwk_thumbprint)}</code>
              </div>
            </div>
            <div className="allowance">
              <div
                className="allowance-dial"
                style={{
                  background: `conic-gradient(var(--signal) ${allowancePercent}%, rgba(255,255,255,.12) ${allowancePercent}% 100%)`,
                }}
              >
                <div>
                  <strong>{Math.round(allowancePercent)}%</strong>
                  <span>used</span>
                </div>
              </div>
              <div className="allowance-copy">
                <span>Available authority</span>
                <strong>
                  {money(Math.max(0, allowanceTotal - allowanceUsed))}
                </strong>
                <small>
                  {money(allowanceUsed)} of {money(allowanceTotal)} committed
                </small>
              </div>
            </div>
          </div>
          <div className="trust-section">
            <div className="trust-label">
              POLICY CHECKS{" "}
              <span className="trace-id">
                {run ? `RUN ${run.id.slice(0, 8)}` : "WAITING"}
              </span>
            </div>
            <div className="decision-list">
              {traceSteps.length ? (
                traceSteps.map((step, index) => (
                  <div className="decision" key={`${step.label}-${index}`}>
                    <span className={step.done ? "check" : ""}>
                      {step.done ? <Check size={12} /> : <Activity size={12} />}
                    </span>
                    <div>
                      <b>{step.label}</b>
                      <small>{step.detail}</small>
                    </div>
                  </div>
                ))
              ) : (
                <>
                  <div className="decision">
                    <span className="check">
                      <Check size={12} />
                    </span>
                    <div>
                      <b>Agent key</b>
                      <small>Proof required on protected requests</small>
                    </div>
                  </div>
                  <div className="decision">
                    <span className="check">
                      <Check size={12} />
                    </span>
                    <div>
                      <b>Grant scope</b>
                      <small>Category and cumulative caps enforced</small>
                    </div>
                  </div>
                  <div className="decision">
                    <span>
                      <Clock3 size={12} />
                    </span>
                    <div>
                      <b>Quote and reservation</b>
                      <small>Evaluated when a request is submitted</small>
                    </div>
                  </div>
                </>
              )}
            </div>
          </div>
          <div className="policy-note">
            <div>
              <ScanLine size={15} />
              <span>Hard model boundary</span>
            </div>
            <p>
              Gemini may select SKUs and quantities. The merchant service owns
              prices, identity, inventory, authority and payment state.
            </p>
          </div>
          <button className="audit-button" onClick={() => setView("inspector")}>
            Inspect the signed evidence
            <ArrowUpRight size={14} />
          </button>
        </aside>
      </div>
    </main>
  );
}
