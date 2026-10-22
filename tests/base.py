"""Shared test setup: put tools/ on the path and give every test a fresh temp database."""
import os
import sys
import tempfile
import unittest

TOOLS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools")
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)


class TempDBTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old_db = os.environ.get("NYP_DB_PATH")
        os.environ["NYP_DB_PATH"] = os.path.join(self._tmp.name, "nyp_inbox.db")

    def tearDown(self):
        if self._old_db is None:
            os.environ.pop("NYP_DB_PATH", None)
        else:
            os.environ["NYP_DB_PATH"] = self._old_db
        self._tmp.cleanup()
