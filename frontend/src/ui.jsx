import React, { useCallback, useEffect, useState } from "react";

export function useToast() {
  const [t, setT] = useState(null);
  const show = useCallback((msg, error = false) => {
    setT({ msg, error, id: Date.now() });
  }, []);
  useEffect(() => {
    if (!t) return;
    const h = setTimeout(() => setT(null), 5000);
    return () => clearTimeout(h);
  }, [t]);
  return { t, show };
}
export const Toast = ({ toast }) => (toast.t ? <div className={"toast " + (toast.t.error ? "bad" : "ok")}>{toast.t.msg}</div> : null);

export function useLoad(fn, deps) {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const reload = useCallback(async () => {
    setLoading(true);
    try {
      setData(await fn());
      setError("");
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  useEffect(() => { reload(); }, [reload]);
  return { data, error, loading, reload, setData };
}

export const Pill = ({ kind, children }) => <span className={"pill " + kind}>{children}</span>;
export const Empty = ({ children }) => <p className="muted empty">{children}</p>;
export const ErrorMsg = ({ error }) => (error ? <p className="error">{error}</p> : null);
