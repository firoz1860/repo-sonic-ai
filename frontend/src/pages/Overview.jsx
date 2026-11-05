import React from "react";
import { api, money } from "../api.js";
import { ErrorMsg, useLoad } from "../ui.jsx";

export default function Overview({ version }) {
  const org = useLoad(() => api.organization(), [version]);
  const sum = useLoad(() => api.summary(), [version]);
  const o = org.data, s = sum.data;
  return (
    <div className="stack">
      <ErrorMsg error={org.error || sum.error} />
      {s && (
        <div className="grid4">
          <div className="card stat"><div className="muted">Invoices</div><b>{s.invoice_counts.sent} sent</b><span className="small muted">{s.invoice_counts.draft} draft · {s.invoice_counts.paid} paid</span></div>
          <div className="card stat"><div className="muted">Overdue invoices</div><b>{s.overdue_count}</b><span className="small muted">as of {s.today}</span></div>
          <div className="card stat"><div className="muted">Unpaid (by currency)</div>{Object.entries(s.unpaid).map(([c, v]) => <b key={c} className="mid">{money(v, c)}</b>)}</div>
          <div className="card stat"><div className="muted">Overdue (by currency)</div>{Object.entries(s.overdue).map(([c, v]) => <b key={c} className="mid">{money(v, c)}</b>)}</div>
        </div>
      )}
      {o && (
        <div className="card">
          <h2>{o.name}</h2>
          <div className="cols">
            <dl>
              <dt>Address</dt><dd>{o.address}</dd>
              <dt>Accounts contact</dt><dd>{o.accounts_contact_name} · {o.accounts_email}</dd>
              <dt>Phone</dt><dd>{o.phone}</dd>
              <dt>Currencies</dt><dd>Reports in {o.default_currency}; accepts {o.accepted_currencies.join(", ")}</dd>
              <dt>Payment terms</dt><dd>{o.payment_terms}</dd>
              <dt>Payment methods</dt><dd>{o.payment_methods.join(", ")}</dd>
            </dl>
            <dl>
              <dt>Bank details</dt>
              <dd>{o.bank_details.account_name}<br />Account {o.bank_details.account_number}<br />{o.bank_details.bank}<br />IFSC {o.bank_details.ifsc} · SWIFT {o.bank_details.swift_code}</dd>
              <dt>Policies</dt>
              <dd><ul>{Object.entries(o.policies).map(([k, v]) => <li key={k}><b>{k.replace("_", " ")}:</b> {v}</li>)}</ul></dd>
            </dl>
          </div>
        </div>
      )}
    </div>
  );
}
