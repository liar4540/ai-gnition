import { Navigate, Route, Routes } from "react-router-dom";
import AppShell from "./layout/AppShell.jsx";
import Dashboard from "./pages/Dashboard.jsx";
import Inbox from "./pages/Inbox.jsx";
import Threats from "./pages/Threats.jsx";
import Tasks from "./pages/Tasks.jsx";
import Activity from "./pages/Activity.jsx";

export default function App({ mode, onToggleMode }) {
  return (
    <AppShell mode={mode} onToggleMode={onToggleMode}>
      <Routes>
        <Route path="/" element={<Dashboard />} />
        <Route path="/inbox" element={<Inbox />} />
        <Route path="/threats" element={<Threats />} />
        <Route path="/tasks" element={<Tasks />} />
        <Route path="/activity" element={<Activity />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </AppShell>
  );
}
