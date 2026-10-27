import { useState } from "react";
import { NavLink, useLocation, useNavigate } from "react-router-dom";
import { Alert, AppBar, Badge, Box, Button, Drawer, IconButton, InputAdornment, List, ListItemButton, ListItemIcon,
  ListItemText, Snackbar, TextField, Toolbar, Tooltip, Typography, useMediaQuery } from "@mui/material";
import { useTheme } from "@mui/material/styles";
import MenuIcon from "@mui/icons-material/Menu";
import DashboardIcon from "@mui/icons-material/SpaceDashboard";
import InboxIcon from "@mui/icons-material/Inbox";
import GppBadIcon from "@mui/icons-material/GppBad";
import ChecklistIcon from "@mui/icons-material/Checklist";
import HistoryIcon from "@mui/icons-material/History";
import ShieldIcon from "@mui/icons-material/Shield";
import SearchIcon from "@mui/icons-material/Search";
import RefreshIcon from "@mui/icons-material/Refresh";
import ForumIcon from "@mui/icons-material/Forum";
import DarkModeIcon from "@mui/icons-material/DarkMode";
import LightModeIcon from "@mui/icons-material/LightMode";
import { useAgentData } from "../api/AgentData.jsx";
import ScanButton from "../components/ScanButton.jsx";
import AssistantDrawer from "../components/AssistantDrawer.jsx";
import EmailDetailDrawer from "../components/EmailDetailDrawer.jsx";
import TaskDetailDrawer from "../components/TaskDetailDrawer.jsx";
import OwnerSignIn from "../components/OwnerSignIn.jsx";
import ErrorBoundary from "../components/ErrorBoundary.jsx";
import { SIDEBAR } from "../theme.js";
import { timeAgo } from "../lib/format.js";

const NAV_WIDTH = 240;

