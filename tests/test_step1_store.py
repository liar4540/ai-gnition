import os
import subprocess
import sys
import unittest

from tests.base import TOOLS_DIR, TempDBTestCase
import nyp_store as store


class StoreTest(TempDBTestCase):
    def test_init_creates_all_tables(self):
        result = store.nyp_store_init.fn()
        self.assertEqual(set(result["counts"]), set(store.TABLES))
        self.assertEqual(result["counts"]["activity_log"], 1)

    def test_sync_state_roundtrip(self):
        self.assertIsNone(store.get_sync_state()["history_id"])
        store.set_sync_state("12345")
        self.assertEqual(store.get_sync_state()["history_id"], "12345")
        store.set_sync_state("12399")
        self.assertEqual(store.get_sync_state()["history_id"], "12399")

    def test_task_insert_update_and_lookup_by_thread(self):
        t = store.upsert_task(thread_id="th1", title="Reply to interview invite", deadline="2026-10-23")
        self.assertEqual(t["status"], "open")
        store.upsert_task(t["task_id"], delta="moved Tue to Fri", status="pending_approval")
        again = store.get_task_by_thread("th1")
        self.assertEqual(again["task_id"], t["task_id"])
        self.assertEqual(again["delta"], "moved Tue to Fri")
        self.assertEqual(again["deadline"], "2026-10-23")
        self.assertEqual([x["task_id"] for x in store.list_tasks("pending_approval")], [t["task_id"]])

    def test_upsert_rejects_unknown_fields_and_bad_status(self):
        with self.assertRaises(ValueError):
            store.upsert_task(thread_id="th1", title="x", subject="leaked email subject")
        with self.assertRaises(Exception):
            store.upsert_task(thread_id="th1", title="x", status="whatever")

    def test_tool_upsert_keeps_deadline_and_limits_title(self):
        t = store.nyp_upsert_task.fn("th2", "Pay utility bill", "2026-11-01")
        updated = store.nyp_upsert_task.fn("th2", "Pay utility bill", None, "done", t["task_id"])
        self.assertEqual(updated["deadline"], "2026-11-01")
        with self.assertRaises(ValueError):
            store.nyp_upsert_task.fn("th2", "x" * 81)

    def test_draft_hash_and_delete_text(self):
        payload = {"to": "a@b.com", "subject": "Re: hi", "body": "Thanks", "thread_id": "th1"}
        d = store.create_draft(1, "send_reply", payload)
        self.assertEqual(d["content_hash"], store.content_hash(payload))
        self.assertEqual(d["payload"], payload)
        # key order must not change the hash
        self.assertEqual(store.content_hash(dict(reversed(list(payload.items())))), d["content_hash"])
        store.delete_draft_text(d["draft_id"])
        gone = store.get_draft(d["draft_id"])
        self.assertIsNone(gone["content"])
        self.assertEqual(gone["content_hash"], d["content_hash"])
        self.assertEqual(gone["deleted"], 1)

    def test_approval_lifecycle_and_single_claim(self):
        a = store.create_approval(1, "send_reply", "abc")
        self.assertEqual((a["decision"], a["state"], a["used"]), ("pending", "prepared", 0))
        self.assertFalse(store.claim_approval(a["approval_id"]))  # not approved yet
        store.mark_approval(a["approval_id"], decision="approved", state="approved")
        self.assertTrue(store.claim_approval(a["approval_id"]))
        self.assertFalse(store.claim_approval(a["approval_id"]))  # second claim refused
        a = store.get_approval(a["approval_id"])
        self.assertEqual((a["used"], a["state"]), (1, "attempted"))
        with self.assertRaises(ValueError):
            store.mark_approval(a["approval_id"], content_hash="tampered")

    def test_analysis_roundtrip(self):
        store.save_analysis("m1", "th1", "suspicious", "high", 20, "low", ["LOOKALIKE_DOMAIN"], True)
        row = store.get_analysis("m1")
        self.assertEqual(row["evidence"], ["LOOKALIKE_DOMAIN"])
        self.assertEqual(len(store.list_analyses(quarantined=True)), 1)

    def test_data_persists_across_processes(self):
        """Self-test from the plan: write in one process, read in another."""
        env = dict(os.environ)
        code_write = ("import sys; sys.path.insert(0, r'%s'); import nyp_store as s; "
                      "s.set_sync_state('persist-check')" % TOOLS_DIR)
        code_read = ("import sys; sys.path.insert(0, r'%s'); import nyp_store as s; "
                     "print(s.get_sync_state()['history_id'])" % TOOLS_DIR)
        subprocess.run([sys.executable, "-c", code_write], env=env, check=True)
        out = subprocess.run([sys.executable, "-c", code_read], env=env, check=True,
                             capture_output=True, text=True).stdout.strip()
        self.assertEqual(out, "persist-check")


if __name__ == "__main__":
    unittest.main()
