import io
import json
import unittest
import urllib.error
from unittest import mock

from tests.base import TOOLS_DIR  # noqa: F401
import nyp_google_auth as auth

GOOD = {"client_id": "cid", "client_secret": "secret", "refresh_token": "rt"}


class FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class GoogleAuthTest(unittest.TestCase):
    def setUp(self):
        auth._cache.clear()
        self.kv = dict(GOOD)
        p = mock.patch.object(auth.connections, "key_value", lambda app_id: self.kv)
        p.start()
        self.addCleanup(p.stop)
        self.requests = []

    def fake_urlopen(self, response=None, error=None):
        def opener(req, timeout=None):
            self.requests.append(req)
            if error:
                raise error
            return FakeResp(json.dumps(response).encode())
        return mock.patch.object(auth.urllib.request, "urlopen", opener)

    def test_refresh_returns_and_caches_token(self):
        with self.fake_urlopen({"access_token": "at-1", "expires_in": 3600}):
            self.assertEqual(auth.access_token("nyp_google_read"), "at-1")
            self.assertEqual(auth.access_token("nyp_google_read"), "at-1")
        self.assertEqual(len(self.requests), 1)                       # second call used the cache
        body = self.requests[0].data.decode()
        self.assertIn("grant_type=refresh_token", body)
        self.assertIn("refresh_token=rt", body)

    def test_each_connection_has_its_own_token(self):
        with self.fake_urlopen({"access_token": "at", "expires_in": 3600}):
            auth.access_token("nyp_google_read")
            auth.access_token("nyp_google_act")
        self.assertEqual(len(self.requests), 2)

    def test_missing_or_placeholder_values_are_reported(self):
        self.kv["refresh_token"] = "<GOOGLE_READ_REFRESH_TOKEN>"
        with self.assertRaises(auth.GoogleNotConfigured) as ctx:
            auth.access_token("nyp_google_read")
        self.assertIn("refresh_token", str(ctx.exception))

    def test_invalid_grant_gives_a_hint(self):
        err = urllib.error.HTTPError(auth.TOKEN_URL, 400, "Bad Request", {},
                                     io.BytesIO(b'{"error": "invalid_grant"}'))
        with self.fake_urlopen(error=err):
            with self.assertRaises(RuntimeError) as ctx:
                auth.access_token("nyp_google_read")
        self.assertIn("invalid_grant", str(ctx.exception))
        self.assertIn("get_google_refresh_token.py", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
