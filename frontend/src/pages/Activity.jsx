import { useDeferredValue, useMemo, useState } from "react";
import { Box, Card, FormControl, InputLabel, MenuItem, Select, Stack, Table, TableBody, TableCell, TableContainer,
  TableHead, TableRow, TextField, Typography } from "@mui/material";
import { useAgentData } from "../api/AgentData.jsx";
import { EmptyState, ErrorBanner, LoadingRows } from "../components/States.jsx";
import { formatDateTime } from "../lib/format.js";

export default function Activity() {
  const { state, loading } = useAgentData();
  const [actor, setActor] = useState("any");
  const [query, setQuery] = useState("");
  const q = useDeferredValue(query.toLowerCase());
  const actors = useMemo(() => [...new Set((state?.activity || []).map((a) => a.actor))].sort(), [state]);
  const rows = useMemo(
    () => (state?.activity || []).filter((a) =>
      (actor === "any" || a.actor === actor) && (!q || `${a.event} ${a.result}`.toLowerCase().includes(q))),
    [state, actor, q]
  );

  return (
    <Box>
      <Typography variant="h1">Activity</Typography>
      <Typography color="text.secondary" sx={{ mb: 2 }}>Append-only log of every agent action. Email text is never logged.</Typography>
      <ErrorBanner />
      <Card>
        <Stack direction={{ xs: "column", sm: "row" }} spacing={1.5} sx={{ p: 2 }}>
          <TextField size="small" label="Search" value={query} onChange={(e) => setQuery(e.target.value)} sx={{ flexGrow: 1 }} />
          <FormControl size="small" sx={{ minWidth: 200 }}>
            <InputLabel id="actor-filter">Tool</InputLabel>
            <Select labelId="actor-filter" label="Tool" value={actor} onChange={(e) => setActor(e.target.value)}>
              <MenuItem value="any">All tools</MenuItem>
              {actors.map((a) => <MenuItem key={a} value={a}>{a}</MenuItem>)}
            </Select>
          </FormControl>
        </Stack>
        {!state && loading ? <Box sx={{ p: 2 }}><LoadingRows /></Box> : !rows.length ? (
          <EmptyState title="No activity" action={false} />
        ) : (
          <TableContainer>
            <Table size="small" aria-label="Activity log" sx={{ "& td": { verticalAlign: "top" } }}>
              <TableHead>
                <TableRow><TableCell>Time</TableCell><TableCell>Tool</TableCell><TableCell>Event</TableCell><TableCell>Result</TableCell></TableRow>
              </TableHead>
              <TableBody>
                {rows.map((a) => (
                  <TableRow key={a.id}>
                    <TableCell sx={{ whiteSpace: "nowrap" }}>{formatDateTime(a.ts)}</TableCell>
                    <TableCell sx={{ fontFamily: "monospace", fontSize: 12 }}>{a.actor}</TableCell>
                    <TableCell sx={{ wordBreak: "break-word" }}>{a.event}</TableCell>
                    <TableCell sx={{ wordBreak: "break-word", color: "text.secondary" }}>{a.result}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
        )}
      </Card>
    </Box>
  );
}
