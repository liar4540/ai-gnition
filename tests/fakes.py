"""Fake Google APIs for tests. No network, no credentials."""
import base64

from nyp_read_mail import GmailError


def b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def gmail_message(mid, thread, sender, subject, body, html=None, reply_to=None, auth=None):
    headers = [{"name": "From", "value": sender}, {"name": "Subject", "value": subject}]
    if reply_to:
        headers.append({"name": "Reply-To", "value": reply_to})
    if auth:
        headers.append({"name": "Authentication-Results", "value": auth})
    parts = [{"mimeType": "text/plain", "body": {"data": b64(body)}}]
    if html:
        parts.append({"mimeType": "text/html", "body": {"data": b64(html)}})
    return {"id": mid, "threadId": thread,
            "payload": {"mimeType": "multipart/alternative", "headers": headers, "body": {}, "parts": parts}}


class FakeGmail:
    """Minimal Gmail: an inbox, a history id, and history.list that can expire."""

    def __init__(self, messages):
        self.messages = {m["id"]: m for m in messages}
        self.order = [m["id"] for m in messages]
        self.history_id = 100
        self.added = []            # (history_id, message_id) after the first sync
        self.history_expired = False
        self.calls = []

    def deliver(self, msg):
        self.messages[msg["id"]] = msg
        self.order.append(msg["id"])
        self.history_id += 1
        self.added.append((self.history_id, msg["id"]))

    def __call__(self, path, params=None):
        params = params or {}
        self.calls.append((path, params))
        if path == "profile":
            return {"historyId": str(self.history_id)}
        if path == "messages":
            ids = list(reversed(self.order))[: params.get("maxResults", 500)]
            return {"messages": [{"id": i} for i in ids]}
        if path.startswith("messages/"):
            msg = self.messages[path.split("/", 1)[1]]
            if params.get("format") == "metadata":
                wanted = {h.lower() for h in params.get("metadataHeaders", [])}
                headers = [h for h in msg["payload"]["headers"] if h["name"].lower() in wanted]
                return {"id": msg["id"], "threadId": msg["threadId"], "payload": {"headers": headers}}
            return msg
        if path == "history":
            if self.history_expired:
                raise GmailError(404, "Not Found")
            start = int(params["startHistoryId"])
            new = [{"messagesAdded": [{"message": {"id": mid}}]} for hid, mid in self.added if hid > start]
            return {"history": new, "historyId": str(self.history_id)}
        raise AssertionError(f"unexpected Gmail call {path}")


class FakeTelegram:
    """Records sendMessage; getUpdates returns queued presses until confirmed with an offset."""

    def __init__(self, fail_send=False):
        self.sent = []
        self.answers = []
        self.queue = []
        self.next_update = 1
        self.fail_send = fail_send

    def press(self, data, chat_id):
        self.queue.append({"update_id": self.next_update,
                           "callback_query": {"id": f"cq{self.next_update}", "data": data,
                                              "message": {"chat": {"id": int(chat_id)}}}})
        self.next_update += 1

    def __call__(self, method, payload):
        if method == "sendMessage":
            if self.fail_send:
                raise RuntimeError("network down")
            self.sent.append(payload)
            return {"message_id": len(self.sent)}
        if method == "getUpdates":
            if "offset" in payload:
                self.queue = [u for u in self.queue if u["update_id"] >= payload["offset"]]
            return list(self.queue)
        if method == "answerCallbackQuery":
            self.answers.append(payload)
            return True
        raise AssertionError(f"unexpected Telegram method {method}")


class FakeCalendar:
    def __init__(self, events):
        self.events = events
        self.calls = []

    def __call__(self, params):
        self.calls.append(params)
        lo, hi = params["timeMin"], params["timeMax"]
        hits = [e for e in self.events if e["start"]["dateTime"] < hi and e["end"]["dateTime"] > lo]
        return {"items": hits}
