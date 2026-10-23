import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from tests.base import TempDBTestCase
from tests.fakes import FakeGmail, FakeTelegram, gmail_message
import nyp_read_mail
import nyp_request_approval as ra
import nyp_store as store
from nyp_safety_check import nyp_safety_check
from nyp_save_analysis import save

CHAT = "111"
OTHER_CHAT = "999"
CONFIG = {"bot_token": "123:abc", "allowed_chat_id": CHAT}
INTERVIEW = gmail_message("m1", "t1", "Acme HR <hr@acmecorp.com>", "Interview invitation", "Tomorrow 2pm.")


class ApprovalTest(TempDBTestCase):
    def setUp(self):
        super().setUp()
        self.tg = FakeTelegram()
        self.kv = dict(CONFIG)
        p1 =mock.patch.object(ra, "_telegram_call", self.tg)
        p2 = mock.patch.object(nyp_read_mail, "_gmail_call", FakeGmail([INTERVIEW]))
        p3 = mock.patch.object(ra.connections, "key_value", lambda app_id: self.kv)
        for p in (p1, p2, p3):
            p.start()
            self.addCleanup(p.stop)
        nyp_safety_check.fn("m1", "hr@acmecorp.com", "", "Interview", "Tomorrow 2pm.", [], "", ["acmecorp.com"])
        self.task_id = save("m1", "t1", "important", "Reply to Acme interview invite", "2026-10-23T14:00:00+08:00",
                            "reply_and_calendar", "Thank you, I will attend.", None, 85,
                            "Recruiter set a time.")["task_id"]

    def request(self, action="send_reply"):
        r = ra.nyp_request_approval.fn(self.task_id, action)
        self.assertTrue(r["ok"], r)
        return r["approval_id"]

    def test_request_sends_card_with_exact_content_and_buttons(self):
        aid = self.request()
        a = store.get_approval(aid)
        self.assertEqual((a["decision"], a["state"], a["used"]), ("pending", "prepared", 0))
        msg = self.tg.sent[0]
        self.assertEqual(msg["chat_id"], CHAT)
        for text in ("Reply to Acme interview invite", "Reply to: Acme HR <hr@acmecorp.com>",
                     "Subject: Re: Interview invitation", "Thank you, I will attend.", "Confidence: 95",
                     "Sender domain is a known contact", "If you approve: Gmail sends exactly this reply"):
            self.assertIn(text, msg["text"])
        buttons = [b["callback_data"] for row in msg["reply_markup"]["inline_keyboard"] for b in row]
        self.assertEqual(buttons, [f"a:{aid}", f"r:{aid}", f"t:formal:{aid}", f"t:firm:{aid}", f"t:short:{aid}"])
        # The hash covers the full payload with recipient and subject filled in by code.
        draft = store.get_draft_by_hash(self.task_id, a["content_hash"])
        self.assertEqual(draft["payload"], {"to": "Acme HR <hr@acmecorp.com>", "subject": "Re: Interview invitation",
                                            "body": "Thank you, I will attend.", "thread_id": "t1"})
        self.assertEqual(a["content_hash"], store.content_hash(draft["payload"]))

    def test_calendar_card_has_event_and_reminders_and_no_tone_buttons(self):
        aid = self.request("calendar_create")
        msg = self.tg.sent[0]
        self.assertIn("When: Fri 23 Oct 2026, 2:00 PM to 3:00 PM (SGT)", msg["text"])
        self.assertIn("Expires: ", msg["text"])
        self.assertNotIn("UTC", msg["text"])
        self.assertIn("Reminders: 24h before, 1h before", msg["text"])
        self.assertEqual(len(msg["reply_markup"]["inline_keyboard"]), 1)
        self.assertEqual(store.get_approval(aid)["action"], "calendar_create")

    def test_approve_press(self):
        aid = self.request()
        self.tg.press(f"a:{aid}", CHAT)
        r = ra.nyp_check_approvals.fn()
        self.assertEqual(r["approved"], [{"approval_id": aid, "task_id": self.task_id, "action": "send_reply"}])
        a = store.get_approval(aid)
        self.assertEqual((a["decision"], a["state"], a["used"]), ("approved", "approved", 0))

    def test_press_from_other_chat_does_nothing(self):
        aid = self.request()
        self.tg.press(f"a:{aid}", OTHER_CHAT)
        r = ra.check_approvals()
        self.assertEqual((r["approved"], r["ignored"]), ([], 1))
        self.assertEqual(store.get_approval(aid)["decision"], "pending")

    def test_second_press_does_nothing(self):
        aid = self.request()
        self.tg.press(f"a:{aid}", CHAT)
        ra.check_approvals()
        self.tg.press(f"a:{aid}", CHAT)          # pressed again later
        r = ra.check_approvals()
        self.assertEqual(r["approved"], [])
        self.assertEqual(r["ignored"], 1)
        self.assertEqual(ra.check_approvals()["ignored"], 0)   # confirmed presses are not returned again

    def test_reject_deletes_draft_text(self):
        aid = self.request()
        self.tg.press(f"r:{aid}", CHAT)
        r = ra.check_approvals()
        self.assertEqual([x["approval_id"] for x in r["rejected"]], [aid])
        a = store.get_approval(aid)
        self.assertEqual(a["decision"], "rejected")
        draft = store.get_draft_by_hash(self.task_id, a["content_hash"])
        self.assertIsNone(draft["content"])
        self.assertEqual(store.get_task(self.task_id)["status"], "cancelled")   # shown as Rejected on the web

    def test_expired_approval_is_marked_expired(self):
        aid = self.request()
        past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        store.mark_approval(aid, expires_at=past)
        self.tg.press(f"a:{aid}", CHAT)
        r = ra.check_approvals()
        self.assertEqual(r["expired"], [aid])
        self.assertEqual(r["approved"], [])
        self.assertEqual(store.get_approval(aid)["decision"], "expired")

    def test_tone_press_then_revise_makes_new_hash_and_new_approval(self):
        old = self.request()
        self.tg.press(f"t:formal:{old}", CHAT)
        r = ra.check_approvals()
        self.assertEqual(r["tone_requests"][0]["tone"], "More formal")
        self.assertEqual(r["tone_requests"][0]["current_body"], "Thank you, I will attend.")
        self.assertEqual(store.get_approval(old)["decision"], "cancelled")
        new = ra.nyp_revise_draft.fn(old, "Dear Ms Tan, thank you for the invitation. I am pleased to confirm.")
        self.assertTrue(new["ok"], new)
        self.assertNotEqual(new["approval_id"], old)
        self.assertNotEqual(store.get_approval(new["approval_id"])["content_hash"],
                            store.get_approval(old)["content_hash"])
        self.assertIn("Dear Ms Tan", self.tg.sent[-1]["text"])
        # A second revise of the same tone request is refused.
        self.assertFalse(ra.revise_draft(old, "another rewrite")["ok"])

    def test_revise_refused_without_tone_press(self):
        aid = self.request()
        self.assertFalse(ra.revise_draft(aid, "sneaky rewrite")["ok"])

    def test_not_configured_creates_no_approval(self):
        for bad in ({}, {"bot_token": "<TELEGRAM_BOT_TOKEN>", "allowed_chat_id": "<TELEGRAM_CHAT_ID>"}):
            self.kv.clear()
            self.kv.update(bad)
            self.assertEqual(ra.nyp_request_approval.fn(self.task_id, "send_reply"),
                             {"ok": False, "error": "telegram not configured"})
            self.assertEqual(ra.nyp_check_approvals.fn(), {"ok": False, "error": "telegram not configured"})
        self.assertEqual(store.count("approvals"), 0)

    def test_failed_send_cancels_the_approval(self):
        self.tg.fail_send = True
        r = ra.request_approval(self.task_id, "send_reply")
        self.assertFalse(r["ok"])
        self.assertEqual([a["decision"] for a in store.list_approvals()], ["cancelled"])

    def test_new_request_cancels_older_pending_one(self):
        first = self.request("calendar_create")
        second = self.request("calendar_create")
        self.assertEqual(store.get_approval(first)["decision"], "cancelled")
        self.assertEqual(store.get_approval(second)["decision"], "pending")

    def test_user_reminder_card_says_it_came_from_chat(self):
        from nyp_save_analysis import create_reminder
        r = create_reminder("Leon's death anniversary", "2026-10-25")
        self.assertTrue(r["ok"], r)
        out = ra.request_approval(r["task_id"], "calendar_create")
        self.assertTrue(out["ok"], out)
        text = self.tg.sent[-1]["text"]
        self.assertIn("When: Sun 25 Oct 2026 (all day)", text)
        self.assertIn("Source: you asked for this in chat", text)
        self.assertNotIn("Risk: unknown", text)

    def test_reminder_tool_sends_its_card_without_a_second_call(self):
        before = len(self.tg.sent)
        r = ra.nyp_create_reminder.fn("Zoom meeting", "2026-10-25T15:00")
        self.assertTrue(r["ok"], r)
        self.assertTrue(r["approval"]["ok"], r["approval"])
        self.assertEqual(len(self.tg.sent), before + 1)
        self.assertIn("Sun 25 Oct 2026, 3:00 PM to 4:00 PM (SGT)", self.tg.sent[-1]["text"])
        a = store.get_approval(r["approval"]["approval_id"])
        self.assertEqual((a["action"], a["decision"]), ("calendar_create", "pending"))

    def test_saving_an_analysis_sends_its_approvals_in_code(self):
        from nyp_save_analysis import save_and_request
        nyp_safety_check.fn("m9", "hr@acmecorp.com", "", "Meet", "Friday 3pm.", [], "", ["acmecorp.com"])
        before = len(self.tg.sent)
        r = save_and_request("m9", "t9", "important", "Meet Acme on Friday", "2026-10-30T15:00:00+08:00",
                             "calendar", None, None, 85, "Meeting with a fixed time.")
        self.assertEqual([x["action"] for x in r["approvals"]], ["calendar_create"])
        self.assertTrue(r["approvals"][0]["ok"], r["approvals"])
        self.assertEqual(len(self.tg.sent), before + 1)

    def test_batch_save_saves_each_email_and_sends_approvals(self):
        from nyp_save_analysis import save_many
        nyp_safety_check.fn("b1", "hr@acmecorp.com", "", "Meet", "Friday 3pm.", [], "", ["acmecorp.com"])
        nyp_safety_check.fn("b2", "news@shop.com", "", "Deals", "Sale.", [], "", [])
        nyp_safety_check.fn("b3", "x@acrnecorp.com", "", "Verify", "Verify your account immediately.", ["evil.xyz"], "", ["acmecorp.com"])
        before = len(self.tg.sent)
        r = save_many([
            {"message_id": "b1", "thread_id": "tb1", "category": "important", "task_title": "Meet Acme Friday",
             "deadline": "2026-10-30T15:00:00+08:00", "action": "calendar", "model_confidence": 85, "reason": "Meeting."},
            {"message_id": "b2", "thread_id": "tb2", "category": "routine", "action": "none", "model_confidence": 95, "reason": "Ad."},
            {"message_id": "b3", "thread_id": "tb3", "category": "important", "action": "none", "model_confidence": 80, "reason": "Odd."},
            {"message_id": "b4", "thread_id": "tb4", "category": "nonsense", "action": "none", "model_confidence": 80, "reason": "x"},
        ])
        self.assertEqual((r["saved"], r["rejected"]), (3, 1))
        res = {x["message_id"]: x for x in r["results"]}
        self.assertEqual(res["b1"]["approvals_sent"], ["calendar_create"])
        self.assertTrue(res["b3"]["quarantined"])
        self.assertFalse(res["b4"]["ok"])
        self.assertEqual(len(self.tg.sent), before + 1)
        self.assertEqual(store.get_analysis("b2")["category"], "routine")

    def test_approve_removes_buttons_and_says_it_is_working(self):
        aid = self.request("calendar_create")
        card = self.tg.last_card_id()
        self.assertEqual(store.get_approval(aid)["telegram_message_id"], str(card))
        self.tg.press(f"a:{aid}", CHAT)
        ra.check_approvals()
        edit = self.tg.edits[-1]
        self.assertEqual((edit["message_id"], "reply_markup" in edit), (card, False))   # buttons gone
        self.assertTrue(edit["text"].endswith("✅ APPROVED"))
        self.assertIn("Approved. Adding it to Google Calendar now", self.tg.replies_to(card)[-1])

    def test_reject_removes_buttons_and_confirms(self):
        aid = self.request()
        card = self.tg.last_card_id()
        self.tg.press(f"r:{aid}", CHAT)
        ra.check_approvals()
        edit = self.tg.edits[-1]
        self.assertEqual((edit["message_id"], "reply_markup" in edit), (card, False))   # buttons gone
        self.assertIn("Approval needed:", edit["text"])                                  # card kept
        self.assertIn("❌ REJECTED", edit["text"])
        self.assertIn("Rejected. Nothing was sent or added.", self.tg.replies_to(card)[-1])

    def test_reject_one_of_two_keeps_task_waiting_for_the_other(self):
        self.request("calendar_create")
        reply = self.request()
        self.tg.press(f"r:{reply}", CHAT)
        ra.check_approvals()
        self.assertEqual(store.get_task(self.task_id)["status"], "pending_approval")

    def test_replaced_and_expired_cards_lose_their_buttons(self):
        first = self.request("calendar_create")
        first_card = self.tg.last_card_id()
        self.request("calendar_create")                  # replaces the first
        self.assertEqual(self.tg.edits[-1]["message_id"], first_card)
        self.assertIn("Replaced by a newer request", self.tg.replies_to(first_card)[-1])
        second = [a for a in store.list_approvals("pending")][0]
        store.mark_approval(second["approval_id"], expires_at=(datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat())
        ra.check_approvals()
        self.assertEqual(self.tg.edits[-1]["message_id"], int(second["telegram_message_id"]))
        self.assertEqual(store.get_approval(first)["decision"], "cancelled")

    def test_pressing_an_already_closed_card_tidies_it(self):
        aid = self.request()
        card = self.tg.last_card_id()
        self.tg.press(f"r:{aid}", CHAT)
        ra.check_approvals()
        edits = len(self.tg.edits)
        self.tg.press(f"a:{aid}", CHAT, card)           # old buttons still showing on another device
        ra.check_approvals()
        self.assertEqual(len(self.tg.edits), edits + 1)
        self.assertEqual(store.get_approval(aid)["decision"], "rejected")   # decision unchanged

    def test_executor_posts_done_under_the_card(self):
        import nyp_executor as ex
        aid = self.request("calendar_create")
        card = self.tg.last_card_id()
        self.tg.press(f"a:{aid}", CHAT)
        ra.check_approvals()
        with mock.patch.object(ex, "_google_call", lambda m, u, b: {"id": "evt-77"}):
            out = ex.execute(aid, "calendar_create")
        self.assertEqual(out["status"], "added")
        done = self.tg.replies_to(card)[-1]
        self.assertTrue(done.startswith("Done! 'Reply to Acme interview invite' was added to your Google Calendar"), done)
        self.assertIn("Fri 23 Oct 2026, 2:00 PM to 3:00 PM (SGT)", done)

    def test_executor_failure_is_reported_under_the_card(self):
        import nyp_executor as ex
        aid = self.request("calendar_create")
        card = self.tg.last_card_id()
        self.tg.press(f"a:{aid}", CHAT)
        ra.check_approvals()
        def boom(*_):
            raise RuntimeError("Google API 503: Service Unavailable")
        with mock.patch.object(ex, "_google_call", boom):
            self.assertEqual(ex.execute(aid, "calendar_create")["status"], "failed")
        self.assertIn("Could not finish this", self.tg.replies_to(card)[-1])
        self.assertIn("Nothing was changed", self.tg.replies_to(card)[-1])

    def test_bad_action_rejected(self):
        self.assertFalse(ra.request_approval(self.task_id, "forward_all")["ok"])


if __name__ == "__main__":
    unittest.main()
