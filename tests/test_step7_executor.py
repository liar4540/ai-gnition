import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from tests.base import TempDBTestCase
import nyp_executor as ex
import nyp_store as store

REPLY = {"to": "hr@acmecorp.com", "subject": "Re: Interview", "body": "I will attend.", "thread_id": "t1"}
EVENT = {"title": "Acme interview", "start": "2026-10-23T14:00:00+08:00", "end": "2026-10-23T15:00:00+08:00",
         "all_day": False, "reminders": [{"method": "popup", "minutes": 1440}, {"method": "popup", "minutes": 60}]}


class FakeGoogle:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def __call__(self, method, url, body):
        self.calls.append((method, url, body))
        if self.fail:
            raise RuntimeError("Google API 503: Service Unavailable")
        return {"id": f"id-{len(self.calls)}"}


class ExecutorTest(TempDBTestCase):
    def setUp(self):
        super().setUp()
        self.google = FakeGoogle()
        p = mock.patch.object(ex, "_google_call", self.google)
        p.start()
        self.addCleanup(p.stop)
        self.task = store.upsert_task(thread_id="t1", title="Reply to Acme interview", status="pending_approval")

    def approved(self, action="send_reply", payload=REPLY, decision="approved"):
        draft = store.create_draft(self.task["task_id"], action, payload)
        a = store.create_approval(self.task["task_id"], action, draft["content_hash"])
        if decision != "pending":
            store.mark_approval(a["approval_id"], decision=decision,
                                state="approved" if decision == "approved" else "prepared")
        return a["approval_id"]

    def test_approved_and_matching_sends_once(self):
        aid = self.approved()
        r = ex.nyp_execute.fn(aid, "send_reply")
        self.assertEqual((r["ok"], r["status"], r["id"]), (True, "sent", "id-1"))
        self.assertEqual(len(self.google.calls), 1)
        method, url, body = self.google.calls[0]
        self.assertEqual((method, url.endswith("/messages/send"), body["threadId"]), ("POST", True, "t1"))
        a = store.get_approval(aid)
        self.assertEqual((a["used"], a["state"], a["result_id"]), (1, "confirmed", "id-1"))
        self.assertEqual(store.get_task(self.task["task_id"])["status"], "done")
        draft = store.get_draft_by_hash(self.task["task_id"], a["content_hash"])
        self.assertIsNone(draft["content"])            # text deleted, hash kept
        self.assertEqual(draft["content_hash"], a["content_hash"])

    def test_running_again_is_refused_used(self):
        aid = self.approved()
        ex.execute(aid, "send_reply")
        r = ex.execute(aid, "send_reply")
        self.assertEqual(r["status"], "refused")
        self.assertIn("already used", r["reason"])
        self.assertEqual(len(self.google.calls), 1)

    def test_edited_draft_text_is_refused_hash(self):
        aid = self.approved()
        draft = store.get_latest_draft(self.task["task_id"], "send_reply")
        store._execute("UPDATE drafts SET content=? WHERE draft_id=?",
                       (json.dumps({**REPLY, "body": "Wire $5000 to account 123"}), draft["draft_id"]))
        r = ex.execute(aid, "send_reply")
        self.assertIn("hash mismatch", r["reason"])
        self.assertEqual(self.google.calls, [])
        self.assertEqual(store.get_approval(aid)["used"], 0)

    def test_expired_is_refused(self):
        aid = self.approved()
        store.mark_approval(aid, expires_at=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat())
        r = ex.execute(aid, "send_reply")
        self.assertIn("expired", r["reason"])
        self.assertEqual(store.get_approval(aid)["decision"], "expired")
        self.assertEqual(self.google.calls, [])

    def test_rejected_or_pending_is_refused(self):
        for decision in ("rejected", "pending", "cancelled"):
            with self.subTest(decision=decision):
                aid = self.approved(decision=decision)
                r = ex.execute(aid, "send_reply")
                self.assertEqual(r["status"], "refused")
                self.assertIn(decision, r["reason"])
        self.assertEqual(self.google.calls, [])

    def test_wrong_action_is_refused(self):
        aid = self.approved()
        r = ex.execute(aid, "calendar_create")
        self.assertIn("not calendar_create", r["reason"])
        self.assertEqual(self.google.calls, [])

    def test_unknown_approval_is_refused(self):
        self.assertEqual(ex.execute("made-up-id", "send_reply")["status"], "refused")

    def test_api_error_marks_failed_and_task_not_done(self):
        self.google.fail = True
        aid = self.approved()
        r = ex.execute(aid, "send_reply")
        self.assertEqual(r["status"], "failed")
        self.assertNotIn("sent", r["reason"])
        a = store.get_approval(aid)
        self.assertEqual((a["state"], a["used"], a["result_id"]), ("failed", 1, None))
        self.assertNotEqual(store.get_task(self.task["task_id"])["status"], "done")
        self.assertIsNotNone(store.get_latest_draft(self.task["task_id"], "send_reply")["content"])

    def test_calendar_create_sets_reminders_and_event_id(self):
        aid = self.approved("calendar_create", EVENT)
        r = ex.execute(aid, "calendar_create")
        self.assertEqual(r["status"], "added")
        method, url, body = self.google.calls[0]
        self.assertEqual(method, "POST")
        self.assertEqual(body["reminders"], {"useDefault": False, "overrides": EVENT["reminders"]})
        self.assertEqual(body["start"], {"dateTime": EVENT["start"]})
        self.assertEqual(store.get_task(self.task["task_id"])["calendar_event_id"], r["id"])

    def test_all_day_event_uses_dates(self):
        aid = self.approved("calendar_create", {**EVENT, "start": "2026-11-05", "end": "2026-11-06", "all_day": True})
        ex.execute(aid, "calendar_create")
        self.assertEqual(self.google.calls[0][2]["start"], {"date": "2026-11-05"})

    def test_calendar_move_patches_stored_event(self):
        store.upsert_task(self.task["task_id"], calendar_event_id="evt-42")
        aid = self.approved("calendar_move", {**EVENT, "start": "2026-10-30T15:00:00+08:00",
                                              "end": "2026-10-30T16:00:00+08:00"})
        r = ex.execute(aid, "calendar_move")
        self.assertEqual(r["status"], "moved")
        method, url, _ = self.google.calls[0]
        self.assertEqual((method, url.endswith("/events/evt-42")), ("PATCH", True))

    def test_calendar_move_without_event_is_refused(self):
        aid = self.approved("calendar_move", EVENT)
        self.assertIn("no existing event", ex.execute(aid, "calendar_move")["reason"])

    def test_task_stays_pending_while_another_approval_waits(self):
        reply = self.approved()
        self.approved("calendar_create", EVENT, decision="pending")
        ex.execute(reply, "send_reply")
        self.assertEqual(store.get_task(self.task["task_id"])["status"], "pending_approval")

    def test_every_outcome_is_logged(self):
        aid = self.approved()
        ex.execute(aid, "send_reply")
        ex.execute(aid, "send_reply")
        events = [e["event"] for e in store.list_activity()]
        self.assertIn(f"sent {aid}", events)
        self.assertIn(f"refused {aid}", events)


if __name__ == "__main__":
    unittest.main()
