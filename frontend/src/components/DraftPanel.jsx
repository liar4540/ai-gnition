// The draft for a task: the exact reply or calendar event the agent prepared, and what the
// person can do with it. The signed-in owner can Send / Don't send / Edit right here; everyone
// else sees the draft and that it waits for the owner's approval (on Telegram or here).
// The server only queues a decision; the agent applies it with the same checks as Telegram.
import { useState } from "react";
import { Alert, Box, Button, CircularProgress, Dialog, DialogActions, DialogContent, DialogTitle, Paper, Stack,
  TextField, Typography } from "@mui/material";
import SendIcon from "@mui/icons-material/Send";
import EventAvailableIcon from "@mui/icons-material/EventAvailable";
import CancelOutlinedIcon from "@mui/icons-material/CancelOutlined";
import EditOutlinedIcon from "@mui/icons-material/EditOutlined";
import { useAgentData } from "../api/AgentData.jsx";
import { ACTION_LABEL } from "../lib/derive.js";
import { formatDateTime, formatDeadline } from "../lib/format.js";
import OwnerSignIn from "./OwnerSignIn.jsx";

const EXPIRED = (a) => a.decision === "expired" || (a.decision === "pending" && new Date(a.expires_at) <= new Date());

// Live drafts for one task (newest per action) with the approval that covers their exact text.
export function draftItems(state, taskId) {
  if (!state) return [];
  const approvals = state.approvals.filter((a) => a.task_id === taskId);
  const queued = new Set((state.web_decisions || []).filter((w) => w.status === "queued").map((w) => w.approval_id));
  const newest = new Map();
  for (const d of [...(state.drafts || [])].filter((x) => x.task_id === taskId).sort((x, y) => y.draft_id - x.draft_id)) {
    if (!newest.has(d.action)) newest.set(d.action, d);
  }
  const order = { send_reply: 0, calendar_create: 1, calendar_move: 1 };   // the reply first, then the event
  return [...newest.values()].sort((x, y) => order[x.action] - order[y.action]).map((draft) => {
    const approval = approvals.filter((a) => a.content_hash === draft.content_hash && a.action === draft.action)
      .sort((x, y) => String(y.expires_at).localeCompare(String(x.expires_at)))[0] || null;
    return { draft, approval, busy: Boolean(approval && queued.has(approval.approval_id)) };
  });
}

function whenText(p) {
  if (p.all_day) {
    const last = new Date(`${p.end}T00:00:00Z`);                  // date-only maths in UTC: no time-zone shift
    last.setUTCDate(last.getUTCDate() - 1);                       // end date is exclusive
    const lastIso = last.toISOString().slice(0, 10);
    return lastIso === p.start ? `${formatDeadline(p.start)} (all day)`
      : `${formatDeadline(p.start)} to ${formatDeadline(lastIso)} (all day)`;
  }
  return `${formatDateTime(p.start)} to ${formatDateTime(p.end)}`;
}

