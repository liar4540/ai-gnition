"""The Supabase backend of nyp_store, against an in-memory fake of Supabase's REST API.

The store and approval test suites run again here unchanged, so both backends must behave the
same. Then: how the backend is chosen, the request headers, and the owner's web decisions."""
import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from tests.base import TempDBTestCase
from tests.fakes import FakeSupabase
from tests.test_step1_store import StoreTest
from tests.test_step6_approval import CHAT, ApprovalTest
import nyp_request_approval as ra
import nyp_store as store


def use_fake_supabase(test):
    """Point the store at a fresh FakeSupabase for the rest of this test."""
    test.supa = FakeSupabase()
    patcher = mock.patch.object(store, "_rest_call", test.supa)
    patcher.start()
    test.addCleanup(patcher.stop)
    old = store._backend_override
    store._backend_override = store._Supabase("https://demo.supabase.co", test.supa.key)
    test.addCleanup(setattr, store, "_backend_override", old)


class SupabaseStoreTest(StoreTest):
    def setUp(self):
        super().setUp()
        use_fake_supabase(self)

    def test_reset_needs_confirmation_and_clears_everything(self):
        # Same as the SQLite test, except Postgres keeps counting ids after a reset.
        store.set_sync_state("old-mailbox-history")
        t = store.upsert_task(thread_id="th1", title="Old task")
        store.create_approval(t["task_id"], "send_reply", "abc")
        self.assertFalse(store.nyp_store_reset.fn("yes")["reset"])
        r = store.nyp_store_reset.fn("RESET")
        self.assertTrue(r["reset"])
        self.assertIsNone(store.get_sync_state()["history_id"])
        self.assertEqual((r["counts"]["tasks"], r["counts"]["approvals"], r["counts"]["activity_log"]), (0, 0, 1))

    def test_data_persists_across_processes(self):
        self.skipTest("the fake lives in this process; persistence is Supabase's job")

    def test_backend_reports_supabase(self):
        self.assertEqual(store.nyp_store_init.fn()["backend"], "supabase")
        self.assertEqual(store.backend_name(), "supabase")


class SupabaseApprovalTest(ApprovalTest):
    """Every approval, Telegram and reject test, on the Supabase backend."""

    def setUp(self):
        use_fake_supabase(self)
        super().setUp()


class BackendChoiceTest(unittest.TestCase):
    def setUp(self):
        self._env = {k: os.environ.pop(k, None) for k in ("NYP_DB_PATH", "SUPABASE_URL", "SUPABASE_SERVICE_KEY")}
        store._supabase_config = None
        self.addCleanup(self._restore)

    def _restore(self):
        for k, v in self._env.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v
        store._supabase_config = None

    def test_nothing_configured_falls_back_to_sqlite(self):
        with mock.patch.object(store.connections, "key_value", side_effect=SystemExit(1)):
            self.assertEqual(store._db().name, "sqlite")

    def test_connection_with_url_and_key_selects_supabase(self):
        kv = {"url": "https://abc.supabase.co", "service_key": "sb_secret_x"}
        with mock.patch.object(store.connections, "key_value", return_value=kv):
            db = store._db()
        self.assertEqual((db.name, db.base), ("supabase", "https://abc.supabase.co/rest/v1/"))

    def test_placeholders_do_not_count_as_configured(self):
        kv = {"url": "<your supabase url>", "service_key": "<secret>"}
        with mock.patch.object(store.connections, "key_value", return_value=kv):
            self.assertEqual(store._db().name, "sqlite")

    def test_nyp_db_path_always_wins_for_tests(self):
        os.environ["NYP_DB_PATH"] = "x.db"
        os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"] = "https://abc.supabase.co", "sb_secret_x"
        self.assertEqual(store._db().name, "sqlite")

    def test_env_vars_select_supabase(self):
        os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"] = "https://abc.supabase.co", "sb_secret_x"
        self.assertEqual(store._db().name, "supabase")


class RequestHeaderTest(unittest.TestCase):
    def _headers(self, key):
        seen = {}

        def fake(method, url, headers, body=None):
            seen.update(headers)
            return 200, [], {}

        with mock.patch.object(store, "_rest_call", fake):
            store._Supabase("https://abc.supabase.co", key).select("tasks", {"status": "open"})
        return seen

    def test_new_secret_key_goes_in_apikey_only(self):
        h = self._headers("sb_secret_abc")
        self.assertEqual(h["apikey"], "sb_secret_abc")
        self.assertNotIn("Authorization", h)

    def test_legacy_service_role_jwt_also_sent_as_bearer(self):
        self.assertEqual(self._headers("eyJhbGciOi.x.y")["Authorization"], "Bearer eyJhbGciOi.x.y")

    def test_table_and_column_names_are_checked(self):
        with self.assertRaises(ValueError):
            store._Supabase("https://abc.supabase.co", "k").select("tasks; drop", {})
        with self.assertRaises(ValueError):
            store._Supabase("https://abc.supabase.co", "k").select("tasks", {"status=eq.x&": "y"})


