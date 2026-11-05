import React, { useMemo, useRef, useState } from "react";
import { api, fdate, money } from "../api.js";
import { Empty, ErrorMsg, Pill, useLoad } from "../ui.jsx";

const scoreClass = (s) => (s >= 85 ? "green" : s >= 50 ? "amber" : "red");
const CASE = { exact: "exact amount", tds: "less 10% TDS", partial: "part payment", multi_exact: "several invoices", multi_tds: "several invoices, less TDS", over: "over-payment" };

function Row({ t, invoices, toast, onChange }) {
  const sug = t.suggestion;
  const open = useMemo(() => invoices.filter((i) => i.currency === t.currency && i.status === "sent" && i.balance_due > 0), [invoices, t.currency]);
  const byNo = Object.fromEntries(invoices.map((i) => [i.invoice_number, i]));
  const [manual, setManual] = useState(false);
  const [pick, setPick] = useState("");
  const [action, setAction] = useState(sug?.suggested_action || "");
  const [busy, setBusy] = useState(false);

  const allocations = useMemo(() => {
    if (manual) return pick ? [{ invoice_number: pick, amount: t.credit }] : [];
    return sug?.status === "match" ? sug.allocations.map((a) => ({ invoice_number: a.invoice_number, amount: a.amount })) : [];
  }, [manual, pick, sug, t.credit]);
  const needsAction = allocations.some((a) => byNo[a.invoice_number] && a.amount < byNo[a.invoice_number].balance_due - 0.004);
  const canApprove = allocations.length > 0 && (!needsAction || action);
  const act = async (fn, okMsg) => {
    setBusy(true);
    try { await fn(); toast.show(okMsg); await onChange(); } catch (e) { toast.show(e.message, true); } finally { setBusy(false); }
  };
  const approve = () => act(() => api.approve(t.txn_id, allocations.map((a) => ({ ...a, action: needsAction && a.amount < byNo[a.invoice_number].balance_due - 0.004 ? action : undefined }))), "Payment approved and applied");

  if (t.status === "ignored") return <tr className="dim"><td>{fdate(t.txn_date)}</td><td>{t.narration}<div className="small muted">{t.reference}</div></td><td className="num">- {money(t.debit, t.currency)}</td><td colSpan="3" className="muted">Money out - not a receipt</td></tr>;
  if (t.status === "invalid") return <tr className="dim"><td colSpan="2">Row {t.row_no}: {t.narration}</td><td colSpan="4" className="red">Invalid: {t.errors.join("; ")}</td></tr>;

  return (
    <tr>
      <td>{fdate(t.txn_date)}</td>
      <td>{t.narration}<div className="small muted">ref {t.reference || "-"}</div>
        {(sug?.linked_claims || []).map((c, i) => <div key={i} className="small warn">Email evidence{c.exact_ref ? " (same reference)" : ""}: "{c.snippet}"</div>)}
      </td>
      <td className="num"><b>{money(t.credit, t.currency)}</b></td>
      <td>
        {t.status === "approved" ? (<>{t.applied.map((a) => <div key={a.invoice_number}><Pill kind="green">{a.invoice_number}</Pill> {money(a.amount, t.currency)}{a.tds_closed > 0 && ` + TDS closed ${money(a.tds_closed, t.currency)}`} → {a.invoice_status_after === "paid" ? "paid" : `balance ${money(a.balance_after, t.currency)}`}{a.warning && <div className="small warn">{a.warning}</div>}</div>)}</>) :
          manual ? (
            <select value={pick} onChange={(e) => setPick(e.target.value)}>
              <option value="">Choose invoice...</option>
              {open.map((i) => <option key={i.invoice_number} value={i.invoice_number}>{i.invoice_number} · {i.customer_name} · balance {money(i.balance_due, i.currency)}</option>)}
            </select>
          ) : sug?.status === "match" ? (
            <div><b>{sug.invoices.join(" + ")}</b> <span className="small muted">({CASE[sug.amount_case]})</span>
              {(sug.alternatives || []).length > 0 && <div className="small muted">also: {sug.alternatives.map((a) => `${a.invoices.join("+")} (${a.score})`).join(", ")}</div>}</div>
          ) : <span className="muted">No match</span>}
        {t.status === "pending" && needsAction && (
          <div className="radios">
            <label><input type="radio" name={t.txn_id} checked={action === "close_tds"} onChange={() => setAction("close_tds")} /> Close invoice (TDS deducted)</label>
            <label><input type="radio" name={t.txn_id} checked={action === "leave_open"} onChange={() => setAction("leave_open")} /> Leave balance open</label>
          </div>
        )}
      </td>
      <td>
        {t.status === "approved" ? <Pill kind="green">approved</Pill> : t.status === "rejected" ? <Pill kind="grey">rejected</Pill> : sug?.status === "match" && !manual ? <><span className={"score " + scoreClass(sug.score)}>{sug.score}</span><div className="small">{sug.reason}</div></> : <div className="small muted">{sug?.reason}</div>}
      </td>
      <td className="actions">
        {t.status === "pending" && (<>
          {allocations.length > 0 && <button className="primary" disabled={busy || !canApprove} onClick={approve}>Approve</button>}
          <button disabled={busy} onClick={() => act(() => api.reject(t.txn_id), "Rejected")}>Reject</button>
          <button disabled={busy} onClick={() => { setManual(!manual); setPick(""); }}>{manual ? "Back to suggestion" : "Choose invoice"}</button>
        </>)}
        {t.status === "rejected" && <button disabled={busy} onClick={() => act(() => api.reopen(t.txn_id), "Reopened")}>Reopen</button>}
      </td>
    </tr>
  );
}

