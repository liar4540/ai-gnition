"""
One-time Google sign-in on your own PC. Saves a refresh token into .env (never printed).

  venv\\Scripts\\python scripts\\get_google_refresh_token.py read   # gmail.readonly + calendar.readonly
  venv\\Scripts\\python scripts\\get_google_refresh_token.py act    # gmail.send + calendar.events

Before running: in Google Cloud Console, add  http://localhost:8765/  to your OAuth client's
"Authorized redirect URIs", and add the test Gmail account as a test user. Sign in with the
TEST Gmail account. Then run scripts\\setup_connections.ps1 to load the token into Orchestrate.
Uses only the Python standard library.
"""
import http.server
import json
import os
import re
import secrets
import socket
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_PATH = os.path.join(ROOT, ".env")
PORT = 8765
REDIRECT_URI = f"http://localhost:{PORT}/"
SCOPES = {
    "read": "https://www.googleapis.com/auth/gmail.readonly https://www.googleapis.com/auth/calendar.readonly",
    "act": "https://www.googleapis.com/auth/gmail.send https://www.googleapis.com/auth/calendar.events",
}
ENV_KEYS = {"read": "GOOGLE_READ_REFRESH_TOKEN", "act": "GOOGLE_ACT_REFRESH_TOKEN"}


def read_env() -> dict:
    values = {}
    with open(ENV_PATH, encoding="utf-8") as f:
        for line in f:
            m = re.match(r'^\s*([A-Z_][A-Z0-9_]*)\s*=\s*"?(.*?)"?\s*$', line)
            if m:
                values[m.group(1)] = m.group(2)
    return values


def write_env_value(key: str, value: str) -> None:
    with open(ENV_PATH, encoding="utf-8") as f:
        text = f.read()
    line = f'{key}="{value}"'
    if re.search(rf"(?m)^{key}=", text):
        text = re.sub(rf"(?m)^{key}=.*$", lambda _: line, text)
    else:
        text = text.rstrip("\n") + f"\n{line}\n"
    with open(ENV_PATH, "w", encoding="utf-8") as f:
        f.write(text)


def main() -> None:
    which = sys.argv[1] if len(sys.argv) > 1 else ""
    if which not in SCOPES:
        sys.exit("Usage: get_google_refresh_token.py read|act")
    env = read_env()
    client_id, client_secret = env.get("GOOGLE_CLIENT_ID", ""), env.get("GOOGLE_CLIENT_SECRET", "")
    if not client_id or client_id.startswith("<") or not client_secret or client_secret.startswith("<"):
        sys.exit("Fill GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in .env first.")

    state = secrets.token_urlsafe(16)
    auth_url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
        "client_id": client_id, "redirect_uri": REDIRECT_URI, "response_type": "code",
        # select_account: always show the account picker, so you can choose the TEST Gmail
        # even when the browser is signed in to another Google account.
        "scope": SCOPES[which], "access_type": "offline", "prompt": "select_account consent", "state": state,
    })
    result = {}
    done = threading.Event()

    def accept(url_or_path: str) -> bool:
        """Take the code from the redirect URL. Returns True once a code or error was found."""
        query = urllib.parse.parse_qs(urllib.parse.urlparse(url_or_path.strip()).query)
        if "code" not in query and "error" not in query:
            return False
        if query.get("state", [""])[0] != state:
            result["error"] = "state mismatch (use the link from THIS run of the script)"
        elif "error" in query:
            result["error"] = query["error"][0]
        else:
            result["code"] = query["code"][0]
        done.set()
        return True

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if not accept(self.path):
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            msg = "Signed in. You can close this tab." if "code" in result else f"Sign-in failed: {result['error']}"
            self.wfile.write(f"<p>{msg}</p>".encode("utf-8"))
            print("\nGot the reply from Google. Press Enter here to finish.")

        def log_message(self, *args):
            pass

    class DualStackServer(http.server.HTTPServer):
        """Listen on both ::1 and 127.0.0.1, because browsers may try either for 'localhost'."""
        address_family = socket.AF_INET6

        def server_bind(self):
            self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
            super().server_bind()

    try:
        server = DualStackServer(("::", PORT), Handler)
    except OSError:
        server = http.server.HTTPServer(("127.0.0.1", PORT), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    print(f"Opening Google sign-in for '{which}' access. Sign in with the TEST Gmail account.")
    print("If the browser does not open, paste this link into it:\n" + auth_url + "\n")
    print("Keep this window open. If the browser ends on 'can't reach this page', copy the FULL address")
    print("from the address bar (it starts with http://localhost:8765/?state=...), paste it here and press Enter.")
    webbrowser.open(auth_url)
    while not done.is_set():
        try:
            pasted = input("> ")
        except EOFError:
            done.wait()
            break
        if done.is_set():
            break
        if not accept(pasted):
            print("That address has no 'code=' in it. Paste the full address after signing in, or wait.")
    server.shutdown()
    if "error" in result:
        sys.exit(f"Sign-in failed: {result['error']}")

    body = urllib.parse.urlencode({"code": result["code"], "client_id": client_id, "client_secret": client_secret,
                                   "redirect_uri": REDIRECT_URI, "grant_type": "authorization_code"}).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request("https://oauth2.googleapis.com/token", data=body), timeout=30) as r:
            tokens = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        sys.exit(f"Token exchange failed: HTTP {e.code} {e.read().decode('utf-8', 'replace')[:300]}")
    if "refresh_token" not in tokens:
        sys.exit("Google returned no refresh token. Remove the app's access at myaccount.google.com/permissions and run again.")

    write_env_value(ENV_KEYS[which], tokens["refresh_token"])
    print(f"\nSaved {ENV_KEYS[which]} in .env (not shown). Next: run scripts\\setup_connections.ps1")


if __name__ == "__main__":
    main()
