import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// /api goes to the local API server (server/index.js), which talks to the agent.
const apiPort = process.env.API_PORT || 8787;

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": `http://localhost:${apiPort}` },
  },
});
