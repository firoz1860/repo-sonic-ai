import React, { useState } from "react";
import { api, fdatetime } from "../api.js";
import { Empty, ErrorMsg, Pill, useLoad } from "../ui.jsx";

const INTENT = { balance_query: "balance", payment_instructions: "how to pay", payment_claim: "says paid", tds_query: "TDS", extension_request: "extension", dispute: "dispute", legal: "legal", other_company: "other company", unknown_sender: "unknown sender", unclear: "unclear", other: "other", out_of_office: "out of office", bounce: "bounce" };

function Draft({ d, toast, done }) {
  const [body, setBody] = useState(d.body);
  const [subject, setSubject] = useState(d.subject);
  const [problems, setProblems] = useState([]);
  const [busy, setBusy] = useState(false);
  const send = async (force = false) => {
    setBusy(true);
    try {
      await api.sendDraft(d.message_id, { subject, body, force });
      toast.show("Reply sent");
      done();
    } catch (e) {
      if (e.status === 422 && e.problems.length) setProblems(e.problems);
      else toast.show(e.message, true);
    } finally {
      setBusy(false);
    }
  };
  const dismiss = async () => {
    try { await api.dismissDraft(d.message_id); toast.show("Dismissed without replying"); done(); } catch (e) { toast.show(e.message, true); }
  };
  return (
    <div className="card inner-card">
      <div className="row between">
        <div><b>{d.customer_name}</b> <span className="muted small">to {d.to}</span></div>
        <span className="small muted">{fdatetime(d.created_at)}</span>
      </div>
      <div className="alert">Why a person is needed: {d.needs_attention_reason}</div>
      {d.inbound && (<>
        <div className="small muted">Customer wrote:</div>
        <blockquote>{d.inbound.subject && <b>{d.inbound.subject}<br /></b>}{d.inbound.body}</blockquote>
        {d.inbound.classification?.intents && <div className="row">{d.inbound.classification.intents.map((i) => <Pill key={i} kind="blue">{INTENT[i] || i}</Pill>)}</div>}
      </>)}
      <div className="small muted">Draft reply (edit before sending). It promises nothing; amounts and dates are checked against the database.</div>
      <input value={subject} onChange={(e) => setSubject(e.target.value)} />
      <textarea rows={10} value={body} onChange={(e) => { setBody(e.target.value); setProblems([]); }} />
      {problems.length > 0 && (
        <div className="alert bad">
          <b>This reply contains details that do not match the database:</b>
          <ul>{problems.map((p) => <li key={p}>{p}</li>)}</ul>
          <button onClick={() => send(true)} disabled={busy}>Send anyway (I take responsibility)</button>
        </div>
      )}
      <div className="row">
        <button className="primary" disabled={busy || !body.trim()} onClick={() => send(false)}>Send reply</button>
        <button onClick={dismiss}>Dismiss</button>
      </div>
    </div>
  );
}

function NeedsAttention({ toast, version, refreshMeta }) {
  const { data, error, reload } = useLoad(() => api.needsAttention(), [version]);
  const done = () => { reload(); refreshMeta(); };
  return (
    <div className="stack">
      <ErrorMsg error={error} />
      {data && data.length === 0 && <Empty>Nothing needs attention. Routine questions are answered automatically.</Empty>}
      {(data || []).map((d) => <Draft key={d.message_id} d={d} toast={toast} done={done} />)}
    </div>
  );
}

function Conversations({ version }) {
  const list = useLoad(() => api.conversations(), [version]);
  const [sel, setSel] = useState(null);
  const conv = useLoad(() => (sel ? api.conversation(sel) : Promise.resolve(null)), [sel, version]);
  return (
    <div className="split">
      <div className="list">
        <ErrorMsg error={list.error} />
        {list.data && list.data.length === 0 && <Empty>No conversations yet. Send reminders, then reply from your customer inbox.</Empty>}
        {(list.data || []).map((c) => (
          <button key={c.conversation_id} className={"item " + (sel === c.conversation_id ? "active" : "")} onClick={() => setSel(c.conversation_id)}>
            <b>{c.customer_name}</b> {c.pending > 0 && <Pill kind="red">needs attention</Pill>}
            <div className="small muted">{c.message_count} messages · {fdatetime(c.last_message_at)}</div>
            <div className="small">{c.preview}</div>
          </button>
        ))}
      </div>
      <div className="thread">
        {!sel && <Empty>Select a conversation.</Empty>}
        {conv.data && conv.data.conversation_id === sel && (<>
          <h3>{conv.data.customer_name}</h3>
          {conv.data.messages.map((m) => (
            <div key={m.message_id} className={"msg " + m.direction}>
              <div className="small muted">
                {m.direction === "inbound" ? "Customer" : m.kind === "reminder" ? "Reminder (agent)" : m.kind === "auto_reply" ? "Auto-reply (agent)" : m.status === "pending_review" ? "Draft - awaiting a person" : m.status === "dismissed" ? "Draft - dismissed" : "Reply (person)"}
                {" · "}{fdatetime(m.created_at)} {m.status === "automated" && <Pill kind="grey">{INTENT[m.classification?.intents?.[0]] || "automated"} - not answered</Pill>}
                {m.status === "needs_attention" && <Pill kind="red">needs attention</Pill>}
              </div>
              {m.subject && <b>{m.subject}</b>}
              <pre>{m.body}</pre>
              {m.classification?.intents && m.direction === "inbound" && m.status !== "automated" && <div className="row">{m.classification.intents.map((i) => <Pill key={i} kind="blue">{INTENT[i] || i}</Pill>)}</div>}
            </div>
          ))}
        </>)}
      </div>
    </div>
  );
}