class WebDecisionTest(ApprovalTest):
    """The owner's Send / Don't send / Edit from the dashboard, applied by nyp_check_approvals."""

    def test_web_approve_goes_through_the_same_gate_as_telegram(self):
        aid = self.request()
        card = self.tg.last_card_id()
        store.queue_web_decision(aid, "approve")
        r = ra.check_approvals()
        self.assertEqual([x["approval_id"] for x in r["approved"]], [aid])   # the agent then runs nyp_execute
        self.assertEqual(store.get_approval(aid)["decision"], "approved")
        edit = self.tg.edits[-1]
        self.assertEqual((edit["message_id"], "reply_markup" in edit), (card, False))   # buttons gone
        self.assertIn("✅ APPROVED ON THE WEB DASHBOARD", edit["text"])
        self.assertIn("Approved on the web dashboard.", self.tg.replies_to(card)[-1])
        self.assertEqual(store.list_queued_web_decisions(), [])

    def test_web_reject_changes_nothing_and_closes_the_card(self):
        aid = self.request()
        store.queue_web_decision(aid, "reject")
        r = ra.check_approvals()
        self.assertEqual([x["approval_id"] for x in r["rejected"]], [aid])
        self.assertEqual(store.get_task(self.task_id)["status"], "cancelled")
        self.assertIsNone(store.get_draft_by_hash(self.task_id, store.get_approval(aid)["content_hash"])["content"])
        self.assertIn("❌ REJECTED ON THE WEB DASHBOARD", self.tg.edits[-1]["text"])

    def test_web_edit_makes_a_new_approval_for_the_new_text(self):
        old = self.request()
        store.queue_web_decision(old, "edit", "Thanks! I confirm 2pm tomorrow.")
        ra.check_approvals()
        self.assertEqual(store.get_approval(old)["decision"], "cancelled")
        new = [a for a in store.list_approvals("pending") if a["action"] == "send_reply"]
        self.assertEqual(len(new), 1)
        draft = store.get_draft_by_hash(self.task_id, new[0]["content_hash"])
        self.assertEqual(draft["payload"]["body"], "Thanks! I confirm 2pm tomorrow.")
        self.assertIn("hr@acmecorp.com", draft["payload"]["to"])            # recipient still from Gmail
        self.assertIn("Thanks! I confirm 2pm tomorrow.", self.tg.sent[-1]["text"])   # new card shows new text
        done = store._db().select("web_decisions", {"status": "applied"})
        self.assertIsNone(done[0]["body"])                                 # edited text not kept

    def test_web_decision_on_a_closed_or_expired_approval_is_refused(self):
        other = self.request("calendar_create")
        aid = self.request()
        self.tg.press(f"r:{aid}", CHAT)
        ra.check_approvals()                                               # rejected on Telegram first
        store.queue_web_decision(aid, "approve")
        r = ra.check_approvals()
        self.assertEqual(r["approved"], [])
        self.assertEqual(store.get_approval(aid)["decision"], "rejected")
        failed = store._db().select("web_decisions", {"status": "failed"})
        self.assertEqual(len(failed), 1)

        store.queue_web_decision(other, "approve")                         # approved on the web ...
        ra.check_approvals()
        store.queue_web_decision(other, "reject")                          # ... so a later reject is refused
        self.assertEqual(ra.check_approvals()["rejected"], [])
        self.assertEqual(store.get_approval(other)["decision"], "approved")

    def _expire(self, aid):
        store.mark_approval(aid, expires_at=(datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat())

    def test_expiry_keeps_the_draft_for_the_dashboard(self):
        aid = self.request()
        self._expire(aid)
        ra.check_approvals()
        a = store.get_approval(aid)
        self.assertEqual(a["decision"], "expired")
        self.assertIsNotNone(store.get_draft_by_hash(self.task_id, a["content_hash"])["payload"])

    def test_owner_send_after_expiry_renews_the_same_content_and_approves_it(self):
        aid = self.request()
        old_hash = store.get_approval(aid)["content_hash"]
        self._expire(aid)
        store.queue_web_decision(aid, "approve")
        r = ra.check_approvals()
        self.assertEqual(store.get_approval(aid)["decision"], "expired")   # the old one stays expired
        self.assertEqual(len(r["approved"]), 1)
        new = store.get_approval(r["approved"][0]["approval_id"])
        self.assertNotEqual(new["approval_id"], aid)
        self.assertEqual((new["decision"], new["content_hash"]), ("approved", old_hash))   # exact same text

    def test_owner_dont_send_after_expiry_drops_the_text(self):
        aid = self.request()
        self._expire(aid)
        ra.check_approvals()
        store.queue_web_decision(aid, "reject")
        r = ra.check_approvals()
        self.assertEqual([x["approval_id"] for x in r["rejected"]], [aid])
        self.assertEqual(store.get_approval(aid)["decision"], "rejected")
        self.assertIsNone(store.get_draft_by_hash(self.task_id, store.get_approval(aid)["content_hash"])["content"])

    def test_edit_of_a_calendar_approval_is_refused(self):
        aid = self.request("calendar_create")
        store.queue_web_decision(aid, "edit", "new text")
        ra.check_approvals()
        self.assertEqual(store.get_approval(aid)["decision"], "pending")
        self.assertEqual(len(store._db().select("web_decisions", {"status": "failed"})), 1)

    def test_unknown_web_decision_is_rejected_by_the_store(self):
        with self.assertRaises(ValueError):
            store.queue_web_decision("x", "send_everything")


class SupabaseWebDecisionTest(WebDecisionTest):
    def setUp(self):
        use_fake_supabase(self)
        super().setUp()


if __name__ == "__main__":
    unittest.main()
