# Notes on the Section 9 documentation (Stripe and Sequence)

My own study notes, in my own words, written while reading the pages listed in Section 9 of the assignment.
They are a summary, not a substitute for the original docs.

## Revenue recognition (Stripe, Sequence)
* **Accrual accounting**: revenue is recognised when it is *earned* (the service is delivered), not when cash
  arrives or an invoice is sent. A customer paying 120 for a one-year subscription in January gives 10 of
  revenue per month for twelve months.
* **Deferred revenue** = invoiced (or paid) but not yet earned, a liability that shrinks as service is delivered.
  **Unbilled revenue** = earned but not yet invoiced (in-arrears billing, e.g. monthly usage billed afterwards).
  **Billed vs recognised revenue** are tracked separately; journals move amounts between them.
* **Performance obligation**: each distinct promise to a customer. Stripe treats every invoice line item and
  subscription item as its own obligation and amortises it across its service period (down to the second).
* **Recognition methods** (Sequence): straight-line (subscriptions), usage-based (as consumed), point-in-time
  (implementation or set-up fees, on a date) and milestone (when a deliverable is accepted).
* **Advance vs arrears billing**: advance creates deferred revenue; arrears creates unbilled revenue.
* **Discounts** are allocated across line items so each schedule uses the discounted net amount.
* **Credit notes** reverse revenue. In Sequence they either cancel future obligations (reducing deferred
  revenue first) or adjust already recognised revenue, depending on an account setting.
* **Minimum fees / true-ups**: if usage falls short of the committed minimum, a true-up line is added and
  recognised at a point in time so the minimum is met.
* Stripe also covers refunds, disputes, tax handling (tax is not revenue), FX/multi-currency and reports
  (period summaries, waterfall) that can be audited down to transactions.

## Contracts, billing schedules, phases, renewals (Sequence)
* A **contract/quote** becomes a **billing schedule**: what is billed, how often, in advance or arrears, and for
  how long. **Phases** split a schedule over time (trial, year 1, year 2, ramped pricing), and renewals continue
  or replace a schedule. Schedules can mix frequencies (annual fee in advance plus monthly usage in arrears).
  In-advance billing does not combine with usage-based pricing; credits are used to prepay usage.

## Pricing models and invoicing (Sequence)
* **Product catalogue** holds products and **prices** (list prices and variants per customer).
* Models: standard/recurring, **usage-based** (metered events priced by tiers, volume or graduated),
  **seat-based** (per seat with proration on upgrades/downgrades), percentage-based, and **minimum commitments**.
* **Invoices** are generated from schedules or one-off; **credit notes** correct them; **usage metering**
  ingests events that are priced at period end.

## How this relates to the assignment
The invoices in the data are one-off, in-arrears style invoices with a status (draft/sent/paid). Collecting and
reconciling payments changes the *billed vs. paid* position (accounts receivable), not recognised revenue.
A TDS-closed invoice is settled with a statutory deduction, which in accounting is a receivable settled
by cash plus a tax asset, not a discount (so I record it as `tds_deducted`, separate from `amount_paid`).
