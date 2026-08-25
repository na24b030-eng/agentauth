'use client';

import { useEffect, useState } from 'react';

const steps = [
  { label: 'Intent understood', detail: 'Repeat basket · under ₹900', done: true },
  { label: 'Catalog checked', detail: '7 products · all available', done: true },
  { label: 'Quote created', detail: '₹842 · delivery included', done: true },
  { label: 'Authority verified', detail: 'Within ₹1,000 order cap', done: true },
];

const basket = [
  ['Aashirvaad Atta', '5 kg', '₹312'],
  ['Amul Taaza Milk', '2 × 1 L', '₹136'],
  ['Fortune Sunflower Oil', '1 L', '₹154'],
  ['India Gate Rice', '1 kg', '₹128'],
];

export default function Home() {
  const [mode, setMode] = useState<'autonomous' | 'razorpay'>('autonomous');
  const [message, setMessage] = useState('Order my usual groceries under ₹900 for delivery tonight');
  const [view, setView] = useState<'commerce' | 'inspector' | 'delegations' | 'developer'>('commerce');
  const [runStage, setRunStage] = useState(4);
  const [fault, setFault] = useState(false);

  useEffect(() => {
    if (runStage >= 4) return;
    const timer = window.setTimeout(() => setRunStage((stage) => Math.min(stage + 1, 4)), 650);
    return () => window.clearTimeout(timer);
  }, [runStage]);

  const startRun = () => {
    if (!message.trim()) return;
    setView('commerce');
    setRunStage(0);
  };

  return (
    <main className="app-shell">
      <header className="topbar">
        <div className="brand-lockup">
          <span className="brand-mark">A</span>
          <div><strong>AgentAuth</strong><span>Agentic commerce, bounded by design</span></div>
        </div>
        <div className="environment"><i /> Test environment</div>
        <button className="avatar" aria-label="Open user menu">DK</button>
      </header>

      <div className="workspace">
        <nav className="rail" aria-label="Primary navigation">
          <button className={`rail-item ${view === 'commerce' ? 'active' : ''}`} onClick={() => setView('commerce')}><span>⌁</span>Commerce</button>
          <button className={`rail-item ${view === 'inspector' ? 'active' : ''}`} onClick={() => setView('inspector')}><span>◎</span>Trust Inspector</button>
          <button className={`rail-item ${view === 'delegations' ? 'active' : ''}`} onClick={() => setView('delegations')}><span>◇</span>Delegations</button>
          <button className={`rail-item ${view === 'developer' ? 'active' : ''}`} onClick={() => setView('developer')}><span>⌘</span>Developer</button>
          <div className="rail-bottom">
            <div className="agent-mini"><span>AI</span><div><b>AgentAuth buyer</b><small>Key verified</small></div></div>
          </div>
        </nav>

        <section className="conversation">
          {view === 'commerce' ? <>
          <div className="section-kicker">LIVE COMMERCE SESSION</div>
          <div className="conversation-heading">
            <div><h1>Good evening, Diksha.</h1><p>What should your agent take care of?</p></div>
            <div className="mode-switch" aria-label="Payment mode">
              <button className={mode === 'autonomous' ? 'selected' : ''} onClick={() => setMode('autonomous')}>Autonomous demo</button>
              <button className={mode === 'razorpay' ? 'selected' : ''} onClick={() => setMode('razorpay')}>Razorpay lab</button>
            </div>
          </div>

          <div className="chat-stream">
            <div className="user-message">{message}</div>
            <div className="agent-response">
              <div className="agent-orb">AI</div>
              <div className="response-copy">
                <p>{runStage < 4 ? 'I’m checking merchant facts and your delegated authority…' : 'I found your regular basket and kept it below your limit. Everything is available for the 7–9 PM slot.'}</p>
                <div className="live-tools" aria-live="polite">
                  {steps.map((step, index) => <span key={step.label} className={index < runStage ? 'complete' : index === runStage ? 'running' : ''}>{index < runStage ? '✓' : index === runStage ? '•' : '·'} {step.label}</span>)}
                </div>
                {runStage >= 4 &&
                <div className="basket-card">
                  <div className="basket-head"><div><span>CANONICAL QUOTE</span><h2>Tonight&apos;s essentials</h2></div><div className="quote-total"><strong>₹842</strong><small>incl. ₹12 delivery</small></div></div>
                  <div className="basket-items">
                    {basket.map(([name, qty, price]) => <div className="basket-row" key={name}><div><b>{name}</b><span>{qty}</span></div><strong>{price}</strong></div>)}
                  </div>
                  <div className="basket-footer"><span><i /> Quote valid for 1:42</span><button onClick={() => setView('inspector')}>Review authority →</button></div>
                </div>}
              </div>
            </div>
          </div>

          <form className="composer" onSubmit={(event) => { event.preventDefault(); startRun(); }}>
            <textarea aria-label="Message your commerce agent" value={message} onChange={(event) => setMessage(event.target.value)} />
            <div className="composer-bar"><span>Agent may use 5 merchant tools</span><button type="submit" aria-label="Send message">↑</button></div>
          </form>
          </> : view === 'delegations' ? <div className="feature-view">
            <div className="section-kicker">USER-ISSUED AUTHORITY</div>
            <h1>Delegation consent</h1><p>Immutable scope approved for one registered agent key.</p>
            <div className="consent-card">
              <div className="consent-agent"><span>AI</span><div><b>AgentAuth Commerce Agent v1</b><code>SHA256 7B:31:9F:2A:···:C8</code></div><i>ACTIVE</i></div>
              <dl><div><dt>Merchant</dt><dd>AgentAuth Daily</dd></div><div><dt>Categories</dt><dd>Dairy, staples, bakery, produce</dd></div><div><dt>Per order</dt><dd>₹1,000</dd></div><div><dt>Cumulative</dt><dd>₹3,000</dd></div><div><dt>Expires</dt><dd>31 Aug 2026</dd></div><div><dt>Auto-execute</dt><dd>Enabled</dd></div></dl>
              <div className="demo-disclosure"><b>Demo identity</b><p>This consent proves application authorization. It is not bank, biometric or device identity.</p></div>
              <button className="danger-button">Revoke for new purchases</button>
            </div>
          </div> : view === 'developer' ? <div className="feature-view">
            <div className="section-kicker">FAILURE LAB</div><h1>Break it safely.</h1><p>Inject deterministic failures and inspect convergence without exposing a secret.</p>
            <div className="developer-grid">
              <button onClick={() => setFault(!fault)}><span>Lost create response</span><b>{fault ? 'ARMED' : 'READY'}</b><small>Discard success, recover by receipt</small></button>
              <button><span>Replay PoP nonce</span><b>RUN</b><small>Expect 409 PROOF_REPLAYED</small></button>
              <button><span>Duplicate webhook</span><b>RUN</b><small>Expect one ledger effect</small></button>
              <button><span>Force model timeout</span><b>RUN</b><small>Expect typed recovery outcome</small></button>
            </div>
            <div className="config-health"><i /> Configuration health: UI ready · provider credentials checked server-side</div>
          </div> : <div className="feature-view inspector-focus">
            <div className="section-kicker">TAMPER-EVIDENT EVIDENCE</div><h1>Trust Inspector</h1><p>The model’s proposal and the deterministic money path are separate facts.</p>
            <div className="canonical-block"><span>SIGNED CANONICAL REQUEST</span><code>TC-POP-V1{`\n`}POST{`\n`}/v1/checkouts{`\n`}…{`\n`}grant_immutable_digest</code></div>
            <div className="hash-chain"><span>01 Quote</span><i /> <span>02 Reserved</span><i /> <span>03 Executing</span><i /> <span>04 Settled</span></div>
          </div>}
        </section>

        <aside className="trust-panel">
          <div className="trust-head"><div><span className="pulse"><i /></span><div><h2>Trust Inspector</h2><p>Every money action, explained</p></div></div><button aria-label="Close inspector">×</button></div>
          <div className="trust-section">
            <div className="trust-label">ACTIVE DELEGATION <span>VERIFIED</span></div>
            <div className="fingerprint"><div className="fingerprint-icon">⌘</div><div><b>AgentAuth Buyer v1</b><code>7B:31:9F:2A:···:C8</code></div></div>
            <div className="allowance"><div><span>Allowance used</span><strong>₹842 <small>of ₹3,000</small></strong></div><div className="allowance-track"><i /></div><p>₹2,158 remaining · expires in 6 days</p></div>
          </div>
          <div className="trust-section">
            <div className="trust-label">DECISION TRACE <span className="trace-id">#TC-2841</span></div>
            <div className="decision-list">
              {steps.map((step, index) => <div className="decision" key={step.label}><span className={index < runStage ? 'check' : ''}>{index < runStage ? '✓' : '·'}</span><div><b>{step.label}</b><small>{step.detail}</small></div></div>)}
            </div>
          </div>
          <div className="policy-note"><span>AI boundary</span><p>The model selected products. Prices, totals, inventory and spending authority were calculated and enforced by deterministic services.</p></div>
          <button className="audit-button" onClick={() => setView('inspector')}>Open full audit trail <span>↗</span></button>
        </aside>
      </div>
    </main>
  );
}
