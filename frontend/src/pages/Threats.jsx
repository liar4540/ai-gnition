import { useMemo } from "react";
import { Box, Card, CardActionArea, CardContent, Grid, Skeleton, Stack, Typography } from "@mui/material";
import VerifiedUserIcon from "@mui/icons-material/VerifiedUser";
import { useAgentData, useMessageMeta } from "../api/AgentData.jsx";
import { EvidenceBadge, RiskChip } from "../components/Badges.jsx";
import { useEmailParam } from "../components/EmailDetailDrawer.jsx";
import { ErrorBanner, LoadingRows } from "../components/States.jsx";
import { RISK_ORDER, parseSender } from "../lib/derive.js";
import { formatDateTime } from "../lib/format.js";

const SEVERITY_ORDER = { high: 0, warning: 1, info: 2, safe: 3 };

export default function Threats() {
  const { emails, state, loading } = useAgentData();
  const { open } = useEmailParam();
  const threats = useMemo(
    () => emails.filter((e) => e.quarantined)
      .sort((a, b) => (RISK_ORDER[b.risk] ?? 0) - (RISK_ORDER[a.risk] ?? 0) || (b.created_at || "").localeCompare(a.created_at || "")),
    [emails]
  );
  useMessageMeta(useMemo(() => threats.map((t) => t.message_id), [threats]));

  return (
    <Box>
      <Typography variant="h1">Threats</Typography>
      <Typography color="text.secondary" sx={{ mb: 2 }}>
        Quarantined by plain-code safety checks. No reply was drafted and no task was created for these.
      </Typography>
      <ErrorBanner />
      {!state && loading ? <LoadingRows /> : !threats.length ? (
        <Card>
          <CardContent sx={{ display: "flex", gap: 1.5, alignItems: "center", color: "success.main" }}>
            <VerifiedUserIcon />
            <Typography>No threats quarantined.</Typography>
          </CardContent>
        </Card>
      ) : (
        <Grid container spacing={2}>
          {threats.map((t) => {
            const s = parseSender(t.from);
            const evidence = [...t.evidence].sort((a, b) => SEVERITY_ORDER[a.severity] - SEVERITY_ORDER[b.severity]);
            return (
              <Grid key={t.message_id} size={{ xs: 12, md: 6, xl: 4 }}>
                <Card sx={{ height: "100%", borderColor: t.risk === "high" ? "error.main" : "divider" }}>
                  <CardActionArea onClick={() => open(t.message_id)} sx={{ height: "100%", alignItems: "stretch" }}>
                    <CardContent>
                      <Stack direction="row" spacing={1} sx={{ alignItems: "center", mb: 1 }}>
                        <RiskChip risk={t.risk} />
                        <Typography variant="caption" color="text.secondary">{formatDateTime(t.receivedAt)}</Typography>
                      </Stack>
                      {t.metaState === "loading" ? <Skeleton width="70%" /> : (
                        <>
                          <Typography variant="h3" sx={{ wordBreak: "break-word" }}>
                            {t.metaState === "ok" ? t.subject || "(no subject)" : "Email no longer in Gmail"}
                          </Typography>
                          {t.metaState === "ok" && (
                            <Typography variant="body2" color="text.secondary" sx={{ wordBreak: "break-all" }}>{s.address}</Typography>
                          )}
                        </>
                      )}
                      <Stack spacing={0.75} sx={{ mt: 1.5, alignItems: "flex-start" }}>
                        {evidence.map((e) => <EvidenceBadge key={e.code} item={e} />)}
                      </Stack>
                    </CardContent>
                  </CardActionArea>
                </Card>
              </Grid>
            );
          })}
        </Grid>
      )}
    </Box>
  );
}
