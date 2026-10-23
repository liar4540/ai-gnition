import { Alert, Box, Button, Skeleton, Stack, Typography } from "@mui/material";
import InboxIcon from "@mui/icons-material/Inbox";
import { useAgentData } from "../api/AgentData.jsx";
import ScanButton from "./ScanButton.jsx";

export function LoadingRows({ rows = 5 }) {
  return (
    <Stack spacing={1} aria-busy="true" aria-label="Loading">
      {Array.from({ length: rows }, (_, i) => <Skeleton key={i} variant="rounded" height={44} />)}
    </Stack>
  );
}

export function EmptyState({ title, text, action = true }) {
  return (
    <Box sx={{ textAlign: "center", py: 6, px: 2 }}>
      <InboxIcon sx={{ fontSize: 40, color: "text.secondary" }} />
      <Typography variant="h3" sx={{ mt: 1 }}>{title}</Typography>
      {text && <Typography color="text.secondary" sx={{ mt: 0.5, mb: 2 }}>{text}</Typography>}
      {action && <ScanButton />}
    </Box>
  );
}

export function ErrorBanner() {
  const { error, refresh, state } = useAgentData();
  if (!error) return null;
  return (
    <Alert
      severity="error"
      sx={{ mb: 2 }}
      action={<Button color="inherit" size="small" onClick={() => refresh(true)}>Retry</Button>}
    >
      {state ? `Showing the last data. Refresh failed: ${error}` : `Could not load the agent's data: ${error}`}
    </Alert>
  );
}
