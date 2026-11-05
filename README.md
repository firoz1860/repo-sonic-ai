# AcmeCorp Collections Agent & Bank Reconciliation

Take-home for the Sonic AI Software Engineering Intern role. An application for AcmeCorp's Accounts Team that
(1) chases overdue invoices by email, reads replies and answers routine questions with an LLM, and
(2) suggests which invoice each bank receipt pays, with a person approving every decision.

Author: Firoz Ahmad. Explanation document: [`explanation.pdf`](explanation.pdf). Study notes for Section 9: [`docs/CONCEPTS.md`](docs/CONCEPTS.md).

## What is finished (honest status)

| Area | Status |
|---|---|
| Part A: organization, customers, invoices (overdue + unpaid), edit customer email, Reset demo data | Done |
| Part B: SMTP reminders, IMAP reading (auto-poll + "Check inbox now"), threading, LLM answers, validation of every figure, Needs attention list with edit and send | Done |
| Part C: CSV upload, local fuzzy matching, score + reason, approve / reject / choose invoice, TDS close vs leave open, balances update, agent stops chasing | Done |
| Bonus: email evidence linked to bank row, one payment for several invoices (split on approval), out-of-office and bounce detection | Done |
| Tests | 65 automated tests (`pytest`), see "Testing" for what they do and do not cover |

**Known limits** (also listed at the end): sentence-embedding matching is not used; there is no undo for an approved
payment; single admin login (no per-user accounts); IMAP/SMTP were verified in my build environment only against a
local SMTP test server and mocked IMAP, so run the end-to-end steps below once with your own mailboxes.

## Architecture

```
frontend/ (React + Vite)  --HTTP/JSON-->  backend/ (FastAPI)  -->  MongoDB
  pages only, no secrets                  routes -> services -> logic / repositories
                                          services also talk to: SMTP+IMAP mail server, LLM API
```

```
backend/app/
  routes/        HTTP layer: auth, organization/customers/invoices, collections, reconciliation
  services/      use cases: agent_service (reminders, inbox, drafts), reconciliation_service,
                 email_service (SMTP/IMAP), llm_service, seed_service
  logic/         pure business rules, no I/O: money, invoices (overdue, TDS), matching/ (narration parser,
                 similarity, engine), collections/ (facts, templates, validator, rules, parser, automated)
  repositories/  store.py: the only code that reads/writes MongoDB
  security.py    token auth, login rate limit
```

The front end only calls the API (`frontend/src/api.js`). It never sees the database, mail server, LLM or any key.

### Database (MongoDB collections)
`organization`, `customers` (unique `customer_id`), `invoices` (unique `invoice_number`, includes `amount_paid`,
`balance_due`, `tds_deducted`, `status`), `conversations`, `messages` (inbound, reminders, auto-replies and drafts, with
`status`: received / answered / needs_attention / pending_review / sent / dismissed / automated), `claims` ("I paid" emails),
`reminders`, `statements`, `transactions` (bank rows with the stored suggestion and status), `payments` (audit trail of
approved allocations), `inbox_seen` (Message-ID dedupe). Money is stored in major units, rounded to 2 decimals;
all arithmetic uses `Decimal`.

## Run it end to end

Requirements: Python 3.11+, Node 18+, MongoDB (Docker or Atlas), two email accounts, optionally an LLM key.

### 1. MongoDB
```bash
docker compose up -d          # local MongoDB on :27017
# or use a MongoDB Atlas free cluster and put its URI in MONGODB_URI
```

### 2. Configure
```bash
cp .env.example .env          # in the repo root, then edit it
```
* `ADMIN_PASSWORD`, `SECRET_KEY`: required (the server refuses to start without them).
* **Organization mailbox** (`SMTP_*`, `IMAP_*`): create a new Gmail (or other) account. For Gmail turn on 2-step
  verification and create an *App password*; use `smtp.gmail.com:465` (ssl) and `imap.gmail.com:993`.
