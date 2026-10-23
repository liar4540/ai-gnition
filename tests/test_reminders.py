import unittest
from unittest import mock

from tests.base import TempDBTestCase
import nyp_executor as ex
import nyp_store as store
from nyp_save_analysis import calendar_payload, create_reminder


class CalendarTimeTest(unittest.TestCase):
    def test_time_without_offset_is_singapore_time(self):
        p = calendar_payload("Dentist", "2026-10-25T09:00")
        self.assertEqual((p["start"], p["end"], p["all_day"]),
                         ("2026-10-25T09:00:00+08:00", "2026-10-25T10:00:00+08:00", False))

    def test_explicit_offset_is_kept(self):
        self.assertEqual(calendar_payload("Call", "2026-10-25T09:00:00+00:00")["start"], "2026-10-25T09:00:00+00:00")

    def test_custom_end_and_all_day_ranges(self):
        p = calendar_payload("Workshop", "2026-10-25T09:00", "2026-10-25T12:30")
        self.assertEqual(p["end"], "2026-10-25T12:30:00+08:00")
        p = calendar_payload("Trip", "2026-10-25", "2026-10-27")
        self.assertEqual((p["start"], p["end"], p["all_day"]), ("2026-10-25", "2026-10-28", True))

    def test_end_before_start_is_rejected(self):
        with self.assertRaises(ValueError):
            calendar_payload("X", "2026-10-25T10:00", "2026-10-25T09:00")
        with self.assertRaises(ValueError):
            calendar_payload("X", "2026-10-25", "2026-10-24")


class ReminderTest(TempDBTestCase):
    def test_creates_task_and_exact_calendar_draft(self):
        r = create_reminder("Leon's death anniversary", "2026-10-25", None, "Asked in chat")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["approvals_needed"], ["calendar_create"])
        task = store.get_task(r["task_id"])
        self.assertEqual((task["status"], task["source_message_ids"], task["deadline"]), ("pending_approval", "", "2026-10-25"))
        draft = store.get_latest_draft(r["task_id"], "calendar_create")
        self.assertEqual(draft["payload"]["start"], "2026-10-25")
        self.assertTrue(draft["payload"]["all_day"])
        self.assertEqual(store.count("analyses"), 0)    # no fake email analysis is created

    def test_rejects_bad_input_without_creating_anything(self):
        for args in (("", "2026-10-25"), ("x" * 81, "2026-10-25"), ("Meet", "next friday"),
                     ("Meet", "2026-10-25T10:00", "2026-10-25T09:00")):
            with self.subTest(args=args):
                self.assertFalse(create_reminder(*args)["ok"])
        self.assertEqual(store.count("tasks"), 0)

    def test_approved_reminder_is_added_with_singapore_time_and_reminders(self):
        r = create_reminder("Dentist", "2026-10-25T09:00")
        draft = store.get_latest_draft(r["task_id"], "calendar_create")
        a = store.create_approval(r["task_id"], "calendar_create", draft["content_hash"])
        store.mark_approval(a["approval_id"], decision="approved", state="approved")
        calls = []
        with mock.patch.object(ex, "_google_call", lambda m, u, b: calls.append(b) or {"id": "evt-1"}):
            out = ex.execute(a["approval_id"], "calendar_create")
        self.assertEqual((out["status"], out["id"]), ("added", "evt-1"))
        self.assertEqual(calls[0]["start"], {"dateTime": "2026-10-25T09:00:00+08:00"})
        self.assertEqual(calls[0]["reminders"]["overrides"], [{"method": "popup", "minutes": 1440},
                                                             {"method": "popup", "minutes": 60}])
        self.assertEqual(store.get_task(r["task_id"])["status"], "done")

    def test_reminder_without_approval_is_refused(self):
        r = create_reminder("Dentist", "2026-10-25T09:00")
        draft = store.get_latest_draft(r["task_id"], "calendar_create")
        a = store.create_approval(r["task_id"], "calendar_create", draft["content_hash"])   # still pending
        self.assertEqual(ex.execute(a["approval_id"], "calendar_create")["status"], "refused")


if __name__ == "__main__":
    unittest.main()
