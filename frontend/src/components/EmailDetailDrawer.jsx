import { Alert, Box, Divider, Drawer, IconButton, Stack, Typography, useMediaQuery } from "@mui/material";
import { useTheme } from "@mui/material/styles";
import CloseIcon from "@mui/icons-material/Close";
import { useSearchParams } from "react-router-dom";
import { useAgentData, useMessageMeta } from "../api/AgentData.jsx";
import { ACTION_LABEL, parseSender } from "../lib/derive.js";
import { formatDateTime, formatDeadline } from "../lib/format.js";
import { CategoryChip, ConfidenceMeter, DecisionChip, EvidenceBadge, RiskChip, StatusChip } from "./Badges.jsx";

const NEXT_SEVERITY = {
  quarantined: "error", awaiting_approval: "warning", needs_input: "warning", on_hold: "warning",
  task_open: "info", done: "success", cancelled: "info", no_action: "info",
};

function Section({ title, children }) {
  return (
    <Box component="section" sx={{ py: 2 }}>
      <Typography variant="overline" color="text.secondary" component="h3">{title}</Typography>
      <Box sx={{ mt: 0.5 }}>{children}</Box>
    </Box>
  );
}

export function useEmailParam() {
  const [params, setParams] = useSearchParams();
  const open = (id) => {
    const next = new URLSearchParams(params);
    next.set("email", id);
    setParams(next);
  };
  const close = () => {
    const next = new URLSearchParams(params);
    next.delete("email");
    setParams(next);
  };
  return { selectedId: params.get("email"), open, close };
}

export default function EmailDetailDrawer() {
  const theme = useTheme();
  const wide = useMediaQuery(theme.breakpoints.up("sm"));
  const { emails } = useAgentData();
  const { selectedId, close } = useEmailParam();
  const email = emails.find((e) => e.message_id === selectedId);
  useMessageMeta(email ? [email.message_id] : []);
  const sender = parseSender(email?.from);

  return (
    <Drawer anchor="right" open={Boolean(selectedId)} onClose={close}
            slotProps={{ paper: { sx: { width: wide ? 480 : "100%" }, role: "dialog", "aria-label": "Email details" } }}>
      <Box sx={{ display: "flex", alignItems: "center", px: 2, py: 1.5, borderBottom: 1, borderColor: "divider" }}>
        <Typography variant="h2" sx={{ flexGrow: 1 }}>Email details</Typography>
        <IconButton onClick={close} aria-label="Close details"><CloseIcon /></IconButton>
      </Box>
      {!email ? (
        <Box sx={{ p: 3 }}><Typography color="text.secondary">This email is not in the current data.</Typography></Box>
      ) : (
        <Box sx={{ px: 2.5, pb: 3, overflowY: "auto" }}>
          <Section title="Message">
            {email.metaState === "ok" ? (
              <>
                <Typography variant="h3" sx={{ wordBreak: "break-word" }}>{email.subject || "(no subject)"}</Typography>
                <Typography variant="body2" sx={{ mt: 0.5 }}>{sender.name}</Typography>
                <Typography variant="body2" color="text.secondary" sx={{ wordBreak: "break-all" }}>{sender.address}</Typography>
                <Typography variant="body2" color="text.secondary">Received {formatDateTime(email.receivedAt)}</Typography>
              </>
            ) : (
              <Typography variant="body2" color="text.secondary">
                {email.metaState === "loading" ? "Loading sender and subject from Gmail…" : "Sender and subject are not available from Gmail (the email may have been deleted)."}
              </Typography>
            )}
            <Typography variant="caption" color="text.secondary" component="p" sx={{ mt: 1 }}>
              Sender and subject are fetched live from Gmail and never stored.
            </Typography>
            <Stack direction="row" spacing={1} useFlexGap sx={{ flexWrap: "wrap", mt: 1.5 }}>
              <CategoryChip category={email.category} />
              <RiskChip risk={email.risk} />
              <StatusChip status={email.status} />
            </Stack>
          </Section>

          <Alert severity={NEXT_SEVERITY[email.status]} sx={{ my: 1 }}>{email.next}</Alert>

          <Divider />
          <Section title="Confidence">
            <ConfidenceMeter value={email.confidence} band={email.band} />
            <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
              The model's score, adjusted by the code checks below. High 90+, medium 70–89, low under 70.
            </Typography>
          </Section>

          <Divider />
          <Section title="Evidence from the safety checks">
            {email.evidence.length ? (
              <Stack spacing={1} sx={{ alignItems: "flex-start" }}>
                {email.evidence.map((e) => <EvidenceBadge key={e.code} item={e} />)}
              </Stack>
            ) : (
              <Typography variant="body2" color="text.secondary">No warning signs or trust signals were found.</Typography>
            )}
          </Section>

          {email.task && (
            <>
              <Divider />
              <Section title="Task">
                <Typography variant="body1" fontWeight={600}>{email.task.title}</Typography>
                {email.task.deadline && (
                  <Typography variant="body2" color="text.secondary">Deadline {formatDeadline(email.task.deadline)}</Typography>
                )}
                {email.task.reason && <Typography variant="body2" sx={{ mt: 1 }}>{email.task.reason}</Typography>}
                {email.task.delta && (
                  <Alert severity="info" icon={false} sx={{ mt: 1.5 }}>
                    <strong>What changed:</strong> {email.task.delta}
                  </Alert>
                )}
              </Section>
            </>
          )}

          {email.approvals.length > 0 && (
            <>
              <Divider />
              <Section title="Approvals">
                <Stack spacing={1.25}>
                  {email.approvals.map((a) => (
                    <Box key={a.approval_id} sx={{ display: "flex", gap: 1, alignItems: "center", flexWrap: "wrap" }}>
                      <Typography variant="body2" sx={{ flexGrow: 1, minWidth: 140 }}>{ACTION_LABEL[a.action] || a.action}</Typography>
                      <DecisionChip decision={a.decision} />
                      {a.state === "confirmed" && <Typography variant="caption" color="success.main">Done</Typography>}
                      {a.state === "failed" && <Typography variant="caption" color="error.main">Failed</Typography>}
                    </Box>
                  ))}
                </Stack>
                <Typography variant="caption" color="text.secondary" component="p" sx={{ mt: 1 }}>
                  Approvals happen only on Telegram. Each one works once and expires after 30 minutes.
                </Typography>
              </Section>
            </>
          )}

          <Divider />
          <Section title="Reference">
            <Typography variant="caption" color="text.secondary" component="p" sx={{ fontFamily: "monospace", wordBreak: "break-all" }}>
              message {email.message_id}<br />thread {email.thread_id}<br />analysed {formatDateTime(email.created_at)}
            </Typography>
          </Section>
        </Box>
      )}
    </Drawer>
  );
}
