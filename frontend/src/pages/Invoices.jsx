import React, { useState } from "react";
import { api, fdate, money } from "../api.js";
import { ErrorMsg, Pill, useLoad } from "../ui.jsx";

export default function Invoices({ version }) {
  const [filter, setFilter] = useState("all");
  const [open, setOpen] = useState(null);
  const { data, error } = useLoad(() => api.invoices(), [version]);
  const detail = useLoad(() => (open ? api.invoice(open) : Promise.resolve(null)), [open, version]);
  const rows = (data || []).filter((i) =>
    filter === "all" ? true : filter === "overdue" ? i.is_overdue : filter === "unpaid" ? i.status === "sent" && i.balance_due > 0 : i.status === filter);
  return (
    <div className="card">
      <div className="row between">
        <h2>Invoices</h2>
        <div className="row">
          {["all", "overdue", "unpaid", "draft", "sent", "paid"].map((f) => (
            <button key={f} className={filter === f ? "active chip" : "chip"} onClick={() => setFilter(f)}>{f}</button>
          ))}
        </div>
      </div>
      <ErrorMsg error={error} />
      <div className="scroll">
        <table>
          <thead><tr><th>Invoice</th><th>Customer</th><th>Date</th><th>Due</th><th>Status</th><th className="num">Total</th><th className="num">Paid</th><th className="num">Unpaid</th></tr></thead>
          <tbody>
            {rows.map((i) => (
              <React.Fragment key={i.invoice_number}>
                <tr className="click" onClick={() => setOpen(open === i.invoice_number ? null : i.invoice_number)}>
                  <td><b>{i.invoice_number}</b>{i.po_number && <div className="small muted">PO {i.po_number}</div>}</td>
                  <td>{i.customer_name}</td>
                  <td>{fdate(i.invoice_date)}</td>
                  <td>{fdate(i.due_date)}{i.is_overdue && <div className="small red">{i.days_overdue} days overdue</div>}</td>
                  <td>{i.is_overdue ? <Pill kind="red">overdue</Pill> : <Pill kind={i.status === "paid" ? "green" : i.status === "draft" ? "grey" : "blue"}>{i.status}</Pill>}</td>
                  <td className="num">{money(i.total, i.currency)}</td>
                  <td className="num">{money(i.amount_paid, i.currency)}</td>
                  <td className="num"><b>{i.status === "sent" ? money(i.unpaid_amount, i.currency) : "-"}</b>{i.tds_deducted > 0 && <div className="small muted">TDS closed {money(i.tds_deducted, i.currency)}</div>}</td>
                </tr>
                {open === i.invoice_number && (
                  <tr><td colSpan="8" className="detail">
                    <table className="inner"><thead><tr><th>Item</th><th className="num">Qty</th><th className="num">Unit price</th><th className="num">Amount</th></tr></thead>
                      <tbody>{i.line_items.map((l, k) => <tr key={k}><td>{l.description}</td><td className="num">{l.quantity}</td><td className="num">{money(l.unit_price, i.currency)}</td><td className="num">{money(l.amount, i.currency)}</td></tr>)}</tbody></table>
                    <div className="small">Subtotal {money(i.subtotal, i.currency)} · Discount {money(i.discount, i.currency)} · {i.tax_name} {money(i.tax, i.currency)} · <b>Total {money(i.total, i.currency)}</b> · {i.payment_terms}
                      {i.tds_amount > 0 && <> · TDS at 10% of subtotal: {money(i.tds_amount, i.currency)}</>}</div>
                    {detail.data && detail.data.invoice_number === i.invoice_number && (<>
                      {detail.data.payments.map((p) => <div key={p.payment_id} className="small green">Payment {money(p.amount, i.currency)}{p.tds_closed > 0 && ` + TDS closed ${money(p.tds_closed, i.currency)}`} · ref {p.reference || "-"} · {fdate(p.txn_date)}</div>)}
                      {detail.data.claims.map((c) => <div key={c.claim_id} className="small warn">Customer says paid{c.claimed_date && ` on ${fdate(c.claimed_date)}`}{c.reference && ` (ref ${c.reference})`} - unverified until the bank statement confirms it.</div>)}
                    </>)}
                  </td></tr>
                )}
              </React.Fragment>
            ))}
          </tbody>
        </table>
      </div>
      <p className="small muted">{rows.length} invoices. Overdue = sent, unpaid balance, due date before today. Drafts are not owed by the customer.</p>
    </div>
  );
}
