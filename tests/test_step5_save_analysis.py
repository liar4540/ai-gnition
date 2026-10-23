import unittest

from tests.base import TempDBTestCase
import nyp_store as store
from nyp_safety_check import nyp_safety_check
from nyp_save_analysis import nyp_save_analysis, save, band_for, calendar_payload

KNOWN = ["acmecorp.com"]


def safety(message_id, **kw):
    args = dict(sender_address="hr@acmecorp.com", reply_to_address="", subject="Interview",
                body="Interview tomorrow at 2pm.", link_domains=[], auth_results="spf=pass",
                known_contacts=KNOWN)
    args.update(kw)
    return nyp_safety_check.fn(message_id=message_id, **args)


def plan(message_id, thread_id="th-int", **kw):
    args = dict(category="important", task_title="Reply to Acme interview invite",
                deadline="2026-10-23T14:00:00+08:00", action="reply_and_calendar",
                draft_reply="Thank you, I confirm I can attend.", delta=None, model_confidence=85,
                reason="Recruiter invited me to an interview with a fixed time.")
    args.update(kw)
    return save(message_id, thread_id, **args)


class SaveAnalysisTest(TempDBTestCase):
    def test_interview_email_creates_one_task_and_drafts(self):
        safety("m-int")
        r = plan("m-int")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["confidence"], 95)          # 85 + 10 for known sender
        self.assertEqual(r["band"], "high")
        self.assertTrue(r["needs_approval"])
        self.assertEqual(r["approvals_needed"], ["send_reply", "calendar_create"])
        self.assertEqual(len(store.list_tasks()), 1)
        self.assertEqual(store.get_task(r["task_id"])["status"], "pending_approval")
        reply = store.get_latest_draft(r["task_id"], "send_reply")
        self.assertEqual(reply["payload"], {"body": "Thank you, I confirm I can attend.", "thread_id": "th-int"})
        event = store.get_latest_draft(r["task_id"], "calendar_create")["payload"]
        self.assertEqual(event["reminders"], [{"method": "popup", "minutes": 1440}, {"method": "popup", "minutes": 60}])
        self.assertFalse(event["all_day"])

    def test_phishing_creates_no_task_and_is_logged_quarantined(self):
        safety("m-phish", sender_address="security@acrnecorp.com", body="Verify your account immediately.",
               link_domains=["evil.xyz"])
        # Even if the model calls it important, code quarantines it.
        r = plan("m-phish", thread_id="th-phish", draft_reply=None, action="calendar")
        self.assertTrue(r["ok"], r)
        self.assertTrue(r["quarantined"])
        self.assertEqual(r["category"], "suspicious")
        self.assertIsNone(r["task_id"])
        self.assertEqual(store.list_tasks(), [])
        self.assertEqual(store.count("drafts"), 0)
        self.assertTrue(any(e["event"] == "quarantined m-phish" for e in store.list_activity()))

    def test_second_email_in_thread_updates_same_task_with_delta(self):
        safety("m1", body="Meeting on Tuesday 3pm.")
        first = plan("m1", thread_id="th-meet", task_title="Attend project meeting", action="calendar",
                     draft_reply=None, deadline="2026-10-27T15:00:00+08:00")
        store.upsert_task(first["task_id"], calendar_event_id="evt-1")   # executor added it earlier
        safety("m2", body="Meeting moved from Tuesday to Friday.")
        second = plan("m2", thread_id="th-meet", task_title="Attend project meeting", action="calendar",
                      draft_reply=None, deadline="2026-10-30T15:00:00+08:00", delta="Moved from Tuesday to Friday")
        self.assertEqual(second["task_id"], first["task_id"])
        self.assertEqual(second["approvals_needed"], ["calendar_move"])
        task = store.get_task(first["task_id"])
        self.assertEqual(task["delta"], "Moved from Tuesday to Friday")
        self.assertEqual(task["source_message_ids"], "m1,m2")
        self.assertEqual(task["deadline"], "2026-10-30T15:00:00+08:00")
        self.assertEqual(len(store.list_tasks()), 1)
        self.assertTrue(any(e["event"] == "replanned" for e in store.list_activity()))

    def test_rejects_bad_planner_output(self):
        safety("m3")
        cases = [
            dict(category="urgent"),
            dict(action="forward_all"),
            dict(task_title="x" * 81),
            dict(category="suspicious", action="none", task_title=None),       # still has a draft_reply
            dict(model_confidence=150),
            dict(deadline="next friday"),
            dict(action="reply", draft_reply=None),
            dict(reason=""),
        ]
        for case in cases:
            with self.subTest(case=case):
                r = plan("m3", **case)
                self.assertFalse(r["ok"])
                self.assertTrue(r["rejected"])
        self.assertEqual(store.list_tasks(), [])

    def test_requires_safety_check_first(self):
        r = plan("never-checked")
        self.assertFalse(r["ok"])
        self.assertIn("nyp_safety_check", r["rejected"][0])

    def test_low_band_asks_instead_of_acting(self):
        safety("m4", sender_address="someone@unknown.org")
        r = plan("m4", thread_id="th-low", model_confidence=50)
        self.assertEqual(r["band"], "low")
        self.assertTrue(r["needs_clarification"])
        self.assertFalse(r["needs_approval"])
        self.assertEqual(r["approvals_needed"], [])
        self.assertEqual(store.count("drafts"), 0)
        self.assertEqual(store.get_task(r["task_id"])["status"], "open")

    def test_review_action_puts_task_on_hold(self):
        safety("m5", sender_address="billing@acmecorp.com")
        r = plan("m5", thread_id="th-rev", action="review", draft_reply=None, deadline=None,
                 task_title="Check odd invoice wording")
        self.assertEqual(store.get_task(r["task_id"])["status"], "on_hold")
        self.assertFalse(r["needs_approval"])

    def test_routine_creates_no_task(self):
        safety("m6", sender_address="news@shop.com")
        r = plan("m6", thread_id="th-news", category="routine", action="none", task_title=None,
                 draft_reply=None, deadline=None, model_confidence=95)
        self.assertTrue(r["ok"])
        self.assertIsNone(r["task_id"])
        self.assertEqual(store.get_analysis("m6")["category"], "routine")

    def test_tool_wrapper_accepts_strings(self):
        safety("m7")
        r = nyp_save_analysis.fn("m7", "th7", "important", "Pay utility bill", "2026-11-05", "calendar", None,
                                 None, 80, "Bill has a due date.")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["band"], "high")   # 80 + 10 = 90

    def test_verified_newsletter_wrongly_labelled_suspicious_is_saved_as_routine(self):
        nyp_safety_check.fn("n1", "deals@shop.com", "", "Sale", "Act now!", ["shop.com"], "dkim=pass", [], True)
        r = plan("n1", thread_id="tn1", category="suspicious", action="none", task_title=None, draft_reply=None,
                 deadline=None)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["category"], "routine")
        self.assertFalse(r["quarantined"])
        self.assertIn("verified newsletter", " ".join(r["reasons"]))

    def test_newsletter_with_a_warning_stays_quarantined_when_labelled_suspicious(self):
        nyp_safety_check.fn("n2", "deals@shop.com", "", "Notice", "Please verify your account.", ["shop.com"],
                            "dkim=pass", [], True)
        r = plan("n2", thread_id="tn2", category="suspicious", action="none", task_title=None, draft_reply=None,
                 deadline=None)
        self.assertEqual(r["category"], "suspicious")
        self.assertTrue(r["quarantined"])

    def test_band_edges_and_all_day_event(self):
        self.assertEqual([band_for(x) for x in (90, 89, 70, 69)], ["high", "medium", "medium", "low"])
        ev = calendar_payload("Pay bill", "2026-11-05")
        self.assertEqual((ev["all_day"], ev["start"], ev["end"]), (True, "2026-11-05", "2026-11-06"))


if __name__ == "__main__":
    unittest.main()
