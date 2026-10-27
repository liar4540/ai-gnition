// A task's details and its draft (reply or calendar event) with Send / Don't send for the owner.
// Opened from Tasks, the Dashboard and the Inbox drawer with ?task=<id>; works for reminders that
// came from chat too (they have no email).
import { Alert, Box, Button, Divider, Drawer, IconButton, Stack, Typography, useMediaQuery } from "@mui/material";
import { useTheme } from "@mui/material/styles";
import CloseIcon from "@mui/icons-material/Close";
import MailOutlineIcon from "@mui/icons-material/MailOutlined";
import { useSearchParams } from "react-router-dom";
import { useAgentData } from "../api/AgentData.jsx";
import { ACTION_LABEL, wasRejected } from "../lib/derive.js";
import { formatDeadline } from "../lib/format.js";
import { DecisionChip } from "./Badges.jsx";
import DraftPanel, { draftItems } from "./DraftPanel.jsx";

const STATUS_TEXT = {
  open: "Open", pending_approval: "Waiting for approval", on_hold: "On hold", done: "Done",
  cancelled: "Cancelled", rejected: "Rejected",
};

export function useTaskParam() {
  const [params, setParams] = useSearchParams();
  const open = (id) => {
    const next = new URLSearchParams(params);
    next.delete("email");
    next.set("task", String(id));
    setParams(next);
  };
  const close = () => {
    const next = new URLSearchParams(params);
    next.delete("task");
    setParams(next);
  };
  return { selectedTask: params.get("task"), open, close };
}

function Section({ title, children }) {
  return (
    <Box component="section" sx={{ py: 2 }}>
      <Typography variant="overline" color="text.secondary" component="h3">{title}</Typography>
      <Box sx={{ mt: 0.5 }}>{children}</Box>
    </Box>
  );
}

export default function TaskDetailDrawer() {
  const theme = useTheme();
  const wide = useMediaQuery(theme.breakpoints.up("sm"));
  const { state } = useAgentData();
  const { selectedTask, close } = useTaskParam();
  const [params, setParams] = useSearchParams();
  const task = state?.tasks.find((t) => String(t.task_id) === selectedTask);
  const approvals = task ? state.approvals.filter((a) => a.task_id === task.task_id) : [];
  const status = task ? (wasRejected(task, approvals) ? "rejected" : task.status) : null;
  const emailId = (task?.source_message_ids || "").split(",").filter(Boolean).pop();
  const hasDrafts = task ? draftItems(state, task.task_id).length > 0 : false;

  const openEmail = () => {
    const next = new URLSearchParams(params);
    next.delete("task");
    next.set("email", emailId);
    setParams(next);
  };

  return (
    <Drawer anchor="right" open={Boolean(selectedTask)} onClose={close}
            slotProps={{ paper: { sx: { width: wide ? 520 : "100%" }, role: "dialog", "aria-label": "Task details" } }}>
      <Box sx={{ display: "flex", alignItems: "center", px: 2, py: 1.5, borderBottom: 1, borderColor: "divider" }}>
        <Typography variant="h2" sx={{ flexGrow: 1 }}>Task</Typography>
        <IconButton onClick={close} aria-label="Close task"><CloseIcon /></IconButton>
      </Box>
      {!task ? (
        <Box sx={{ p: 3 }}><Typography color="text.secondary">This task is not in the current data.</Typography></Box>
      ) : (
        <Box sx={{ px: 2.5, pb: 3, overflowY: "auto" }}>
          <Section title="What to do">
            <Typography variant="h3" sx={{ wordBreak: "break-word" }}>{task.title}</Typography>
            <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
              {STATUS_TEXT[status] || status}{task.deadline ? ` · due ${formatDeadline(task.deadline)}` : ""}
              {task.confidence != null ? ` · confidence ${task.confidence}` : ""}
            </Typography>
            {task.reason && <Typography variant="body2" sx={{ mt: 1 }}>{task.reason}</Typography>}
            {task.delta && <Alert severity="info" icon={false} sx={{ mt: 1.5 }}><strong>What changed:</strong> {task.delta}</Alert>}
            {emailId && (
              <Button size="small" startIcon={<MailOutlineIcon />} onClick={openEmail} sx={{ mt: 1.5 }}>
                View the email and its safety evidence
              </Button>
            )}
          </Section>

          <Divider />
          <Section title="Draft">
            {hasDrafts ? <DraftPanel taskId={task.task_id} /> : (
              <Typography variant="body2" color="text.secondary">
                {status === "done" ? "Done. The draft was used and its text deleted."
                  : status === "rejected" ? "Rejected. Nothing was sent or added, and the draft was deleted."
                    : "No draft is waiting for this task."}
              </Typography>
            )}
          </Section>

          {approvals.length > 0 && (
            <>
              <Divider />
              <Section title="Approval history">
                <Stack spacing={1}>
                  {approvals.map((a) => (
                    <Box key={a.approval_id} sx={{ display: "flex", gap: 1, alignItems: "center", flexWrap: "wrap" }}>
                      <Typography variant="body2" sx={{ flexGrow: 1, minWidth: 140 }}>{ACTION_LABEL[a.action] || a.action}</Typography>
                      <DecisionChip decision={a.decision} />
                      {a.state === "confirmed" && <Typography variant="caption" color="success.main">Done</Typography>}
                      {a.state === "failed" && <Typography variant="caption" color="error.main">Failed</Typography>}
                    </Box>
                  ))}
                </Stack>
              </Section>
            </>
          )}
        </Box>
      )}
    </Drawer>
  );
}
