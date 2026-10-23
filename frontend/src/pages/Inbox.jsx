import { useDeferredValue, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Box, Card, FormControl, InputAdornment, InputLabel, MenuItem, Select, Stack, Tab, Tabs, TextField,
  Typography } from "@mui/material";
import SearchIcon from "@mui/icons-material/Search";
import { useAgentData, useMessageMeta } from "../api/AgentData.jsx";
import EmailTable from "../components/EmailTable.jsx";
import { useEmailParam } from "../components/EmailDetailDrawer.jsx";
import { EmptyState, ErrorBanner, LoadingRows } from "../components/States.jsx";
import { STATUS } from "../lib/derive.js";
import { matchesQuery, sortEmails } from "../lib/sort.js";

const TABS = [
  { id: "all", label: "All" },
  { id: "important", label: "Important" },
  { id: "routine", label: "Routine" },
  { id: "suspicious", label: "Suspicious" },
];

export default function Inbox() {
  const { emails, state, loading } = useAgentData();
  const { open } = useEmailParam();
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") || "all";
  const query = params.get("q") || "";
  const deferredQuery = useDeferredValue(query);
  const [risk, setRisk] = useState("any");
  const [status, setStatus] = useState("any");
  const [sort, setSort] = useState({ by: "received", dir: "desc" });

  useMessageMeta(useMemo(() => emails.map((e) => e.message_id), [emails]));

  const setParam = (key, value) => {
    const next = new URLSearchParams(params);
    if (value && value !== "all") next.set(key, value);
    else next.delete(key);
    setParams(next, { replace: true });
  };

  const counts = useMemo(() => ({
    all: emails.length,
    important: emails.filter((e) => e.category === "important").length,
    routine: emails.filter((e) => e.category === "routine").length,
    suspicious: emails.filter((e) => e.category === "suspicious").length,
  }), [emails]);

  const rows = useMemo(() => {
    const filtered = emails.filter((e) =>
      (tab === "all" || e.category === tab) &&
      (risk === "any" || e.risk === risk) &&
      (status === "any" || e.status === status) &&
      matchesQuery(e, deferredQuery));
    return sortEmails(filtered, sort.by, sort.dir);
  }, [emails, tab, risk, status, deferredQuery, sort]);

  const onSort = (by) => setSort((s) => ({ by, dir: s.by === by && s.dir === "desc" ? "asc" : "desc" }));
  const presentStatuses = [...new Set(emails.map((e) => e.status))];

  return (
    <Box>
      <Typography variant="h1" sx={{ mb: 2 }}>Inbox</Typography>
      <ErrorBanner />
      <Card>
        <Tabs value={tab} onChange={(_, v) => setParam("tab", v)} variant="scrollable" allowScrollButtonsMobile
              sx={{ borderBottom: 1, borderColor: "divider", px: 1 }} aria-label="Email category">
          {TABS.map((t) => <Tab key={t.id} value={t.id} label={`${t.label} (${counts[t.id]})`} />)}
        </Tabs>
        <Stack direction={{ xs: "column", md: "row" }} spacing={1.5} sx={{ p: 2 }}>
          <TextField
            size="small" label="Search" value={query} onChange={(e) => setParam("q", e.target.value)}
            placeholder="Sender, subject, task or evidence" sx={{ flexGrow: 1 }}
            slotProps={{ input: { startAdornment: <InputAdornment position="start"><SearchIcon fontSize="small" /></InputAdornment> } }}
          />
          <FormControl size="small" sx={{ minWidth: 150 }}>
            <InputLabel id="risk-filter">Risk</InputLabel>
            <Select labelId="risk-filter" label="Risk" value={risk} onChange={(e) => setRisk(e.target.value)}>
              <MenuItem value="any">Any risk</MenuItem>
              <MenuItem value="high">High</MenuItem>
              <MenuItem value="medium">Medium</MenuItem>
              <MenuItem value="low">Low</MenuItem>
            </Select>
          </FormControl>
          <FormControl size="small" sx={{ minWidth: 180 }}>
            <InputLabel id="status-filter">Status</InputLabel>
            <Select labelId="status-filter" label="Status" value={status} onChange={(e) => setStatus(e.target.value)}>
              <MenuItem value="any">Any status</MenuItem>
              {presentStatuses.map((s) => <MenuItem key={s} value={s}>{STATUS[s]?.label || s}</MenuItem>)}
            </Select>
          </FormControl>
        </Stack>
        <Typography variant="body2" color="text.secondary" role="status" sx={{ px: 2, pb: 1 }}>
          {state ? `${rows.length} of ${emails.length} emails` : ""}
        </Typography>
        {!state && loading ? (
          <Box sx={{ p: 2 }}><LoadingRows /></Box>
        ) : !emails.length ? (
          <EmptyState title="No emails analysed yet" text="Run a scan to read the inbox and classify new mail." />
        ) : !rows.length ? (
          <EmptyState title="No emails match" text="Try another tab, filter or search." action={false} />
        ) : (
          <EmailTable emails={rows} sort={sort} onSort={onSort} onOpen={open} />
        )}
      </Card>
    </Box>
  );
}
