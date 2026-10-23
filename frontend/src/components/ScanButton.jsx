import { useRef, useState } from "react";
import { Button, ButtonGroup, CircularProgress, Dialog, DialogActions, DialogContent, DialogTitle, Divider,
  IconButton, ListSubheader, Menu, MenuItem, Stack, TextField, Tooltip, Typography } from "@mui/material";
import ManageSearchIcon from "@mui/icons-material/ManageSearch";
import ArrowDropDownIcon from "@mui/icons-material/ArrowDropDown";
import { useAgentData } from "../api/AgentData.jsx";

// Starts the agent's real scans. New mail: one run. Older mail: the API server reads the chosen
// window in batches until every email is analysed. Scans request approvals on Telegram; they
// never send an email or write to Calendar.
const OLDER = [
  { label: "Last 2 weeks", window: { period: "2 weeks" } },
  { label: "Last month", window: { period: "1 month" } },
  { label: "Last 3 months", window: { period: "3 months" } },
  { label: "Last 6 months", window: { period: "6 months" } },
];

function progressLabel(scan) {
  const p = scan.progress;
  if (!p || !p.found) return "Scanning…";
  return `Scanning… ${p.analysed}/${p.found}`;
}

export default function ScanButton({ size = "medium", compact = false }) {
  const { scan, startScan } = useAgentData();
  const running = scan.status === "running";
  const anchor = useRef(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const [datesOpen, setDatesOpen] = useState(false);
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const label = running ? progressLabel(scan) : "Scan inbox";

  const choose = (window) => {
    setMenuOpen(false);
    startScan(window);
  };
  const datesValid = /^\d{4}-\d{2}-\d{2}$/.test(start) && (!end || end >= start);

  const menu = (
    <Menu anchorEl={anchor.current} open={menuOpen} onClose={() => setMenuOpen(false)}>
      <MenuItem onClick={() => choose(null)}>New mail since last scan</MenuItem>
      <Divider />
      <ListSubheader sx={{ lineHeight: 2.5 }}>Older emails</ListSubheader>
      {OLDER.map((o) => <MenuItem key={o.label} onClick={() => choose(o.window)}>{o.label}</MenuItem>)}
      <MenuItem onClick={() => { setMenuOpen(false); setDatesOpen(true); }}>Choose dates…</MenuItem>
    </Menu>
  );

  const dates = (
    <Dialog open={datesOpen} onClose={() => setDatesOpen(false)} aria-labelledby="scan-dates-title">
      <DialogTitle id="scan-dates-title">Scan emails between dates</DialogTitle>
      <DialogContent>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
          Every email in this range is read and analysed in batches. Already analysed emails are skipped.
        </Typography>
        <Stack direction={{ xs: "column", sm: "row" }} spacing={2}>
          <TextField type="date" label="From" value={start} onChange={(e) => setStart(e.target.value)}
                     slotProps={{ inputLabel: { shrink: true } }} required />
          <TextField type="date" label="To (optional)" value={end} onChange={(e) => setEnd(e.target.value)}
                     slotProps={{ inputLabel: { shrink: true } }} error={Boolean(end && start && end < start)}
                     helperText={end && start && end < start ? "Must be on or after From" : " "} />
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={() => setDatesOpen(false)}>Cancel</Button>
        <Button variant="contained" disabled={!datesValid}
                onClick={() => { setDatesOpen(false); startScan({ start_date: start, ...(end ? { end_date: end } : {}) }); }}>
          Scan
        </Button>
      </DialogActions>
    </Dialog>
  );

  if (compact) {
    return (
      <>
        <Tooltip title={running ? label : "Scan inbox (choose new mail or older emails)"}>
          <span ref={anchor}>
            <IconButton onClick={() => setMenuOpen(true)} disabled={running} aria-label={label} aria-haspopup="menu"
                        sx={{ bgcolor: "primary.main", color: "primary.contrastText", "&:hover": { bgcolor: "primary.dark" },
                              "&.Mui-disabled": { bgcolor: "action.disabledBackground" } }}>
              {running ? <CircularProgress size={18} color="inherit" /> : <ManageSearchIcon />}
            </IconButton>
          </span>
        </Tooltip>
        {menu}
        {dates}
      </>
    );
  }

  return (
    <>
      <ButtonGroup variant="contained" size={size} ref={anchor} aria-label="Scan inbox" disabled={running}>
        <Button onClick={() => startScan(null)} aria-live="polite"
                startIcon={running ? <CircularProgress size={16} color="inherit" /> : <ManageSearchIcon />}>
          {label}
        </Button>
        <Button size="small" onClick={() => setMenuOpen(true)} aria-label="Choose what to scan" aria-haspopup="menu"
                sx={{ px: 0.5, minWidth: 32 }}>
          <ArrowDropDownIcon />
        </Button>
      </ButtonGroup>
      {menu}
      {dates}
    </>
  );
}
