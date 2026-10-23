import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { api } from "./client.js";
import { buildEmails } from "../lib/derive.js";

const POLL_MS = 60_000;
const SCAN_POLL_MS = 3_000;
const SYNC_POLL_MS = 15_000;

const AgentDataContext = createContext(null);

export function AgentDataProvider({ children }) {
  const [state, setState] = useState(null); // raw nyp_export_state output
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [lastUpdated, setLastUpdated] = useState(null);
  const [scan, setScan] = useState({ status: "idle" }); // idle | running | completed | failed (+ progress)
  const [sync, setSync] = useState(null); // last Telegram approval sync from the API server
  const [notice, setNotice] = useState(null); // { severity, text }
  const [meta, setMeta] = useState({}); // message_id -> live Gmail metadata (memory only)
  const metaPending = useRef(new Set());
  const stateRef = useRef(null);

  const refresh = useCallback(async (force = false) => {
    setLoading(true);
    try {
      const data = await api.state(force);
      const before = stateRef.current?.counts?.quarantined;
      stateRef.current = data;
      setState(data);
      setError("");
      setLastUpdated(new Date());
      if (before !== undefined && data.counts.quarantined > before) {
        const n = data.counts.quarantined - before;
        setNotice({ severity: "warning", text: `${n} new suspicious email${n > 1 ? "s" : ""} quarantined.` });
      }
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    const id = setInterval(() => refresh(), POLL_MS);
    return () => clearInterval(id);
  }, [refresh]);

  // Telegram presses are picked up by the API server's approval sync; refresh when it ran.
  useEffect(() => {
    let lastAt = null;
    const tick = async () => {
      try {
        const s = await api.sync();
        setSync(s);
        if (s.at && s.at !== lastAt) {
          if (lastAt) refresh();
          lastAt = s.at;
        }
      } catch {
        /* the state error banner already covers an unreachable API */
      }
    };
    tick();
    const id = setInterval(tick, SYNC_POLL_MS);
    return () => clearInterval(id);
  }, [refresh]);

  // window: null = new mail; { period } or { start_date, end_date } = older mail, read in batches
  const startScan = useCallback(async (window = null) => {
    setScan({ status: "running", window, progress: null });
    setNotice({ severity: "info", text: window ? "Scanning older emails in batches. Progress shows on the Scan button." : "Scanning the inbox. This can take a minute." });
    let runId;
    try {
      runId = (await api.startScan(window)).run_id;
    } catch (e) {
      if (e.status === 409 && e.body?.run_id) {
        runId = e.body.run_id;
      } else {
        setScan({ status: "failed", error: e.message });
        setNotice({ severity: "error", text: `Scan could not start: ${e.message}` });
        return;
      }
    }
    let seenBatches = 0;
    for (;;) {
      await new Promise((r) => setTimeout(r, SCAN_POLL_MS));
      let s;
      try {
        s = await api.scanStatus(runId);
      } catch (e) {
        setScan({ status: "failed", error: e.message });
        setNotice({ severity: "error", text: `Lost track of the scan: ${e.message}` });
        return;
      }
      if (s.status === "running") {
        setScan({ status: "running", window, progress: s.progress || null });
        if ((s.progress?.batches || 0) > seenBatches) {   // show results as each batch lands
          seenBatches = s.progress.batches;
          refresh();
        }
        continue;
      }
      setScan({ ...s, finishedAt: new Date() });
      if (s.status === "completed") {
        setNotice({ severity: "success", text: s.text ? firstLine(s.text) : "Scan finished." });
      } else {
        setNotice({ severity: "error", text: `Scan failed: ${s.error || s.status}` });
      }
      await refresh(true);
      return;
    }
  }, [refresh]);

  const loadMeta = useCallback(async (ids) => {
    const missing = ids.filter((id) => !(id in meta) && !metaPending.current.has(id));
    if (!missing.length) return;
    missing.forEach((id) => metaPending.current.add(id));
    try {
      for (let i = 0; i < missing.length; i += 100) {
        const { messages } = await api.messages(missing.slice(i, i + 100));
        setMeta((m) => ({ ...m, ...Object.fromEntries(messages.map((x) => [x.id, x])) }));
      }
    } catch (e) {
      setMeta((m) => ({ ...m, ...Object.fromEntries(missing.map((id) => [id, { id, available: false, error: e.message }])) }));
    } finally {
      missing.forEach((id) => metaPending.current.delete(id));
    }
  }, [meta]);

  const emails = useMemo(() => (state ? buildEmails(state, meta) : []), [state, meta]);

  const value = {
    state, emails, loading, error, lastUpdated, refresh, scan, startScan, sync,
    notice, clearNotice: () => setNotice(null), loadMeta, meta,
  };
  return <AgentDataContext.Provider value={value}>{children}</AgentDataContext.Provider>;
}

function firstLine(text) {
  const line = text.replace(/[*#>`_]/g, "").split("\n").map((l) => l.trim()).find(Boolean) || "Scan finished.";
  return line.length > 180 ? `${line.slice(0, 177)}…` : line;
}

export function useAgentData() {
  const ctx = useContext(AgentDataContext);
  if (!ctx) throw new Error("useAgentData must be used inside AgentDataProvider");
  return ctx;
}

// Fetch live sender/subject/date for the given message ids (once each).
export function useMessageMeta(ids) {
  const { loadMeta } = useAgentData();
  const key = ids.join(",");
  useEffect(() => {
    if (key) loadMeta(key.split(","));
  }, [key, loadMeta]);
}