export default function Reconciliation({ toast, version, bump }) {
  const fileRef = useRef();
  const [sid, setSid] = useState(null);
  const [busy, setBusy] = useState(false);
  const stmts = useLoad(() => api.statements(), [version]);
  const st = useLoad(() => (sid ? api.statement(sid) : Promise.resolve(null)), [sid, version]);
  const invoices = useLoad(() => api.invoices(), [version, st.data]);

  const upload = async (file) => {
    if (!file) return;
    setBusy(true);
    try {
      const s = await api.upload(file);
      setSid(s.statement_id);
      toast.show(`${file.name}: ${s.transactions.length} rows read`);
      bump();
    } catch (e) { toast.show(e.message, true); } finally { setBusy(false); if (fileRef.current) fileRef.current.value = ""; }
  };
  const changed = async () => { await st.reload(); await invoices.reload(); bump(); };
  const s = st.data;
  const counts = s?.summary;
  return (
    <div className="stack">
      <div className="card">
        <div className="row between">
          <div><h2>Bank reconciliation</h2><p className="muted small">Upload a statement CSV (txn_date, narration, reference, credit, debit, currency). Suggestions are only suggestions: nothing changes until you approve.</p></div>
          <div className="row">
            <input ref={fileRef} type="file" accept=".csv" hidden onChange={(e) => upload(e.target.files[0])} />
            <button className="primary" disabled={busy} onClick={() => fileRef.current.click()}>{busy ? "Matching..." : "Upload statement CSV"}</button>
            {s && <button disabled={busy} onClick={async () => { await api.rematch(sid); toast.show("Suggestions refreshed against current balances"); changed(); }}>Re-run matching</button>}
          </div>
        </div>
        {stmts.data && stmts.data.length > 0 && (
          <div className="row"><span className="small muted">Statements:</span>
            {stmts.data.map((x) => <button key={x.statement_id} className={sid === x.statement_id ? "chip active" : "chip"} onClick={() => setSid(x.statement_id)}>{x.filename} ({x.rows})</button>)}</div>
        )}
      </div>
      <ErrorMsg error={st.error} />
      {!s && <Empty>Upload a statement to begin.</Empty>}
      {s && (
        <div className="card">
          <div className="row small">{Object.entries(counts).filter(([, v]) => v > 0).map(([k, v]) => <Pill key={k} kind={k === "pending" ? "amber" : k === "approved" ? "green" : "grey"}>{v} {k}</Pill>)}</div>
          <div className="scroll">
            <table className="recon">
              <thead><tr><th>Date</th><th>Narration</th><th className="num">Amount</th><th>Suggested</th><th>Score · reason</th><th>Decision</th></tr></thead>
              <tbody>{s.transactions.map((t) => <Row key={t.txn_id} t={t} invoices={invoices.data || []} toast={toast} onChange={changed} />)}</tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
