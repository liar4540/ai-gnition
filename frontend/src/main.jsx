import { StrictMode, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { CssBaseline, ThemeProvider, useMediaQuery } from "@mui/material";
import { makeTheme } from "./theme.js";
import { AgentDataProvider } from "./api/AgentData.jsx";
import App from "./App.jsx";

const MODE_KEY = "nyp-theme-mode";

function readSavedMode() {
  try {
    return localStorage.getItem(MODE_KEY);
  } catch {
    return null;
  }
}

function Root() {
  const prefersDark = useMediaQuery("(prefers-color-scheme: dark)");
  const [saved, setSaved] = useState(readSavedMode);
  const mode = saved || (prefersDark ? "dark" : "light");
  const theme = useMemo(() => makeTheme(mode), [mode]);

  const toggleMode = () => {
    const next = mode === "dark" ? "light" : "dark";
    setSaved(next);
    try {
      localStorage.setItem(MODE_KEY, next);
    } catch {
      /* storage blocked: keep the choice for this visit only */
    }
  };

  return (
    <ThemeProvider theme={theme}>
      <CssBaseline />
      <BrowserRouter>
        <AgentDataProvider>
          <App mode={mode} onToggleMode={toggleMode} />
        </AgentDataProvider>
      </BrowserRouter>
    </ThemeProvider>
  );
}

createRoot(document.getElementById("app")).render(
  <StrictMode>
    <Root />
  </StrictMode>
);
