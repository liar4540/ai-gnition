import { useMemo, useState } from "react";
import { Box, Card, Chip, Stack, Tab, Table, TableBody, TableCell, TableContainer, TableHead, TableRow, Tabs,
  Typography, useMediaQuery } from "@mui/material";
import { useTheme } from "@mui/material/styles";
import { useAgentData } from "../api/AgentData.jsx";
import { ConfidenceMeter, DecisionChip } from "../components/Badges.jsx";
import { useEmailParam } from "../components/EmailDetailDrawer.jsx";
import { EmptyState, ErrorBanner, LoadingRows } from "../components/States.jsx";
import { ACTION_LABEL, wasRejected } from "../lib/derive.js";
import { formatDeadline } from "../lib/format.js";

const TASK_STATUS = {
  open: { label: "Open", color: "info" },
  pending_approval: { label: "Awaiting approval", color: "warning" },
  on_hold: { label: "On hold", color: "warning" },
  done: { label: "Done", color: "success" },
  rejected: { label: "Rejected", color: "error" },
  cancelled: { label: "Cancelled", color: "default" },
};
const TABS = ["all", "pending_approval", "open", "on_hold", "done", "rejected"];

export default function Tasks() {
  const theme = useTheme();
  const wide = useMediaQuery(theme.breakpoints.up("md"));
  const { state, loading } = useAgentData();
  const { open } = useEmailParam();
  const [tab, setTab] = useState("all");

  const rows = useMemo(() => {
    if (!state) return [];
    const byTask = new Map();
    for (const a of state.approvals) {
      if (!byTask.has(a.task_id)) byTask.set(a.task_id, []);
      byTask.get(a.task_id).push(a);
    }
    return state.tasks
      .map((t) => {
        const approvals = byTask.get(t.task_id) || [];
        return { ...t, approvals, status: wasRejected(t, approvals) ? "rejected" : t.status };
      })
      .filter((t) => tab === "all" || t.status === tab)
      .reverse();
  }, [state, tab]);

  const firstMessage = (t) => (t.source_message_ids || "").split(",").filter(Boolean).pop();

  return (
    <Box>
      <Typography variant="h1" sx={{ mb: 2 }}>Tasks</Typography>
      <ErrorBanner />
      <Card>
        <Tabs value={tab} onChange={(_, v) => setTab(v)} variant="scrollable" allowScrollButtonsMobile
              sx={{ borderBottom: 1, borderColor: "divider", px: 1 }} aria-label="Task status">
          {TABS.map((t) => <Tab key={t} value={t} label={t === "all" ? "All" : TASK_STATUS[t].label} />)}
        </Tabs>
        {!state && loading ? <Box sx={{ p: 2 }}><LoadingRows /></Box> : !rows.length ? (
          <EmptyState title="No tasks here" text="Important emails become tasks after a scan." action={tab === "all"} />
        ) : wide ? (
          <TableContainer>
            <Table size="small" aria-label="Tasks">
              <TableHead>
                <TableRow>
                  <TableCell>Task</TableCell><TableCell>Deadline</TableCell><TableCell>Status</TableCell>
                  <TableCell>Confidence</TableCell><TableCell>What changed</TableCell><TableCell>Approvals</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {rows.map((t) => (
                  <TableRow key={t.task_id} hover tabIndex={0} sx={{ cursor: "pointer" }}
                            onClick={() => firstMessage(t) && open(firstMessage(t))}
                            onKeyDown={(e) => { if (e.key === "Enter" && firstMessage(t)) open(firstMessage(t)); }}>
                    <TableCell sx={{ maxWidth: 280 }}>
                      <Typography variant="body2" fontWeight={600}>{t.title}</Typography>
                      {t.reason && <Typography variant="caption" color="text.secondary">{t.reason}</Typography>}
                    </TableCell>
                    <TableCell sx={{ whiteSpace: "nowrap" }}>{formatDeadline(t.deadline) || "–"}</TableCell>
                    <TableCell>
                      <Chip size="small" variant="outlined" label={TASK_STATUS[t.status]?.label || t.status}
                            color={TASK_STATUS[t.status]?.color || "default"} />
                    </TableCell>
                    <TableCell><ConfidenceMeter value={t.confidence} band={t.confidence >= 90 ? "high" : t.confidence >= 70 ? "medium" : "low"} compact /></TableCell>
                    <TableCell sx={{ maxWidth: 220 }}><Typography variant="body2">{t.delta || "–"}</Typography></TableCell>
                    <TableCell>
                      <Stack spacing={0.5}>
                        {t.approvals.length ? t.approvals.slice(-2).map((a) => (
                          <Stack key={a.approval_id} direction="row" spacing={0.75} sx={{ alignItems: "center" }}>
                            <Typography variant="caption">{ACTION_LABEL[a.action]}</Typography>
                            <DecisionChip decision={a.decision} />
                          </Stack>
                        )) : <Typography variant="body2" color="text.secondary">–</Typography>}
                      </Stack>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
        ) : (
          <Stack divider={<Box sx={{ borderTop: 1, borderColor: "divider" }} />}>
            {rows.map((t) => (
              <Box key={t.task_id} sx={{ p: 2, cursor: "pointer" }} role="button" tabIndex={0}
                   onClick={() => firstMessage(t) && open(firstMessage(t))}
                   onKeyDown={(e) => { if (e.key === "Enter" && firstMessage(t)) open(firstMessage(t)); }}>
                <Typography variant="body1" fontWeight={600}>{t.title}</Typography>
                <Stack direction="row" spacing={1} useFlexGap sx={{ mt: 1, flexWrap: "wrap", alignItems: "center" }}>
                  <Chip size="small" variant="outlined" label={TASK_STATUS[t.status]?.label || t.status}
                        color={TASK_STATUS[t.status]?.color || "default"} />
                  {t.deadline && <Typography variant="body2" color="text.secondary">Due {formatDeadline(t.deadline)}</Typography>}
                </Stack>
                {t.delta && <Typography variant="body2" sx={{ mt: 1 }}>Changed: {t.delta}</Typography>}
              </Box>
            ))}
          </Stack>
        )}
      </Card>
    </Box>
  );
}
