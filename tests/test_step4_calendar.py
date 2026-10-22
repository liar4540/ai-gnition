import unittest
from unittest import mock

from tests.base import TempDBTestCase
from tests.fakes import FakeCalendar
import nyp_calendar_check
import nyp_store as store

EVENTS = [{"summary": "Dentist", "start": {"dateTime": "2026-10-23T14:00:00+08:00"},
           "end": {"dateTime": "2026-10-23T15:00:00+08:00"}}]


class CalendarCheckTest(TempDBTestCase):
    def setUp(self):
        super().setUp()
        self.cal = FakeCalendar(EVENTS)
        patcher = mock.patch.object(nyp_calendar_check, "_calendar_call", self.cal)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_interview_time_clashes(self):
        r = nyp_calendar_check.nyp_calendar_conflicts.fn("2026-10-23T14:00:00+08:00", "2026-10-23T15:00:00+08:00", "m1")
        self.assertTrue(r["clash"])
        self.assertEqual(r["events"], [{"title": "Dentist", "start": "2026-10-23T14:00:00+08:00",
                                        "end": "2026-10-23T15:00:00+08:00"}])
        self.assertIn("CALENDAR_CLASH", store.get_analysis("m1")["evidence"])

    def test_empty_slot_reports_none(self):
        r = nyp_calendar_check.nyp_calendar_conflicts.fn("2026-10-23T16:00:00+08:00", "2026-10-23T17:00:00+08:00", "m2")
        self.assertEqual(r, {"clash": False, "events": []})
        self.assertIsNone(store.get_analysis("m2"))

    def test_clash_evidence_survives_a_later_safety_check(self):
        store.add_evidence("m3", "CALENDAR_CLASH")
        store.record_safety("m3", "low", ["SENDER_DOMAIN_KNOWN"], False)
        self.assertEqual(store.get_analysis("m3")["evidence"], ["SENDER_DOMAIN_KNOWN", "CALENDAR_CLASH"])

    def test_file_has_no_write_calls(self):
        with open(nyp_calendar_check.__file__, encoding="utf-8") as f:
            src = f.read()
        for word in ("method=\"POST\"", "method='POST'", "PUT", "PATCH", "DELETE", "/insert", "/move"):
            self.assertNotIn(word, src)


if __name__ == "__main__":
    unittest.main()
