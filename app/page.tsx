'use client';

import { FormEvent, useEffect, useMemo, useRef, useState } from 'react';

type View = 'commerce' | 'inspector' | 'delegations' | 'developer';
type Mode = 'DELEGATED_DEBIT_SIMULATOR' | 'RAZORPAY_PAYMENT_LAB';
type Connection = 'checking' | 'preview' | 'login' | 'ready';
type ApiError = { code?: string; message?: string };
type SessionUser = { user_id: string; display_name: string; access_token: string };
type AgentIdentity = { id: string; name: string; jwk_thumbprint: string; key_version: number; status: string };
type Grant = { id: string; allowed_categories: string[]; per_order_limit_paise: number; cumulative_limit_paise: number; held_paise: number; spent_paise: number; expires_at: string; auto_execute: boolean; immutable_digest: string; status: string };
type QuoteItem = { sku: string; product_name: string; category: string; quantity: number; unit_price_paise: number; line_total_paise: number };
type Quote = { id: string; status: string; total_paise: number; subtotal_paise: number; delivery_fee_paise: number; tax_paise: number; expires_at: string; canonical_hash: string; remaining_grant_paise: number; items: QuoteItem[] };
type AgentRun = { id: string; status: string; final_response: string | null; active_quote_id: string | null; checkout_id: string | null; tool_call_count: number; turn_count: number; error_code: string | null };
type Checkout = { id: string; status: string; payment_mode: Mode; amount_paise: number; currency: string; receipt: string; execute_after: string; payment_deadline_at: string; version: number; razorpay_order_id: string | null; test_fixture_applied: boolean };
type AuditEvent = { id: number; sequence: number; layer: string; action: string; reason_code: string; explanation: string; amount_delta_paise: number; event_hash: string; previous_hash: string | null };
type ToolEvent = { sequence: number; tool: string; status: string; summary: string };

declare global {
  interface Window { Razorpay?: new (options: Record<string, unknown>) => { open: () => void } }
}

const envMerchant = process.env.NEXT_PUBLIC_MERCHANT_API_URL ?? '';
const envAgent = process.env.NEXT_PUBLIC_AGENT_API_URL ?? '';
const isLocalBrowser = () => typeof window !== 'undefined' && ['localhost', '127.0.0.1'].includes(window.location.hostname);
const merchantBase = () => envMerchant || (isLocalBrowser() ? 'http://localhost:8000' : '');
const agentBase = () => envAgent || (isLocalBrowser() ? 'http://localhost:8001' : '');

const previewQuote: Quote = {
  id: 'preview-quote', status: 'OPEN', subtotal_paise: 34600, delivery_fee_paise: 1200,
  tax_paise: 0, total_paise: 35800, expires_at: new Date(Date.now() + 120_000).toISOString(),
  canonical_hash: 'preview-only-not-a-server-hash', remaining_grant_paise: 264200,
  items: [
    { sku: 'MILK-1L', product_name: 'FarmFresh Toned Milk', category: 'dairy', quantity: 2, unit_price_paise: 6400, line_total_paise: 12800 },
    { sku: 'BREAD-WW', product_name: 'Whole Wheat Bread', category: 'bakery', quantity: 1, unit_price_paise: 5200, line_total_paise: 5200 },
    { sku: 'EGGS-12', product_name: 'Free Range Eggs · 12', category: 'breakfast', quantity: 1, unit_price_paise: 11800, line_total_paise: 11800 },
    { sku: 'BANANA-6', product_name: 'Robusta Bananas · 6', category: 'produce', quantity: 1, unit_price_paise: 4800, line_total_paise: 4800 },
  ],
};

