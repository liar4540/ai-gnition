import { Box, Card, CardActionArea, CardContent, Typography } from "@mui/material";
import { Link as RouterLink } from "react-router-dom";

const TONE = {
  neutral: "text.secondary",
  primary: "primary.main",
  error: "error.main",
  warning: "warning.main",
  success: "success.main",
};

export default function StatCard({ label, value, hint, icon, tone = "neutral", to }) {
  const body = (
    <CardContent sx={{ display: "flex", gap: 1.5, alignItems: "flex-start" }}>
      <Box sx={{ color: TONE[tone], display: "flex", mt: 0.25 }} aria-hidden="true">{icon}</Box>
      <Box sx={{ minWidth: 0 }}>
        <Typography variant="body2" color="text.secondary" noWrap>{label}</Typography>
        <Typography component="p" sx={{ fontSize: "1.75rem", fontWeight: 700, lineHeight: 1.2 }}>{value}</Typography>
        {hint && <Typography variant="body2" color="text.secondary">{hint}</Typography>}
      </Box>
    </CardContent>
  );
  return (
    <Card sx={{ height: "100%" }}>
      {to ? <CardActionArea component={RouterLink} to={to} sx={{ height: "100%" }}>{body}</CardActionArea> : body}
    </Card>
  );
}