function Reminders({ version }) {
  const { data, error } = useLoad(() => api.reminders(), [version]);
  const claims = useLoad(() => api.claims(), [version]);
  return (
    <div className="stack">
      <ErrorMsg error={error} />
      <h3>Reminders sent</h3>
      {data && data.length === 0 ? <Empty>No reminders sent yet.</Empty> : (
        <table><thead><tr><th>When</th><th>Invoice</th><th>To</th></tr></thead>
          <tbody>{(data || []).map((r, i) => <tr key={i}><td>{fdatetime(r.sent_at)}</td><td>{r.invoice_number}</td><td>{r.to}</td></tr>)}</tbody></table>
      )}
      <h3>Payment claims noted from emails <span className="small muted">(never marks anything paid)</span></h3>
      {claims.data && claims.data.length === 0 ? <Empty>None.</Empty> : (
        <table><thead><tr><th>When</th><th>Invoices</th><th>Claimed date</th><th>Reference</th></tr></thead>
          <tbody>{(claims.data || []).map((c) => <tr key={c.claim_id}><td>{fdatetime(c.created_at)}</td><td>{c.invoice_numbers.join(", ") || "-"}</td><td>{c.claimed_date || "-"}</td><td>{c.reference || "-"}</td></tr>)}</tbody></table>
      )}
    </div>
  );
}

export default function Collections({ toast, version, bump, refreshMeta, status }) {
  const [tab, setTab] = useState("attention");
  const [preview, setPreview] = useState(null);
  const [busy, setBusy] = useState("");

  const run = async (name, fn) => {
    setBusy(name);
    try { await fn(); } catch (e) { toast.show(e.message, true); } finally { setBusy(""); }
  };
  const previewReminders = () => run("preview", async () => setPreview((await api.sendReminders({ dry_run: true })).results));
  const sendReminders = () => run("send", async () => {
    const r = (await api.sendReminders({})).results;
    const sent = r.filter((x) => x.status === "sent").length;
    toast.show(`${sent} reminder email${sent === 1 ? "" : "s"} sent, ${r.filter((x) => x.status === "skipped").length} skipped, ${r.filter((x) => x.status === "failed").length} failed`, r.some((x) => x.status === "failed"));
    setPreview(null);
    bump();
  });
  const checkInbox = () => run("inbox", async () => {
    const r = await api.checkInbox();
    toast.show(r.status === "busy" ? "A check is already running" : `Inbox checked: ${r.processed} new email${r.processed === 1 ? "" : "s"} processed`);
    bump();
  });

  return (
    <div className="stack">
      <div className="card">
        <div className="row between">
          <div><h2>Collections agent</h2>
            <p className="muted small">Sends reminders for overdue invoices, reads replies over IMAP and answers routine questions. Anything needing judgement is held for a person.
              {status && !status.smtp_configured && <span className="warn"> SMTP is not configured in the back end .env.</span>}
              {status && !status.imap_configured && <span className="warn"> IMAP is not configured.</span>}</p></div>
          <div className="row">
            <button onClick={previewReminders} disabled={!!busy}>Preview reminders</button>
            <button className="primary" onClick={sendReminders} disabled={!!busy}>{busy === "send" ? "Sending..." : "Send reminders now"}</button>
            <button className="primary" onClick={checkInbox} disabled={!!busy}>{busy === "inbox" ? "Checking..." : "Check inbox now"}</button>
          </div>
        </div>
        {preview && (
          <div className="preview">
            <div className="row between"><b>Reminder preview (nothing sent)</b><button onClick={() => setPreview(null)}>Close</button></div>
            {preview.length === 0 && <Empty>No overdue invoices.</Empty>}
            {preview.map((p) => (
              <details key={p.customer_id}><summary><b>{p.customer_name}</b> · {p.invoices.join(", ")} · {p.status === "skipped" ? <span className="warn">skipped: {p.reason}</span> : <>to {p.to}</>}</summary>
                {p.body && <pre>{p.subject + "\n\n" + p.body}</pre>}</details>
            ))}
          </div>
        )}
      </div>
      <div className="tabs">
        {[["attention", "Needs attention"], ["conversations", "Conversations"], ["log", "Reminders & claims"]].map(([id, l]) => <button key={id} className={tab === id ? "active" : ""} onClick={() => setTab(id)}>{l}</button>)}
      </div>
      <div className="card">
        {tab === "attention" && <NeedsAttention toast={toast} version={version} refreshMeta={() => { refreshMeta(); bump(); }} />}
        {tab === "conversations" && <Conversations version={version} />}
        {tab === "log" && <Reminders version={version} />}
      </div>
    </div>
  );
}
