import { createTheme } from "@mui/material/styles";

// Design tokens from UI UX Pro Max ("email security inbox SaaS dashboard"): flat style,
// inbox blue primary, red reserved for threats, slate text, Plus Jakarta Sans.
// Status colours are always paired with an icon and a word (never colour alone).
const tokens = {
  light: {
    primary: "#2563EB",
    background: "#F6F8FC",
    paper: "#FFFFFF",
    text: "#0F172A",
    muted: "#475569",
    border: "#E2E8F0",
    hover: "#F1F5FD",
    success: "#15803D",
    warning: "#B45309",
    error: "#DC2626",
    info: "#2563EB",
  },
  dark: {
    primary: "#60A5FA",
    background: "#0B1220",
    paper: "#111A2E",
    text: "#E2E8F0",
    muted: "#94A3B8",
    border: "#1E293B",
    hover: "#16223B",
    success: "#4ADE80",
    warning: "#FBBF24",
    error: "#F87171",
    info: "#60A5FA",
  },
};

export const SIDEBAR = { bg: "#0F172A", text: "#CBD5E1", active: "#1E293B", accent: "#60A5FA" };

export function makeTheme(mode) {
  const t = tokens[mode];
  return createTheme({
    palette: {
      mode,
      primary: { main: t.primary, contrastText: mode === "dark" ? "#0B1220" : "#FFFFFF" },
      success: { main: t.success },
      warning: { main: t.warning },
      error: { main: t.error },
      info: { main: t.info },
      background: { default: t.background, paper: t.paper },
      text: { primary: t.text, secondary: t.muted },
      divider: t.border,
      action: { hover: t.hover },
    },
    shape: { borderRadius: 8 },
    spacing: 8,
    typography: {
      fontFamily: '"Plus Jakarta Sans", "Segoe UI", system-ui, -apple-system, sans-serif',
      fontSize: 14,
      h1: { fontSize: "1.5rem", fontWeight: 700, lineHeight: 1.3 },
      h2: { fontSize: "1.125rem", fontWeight: 700, lineHeight: 1.4 },
      h3: { fontSize: "1rem", fontWeight: 600, lineHeight: 1.4 },
      body1: { lineHeight: 1.55 },
      body2: { lineHeight: 1.5 },
      overline: { fontWeight: 600, letterSpacing: "0.06em" },
      button: { textTransform: "none", fontWeight: 600 },
    },
    components: {
      MuiPaper: { defaultProps: { elevation: 0 }, styleOverrides: { root: { backgroundImage: "none" } } },
      MuiCard: { defaultProps: { variant: "outlined" } },
      MuiButton: { defaultProps: { disableElevation: true }, styleOverrides: { root: { minHeight: 40 } } },
      MuiChip: { styleOverrides: { root: { fontWeight: 600 } } },
      MuiTableCell: { styleOverrides: { head: { fontWeight: 600, color: t.muted, whiteSpace: "nowrap" } } },
      MuiTableRow: {
        styleOverrides: {
          root: { transition: "background-color 150ms ease", "&.MuiTableRow-hover:hover": { backgroundColor: t.hover } },
        },
      },
      MuiCssBaseline: {
        styleOverrides: {
          "@media (prefers-reduced-motion: reduce)": {
            "*, *::before, *::after": { transitionDuration: "0.01ms !important", animationDuration: "0.01ms !important" },
          },
        },
      },
    },
  });
}
