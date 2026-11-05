// The front end talks to the back end ONLY through this file. No database, mail server, LLM or secret is used here.
const BASE = import.meta.env.VITE_API_URL || "";
let token = sessionStorage.getItem("token") || "";
let onUnauthorized = () => {};

export const setUnauthorizedHandler = (fn) => (onUnauthorized = fn);
export const hasToken = () => !!token;
export function logout() {
  token = "";
  sessionStorage.removeItem("token");
}

export class ApiError extends Error {
  constructor(message, status, problems) {
    super(message);
    this.status = status;
    this.problems = problems || [];
  }
}

async function request(method, path, body, isForm = false) {
  const headers = {};
  if (token) headers.Authorization = `Bearer ${token}`;
  if (body && !isForm) headers["Content-Type"] = "application/json";
  const res = await fetch(BASE + path, { method, headers, body: isForm ? body : body ? JSON.stringify(body) : undefined });
  const data = await res.json().catch(() => ({}));
  if (res.status === 401 && path !== "/api/auth/login") {
    logout();
    onUnauthorized();
  }
  if (!res.ok) {
    const detail = Array.isArray(data.detail) ? data.detail.map((d) => d.msg).join("; ") : data.detail;
    throw new ApiError(detail || `Request failed (${res.status})`, res.status, data.problems);
  }
  return data;
}

export async function login(password) {
  const d = await request("POST", "/api/auth/login", { password });
  token = d.token;
  sessionStorage.setItem("token", token);
}

export const api = {
  status: () => request("GET", "/api/status"),
  summary: () => request("GET", "/api/summary"),
  organization: () => request("GET", "/api/organization"),
  customers: () => request("GET", "/api/customers"),
  setEmail: (id, email) => request("PATCH", `/api/customers/${id}`, { contact_email: email }),
  invoices: (q = "") => request("GET", `/api/invoices${q}`),
  invoice: (n) => request("GET", `/api/invoices/${n}`),
  reset: (keepEmails) => request("POST", "/api/admin/reset", { keep_emails: keepEmails }),
  sendReminders: (opts) => request("POST", "/api/collections/reminders/send", opts),
  reminders: () => request("GET", "/api/collections/reminders"),
  checkInbox: () => request("POST", "/api/collections/inbox/check"),
  conversations: () => request("GET", "/api/collections/conversations"),
  conversation: (id) => request("GET", `/api/collections/conversations/${id}`),
  needsAttention: () => request("GET", "/api/collections/needs-attention"),
  sendDraft: (id, body) => request("POST", `/api/collections/messages/${id}/send`, body),
  dismissDraft: (id) => request("POST", `/api/collections/messages/${id}/dismiss`),
  claims: () => request("GET", "/api/collections/claims"),
  upload: (file) => {
    const f = new FormData();
    f.append("file", file);
    return request("POST", "/api/reconciliation/statements", f, true);
  },
  statements: () => request("GET", "/api/reconciliation/statements"),
  statement: (id) => request("GET", `/api/reconciliation/statements/${id}`),
  rematch: (id) => request("POST", `/api/reconciliation/statements/${id}/rematch`),
  approve: (id, allocations) => request("POST", `/api/reconciliation/transactions/${id}/approve`, allocations ? { allocations } : {}),
  reject: (id) => request("POST", `/api/reconciliation/transactions/${id}/reject`),
  reopen: (id) => request("POST", `/api/reconciliation/transactions/${id}/reopen`),
  payments: () => request("GET", "/api/reconciliation/payments"),
};

const LOCALE = { INR: "en-IN", USD: "en-US", EUR: "en-US", GBP: "en-GB" };
export const money = (v, cur) =>
  new Intl.NumberFormat(LOCALE[cur] || "en-US", { style: "currency", currency: cur, minimumFractionDigits: 2 }).format(v ?? 0);
export const fdate = (d) => (d ? new Date(d + (d.length === 10 ? "T00:00:00" : "")).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" }) : "");
export const fdatetime = (d) => (d ? new Date(d).toLocaleString("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "");