* **Customer mailbox**: a second account you will reply from. Put its address on a customer in the UI.
* **LLM** (optional): set `LLM_PROVIDER=openai` with `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` for any OpenAI-compatible
  API (examples with free tiers: Groq `https://api.groq.com/openai/v1`, Google Gemini
  `https://generativelanguage.googleapis.com/v1beta/openai`; pick a current model name from the provider's docs),
  or `LLM_PROVIDER=anthropic`. With `LLM_PROVIDER=none` the agent runs in a rule-based offline mode (keyword
  classification plus the same verified templates), which is useful for testing without a key.
* `EMAIL_DRY_RUN=true` logs emails instead of sending them.

### 3. Back end
```bash
cd backend
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app_factory --factory --port 8000
```
On first start the data in `/data` is loaded automatically (`python -m scripts.seed` reloads it). Interactive API docs:
http://localhost:8000/docs (use the *Authorize* button after `POST /api/auth/login`).

### 4. Front end
```bash
cd frontend
npm install
npm run dev                  # http://localhost:5173 (proxies /api to :8000)
```
For a production build: `npm run build`, set `VITE_API_URL` and `FRONTEND_ORIGIN` accordingly.

### 5. Try it
1. Sign in with `ADMIN_PASSWORD`. **Customers**: set e.g. Kestrel Foods' email to your customer inbox.
2. **Collections > Preview reminders**, then **Send reminders now**. Check the customer inbox: invoice numbers, amounts and due dates come
   from the database. Customers still on `example.com` are skipped (they cannot receive mail).
3. Reply from the customer inbox: *"Is this the only amount pending from us? Please send bank details."* then press
   **Check inbox now** (or wait for the poller). The agent answers automatically.
   Then try: "We paid INV-1004 yesterday, reference N123456789" (noted, never marked paid); "Can we get more time?" or
   "I will take legal action" (goes to **Needs attention**: edit and send); a question about another company; an out-of-office auto-reply.
4. **Bank reconciliation > Upload** `data/bank_statement_sample.csv`. Approve rows; the invoice balance changes, a fully paid
   invoice becomes `paid` and disappears from reminders and from the agent's context. For the TDS and part-payment rows choose
   *Close invoice (TDS deducted)* or *Leave balance open*.
5. **Reset demo data** restores the original data and clears conversations, statements and payments (you are asked whether to keep the email addresses you edited).

`AS_OF_DATE=2026-11-05` pins "today" if you want the overdue list to match the examples in this document (the default is the real date in IST).

## Testing
```bash
cd backend && pip install -r requirements.txt && pytest
```
65 tests. They use an in-memory MongoDB (`mongomock`), a fake mail transport, a mocked LLM HTTP endpoint and a real local
SMTP server (aiosmtpd). They cover the sample statement and many variants, every approval rule, reminder content,
reply correctness, validation failures, escalation paths, prompt-injection and cross-customer leakage checks, auth,
rate limiting and header injection. They do **not** exercise a real MongoDB server, a real IMAP server or a real LLM.

## API (all under `/api`, bearer token except `health` and `auth/login`)

| Method and path | Purpose |
|---|---|
| `POST /auth/login` | `{password}` -> `{token}` (rate limited) |
| `GET /status`, `GET /summary`, `GET /organization` | environment info, dashboard totals, organization |
| `GET /customers`, `GET /customers/{id}`, `PATCH /customers/{id}` | list/detail; edit `contact_email` |
| `GET /invoices?customer_id&status&overdue`, `GET /invoices/{no}` | invoices with `is_overdue`, `unpaid_amount`; detail with payments and claims |
| `POST /admin/reset` | `{keep_emails}` restore data, clear conversations/payments/statements |
| `POST /collections/reminders/send` | `{customer_id?, dry_run?, force?}` |
| `POST /collections/inbox/check` | read IMAP now |
| `GET /collections/conversations`, `GET /collections/conversations/{id}` | threads |
| `GET /collections/needs-attention` | drafts waiting for a person |
| `POST /collections/messages/{id}/send` | `{subject?, body, force?}` edit and send a draft (422 with `problems` if figures do not match) |
| `POST /collections/messages/{id}/dismiss`, `GET /collections/claims` | dismiss; payment claims noted from emails |
| `POST /reconciliation/statements` | multipart CSV upload -> rows with suggestions |
| `GET /reconciliation/statements[/{id}]`, `POST /reconciliation/statements/{id}/rematch` | list, view, recompute |
| `POST /reconciliation/transactions/{id}/approve` | `{allocations?: [{invoice_number, amount?, action?: close_tds\|leave_open}]}` (empty body = approve the suggestion) |
| `POST /reconciliation/transactions/{id}/reject`, `.../reopen` | decisions |
| `GET /reconciliation/payments` | audit trail |

## Key decisions and assumptions
* **Overdue** = status `sent`, balance > 0, due date before today. Drafts are never chased, answered about or matched.
* **TDS** = 10% of the invoice subtotal (assignment definition), Indian GST invoices only. For discounted invoices (INV-1012) 10% of the discounted subtotal is also accepted when matching. Closing an invoice with TDS records `tds_deducted` separately from `amount_paid`; if the shortfall differs from the expected TDS the approval still works but shows a warning.
* **One reminder email per customer** listing its overdue invoices; the same invoice is not re-reminded within `REMINDER_MIN_DAYS`.
* **Customer identity** is the From address matched against the customer's stored email, so each address must be unique to one customer; replies from unknown senders get nothing but a human-reviewed holding draft. Failed SPF/DKIM/DMARC headers are escalated.
* **Replies always go to the address on record**, never to Reply-To.
* **Reset** restores the original files as the assignment asks; because the originals use `example.com`, you are asked whether to keep the addresses you edited.
* Overpayments are not auto-applied; the person must split them or pick another invoice.
* Local time zone for "today": Asia/Kolkata.

## Security measures
Password login with signed, expiring tokens and login rate limiting; every data route requires auth; CORS limited to the front-end origin; strict request validation (pydantic types stop Mongo operator injection); uploads limited in size, rows and type; plain-text emails only; CR/LF stripped from headers; HTML in inbound mail is stripped; secrets only in `.env` (git-ignored; `.env.example` has placeholders); the LLM receives only the sender's own facts, treats email as untrusted data, and its output is validated against the database before sending; deterministic keyword rules overrule the model on legal/dispute/extension; loop protection (per-conversation auto-reply cap, own-address, auto-submitted and bounce detection, Message-ID dedupe); atomic status transitions prevent double approval or double sending; errors do not leak internals.

## Known limitations
* No sentence-embedding model (see explanation.pdf for why); no undo/reversal of approved payments; single shared admin login; no pagination for very large statements (limit 2000 rows); Gmail-style IMAP is polled, not IDLE-pushed.
* The offline rule-based mode is deliberately conservative: it escalates anything it cannot classify.

## AI assistance
AI tools were used to help build this project (allowed by the assignment). I reviewed the code and can explain every part.