const toolLabels: Record<string, string> = {
  get_usual_basket: 'Purchase history', search_catalog: 'Merchant catalog',
  get_delivery_options: 'Delivery options', quote_cart: 'Canonical quote',
  place_order: 'Authorization + reservation', request_purchase_approval: 'Purchase approval',
};
const terminalCheckoutStates = new Set(['PAID', 'SIMULATED_SETTLED', 'CANCELLED', 'EXPIRED', 'FAILED_TERMINAL', 'LATE_CAPTURE_INCIDENT']);
const money = (paise: number) => new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 0 }).format(paise / 100);
const shortFingerprint = (value?: string) => value ? `${value.slice(0, 10)}···${value.slice(-8)}` : 'Unavailable';

async function requestJson<T>(base: string, path: string, token?: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${base}${path}`, {
    ...init,
    headers: { ...(init?.body ? { 'Content-Type': 'application/json' } : {}), ...(token ? { Authorization: `Bearer ${token}` } : {}), ...init?.headers },
  });
  const data = await response.json().catch(() => ({})) as ApiError & T;
  if (!response.ok) throw new Error(data.message || data.code || `Request failed (${response.status})`);
  return data;
}

async function loadRazorpayScript(): Promise<void> {
  if (window.Razorpay) return;
  await new Promise<void>((resolve, reject) => {
    const existing = document.querySelector<HTMLScriptElement>('script[data-agentauth-razorpay]');
    if (existing) {
      existing.addEventListener('load', () => resolve(), { once: true });
      existing.addEventListener('error', () => reject(new Error('Razorpay Checkout failed to load')), { once: true });
      return;
    }
    const script = document.createElement('script');
    script.src = 'https://checkout.razorpay.com/v1/checkout.js';
    script.async = true;
    script.dataset.agentauthRazorpay = 'true';
    script.onload = () => resolve();
    script.onerror = () => reject(new Error('Razorpay Checkout failed to load'));
    document.head.appendChild(script);
  });
}

export default function Home() {
  const [connection, setConnection] = useState<Connection>('checking');
  const [sessionUser, setSessionUser] = useState<SessionUser | null>(null);
  const [email, setEmail] = useState('demo@trustcart.local');
  const [passcode, setPasscode] = useState('trustcart-demo');
  const [agent, setAgent] = useState<AgentIdentity | null>(null);
  const [grant, setGrant] = useState<Grant | null>(null);
  const [view, setView] = useState<View>('commerce');
  const [mode, setMode] = useState<Mode>('DELEGATED_DEBIT_SIMULATOR');
  const [draft, setDraft] = useState('Order my usual groceries under ₹900 for delivery tonight');
  const [submittedMessage, setSubmittedMessage] = useState('');
  const [run, setRun] = useState<AgentRun | null>(null);
  const [toolEvents, setToolEvents] = useState<ToolEvent[]>([]);
  const [quote, setQuote] = useState<Quote | null>(null);
  const [checkout, setCheckout] = useState<Checkout | null>(null);
  const [audit, setAudit] = useState<AuditEvent[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [armedFaults, setArmedFaults] = useState<string[]>([]);
  const [now, setNow] = useState(0);
  const runGeneration = useRef(0);

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    const merchant = merchantBase(); const agentApi = agentBase();
    if (!merchant || !agentApi) {
      const previewTimer = window.setTimeout(() => {
        setConnection('preview'); setSubmittedMessage('Order my usual groceries under ₹900 for delivery tonight'); setDraft(''); setQuote(previewQuote);
        setToolEvents(Object.keys(toolLabels).slice(0, 5).map((tool, index) => ({ sequence: index + 1, tool, status: 'SUCCEEDED', summary: 'Guided preview event' })));
      }, 0);
      return () => window.clearTimeout(previewTimer);
    }
    Promise.all([fetch(`${merchant}/health`), fetch(`${agentApi}/health`)])
      .then((responses) => setConnection(responses.every((response) => response.ok) ? 'login' : 'preview'))
      .catch(() => setConnection('preview'));
  }, []);

  const token = sessionUser?.access_token;
  const displayName = sessionUser?.display_name || (connection === 'preview' ? 'Diksha' : 'there');
  const quoteSeconds = quote ? (now ? Math.max(0, Math.floor((new Date(quote.expires_at).getTime() - now) / 1000)) : 120) : 0;
  const allowanceUsed = grant ? grant.spent_paise + grant.held_paise : quote?.total_paise || 0;
  const allowanceTotal = grant?.cumulative_limit_paise || 300000;
  const allowancePercent = Math.min(100, (allowanceUsed / allowanceTotal) * 100);
  const traceSteps = useMemo(() => {
    const events = toolEvents.map((event) => ({ label: toolLabels[event.tool] || event.tool, detail: event.summary, done: event.status === 'SUCCEEDED' }));
    if (checkout) events.push({ label: checkout.status.replaceAll('_', ' '), detail: `Checkout ${checkout.receipt}`, done: terminalCheckoutStates.has(checkout.status) });
    return events;
  }, [checkout, toolEvents]);

  async function loadIdentityAndGrants(activeToken: string) {
    const [identity, grants] = await Promise.all([
      requestJson<AgentIdentity>(merchantBase(), '/v1/agents/current', activeToken),
      requestJson<Grant[]>(merchantBase(), '/v1/grants', activeToken),
    ]);
    setAgent(identity);
    const active = grants.find((item) => item.status === 'ACTIVE' && new Date(item.expires_at) > new Date()) || null;
    setGrant(active); if (!active) setView('delegations');
  }

  async function login(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError('');
    try {
      const result = await requestJson<SessionUser>(merchantBase(), '/v1/demo/login', undefined, { method: 'POST', body: JSON.stringify({ email, passcode }) });
      setSessionUser(result); await loadIdentityAndGrants(result.access_token); setConnection('ready');
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Login failed'); }
    finally { setBusy(false); }
  }

  async function createGrant() {
    if (!token || !sessionUser) return;
    setBusy(true); setError('');
    try {
      const requested = await requestJson<{ id: string }>(agentBase(), '/v1/grant-requests', token, {
        method: 'POST', body: JSON.stringify({ user_id: sessionUser.user_id, allowed_categories: ['bakery', 'breakfast', 'dairy', 'produce', 'staples'], per_order_limit_paise: 100000, cumulative_limit_paise: 300000, expires_at: new Date(Date.now() + 7 * 86400000).toISOString(), auto_execute: true }),
      });
      const approved = await requestJson<Grant>(merchantBase(), `/v1/grant-requests/${requested.id}/approve`, token, { method: 'POST', body: JSON.stringify({ acknowledge_demo_identity: true }) });
      setGrant(approved); setView('commerce'); setNotice('Bounded authority approved for this registered agent key.');
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Grant approval failed'); }
    finally { setBusy(false); }
  }

  async function revokeGrant() {
    if (!token || !grant) return;
    setBusy(true); setError('');
    try {
      const revoked = await requestJson<Grant>(merchantBase(), `/v1/grants/${grant.id}/revoke`, token, { method: 'POST' });
      setGrant(revoked); setNotice('Grant revoked. Existing reservations continue through reconciliation.');
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Revocation failed'); }
    finally { setBusy(false); }
  }

  async function consumeEvents(runId: string, activeToken: string, generation: number) {
    const response = await fetch(`${agentBase()}/v1/agent-runs/${runId}/events`, { headers: { Authorization: `Bearer ${activeToken}` } });
    if (!response.ok || !response.body) throw new Error('Could not open the agent event stream');
    const reader = response.body.getReader(); const decoder = new TextDecoder(); let buffer = '';
    while (true) {
      const chunk = await reader.read(); if (chunk.done || runGeneration.current !== generation) break;
      buffer += decoder.decode(chunk.value, { stream: true }).replaceAll('\r\n', '\n');
      let boundary = buffer.indexOf('\n\n');
      while (boundary >= 0) {
        const block = buffer.slice(0, boundary); buffer = buffer.slice(boundary + 2);
        const event = block.match(/^event: (.+)$/m)?.[1]; const raw = block.match(/^data: (.+)$/m)?.[1];
        if (raw) {
          const data = JSON.parse(raw);
          if (event === 'tool') setToolEvents((current) => current.some((item) => item.sequence === data.sequence) ? current : [...current, data]);
          if (event === 'state') setRun((current) => current ? { ...current, ...data, active_quote_id: data.quote_id ?? current.active_quote_id } : current);
          if (event === 'error') throw new Error(data.code || 'Agent event stream failed');
        }
        boundary = buffer.indexOf('\n\n');
      }
    }
  }

  async function hydrateRun(runId: string, activeToken: string) {
    const latest = await requestJson<AgentRun>(agentBase(), `/v1/agent-runs/${runId}`, activeToken); setRun(latest);
    if (latest.active_quote_id) setQuote(await requestJson<Quote>(agentBase(), `/v1/agent-runs/${runId}/quote`, activeToken));
    return latest;
  }

  async function pollCheckout(runId: string, activeToken: string, generation: number) {
    for (let attempt = 0; attempt < 900 && runGeneration.current === generation; attempt += 1) {
      try {
        const latest = await requestJson<Checkout>(agentBase(), `/v1/agent-runs/${runId}/checkout`, activeToken); setCheckout(latest);
        if (terminalCheckoutStates.has(latest.status)) {
          setAudit(await requestJson<AuditEvent[]>(merchantBase(), `/v1/audit-events?checkout_id=${latest.id}`, activeToken)); return;
        }
      } catch (cause) { if (attempt > 8) throw cause; }
      await new Promise((resolve) => window.setTimeout(resolve, 1000));
    }
  }

  async function startRun(event?: FormEvent) {
    event?.preventDefault(); if (!draft.trim()) return;
    if (connection === 'preview') { setSubmittedMessage(draft.trim()); setDraft(''); setNotice('This is a labelled preview fixture. Connect the APIs to execute a real agent run.'); return; }
    if (!token || !grant || grant.status !== 'ACTIVE') { setView('delegations'); setError('Approve an active delegation before ordering.'); return; }
    const generation = ++runGeneration.current;
    setBusy(true); setError(''); setNotice(''); setView('commerce'); setSubmittedMessage(draft.trim()); setRun(null); setToolEvents([]); setQuote(null); setCheckout(null); setAudit([]);
    try {
      const created = await requestJson<AgentRun>(agentBase(), '/v1/agent-runs', token, { method: 'POST', body: JSON.stringify({ message: draft.trim(), payment_mode: mode, grant_id: grant.id }) });
      setRun(created); setDraft(''); await consumeEvents(created.id, token, generation);
      const terminal = await hydrateRun(created.id, token); if (terminal.error_code) throw new Error(terminal.error_code);
      if (terminal.checkout_id) await pollCheckout(created.id, token, generation);
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Agent run failed'); }
    finally { if (runGeneration.current === generation) setBusy(false); }
  }

  async function cancelCurrent() {
    if (!token || !run) return;
    setError('');
    try { setRun(await requestJson<AgentRun>(agentBase(), `/v1/agent-runs/${run.id}/cancel`, token, { method: 'POST' })); setNotice('Cancellation requested through the same signed agent boundary.'); }
    catch (cause) { setError(cause instanceof Error ? cause.message : 'Cancellation failed'); }
  }

  async function openRazorpay() {
    if (!checkout?.razorpay_order_id) { setError('The worker has not created the Razorpay Test Order yet.'); return; }
    setError('');
    try {
      const config = await requestJson<{ enabled: boolean; key_id: string | null }>(merchantBase(), '/v1/payment-config');
      if (!config.enabled || !config.key_id) throw new Error('Razorpay Test Mode credentials are not configured on the server');
      await loadRazorpayScript(); if (!window.Razorpay) throw new Error('Razorpay Checkout is unavailable');
      new window.Razorpay({ key: config.key_id, order_id: checkout.razorpay_order_id, amount: checkout.amount_paise, currency: checkout.currency, name: 'AgentAuth Payment Lab', description: `Test Mode checkout ${checkout.receipt}`, handler: () => setNotice('Checkout returned successfully. Waiting for the signed capture webhook before marking this paid.'), modal: { ondismiss: () => setNotice('Payment window closed. The reservation remains pending until cancellation or reconciliation.') }, theme: { color: '#b97820' } }).open();
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Could not open Razorpay Checkout'); }
  }

  async function armFault(key: 'DROP_ORDER_CREATE_RESPONSE' | 'FORCE_MODEL_TIMEOUT') {
    if (!token || connection !== 'ready') return;
    setError('');
    try {
      await requestJson(merchantBase(), `/v1/developer/faults/${key}`, token, {
        method: 'POST', body: JSON.stringify({ armed: true }),
      });
      setArmedFaults((current) => current.includes(key) ? current : [...current, key]);
      setNotice(key === 'DROP_ORDER_CREATE_RESPONSE' ? 'One-shot lost-response fixture armed. The next Razorpay Order will reconcile by receipt.' : 'One-shot typed model-timeout fixture armed for the next agent run.');
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Could not arm failure fixture'); }
  }

  async function replayNonce() {
    if (!token || !grant || connection !== 'ready') return;
    setError('');
    try {
      const result = await requestJson<{ first_status: number; second_status: number; second_code: string | null; proof_replayed: boolean }>(agentBase(), '/v1/developer/replay-nonce', token, {
        method: 'POST', body: JSON.stringify({ grant_id: grant.id }),
      });
      if (!result.proof_replayed) throw new Error(`Unexpected replay result: ${result.first_status} then ${result.second_status}`);
      setNotice(`Replay proof passed: first request ${result.first_status}; identical nonce rejected ${result.second_status} ${result.second_code}.`);
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Nonce replay fixture failed'); }
  }

  async function runWebhookFixture() {
    if (!token || !checkout || connection !== 'ready') return;
    setError('');
    try {
      const result = await requestJson<{ checkout: Checkout; created_events: number; duplicate_deduplicated: boolean; disclosure: string }>(merchantBase(), '/v1/developer/webhook-fixture', token, {
        method: 'POST', body: JSON.stringify({ checkout_id: checkout.id }),
      });
      setCheckout(result.checkout);
      setAudit(await requestJson<AuditEvent[]>(merchantBase(), `/v1/audit-events?checkout_id=${checkout.id}`, token));
      setNotice(`${result.disclosure} ${result.created_events} unique events; duplicate deduplicated: ${result.duplicate_deduplicated}.`);
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Webhook fixture failed'); }
  }

  if (connection === 'checking') return <main className="boot-screen"><div className="brand-mark">A</div><p>Checking deterministic services…</p></main>;
  if (connection === 'login') return <main className="login-screen"><form className="login-card" onSubmit={login}>
    <span className="brand-mark">A</span><div className="section-kicker">DEMO IDENTITY</div><h1>Enter AgentAuth</h1><p>This login proves application consent only. It is not bank, biometric, or device identity.</p>
    <label>Email<input value={email} onChange={(e) => setEmail(e.target.value)} type="email" /></label><label>Demo passcode<input value={passcode} onChange={(e) => setPasscode(e.target.value)} type="password" /></label>
    {error && <div className="error-banner">{error}</div>}<button disabled={busy}>{busy ? 'Verifying…' : 'Enter test environment'}</button>
  </form></main>;

  return <main className="app-shell">
    <header className="topbar"><div className="brand-lockup"><span className="brand-mark">A</span><div><strong>AgentAuth</strong><span>Agentic commerce, bounded by design</span></div></div><div className={`environment ${connection === 'preview' ? 'preview' : ''}`}><i />{connection === 'preview' ? 'Preview fixture' : 'Live test services'}</div><button className="avatar" aria-label="Current demo user">{displayName.slice(0, 2).toUpperCase()}</button></header>
    {(error || notice) && <div className={error ? 'global-banner error' : 'global-banner'}>{error || notice}<button onClick={() => { setError(''); setNotice(''); }}>×</button></div>}
    <div className="workspace">
      <nav className="rail" aria-label="Primary navigation">
        {([['commerce', '⌁', 'Commerce'], ['inspector', '◎', 'Trust Inspector'], ['delegations', '◇', 'Delegations'], ['developer', '⌘', 'Developer']] as const).map(([key, icon, label]) => <button key={key} className={`rail-item ${view === key ? 'active' : ''}`} onClick={() => setView(key)}><span>{icon}</span>{label}</button>)}
        <div className="rail-bottom"><div className="agent-mini"><span>AI</span><div><b>AgentAuth buyer</b><small>{agent ? 'Key verified' : 'Preview identity'}</small></div></div></div>
      </nav>
      <section className="conversation">
        {view === 'commerce' ? <>
          <div className="section-kicker">{connection === 'preview' ? 'GUIDED PRODUCT PREVIEW' : 'LIVE COMMERCE SESSION'}</div>
          <div className="conversation-heading"><div><h1>Good evening, {displayName}.</h1><p>What should your bounded commerce agent take care of?</p></div><div><div className="mode-switch" aria-label="Payment mode"><button type="button" className={mode === 'DELEGATED_DEBIT_SIMULATOR' ? 'selected' : ''} onClick={() => setMode('DELEGATED_DEBIT_SIMULATOR')}>Autonomous demo</button><button type="button" className={mode === 'RAZORPAY_PAYMENT_LAB' ? 'selected' : ''} onClick={() => setMode('RAZORPAY_PAYMENT_LAB')}>Razorpay Payment Lab</button></div><small className="mode-help">{mode === 'DELEGATED_DEBIT_SIMULATOR' ? 'Deterministic simulated debit; no real payment.' : 'Creates a real Test Mode Order; paid only after capture.'}</small></div></div>
          <div className="chat-stream">
            {submittedMessage && <div className="user-message">{submittedMessage}</div>}
            {(submittedMessage || busy || run) && <div className="agent-response"><div className="agent-orb">AI</div><div className="response-copy"><p>{run?.final_response || (busy ? 'I’m using merchant facts, then deterministic services will independently enforce authority and reservations.' : connection === 'preview' ? 'Preview: the usual basket fits the sample grant and delivery constraint.' : 'Ready.')}</p>
              <div className="live-tools" aria-live="polite">{traceSteps.map((step, index) => <span key={`${step.label}-${index}`} className={step.done ? 'complete' : 'running'}>{step.done ? '✓' : '•'} {step.label}</span>)}</div>
              {quote && <div className="basket-card"><div className="basket-head"><div><span>CANONICAL QUOTE {connection === 'preview' && '· PREVIEW'}</span><h2>Merchant-calculated basket</h2></div><div className="quote-total"><strong>{money(quote.total_paise)}</strong><small>incl. {money(quote.delivery_fee_paise)} delivery</small></div></div><div className="basket-items">{quote.items.map((item) => <div className="basket-row" key={item.sku}><div><b>{item.product_name}</b><span>{item.quantity} × {money(item.unit_price_paise)}</span></div><strong>{money(item.line_total_paise)}</strong></div>)}</div><div className="basket-footer"><span><i />{quoteSeconds ? `Quote valid for ${Math.floor(quoteSeconds / 60)}:${String(quoteSeconds % 60).padStart(2, '0')}` : quote.status}</span><button onClick={() => setView('inspector')}>Review authority →</button></div></div>}
              {checkout && <div className={`checkout-card ${checkout.test_fixture_applied ? 'fixture' : ''}`}><div><span>CHECKOUT STATE {checkout.test_fixture_applied && '· DEVELOPER FIXTURE'}</span><strong>{checkout.status.replaceAll('_', ' ')}</strong><small>{checkout.receipt} · version {checkout.version}{checkout.test_fixture_applied && ' · not a provider payment'}</small></div>{checkout.status === 'CANCEL_WINDOW' && <button onClick={cancelCurrent}>Cancel before execution</button>}{mode === 'RAZORPAY_PAYMENT_LAB' && checkout.status === 'PAYMENT_PENDING' && <button onClick={openRazorpay}>Open Razorpay Test Checkout</button>}</div>}
            </div></div>}
          </div>
          <form className="composer" onSubmit={startRun}><textarea aria-label="Message your commerce agent" placeholder="Message your commerce agent" value={draft} onChange={(event) => setDraft(event.target.value)} disabled={busy} /><div className="composer-bar"><span title="The model can read history, search catalog, check delivery and request a quote. Checkout appears only after a valid quote and matching grant.">4 discovery tools · checkout is structurally gated</span><button type="submit" aria-label="Send message" disabled={busy}>↑</button></div></form>
        </> : view === 'delegations' ? <div className="feature-view"><div className="section-kicker">USER-ISSUED AUTHORITY</div><h1>Delegation consent</h1><p>A bounded scope is immutably tied to one registered agent key.</p><div className="consent-card"><div className="consent-agent"><span>AI</span><div><b>{agent?.name || 'AgentAuth Commerce Agent v1'}</b><code>SHA256 {shortFingerprint(agent?.jwk_thumbprint)}</code></div><i>{grant?.status || 'NOT APPROVED'}</i></div><dl><div><dt>Merchant</dt><dd>AgentAuth Daily</dd></div><div><dt>Categories</dt><dd>{grant?.allowed_categories.join(', ') || 'Bakery, breakfast, dairy, produce, staples'}</dd></div><div><dt>Per order</dt><dd>{money(grant?.per_order_limit_paise || 100000)}</dd></div><div><dt>Cumulative</dt><dd>{money(grant?.cumulative_limit_paise || 300000)}</dd></div><div><dt>Expires</dt><dd>{grant ? new Date(grant.expires_at).toLocaleDateString('en-IN') : '7 days after approval'}</dd></div><div><dt>Auto-execute</dt><dd>{grant?.auto_execute === false ? 'Disabled' : 'Enabled'}</dd></div></dl><div className="demo-disclosure"><b>Demo identity</b><p>This consent proves application authorization. It is not bank, biometric, or device identity.</p></div>{connection === 'preview' ? <button disabled>Connect backend to approve</button> : grant?.status === 'ACTIVE' ? <button className="danger-button" disabled={busy} onClick={revokeGrant}>Revoke for new purchases</button> : <button disabled={busy} onClick={createGrant}>{busy ? 'Approving…' : 'Approve bounded authority'}</button>}</div></div>
        : view === 'developer' ? <div className="feature-view"><div className="section-kicker">FAILURE LAB</div><h1>Break it safely.</h1><p>These are evidence-backed integration scenarios, not decorative UI toggles.</p><div className="developer-grid"><button disabled={connection !== 'ready'} onClick={() => armFault('DROP_ORDER_CREATE_RESPONSE')}><span>Lost create response</span><b>{armedFaults.includes('DROP_ORDER_CREATE_RESPONSE') ? 'ARMED ONCE' : 'ARM WORKER'}</b><small>Discard success, then recover exactly one Order by receipt</small></button><button disabled={connection !== 'ready' || !grant} onClick={replayNonce}><span>Replay PoP nonce</span><b>RUN SIGNED REPLAY</b><small>First request succeeds; identical proof must return PROOF_REPLAYED</small></button><button disabled={connection !== 'ready' || !checkout?.razorpay_order_id || checkout.payment_mode !== 'RAZORPAY_PAYMENT_LAB'} onClick={runWebhookFixture}><span>Out-of-order + duplicate webhook</span><b>RUN DISCLOSED FIXTURE</b><small>Signed capture → stale failure → duplicate failure; exactly one ledger effect</small></button><button disabled={connection !== 'ready'} onClick={() => armFault('FORCE_MODEL_TIMEOUT')}><span>Force model timeout</span><b>{armedFaults.includes('FORCE_MODEL_TIMEOUT') ? 'ARMED ONCE' : 'ARM AGENT'}</b><small>Next run returns a typed recovery outcome</small></button></div><div className={`config-health ${connection === 'preview' ? 'warning' : ''}`}><i />{connection === 'preview' ? 'Frontend preview only — no backend or provider claim is being made.' : 'Agent and merchant APIs are reachable. Provider secrets remain server-side.'}</div></div>
        : <div className="feature-view inspector-focus"><div className="section-kicker">TAMPER-EVIDENT EVIDENCE</div><h1>Trust Inspector</h1><p>The model’s proposal and the deterministic money path are separate facts.</p><div className="canonical-block"><span>SIGNED CANONICAL REQUEST</span><code>TC-POP-V1{`\n`}POST{`\n`}/v1/checkouts{`\n`}raw_body_sha256 · timestamp · nonce{`\n`}{grant?.immutable_digest || 'grant_immutable_digest'}</code></div><div className="hash-chain">{(audit.length ? audit : [{ sequence: 1, action: 'quote.preview' }, { sequence: 2, action: 'checkout.reserved' }]).map((item) => <span key={`${item.sequence}-${item.action}`}>{String(item.sequence).padStart(2, '0')} {item.action}</span>)}</div>{audit.length > 0 && <div className="audit-list">{audit.map((event) => <div key={event.id}><b>{event.reason_code}</b><p>{event.explanation}</p><code>{event.event_hash.slice(0, 18)}…</code></div>)}</div>}</div>}
      </section>
      <aside className="trust-panel"><div className="trust-head"><div><span className="pulse"><i /></span><div><h2>Trust Inspector</h2><p>Every money action, explained</p></div></div></div><div className="trust-section"><div className="trust-label">ACTIVE DELEGATION <span>{grant?.status || (connection === 'preview' ? 'PREVIEW' : 'MISSING')}</span></div><div className="fingerprint"><div className="fingerprint-icon">⌘</div><div><b>{agent?.name || 'AgentAuth Buyer v1'}</b><code>{shortFingerprint(agent?.jwk_thumbprint)}</code></div></div><div className="allowance"><div><span>Allowance reserved or spent</span><strong>{money(allowanceUsed)} <small>of {money(allowanceTotal)}</small></strong></div><div className="allowance-track"><i style={{ width: `${allowancePercent}%` }} /></div><p>{money(Math.max(0, allowanceTotal - allowanceUsed))} available under this grant</p></div></div><div className="trust-section"><div className="trust-label">DECISION TRACE <span className="trace-id">{run ? `#${run.id.slice(0, 8)}` : '#NO-RUN'}</span></div><div className="decision-list">{traceSteps.length ? traceSteps.map((step, index) => <div className="decision" key={`${step.label}-${index}`}><span className={step.done ? 'check' : ''}>{step.done ? '✓' : '·'}</span><div><b>{step.label}</b><small>{step.detail}</small></div></div>) : <p className="empty-trace">Start a run to produce durable tool evidence.</p>}</div></div><div className="policy-note"><span>AI boundary · Gemini 3.7 Flash</span><p>The free-tier model may select SKUs and quantities. Prices, totals, identity, inventory, authority, payment state, and reconciliation are deterministic.</p></div><button className="audit-button" onClick={() => setView('inspector')}>Open full audit trail <span>↗</span></button></aside>
    </div>
  </main>;
}
