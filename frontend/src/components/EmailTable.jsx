import { Box, List, ListItemButton, Skeleton, Stack, Table, TableBody, TableCell, TableContainer, TableHead,
  TableRow, TableSortLabel, Typography, useMediaQuery } from "@mui/material";
import { useTheme } from "@mui/material/styles";
import { parseSender } from "../lib/derive.js";
import { formatDateTime } from "../lib/format.js";
import { CategoryChip, ConfidenceMeter, RiskChip, StatusChip } from "./Badges.jsx";

const COLUMNS = [
  { id: "sender", label: "From", sortable: true },
  { id: "subject", label: "Subject", sortable: true },
  { id: "received", label: "Received", sortable: true },
  { id: "category", label: "Category", sortable: true },
  { id: "risk", label: "Risk", sortable: true },
  { id: "confidence", label: "Confidence", sortable: true },
  { id: "status", label: "Status", sortable: true },
];

function MetaText({ email, field, fallbackWidth = 120 }) {
  if (email.metaState === "loading") return <Skeleton width={fallbackWidth} />;
  if (email.metaState !== "ok") return <Typography variant="body2" color="text.secondary">Not in Gmail</Typography>;
  if (field === "sender") {
    const s = parseSender(email.from);
    return (
      <Box sx={{ minWidth: 0 }}>
        <Typography variant="body2" fontWeight={600} noWrap title={s.address}>{s.name}</Typography>
        <Typography variant="caption" color="text.secondary" noWrap component="div">{s.address}</Typography>
      </Box>
    );
  }
  return <Typography variant="body2" noWrap title={email.subject}>{email.subject || "(no subject)"}</Typography>;
}

export default function EmailTable({ emails, sort, onSort, onOpen }) {
  const theme = useTheme();
  const wide = useMediaQuery(theme.breakpoints.up("lg"));
  const keyOpen = (id) => (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      onOpen(id);
    }
  };

  if (!wide) {
    return (
      <List disablePadding aria-label="Emails">
        {emails.map((e) => (
          <ListItemButton key={e.message_id} onClick={() => onOpen(e.message_id)} divider
                          sx={{ alignItems: "flex-start", flexDirection: "column", gap: 0.75, py: 1.5 }}>
            <Box sx={{ display: "flex", width: "100%", gap: 1, minWidth: 0 }}>
              <Box sx={{ flexGrow: 1, minWidth: 0 }}><MetaText email={e} field="sender" /></Box>
              <Typography variant="caption" color="text.secondary" sx={{ whiteSpace: "nowrap" }}>
                {formatDateTime(e.receivedAt)}
              </Typography>
            </Box>
            <Box sx={{ width: "100%", minWidth: 0 }}><MetaText email={e} field="subject" fallbackWidth={200} /></Box>
            <Stack direction="row" spacing={0.75} useFlexGap sx={{ flexWrap: "wrap" }}>
              <CategoryChip category={e.category} />
              <RiskChip risk={e.risk} />
              <StatusChip status={e.status} />
            </Stack>
          </ListItemButton>
        ))}
      </List>
    );
  }

  return (
    <TableContainer>
      <Table size="small" aria-label="Emails" sx={{ tableLayout: "fixed", "& td, & th": { py: 1.25 } }}>
        <colgroup>
          <col style={{ width: "19%" }} /><col style={{ width: "22%" }} /><col style={{ width: "13%" }} />
          <col style={{ width: "11%" }} /><col style={{ width: "10%" }} /><col style={{ width: "10%" }} />
          <col style={{ width: "15%" }} />
        </colgroup>
        <TableHead>
          <TableRow>
            {COLUMNS.map((c) => (
              <TableCell key={c.id} sortDirection={sort.by === c.id ? sort.dir : false}>
                <TableSortLabel active={sort.by === c.id} direction={sort.by === c.id ? sort.dir : "asc"}
                                onClick={() => onSort(c.id)}>
                  {c.label}
                </TableSortLabel>
              </TableCell>
            ))}
          </TableRow>
        </TableHead>
        <TableBody>
          {emails.map((e) => (
            <TableRow key={e.message_id} hover tabIndex={0} onClick={() => onOpen(e.message_id)}
                      onKeyDown={keyOpen(e.message_id)} sx={{ cursor: "pointer" }}
                      aria-label={`Open email ${e.subject || e.message_id}`}>
              <TableCell sx={{ minWidth: 0 }}><MetaText email={e} field="sender" /></TableCell>
              <TableCell><MetaText email={e} field="subject" fallbackWidth={180} /></TableCell>
              <TableCell>
                <Typography variant="body2" color="text.secondary" noWrap>{formatDateTime(e.receivedAt) || "–"}</Typography>
              </TableCell>
              <TableCell><CategoryChip category={e.category} /></TableCell>
              <TableCell><RiskChip risk={e.risk} short /></TableCell>
              <TableCell><ConfidenceMeter value={e.confidence} band={e.band} compact /></TableCell>
              <TableCell><StatusChip status={e.status} /></TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </TableContainer>
  );
}
