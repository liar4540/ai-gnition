import { useMemo } from "react";
import { Link as RouterLink } from "react-router-dom";
import { Box, Button, Card, CardContent, CardHeader, Chip, Grid, List, ListItemButton, ListItemText, Stack,
  Typography } from "@mui/material";
import MarkEmailReadIcon from "@mui/icons-material/MarkEmailRead";
import GppBadIcon from "@mui/icons-material/GppBad";
import HourglassTopIcon from "@mui/icons-material/HourglassTop";
import TaskAltIcon from "@mui/icons-material/TaskAlt";
import EventAvailableIcon from "@mui/icons-material/EventAvailable";
import VerifiedUserIcon from "@mui/icons-material/VerifiedUser";
import { useAgentData, useMessageMeta } from "../api/AgentData.jsx";
import StatCard from "../components/StatCard.jsx";
import HowToTry from "../components/HowToTry.jsx";
import { DecisionChip, EvidenceBadge, RiskChip } from "../components/Badges.jsx";
import { useEmailParam } from "../components/EmailDetailDrawer.jsx";
import { useTaskParam } from "../components/TaskDetailDrawer.jsx";
import { EmptyState, ErrorBanner, LoadingRows } from "../components/States.jsx";
import { ACTION_LABEL, parseSender } from "../lib/derive.js";
import { formatDateTime, timeAgo } from "../lib/format.js";

function Panel({ title, action, children }) {
  return (
    <Card sx={{ height: "100%" }}>
      <CardHeader title={title} action={action} slotProps={{ title: { variant: "h2", component: "h2" } }}
                  sx={{ pb: 0 }} />
      <CardContent>{children}</CardContent>
    </Card>
  );
}

function CategoryBreakdown({ counts }) {
  const parts = [
    { key: "important", label: "Important", color: "primary.main" },
    { key: "routine", label: "Routine", color: "text.disabled" },
    { key: "suspicious", label: "Suspicious", color: "error.main" },
  ];
  const total = counts.analysed || 0;
  return (
    <Box>
      <Box sx={{ display: "flex", height: 12, borderRadius: 6, overflow: "hidden", bgcolor: "action.hover" }}
           role="img" aria-label={parts.map((p) => `${counts[p.key]} ${p.label}`).join(", ")}>
        {total > 0 && parts.map((p) => (
          <Box key={p.key} sx={{ width: `${(counts[p.key] / total) * 100}%`, bgcolor: p.color }} />
        ))}
      </Box>
      <Stack direction="row" spacing={3} useFlexGap sx={{ mt: 2, flexWrap: "wrap" }}>
        {parts.map((p) => (
          <Box key={p.key} component={RouterLink} to={`/inbox?tab=${p.key}`}
               sx={{ textDecoration: "none", color: "inherit", "&:hover .lbl": { textDecoration: "underline" } }}>
            <Stack direction="row" spacing={1} sx={{ alignItems: "center" }}>
              <Box sx={{ width: 10, height: 10, borderRadius: "50%", bgcolor: p.color }} />
              <Typography className="lbl" variant="body2" color="text.secondary">{p.label}</Typography>
            </Stack>
            <Typography sx={{ fontSize: "1.25rem", fontWeight: 700 }}>
              {counts[p.key]}
              <Typography component="span" variant="body2" color="text.secondary">
                {" "}{total ? Math.round((counts[p.key] / total) * 100) : 0}%
              </Typography>
            </Typography>
          </Box>
        ))}
      </Stack>
    </Box>
  );
}