function DraftContent({ action, payload }) {
  if (action === "send_reply") {
    return (
      <>
        {payload.to && <Typography variant="body2"><b>To:</b> {payload.to}</Typography>}
        {payload.subject && <Typography variant="body2"><b>Subject:</b> {payload.subject}</Typography>}
        <Typography variant="body2" component="div"
                    sx={{ mt: 1, p: 1.5, bgcolor: "action.hover", borderRadius: 1, whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
          {payload.body}
        </Typography>
      </>
    );
  }
  const reminders = (payload.reminders || []).map((r) => (r.minutes >= 60 ? `${r.minutes / 60}h` : `${r.minutes} min`) + " before");
  return (
    <>
      <Typography variant="body2"><b>Event:</b> {payload.title}</Typography>
      <Typography variant="body2"><b>When:</b> {whenText(payload)}</Typography>
      {reminders.length > 0 && <Typography variant="body2"><b>Reminders:</b> {reminders.join(", ")}</Typography>}
    </>
  );
}

function DraftCard({ item }) {
  const { owner, decide } = useAgentData();
  const { draft, approval, busy } = item;
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(draft.payload.body || "");
  const [confirm, setConfirm] = useState(null);       // "approve" | "reject"
  const [working, setWorking] = useState(false);
  const [error, setError] = useState("");
  const isReply = draft.action === "send_reply";
  const sendLabel = isReply ? "Send email" : draft.action === "calendar_move" ? "Move event" : "Add to calendar";
  const canAct = owner.owner && owner.web_actions && approval && !busy &&
    (approval.decision === "pending" || approval.decision === "expired");
  const expired = approval && EXPIRED(approval);

  const run = async (decision, body) => {
    setWorking(true);
    setError("");
    try {
      await decide(approval.approval_id, decision, body);
      setEditing(false);
      setConfirm(null);
    } catch (e) {
      setError(e.message);
    } finally {
      setWorking(false);
    }
  };

  let status;
  if (busy || working) status = <Alert severity="info" icon={<CircularProgress size={18} />}>Working on it. This takes a few seconds.</Alert>;
  else if (!approval) status = <Alert severity="info">Not sent for approval yet.</Alert>;
  else if (approval.decision === "approved" && approval.state !== "confirmed" && approval.state !== "failed")
    status = <Alert severity="info" icon={<CircularProgress size={18} />}>Approved. {isReply ? "Sending" : "Adding it"} now…</Alert>;
  else if (approval.state === "failed") status = <Alert severity="error">Approved, but Google refused it. Nothing was changed.</Alert>;
  else if (!owner.owner) {
    status = (
      <Alert severity="warning" action={owner.enabled ? <OwnerSignIn variant="text" label="Owner sign in" /> : null}>
        Waiting for the owner&apos;s approval. Nothing is {isReply ? "sent" : "added"} until they approve.
      </Alert>
    );
  } else if (!owner.web_actions) {
    status = <Alert severity="warning">Approve this on Telegram. Deciding here needs the database to be connected.</Alert>;
  } else if (expired) {
    status = <Alert severity="info">The 30-minute approval expired. {sendLabel} creates a new approval for this exact text.</Alert>;
  }

  return (
    <Paper variant="outlined" sx={{ p: 2 }}>
      <Typography variant="overline" color="text.secondary" component="h4">
        {ACTION_LABEL[draft.action] || draft.action}
      </Typography>
      {editing ? (
        <TextField multiline minRows={5} fullWidth value={text} onChange={(e) => setText(e.target.value)}
                   slotProps={{ htmlInput: { "aria-label": "Reply text", maxLength: 5000 } }} sx={{ mt: 1 }} />
      ) : (
        <DraftContent action={draft.action} payload={draft.payload} />
      )}
      {status && <Box sx={{ mt: 1.5 }}>{status}</Box>}
      {error && <Alert severity="error" sx={{ mt: 1.5 }}>{error}</Alert>}

      {canAct && !working && (
        editing ? (
          <Stack direction="row" spacing={1} useFlexGap sx={{ mt: 1.5, flexWrap: "wrap" }}>
            <Button variant="contained" onClick={() => run("edit", text)} disabled={!text.trim() || text.trim() === draft.payload.body}>
              Save changes
            </Button>
            <Button onClick={() => { setEditing(false); setText(draft.payload.body || ""); }}>Cancel</Button>
          </Stack>
        ) : (
          <Stack direction="row" spacing={1} useFlexGap sx={{ mt: 1.5, flexWrap: "wrap" }}>
            <Button variant="contained" startIcon={isReply ? <SendIcon /> : <EventAvailableIcon />} onClick={() => setConfirm("approve")}>
              {sendLabel}
            </Button>
            <Button variant="outlined" color="error" startIcon={<CancelOutlinedIcon />} onClick={() => setConfirm("reject")}>
              {isReply ? "Don't send" : "Don't add"}
            </Button>
            {isReply && <Button startIcon={<EditOutlinedIcon />} onClick={() => setEditing(true)}>Edit</Button>}
          </Stack>
        )
      )}
      {editing && (
        <Typography variant="caption" color="text.secondary" component="p" sx={{ mt: 1 }}>
          Saving makes a new approval for the edited text; the old one is cancelled.
        </Typography>
      )}

      <Dialog open={Boolean(confirm)} onClose={() => setConfirm(null)} aria-labelledby="confirm-title">
        <DialogTitle id="confirm-title">
          {confirm === "approve" ? `${sendLabel}?` : isReply ? "Don't send this reply?" : "Don't add this event?"}
        </DialogTitle>
        <DialogContent>
          <Typography variant="body2">
            {confirm === "approve"
              ? (isReply ? `Gmail sends exactly this reply${draft.payload.to ? ` to ${draft.payload.to}` : ""}, in the original thread.`
                : "Google Calendar gets exactly this event with these reminders.")
              : "Nothing is sent or added, and the draft is discarded."}
          </Typography>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setConfirm(null)}>Cancel</Button>
          <Button variant="contained" color={confirm === "approve" ? "primary" : "error"} onClick={() => run(confirm)}>
            {confirm === "approve" ? sendLabel : isReply ? "Don't send" : "Don't add"}
          </Button>
        </DialogActions>
      </Dialog>
    </Paper>
  );
}

export default function DraftPanel({ taskId }) {
  const { state } = useAgentData();
  const items = draftItems(state, taskId);
  if (!items.length) return null;
  return (
    <Stack spacing={1.5}>
      {items.map((item) => <DraftCard key={item.draft.draft_id} item={item} />)}
    </Stack>
  );
}
