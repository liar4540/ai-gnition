// "How to try this" box for judges and first-time visitors. The demo address and video link come
// from /api/config (DEMO_GMAIL, DEMO_VIDEO_LINK in frontend/server/.env); nothing here is a secret.
import { useEffect, useState } from "react";
import { Card, CardContent, Link, Typography } from "@mui/material";

export default function HowToTry() {
  const [config, setConfig] = useState({});

  useEffect(() => {
    fetch("/api/config").then((r) => (r.ok ? r.json() : {})).then(setConfig).catch(() => setConfig({}));
  }, []);

  return (
    <Card component="section" aria-labelledby="how-to-try" sx={{ mb: 2, borderColor: "primary.main" }}>
      <CardContent>
        <Typography id="how-to-try" variant="h2" component="h2" sx={{ mb: 1 }}>How to try this</Typography>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
          This is a demo inbox with sample emails. No login needed.
        </Typography>
        <Typography component="ol" variant="body2" sx={{ m: 0, pl: 3, "& li": { mb: 0.75 } }}>
          <li>Press <b>Scan inbox</b> (top bar). Use the <b>▾</b> arrow next to it to scan older emails, e.g. the last 2 weeks.</li>
          <li>Open <b>Threats</b> to see why emails were quarantined.</li>
          <li>Open <b>Tasks</b> and click a task: you see why, and the <b>exact draft</b> reply and calendar event waiting for a decision.</li>
          <li>
            Optional: send an email from your own address to{" "}
            <b>{config.demo_email || "the demo inbox"}</b>, then scan again.
          </li>
        </Typography>
        <Typography variant="body2" sx={{ mt: 1 }}>
          Sending a reply or adding a calendar event is never automatic: only the owner can approve, on Telegram or
          here after <b>Owner sign in</b>.
          {config.video_link && (
            <> A demo video of the approval step is{" "}
              <Link href={config.video_link} target="_blank" rel="noopener">here</Link>.</>
          )}
        </Typography>
      </CardContent>
    </Card>
  );
}
