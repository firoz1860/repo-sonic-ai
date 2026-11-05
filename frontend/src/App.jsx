import React, { useCallback, useEffect, useState } from "react";
import { api, hasToken, login, logout, setUnauthorizedHandler, money } from "./api.js";
import Overview from "./pages/Overview.jsx";
import Customers from "./pages/Customers.jsx";
import Invoices from "./pages/Invoices.jsx";
import Collections from "./pages/Collections.jsx";
import Reconciliation from "./pages/Reconciliation.jsx";
import { Toast, useToast } from "./ui.jsx";

const TABS = [
  ["overview", "Organization"],
  ["customers", "Customers"],
  ["invoices", "Invoices"],
  ["collections", "Collections"],
  ["reconciliation", "Bank reconciliation"],
];

function Login({ onDone }) {
  const [pw, setPw] = useState("");
  const [err, setErr] = useState("");
  const submit = async (e) => {
    e.preventDefault();
    try {
      await login(pw);
      onDone();
    } catch (x) {
      setErr(x.message);
    }
  };
  return (
    <div className="login">
      <form className="card" onSubmit={submit}>
        <h1>AcmeCorp Accounts</h1>
        <p className="muted">Collections agent &amp; bank reconciliation</p>
        <input type="password" autoFocus placeholder="Admin password" value={pw} onChange={(e) => setPw(e.target.value)} />
        <button className="primary" disabled={!pw}>Sign in</button>
        {err && <p className="error">{err}</p>}
      </form>
    </div>
  );
}

export default function App() {
  const [authed, setAuthed] = useState(hasToken());
  const [tab, setTab] = useState(() => location.hash.slice(1) || "overview");
  const [status, setStatus] = useState(null);
  const [badge, setBadge] = useState(0);
  const [version, setVersion] = useState(0); // bump to make pages reload after reset / approvals
  const toast = useToast();

  useEffect(() => setUnauthorizedHandler(() => setAuthed(false)), []);
  useEffect(() => { location.hash = tab; }, [tab]);
  const refreshMeta = useCallback(async () => {
    try {
      const [s, sum] = await Promise.all([api.status(), api.summary()]);
      setStatus(s);
      setBadge(sum.needs_attention);
    } catch { /* handled by 401 handler */ }
  }, []);
  useEffect(() => { if (authed) refreshMeta(); }, [authed, version, refreshMeta]);
  const bump = () => setVersion((v) => v + 1);

  const reset = async () => {
    if (!confirm("Reset demo data? This restores the original customers and invoices and deletes all conversations, statements and payments.")) return;
    const keep = confirm("Keep the customer email addresses you edited?\n\nOK = keep my addresses   Cancel = restore the original example.com addresses");
    try {
      await api.reset(keep);
      toast.show("Demo data restored");
      bump();
    } catch (e) {
      toast.show(e.message, true);
    }
  };

  if (!authed) return <Login onDone={() => setAuthed(true)} />;
  const props = { toast, bump, version, refreshMeta };
  return (
    <div className="app">
      <header>
        <div>
          <strong>AcmeCorp Accounts</strong>
          {status && <span className="muted small"> · as of {status.today} · LLM: {status.llm}{status.email_dry_run ? " · email DRY RUN" : ""}</span>}
        </div>
        <div className="row">
          <button className="danger" onClick={reset}>Reset demo data</button>
          <button onClick={() => { logout(); setAuthed(false); }}>Sign out</button>
        </div>
      </header>
      <nav>
        {TABS.map(([id, label]) => (
          <button key={id} className={tab === id ? "active" : ""} onClick={() => setTab(id)}>
            {label}{id === "collections" && badge > 0 && <span className="badge">{badge}</span>}
          </button>
        ))}
      </nav>
      <main>
        {tab === "overview" && <Overview {...props} />}
        {tab === "customers" && <Customers {...props} />}
        {tab === "invoices" && <Invoices {...props} />}
        {tab === "collections" && <Collections {...props} status={status} />}
        {tab === "reconciliation" && <Reconciliation {...props} />}
      </main>
      <Toast toast={toast} />
    </div>
  );
}
