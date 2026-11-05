import React, { useState } from "react";
import { api, money } from "../api.js";
import { ErrorMsg, Pill, useLoad } from "../ui.jsx";

function Row({ c, toast, reload }) {
  const [email, setEmail] = useState(c.contact_email);
  const [busy, setBusy] = useState(false);
  const dirty = email.trim() !== c.contact_email;
  const save = async () => {
    setBusy(true);
    try {
      await api.setEmail(c.customer_id, email.trim());
      toast.show(`Email for ${c.name} updated`);
      await reload();
    } catch (e) {
      toast.show(e.message, true);
    } finally {
      setBusy(false);
    }
  };
  return (
    <tr>
      <td><b>{c.name}</b><div className="small muted">{c.customer_id} · {c.city}, {c.country}</div></td>
      <td>{c.contact_name}<div className="small muted">{c.phone}</div></td>
      <td>
        <div className="row">
          <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} aria-label={`Email for ${c.name}`} />
          <button className="primary" disabled={!dirty || busy} onClick={save}>Save</button>
        </div>
        {/example\.(com|org|net)$/i.test(c.contact_email) && <div className="small warn">Placeholder address - it cannot receive mail. Enter your own test inbox.</div>}
      </td>
      <td>{c.currency}</td>
      <td className="num">{c.invoice_count}</td>
      <td className="num">{c.overdue_count > 0 ? <Pill kind="red">{c.overdue_count} overdue</Pill> : "-"}</td>
      <td className="num">{Object.entries(c.unpaid).map(([k, v]) => <div key={k}>{money(v, k)}</div>)}</td>
    </tr>
  );
}

export default function Customers({ toast, version }) {
  const { data, error, reload } = useLoad(() => api.customers(), [version]);
  return (
    <div className="card">
      <h2>Customers</h2>
      <p className="muted small">Edit a customer's email address to your own test inbox. Replies are matched to customers by this address.</p>
      <ErrorMsg error={error} />
      <div className="scroll">
        <table>
          <thead><tr><th>Customer</th><th>Contact</th><th>Email</th><th>Cur.</th><th className="num">Invoices</th><th className="num">Overdue</th><th className="num">Unpaid</th></tr></thead>
          <tbody>{(data || []).map((c) => <Row key={c.customer_id} c={c} toast={toast} reload={reload} />)}</tbody>
        </table>
      </div>
    </div>
  );
}