export default function Dashboard() {
  const { state, emails, loading, sync } = useAgentData();
  const { open } = useEmailParam();
  const { open: openTask } = useTaskParam();
  const threats = useMemo(() => emails.filter((e) => e.quarantined).slice(-5).reverse(), [emails]);
  const pending = useMemo(() => {
    if (!state) return [];
    const tasks = new Map(state.tasks.map((t) => [t.task_id, t]));
    return state.approvals.filter((a) => a.decision === "pending").map((a) => ({ ...a, task: tasks.get(a.task_id) }));
  }, [state]);
  const decided = useMemo(() => {
    if (!state) return [];
    const tasks = new Map(state.tasks.map((t) => [t.task_id, t]));
    return state.approvals.filter((a) => a.decision !== "pending" && a.decided_at)
      .sort((x, y) => y.decided_at.localeCompare(x.decided_at)).slice(0, 3)
      .map((a) => ({ ...a, task: tasks.get(a.task_id) }));
  }, [state]);
  useMessageMeta(useMemo(() => threats.map((t) => t.message_id), [threats]));

  if (!state) {
    return (
      <Box>
        <Typography variant="h1" sx={{ mb: 2 }}>Dashboard</Typography>
        <ErrorBanner />
        <HowToTry />
        {loading && <LoadingRows rows={6} />}
      </Box>
    );
  }
  const c = state.counts;

  return (
    <Box>
      <Stack direction={{ xs: "column", sm: "row" }} spacing={2} sx={{ mb: 2, alignItems: { sm: "center" } }}>
        <Typography variant="h1" sx={{ flexGrow: 1 }}>Dashboard</Typography>
      </Stack>
      <ErrorBanner />
      <HowToTry />

      {c.analysed === 0 ? (
        <Card><EmptyState title="No emails analysed yet" text="Scan the inbox to classify mail, flag threats and create tasks." /></Card>
      ) : (
        <Grid container spacing={2}>
          {[
            { label: "Emails analysed", value: c.analysed, icon: <MarkEmailReadIcon />, tone: "primary", to: "/inbox" },
            { label: "Threats quarantined", value: c.quarantined, icon: <GppBadIcon />, tone: c.quarantined ? "error" : "neutral", to: "/threats" },
            { label: "Awaiting approval", value: c.approvals_pending, icon: <HourglassTopIcon />, tone: c.approvals_pending ? "warning" : "neutral", hint: "Approve on Telegram" },
            { label: "Open tasks", value: c.tasks_pending, icon: <TaskAltIcon />, tone: "primary", to: "/tasks", hint: `${c.tasks_done} done` },
            { label: "Events added", value: c.events_added, icon: <EventAvailableIcon />, tone: "success", hint: `${c.replies_sent} replies sent` },
            { label: "Actions without approval", value: c.actions_without_approval, icon: <VerifiedUserIcon />, tone: c.actions_without_approval ? "error" : "success", hint: "Every send needs your approval" },
          ].map((s) => (
            <Grid key={s.label} size={{ xs: 12, sm: 6, lg: 4, xl: 2 }}><StatCard {...s} /></Grid>
          ))}

          <Grid size={{ xs: 12, lg: 5 }}>
            <Panel title="Categories"><CategoryBreakdown counts={c} /></Panel>
          </Grid>

          <Grid size={{ xs: 12, lg: 7 }}>
            <Panel title="Threat alerts" action={<Button component={RouterLink} to="/threats" size="small">View all</Button>}>
              {threats.length ? (
                <List disablePadding>
                  {threats.map((t) => {
                    const s = parseSender(t.from);
                    return (
                      <ListItemButton key={t.message_id} onClick={() => open(t.message_id)} divider sx={{ px: 1, alignItems: "flex-start" }}>
                        <ListItemText
                          primary={t.metaState === "ok" ? (t.subject || "(no subject)") : "Quarantined email"}
                          secondary={
                            <Stack spacing={0.75} component="span" sx={{ mt: 0.5, display: "flex" }}>
                              <Typography variant="caption" component="span" color="text.secondary">
                                {t.metaState === "ok" ? s.address : ""} {t.receivedAt ? `· ${formatDateTime(t.receivedAt)}` : ""}
                              </Typography>
                              <Stack direction="row" spacing={0.75} useFlexGap component="span" sx={{ flexWrap: "wrap" }}>
                                <RiskChip risk={t.risk} />
                                {t.evidence.filter((e) => e.severity !== "safe").slice(0, 2).map((e) => <EvidenceBadge key={e.code} item={e} />)}
                              </Stack>
                            </Stack>
                          }
                          slotProps={{ primary: { fontWeight: 600, noWrap: true }, secondary: { component: "span" } }}
                        />
                      </ListItemButton>
                    );
                  })}
                </List>
              ) : (
                <Stack direction="row" spacing={1} sx={{ alignItems: "center", color: "success.main" }}>
                  <VerifiedUserIcon fontSize="small" />
                  <Typography variant="body2">No threats detected.</Typography>
                </Stack>
              )}
            </Panel>
          </Grid>

          <Grid size={{ xs: 12, lg: 6 }}>
            <Panel title="Waiting for your approval">
              {pending.length ? (
                <List disablePadding>
                  {pending.map((a) => {
                    return (
                      <ListItemButton key={a.approval_id} divider sx={{ px: 1 }} onClick={() => openTask(a.task_id)}>
                        <ListItemText primary={a.task?.title || `Task ${a.task_id}`}
                                      secondary={`${ACTION_LABEL[a.action] || a.action} · expires ${timeAgo(a.expires_at)}`}
                                      slotProps={{ primary: { fontWeight: 600 } }} />
                        <Chip size="small" label="Review draft" color="warning" variant="outlined" icon={<HourglassTopIcon />} />
                      </ListItemButton>
                    );
                  })}
                </List>
              ) : (
                <Typography variant="body2" color="text.secondary">Nothing is waiting. Approvals appear here after a scan.</Typography>
              )}
              {decided.length > 0 && (
                <>
                  <Typography variant="overline" color="text.secondary" component="p" sx={{ mt: 1.5 }}>Recently decided</Typography>
                  <List disablePadding dense>
                    {decided.map((a) => (
                      <ListItemText key={a.approval_id} sx={{ py: 0.5, m: 0, display: "flex", alignItems: "center", gap: 1 }}
                                    primary={<>
                                      <DecisionChip decision={a.decision} />{" "}
                                      {a.task?.title || `Task ${a.task_id}`}
                                    </>}
                                    secondary={`${ACTION_LABEL[a.action] || a.action} · ${timeAgo(a.decided_at)}`} />
                    ))}
                  </List>
                </>
              )}
              <Typography variant="caption" color="text.secondary" component="p" sx={{ mt: 1.5 }} role="status">
                {c.approvals_approved} approved · {c.approvals_rejected} rejected · {c.approvals_cancelled} cancelled or expired
                {sync?.at ? ` · Telegram checked ${timeAgo(sync.at)}` : ""}
                {sync?.running ? " · checking now…" : ""}
              </Typography>
              {sync?.error && (
                <Typography variant="caption" color="error.main" component="p">Last Telegram check failed: {sync.error}</Typography>
              )}
            </Panel>
          </Grid>

          <Grid size={{ xs: 12, lg: 6 }}>
            <Panel title="Recent activity" action={<Button component={RouterLink} to="/activity" size="small">View all</Button>}>
              <List disablePadding dense>
                {state.activity.slice(0, 6).map((a) => (
                  <ListItemText key={a.id} sx={{ py: 0.75, borderBottom: 1, borderColor: "divider", m: 0 }}
                                primary={`${a.event} — ${a.result}`}
                                secondary={`${a.actor} · ${timeAgo(a.ts)}`}
                                slotProps={{ primary: { variant: "body2", noWrap: true, title: `${a.event} — ${a.result}` } }} />
                ))}
              </List>
            </Panel>
          </Grid>
        </Grid>
      )}
    </Box>
  );
}
