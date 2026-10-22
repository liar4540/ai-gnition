import unittest

from tests.base import TOOLS_DIR  # noqa: F401  (puts tools/ on sys.path)
from nyp_ping import nyp_ping


class PingTest(unittest.TestCase):
    def test_ping_ok(self):
        result = nyp_ping.fn()
        self.assertTrue(result["ok"])
        self.assertIn("T", result["time"])


if __name__ == "__main__":
    unittest.main()
