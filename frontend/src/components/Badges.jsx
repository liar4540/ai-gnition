import { Box, Chip, LinearProgress, Tooltip, Typography } from "@mui/material";
import ReportIcon from "@mui/icons-material/Report";
import WarningAmberIcon from "@mui/icons-material/WarningAmber";
import VerifiedUserIcon from "@mui/icons-material/VerifiedUser";
import InfoOutlinedIcon from "@mui/icons-material/InfoOutlined";
import StarIcon from "@mui/icons-material/Star";
import MailOutlineIcon from "@mui/icons-material/MailOutlined";
import GppBadIcon from "@mui/icons-material/GppBad";
import HourglassTopIcon from "@mui/icons-material/HourglassTop";
import CheckCircleIcon from "@mui/icons-material/CheckCircle";
import TaskAltIcon from "@mui/icons-material/TaskAlt";
import HelpOutlineIcon from "@mui/icons-material/HelpOutlined";
import PauseCircleOutlineIcon from "@mui/icons-material/PauseCircleOutlined";
import RemoveCircleOutlineIcon from "@mui/icons-material/RemoveCircleOutlined";
import CancelOutlinedIcon from "@mui/icons-material/CancelOutlined";
import { CATEGORY, DECISION, RISK, STATUS } from "../lib/derive.js";

const CATEGORY_ICON = { important: <StarIcon />, routine: <MailOutlineIcon />, suspicious: <GppBadIcon /> };
const RISK_ICON = { low: <VerifiedUserIcon />, medium: <WarningAmberIcon />, high: <ReportIcon /> };
const STATUS_ICON = {
  quarantined: <GppBadIcon />, awaiting_approval: <HourglassTopIcon />, needs_input: <HelpOutlineIcon />,
  on_hold: <PauseCircleOutlineIcon />, task_open: <TaskAltIcon />, done: <CheckCircleIcon />,
  rejected: <CancelOutlinedIcon />, cancelled: <RemoveCircleOutlineIcon />, no_action: <RemoveCircleOutlineIcon />,
};
const DECISION_ICON = {
  pending: <HourglassTopIcon />, approved: <CheckCircleIcon />, rejected: <CancelOutlinedIcon />,
  cancelled: <RemoveCircleOutlineIcon />, expired: <RemoveCircleOutlineIcon />,
};
const SEVERITY = {
  high: { color: "error", icon: <ReportIcon />, word: "High" },
  warning: { color: "warning", icon: <WarningAmberIcon />, word: "Warning" },
  safe: { color: "success", icon: <VerifiedUserIcon />, word: "Trust signal" },
  info: { color: "info", icon: <InfoOutlinedIcon />, word: "Info" },
};

export function CategoryChip({ category, size = "small" }) {
  const c = CATEGORY[category] || { label: category, color: "default" };
  return <Chip size={size} icon={CATEGORY_ICON[category]} label={c.label} color={c.color} variant="outlined" />;
}

export function RiskChip({ risk, size = "small", short = false }) {
  const r = RISK[risk];
  if (!r) return null;
  const label = short ? r.label.replace(" risk", "") : r.label;
  return (
    <Chip size={size} icon={RISK_ICON[risk]} label={label} color={r.color} variant={risk === "high" ? "filled" : "outlined"}
          aria-label={r.label} />
  );
}

export function StatusChip({ status, size = "small" }) {
  const s = STATUS[status] || STATUS.no_action;
  return <Chip size={size} icon={STATUS_ICON[status]} label={s.label} color={s.color} variant="outlined" />;
}

export function DecisionChip({ decision }) {
  const d = DECISION[decision] || { label: decision, color: "default" };
  return <Chip size="small" icon={DECISION_ICON[decision]} label={d.label} color={d.color} variant="outlined" />;
}

export function EvidenceBadge({ item }) {
  const s = SEVERITY[item.severity] || SEVERITY.info;
  return (
    <Tooltip title={`${s.word}: ${item.code}`}>
      <Chip size="small" icon={s.icon} label={item.label} color={s.color} variant="outlined"
            sx={{ maxWidth: "100%", "& .MuiChip-label": { whiteSpace: "normal" }, height: "auto", py: 0.25 }} />
    </Tooltip>
  );
}

export function ConfidenceMeter({ value, band, compact = false }) {
  if (value === null || value === undefined) return <Typography variant="body2" color="text.secondary">–</Typography>;
  const color = band === "high" ? "success" : band === "medium" ? "warning" : "error";
  return (
    <Box sx={{ minWidth: compact ? 72 : 140 }} aria-label={`Confidence ${value} of 100, ${band} band`}>
      <Box sx={{ display: "flex", justifyContent: "space-between", gap: 1 }}>
        <Typography variant="body2" fontWeight={600}>{value}</Typography>
        {!compact && <Typography variant="body2" color="text.secondary">{band} band</Typography>}
      </Box>
      <LinearProgress variant="determinate" value={value} color={color} sx={{ height: 6, borderRadius: 3, mt: 0.5 }} />
    </Box>
  );
}
