import unittest
from unittest import mock

from tests.base import TempDBTestCase
from tests.fakes import FakeGmail, b64, gmail_message
import nyp_read_mail
import nyp_store as store

INTERVIEW = gmail_message("m1", "t1", "Acme HR <hr@acmecorp.com>", "Interview invitation",
                          "Please join us tomorrow at 2pm. Details: https://careers.acmecorp.com/i/42",
                          auth="mx.google.com; spf=pass; dkim=pass; dmarc=pass")
PHISH = gmail_message("m2", "t2", "Security <security@paypa1.com>", "Verify your account",
                      "Click now", html='<p>Click <a href="https://evil-login.xyz:8443/a">here</a></p>',
                      reply_to="collect@other.net")


class ReadMailTest(TempDBTestCase):
    def setUp(self):
        super().setUp()
        self.gmail = FakeGmail([INTERVIEW, PHISH])
        patcher = mock.patch.object(nyp_read_mail, "_gmail_call", self.gmail)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_first_run_returns_inbox_then_nothing_new(self):
        first = nyp_read_mail.nyp_read_new_mail.fn()
        self.assertEqual({m["message_id"] for m in first}, {"m1", "m2"})
        self.assertEqual(store.get_sync_state()["history_id"], "100")
        self.assertEqual(nyp_read_mail.nyp_read_new_mail.fn(), [])

    def test_new_mail_arrives_via_history(self):
        nyp_read_mail.read_new_mail()
        self.gmail.deliver(gmail_message("m3", "t1", "hr@acmecorp.com", "Re: Interview", "Moved to Friday."))
        again = nyp_read_mail.read_new_mail()
        self.assertEqual([m["message_id"] for m in again], ["m3"])
        self.assertEqual(nyp_read_mail.read_new_mail(), [])

    def test_expired_history_resyncs_and_logs(self):
        nyp_read_mail.read_new_mail()
        store.save_analysis("m1", "t1", "important", "low", 95, "high", [], False)   # already analysed
        self.gmail.history_expired = True
        out = nyp_read_mail.read_new_mail()
        self.assertEqual([m["message_id"] for m in out], ["m2"])        # m1 skipped
        resync_query = [p for path, p in self.gmail.calls if path == "messages" and "q" in p]
        self.assertEqual(resync_query[0]["q"], "newer_than:3d")
        self.assertTrue(any(e["event"] == "history_expired_resync" for e in store.list_activity(50)))

    def test_message_fields_and_link_domains(self):
        m = nyp_read_mail.nyp_get_message.fn("m2")
        self.assertEqual(set(m), {"message_id", "thread_id", "sender_address", "reply_to_address", "subject",
                                  "body", "link_domains", "auth_results", "bulk_mail"})
        self.assertEqual(m["reply_to_address"], "collect@other.net")
        self.assertEqual(m["link_domains"], ["evil-login.xyz"])
        i = nyp_read_mail.get_message("m1")
        self.assertEqual(i["link_domains"], ["careers.acmecorp.com"])
        self.assertIn("dmarc=pass", i["auth_results"])

    def test_body_truncated_and_html_only_body(self):
        long = gmail_message("m9", "t9", "a@b.com", "Long", "x" * 9000)
        self.gmail.messages["m9"] = long
        self.assertEqual(len(nyp_read_mail.get_message("m9")["body"]), 4000)
        html_only = {"id": "m10", "threadId": "t10", "payload": {
            "mimeType": "text/html", "headers": [],
            "body": {"data": b64("<p>Hello&nbsp;<b>there</b></p><script>x()</script>")}}}
        self.gmail.messages["m10"] = html_only
        self.assertEqual(nyp_read_mail.get_message("m10")["body"], "Hello there")

    def test_no_email_text_in_store(self):
        nyp_read_mail.read_new_mail()
        dumped = str(store.list_activity(50)) + str(store.get_sync_state())
        for text in ("Interview invitation", "acmecorp", "paypa1", "Click"):
            self.assertNotIn(text, dumped)

    def test_reply_headers_prefer_reply_to(self):
        self.assertEqual(nyp_read_mail.get_reply_headers("m2"),
                         {"to": "collect@other.net", "subject": "Re: Verify your account", "thread_id": "t2"})
        self.assertEqual(nyp_read_mail.get_reply_headers("m1")["to"], "Acme HR <hr@acmecorp.com>")

    def test_range_query_from_period_or_dates(self):
        q = nyp_read_mail.range_query
        self.assertEqual(q("2 weeks"), "in:inbox newer_than:14d")
        self.assertEqual(q("10 days"), "in:inbox newer_than:10d")
        self.assertEqual(q("3 months"), "in:inbox newer_than:3m")
        self.assertEqual(q("1w"), "in:inbox newer_than:7d")
        self.assertEqual(q(start_date="2026-09-01"), "in:inbox after:2026/09/01")
        self.assertEqual(q(start_date="2026-09-01", end_date="2026-09-30"), "in:inbox after:2026/09/01 before:2026/10/01")
        for bad in (dict(), dict(period="2 weeks", start_date="2026-09-01"), dict(period="soon"),
                    dict(period="13 months"), dict(start_date="01/09/2026"),
                    dict(start_date="2026-09-30", end_date="2026-09-01")):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                q(**bad)

    def test_range_read_skips_analysed_batches_and_keeps_bookmark(self):
        for i in range(12):
            self.gmail.messages[f"old{i}"] = gmail_message(f"old{i}", f"to{i}", "a@b.com", f"Old {i}", "text")
            self.gmail.order.insert(0, f"old{i}")
        store.set_sync_state("555")
        store.save_analysis("m1", "t1", "important", "low", 95, "high", [], False)
        out = nyp_read_mail.nyp_read_mail_range.fn("2 weeks")
        self.assertEqual(out["query"], "in:inbox newer_than:14d")
        self.assertEqual((out["found"], out["already_analysed"], out["returned"]), (14, 1, 8))
        self.assertEqual(out["remaining_after_this_batch"], 5)
        self.assertNotIn("m1", [m["message_id"] for m in out["messages"]])
        self.assertEqual(store.get_sync_state()["history_id"], "555")       # new-mail bookmark untouched
        self.assertEqual([p["q"] for path, p in self.gmail.calls if path == "messages"], ["in:inbox newer_than:14d"])

    def test_read_tools_run_safety_checks_in_code(self):
        out = nyp_read_mail.read_new_mail(["acmecorp.com"])
        by_id = {m["message_id"]: m for m in out}
        self.assertIn("SENDER_DOMAIN_KNOWN", by_id["m1"]["safety"]["evidence"])
        self.assertIn("REPLY_TO_MISMATCH", by_id["m2"]["safety"]["evidence"])
        self.assertIn("LINK_DOMAIN_MISMATCH", by_id["m2"]["safety"]["evidence"])
        row = store.get_analysis("m2")                       # evidence stored as codes only
        self.assertEqual(row["evidence"], by_id["m2"]["safety"]["evidence"])
        self.assertIsNone(row["category"])                   # not yet classified by the model

    def test_safety_runs_on_full_body_before_shortening(self):
        long = gmail_message("m20", "t20", "a@b.com", "Long", "x " * 2000 + "Please verify your account immediately.")
        self.gmail.messages["m20"] = long
        self.gmail.order.insert(0, "m20")
        out = nyp_read_mail.read_mail_range("2 weeks")
        m = next(x for x in out["messages"] if x["message_id"] == "m20")
        self.assertLessEqual(len(m["body"]), nyp_read_mail.RANGE_BODY_LIMIT)
        self.assertIn("URGENT_LANGUAGE", m["safety"]["evidence"])   # found beyond the shortened part

    def test_range_tool_returns_error_instead_of_raising(self):
        out = nyp_read_mail.nyp_read_mail_range.fn()
        self.assertIn("either a period", out["error"])
        self.assertEqual(out["messages"], [])

    def test_only_get_requests_no_modify_paths(self):
        nyp_read_mail.read_new_mail()
        for path, _ in self.gmail.calls:
            self.assertNotIn(path.split("/")[-1], ("send", "modify", "trash", "labels"))


if __name__ == "__main__":
    unittest.main()
