// Owner sign-in: a PIN only the inbox owner knows (OWNER_PIN on the server). Signed in, the
// owner can Send / Don't send / Edit drafts on the dashboard. Hidden when the server has no PIN.
import { useState } from "react";
import { Alert, Button, Chip, Dialog, DialogActions, DialogContent, DialogContentText, DialogTitle, TextField,
  Tooltip } from "@mui/material";
import LockOutlinedIcon from "@mui/icons-material/LockOutlined";
import VerifiedUserIcon from "@mui/icons-material/VerifiedUser";
import { useAgentData } from "../api/AgentData.jsx";

export default function OwnerSignIn({ variant = "outlined", label = "Owner sign in", compact = false }) {
  const { owner, signIn, signOut } = useAgentData();
  const [open, setOpen] = useState(false);
  const [pin, setPin] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  if (!owner.enabled) return null;
  if (owner.owner) {
    return (
      <Tooltip title="Signed in as the owner: you can send or reject drafts here. Click to sign out.">
        <Chip icon={<VerifiedUserIcon />} label={compact ? "Owner" : "Owner · sign out"} color="success" variant="outlined"
              onClick={signOut} aria-label="Signed in as owner. Sign out" />
      </Tooltip>
    );
  }

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await signIn(pin);
      setOpen(false);
      setPin("");
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      {compact ? (
        <Tooltip title={label}>
          <Button size="small" variant={variant} onClick={() => setOpen(true)} aria-label={label} sx={{ minWidth: 40 }}>
            <LockOutlinedIcon fontSize="small" />
          </Button>
        </Tooltip>
      ) : (
        <Button size="small" variant={variant} startIcon={<LockOutlinedIcon />} onClick={() => setOpen(true)}>{label}</Button>
      )}
      <Dialog open={open} onClose={() => setOpen(false)} aria-labelledby="owner-title">
        <form onSubmit={submit}>
          <DialogTitle id="owner-title">Owner sign in</DialogTitle>
          <DialogContent>
            <DialogContentText sx={{ mb: 2 }}>
              Only the inbox owner can send replies or add events from the dashboard. Everyone else can look,
              and approvals still work on Telegram.
            </DialogContentText>
            <TextField autoFocus fullWidth type="password" label="PIN" value={pin} onChange={(e) => setPin(e.target.value)}
                       slotProps={{ htmlInput: { inputMode: "numeric", autoComplete: "current-password" } }} />
            {error && <Alert severity="error" sx={{ mt: 2 }}>{error}</Alert>}
          </DialogContent>
          <DialogActions>
            <Button onClick={() => setOpen(false)}>Cancel</Button>
            <Button type="submit" variant="contained" disabled={!pin || busy}>Sign in</Button>
          </DialogActions>
        </form>
      </Dialog>
    </>
  );
}