export default function AppShell({ children, mode, onToggleMode }) {
  const theme = useTheme();
  const desktop = useMediaQuery(theme.breakpoints.up("md"));
  const [navOpen, setNavOpen] = useState(false);
  const [chatOpen, setChatOpen] = useState(false);
  const [search, setSearch] = useState("");
  const location = useLocation();
  const navigate = useNavigate();
  const { state, loading, lastUpdated, refresh, notice, clearNotice } = useAgentData();
  const c = state?.counts;

  const nav = [
    { to: "/", label: "Dashboard", icon: <DashboardIcon /> },
    { to: "/inbox", label: "Inbox", icon: <InboxIcon />, count: c?.analysed },
    { to: "/threats", label: "Threats", icon: <GppBadIcon />, count: c?.quarantined, alert: true },
    { to: "/tasks", label: "Tasks", icon: <ChecklistIcon />, count: c?.tasks_pending },
    { to: "/activity", label: "Activity", icon: <HistoryIcon /> },
  ];

  const sidebar = (
    <Box sx={{ width: NAV_WIDTH, height: "100%", bgcolor: SIDEBAR.bg, color: SIDEBAR.text, display: "flex", flexDirection: "column" }}>
      <Toolbar sx={{ gap: 1 }}>
        <ShieldIcon sx={{ color: SIDEBAR.accent }} />
        <Typography sx={{ fontWeight: 700, color: "#F8FAFC", fontSize: "1.05rem" }}>Inbox Shield</Typography>
      </Toolbar>
      <List component="nav" aria-label="Main" sx={{ px: 1 }}>
        {nav.map((item) => {
          const active = item.to === "/" ? location.pathname === "/" : location.pathname.startsWith(item.to);
          return (
            <ListItemButton key={item.to} component={NavLink} to={item.to} onClick={() => setNavOpen(false)}
                            aria-current={active ? "page" : undefined}
                            sx={{
                              borderRadius: 1.5, mb: 0.5, color: active ? "#F8FAFC" : SIDEBAR.text,
                              bgcolor: active ? SIDEBAR.active : "transparent",
                              "&:hover": { bgcolor: SIDEBAR.active },
                              "&.Mui-focusVisible": { outline: `2px solid ${SIDEBAR.accent}`, outlineOffset: -2 },
                            }}>
              <ListItemIcon sx={{ minWidth: 36, color: active ? SIDEBAR.accent : SIDEBAR.text }}>{item.icon}</ListItemIcon>
              <ListItemText primary={item.label} slotProps={{ primary: { fontWeight: active ? 600 : 500 } }} />
              {item.count > 0 && (
                <Badge badgeContent={item.count} max={999} color={item.alert ? "error" : "primary"}
                       sx={{ "& .MuiBadge-badge": { position: "static", transform: "none" } }} />
              )}
            </ListItemButton>
          );
        })}
      </List>
      <Box sx={{ mt: "auto", p: 2 }}>
        <Typography variant="caption" sx={{ color: "#94A3B8" }}>
          Nothing is sent without approval: on Telegram, or here by the signed-in owner.
        </Typography>
      </Box>
    </Box>
  );

  const submitSearch = (e) => {
    e.preventDefault();
    navigate(`/inbox${search.trim() ? `?q=${encodeURIComponent(search.trim())}` : ""}`);
  };

  return (
    <Box sx={{ display: "flex", minHeight: "100vh", bgcolor: "background.default" }}>
      {desktop ? (
        <Box component="aside" sx={{ width: NAV_WIDTH, flexShrink: 0, position: "fixed", inset: "0 auto 0 0" }}>{sidebar}</Box>
      ) : (
        <Drawer open={navOpen} onClose={() => setNavOpen(false)}>{sidebar}</Drawer>
      )}

      <Box sx={{ flexGrow: 1, minWidth: 0, ml: desktop ? `${NAV_WIDTH}px` : 0 }}>
        <AppBar position="sticky" color="inherit" elevation={0}
                sx={{ borderBottom: 1, borderColor: "divider", bgcolor: "background.paper" }}>
          <Toolbar sx={{ gap: { xs: 0.5, sm: 1 }, px: { xs: 1, sm: 3 } }}>
            {!desktop && (
              <IconButton edge="start" onClick={() => setNavOpen(true)} aria-label="Open navigation"><MenuIcon /></IconButton>
            )}
            {desktop ? (
              <Box component="form" onSubmit={submitSearch} role="search" sx={{ flexGrow: 1, maxWidth: 420 }}>
                <TextField size="small" fullWidth placeholder="Search emails, tasks, evidence" value={search}
                           onChange={(e) => setSearch(e.target.value)}
                           slotProps={{
                             htmlInput: { "aria-label": "Search emails" },
                             input: { startAdornment: <InputAdornment position="start"><SearchIcon fontSize="small" /></InputAdornment> },
                           }} />
              </Box>
            ) : (
              <Typography noWrap sx={{ fontWeight: 700, flexGrow: 1, minWidth: 0 }}>Inbox Shield</Typography>
            )}
            {desktop && <Box sx={{ flexGrow: 1 }} />}
            {desktop && lastUpdated && (
              <Typography variant="caption" color="text.secondary" role="status">Updated {timeAgo(lastUpdated)}</Typography>
            )}
            <Tooltip title="Refresh data">
              <span>
                <IconButton onClick={() => refresh(true)} disabled={loading} aria-label="Refresh data"><RefreshIcon /></IconButton>
              </span>
            </Tooltip>
            <Tooltip title={mode === "dark" ? "Light mode" : "Dark mode"}>
              <IconButton onClick={onToggleMode} aria-label="Toggle dark mode">{mode === "dark" ? <LightModeIcon /> : <DarkModeIcon />}</IconButton>
            </Tooltip>
            <OwnerSignIn compact={!desktop} />
            {desktop ? (
              <Button variant="outlined" startIcon={<ForumIcon />} onClick={() => setChatOpen(true)}>Assistant</Button>
            ) : (
              <IconButton onClick={() => setChatOpen(true)} aria-label="Open assistant"><ForumIcon /></IconButton>
            )}
            <ScanButton compact={!desktop} />
          </Toolbar>
        </AppBar>

        <Box component="main" sx={{ p: { xs: 2, sm: 3 }, maxWidth: 1600, mx: "auto" }}>
          <ErrorBoundary key={location.pathname} label="This page">{children}</ErrorBoundary>
        </Box>
      </Box>

      <ErrorBoundary label="The assistant"><AssistantDrawer open={chatOpen} onClose={() => setChatOpen(false)} /></ErrorBoundary>
      <ErrorBoundary label="The email details"><EmailDetailDrawer /></ErrorBoundary>
      <ErrorBoundary label="The task details"><TaskDetailDrawer /></ErrorBoundary>

      <Snackbar open={Boolean(notice)} autoHideDuration={notice?.severity === "info" ? 4000 : 8000} onClose={clearNotice}
                anchorOrigin={{ vertical: "bottom", horizontal: "left" }}>
        {notice ? (
          <Alert severity={notice.severity} variant="filled" onClose={clearNotice} role="status" sx={{ maxWidth: 520 }}>
            {notice.text}
          </Alert>
        ) : <span />}
      </Snackbar>
    </Box>
  );
}
