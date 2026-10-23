import { useEffect, useRef, useState } from "react";
import { Alert, Box, Chip, CircularProgress, Drawer, IconButton, Stack, TextField, Typography, useMediaQuery } from "@mui/material";
import { useTheme } from "@mui/material/styles";
import CloseIcon from "@mui/icons-material/Close";
import SendIcon from "@mui/icons-material/Send";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api } from "../api/client.js";
import { useAgentData } from "../api/AgentData.jsx";

// Read-only questions only. Actions (e.g. "check approvals") must be typed by the user.
const SUGGESTIONS = ["What are my open tasks?", "Why was the latest suspicious email flagged?", "Show the dashboard"];

function Bubble({ role, text }) {
  const mine = role === "user";
  return (
    <Box sx={{ alignSelf: mine ? "flex-end" : "flex-start", maxWidth: "92%" }}>
      <Box sx={{
        px: 1.5, py: 1, borderRadius: 2, bgcolor: mine ? "primary.main" : "action.hover",
        color: mine ? "primary.contrastText" : "text.primary", overflowX: "auto",
        "& p": { my: 0.5 }, "& table": { borderCollapse: "collapse", fontSize: 13, my: 1 },
        "& th, & td": { border: 1, borderColor: "divider", px: 0.75, py: 0.25, textAlign: "left" },
        "& ul, & ol": { pl: 2.5, my: 0.5 }, "& code": { fontSize: 12 },
      }}>
        {mine ? <Typography variant="body2">{text}</Typography> : <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>}
      </Box>
    </Box>
  );
}

export default function AssistantDrawer({ open, onClose }) {
  const theme = useTheme();
  const wide = useMediaQuery(theme.breakpoints.up("sm"));
  const { refresh } = useAgentData();
  const [messages, setMessages] = useState([]);
  const [threadId, setThreadId] = useState(null);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const endRef = useRef(null);

  // Braces matter: scrollIntoView returns a Promise in current browsers, and an effect must
  // return nothing (or a cleanup function), otherwise React crashes on the next update.
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, busy]);

  const send = async (text) => {
    const message = text.trim();
    if (!message || busy) return;
    setInput("");
    setError("");
    setMessages((m) => [...m, { role: "user", text: message }]);
    setBusy(true);
    try {
      const r = await api.chat(message, threadId);
      setThreadId(r.thread_id || threadId);
      setMessages((m) => [...m, { role: "agent", text: r.text || (r.error ? `Error: ${r.error}` : "(no reply)") }]);
      refresh(true); // the chat may have changed data, e.g. after "check approvals"
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Drawer anchor="right" open={open} onClose={onClose}
            slotProps={{ paper: { sx: { width: wide ? 440 : "100%", display: "flex" }, "aria-label": "Assistant" } }}>
      <Box sx={{ display: "flex", alignItems: "center", px: 2, py: 1.5, borderBottom: 1, borderColor: "divider" }}>
        <Box sx={{ flexGrow: 1 }}>
          <Typography variant="h2">Assistant</Typography>
          <Typography variant="caption" color="text.secondary">Ask the agent anything about your inbox.</Typography>
        </Box>
        <IconButton onClick={onClose} aria-label="Close assistant"><CloseIcon /></IconButton>
      </Box>

      <Stack spacing={1.25} sx={{ flex: 1, overflowY: "auto", p: 2 }} role="log" aria-live="polite">
        {!messages.length && (
          <Box>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>Try:</Typography>
            <Stack direction="row" spacing={1} useFlexGap sx={{ flexWrap: "wrap" }}>
              {SUGGESTIONS.map((s) => <Chip key={s} label={s} onClick={() => send(s)} variant="outlined" clickable />)}
            </Stack>
            <Typography variant="caption" color="text.secondary" component="p" sx={{ mt: 2 }}>
              After approving on Telegram, type "check approvals" here to carry it out.
            </Typography>
          </Box>
        )}
        {messages.map((m, i) => <Bubble key={i} role={m.role} text={m.text} />)}
        {busy && (
          <Stack direction="row" spacing={1} sx={{ alignItems: "center", color: "text.secondary" }} role="status">
            <CircularProgress size={16} /><Typography variant="body2">The agent is working…</Typography>
          </Stack>
        )}
        {error && <Alert severity="error">{error}</Alert>}
        <div ref={endRef} />
      </Stack>

      <Box component="form" onSubmit={(e) => { e.preventDefault(); send(input); }}
           sx={{ display: "flex", gap: 1, p: 2, borderTop: 1, borderColor: "divider" }}>
        <TextField size="small" fullWidth label="Message the agent" value={input} onChange={(e) => setInput(e.target.value)}
                   disabled={busy} slotProps={{ htmlInput: { maxLength: 2000 } }} />
        <IconButton type="submit" color="primary" disabled={busy || !input.trim()} aria-label="Send message"><SendIcon /></IconButton>
      </Box>
    </Drawer>
  );
}
