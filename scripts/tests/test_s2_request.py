# -*- coding: utf-8 -*-
import io
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fetch_common as fc


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def too_many(retry_after=None):
    h = {"Retry-After": retry_after} if retry_after else {}
    return urllib.error.HTTPError("u", 429, "Too Many", h, None)


class S2RequestTests(unittest.TestCase):
    def setUp(self):
        fc._s2_last_request = 0.0
        self.now = [1000.0]
        self.sleeps = []

    def clock(self):
        return self.now[0]

    def sleep(self, s):
        self.sleeps.append(s)
        self.now[0] += s

    def test_requests_are_spaced_by_the_minimum_interval(self):
        with patch("urllib.request.urlopen", side_effect=lambda *a, **k: Resp(b"{}")):
            fc.s2_request("r", sleep=self.sleep, clock=self.clock)
            fc.s2_request("r", sleep=self.sleep, clock=self.clock)
        self.assertAlmostEqual(self.sleeps[-1], fc.S2_MIN_INTERVAL)

    def test_429_waits_and_retries(self):
        answers = [too_many("7"), too_many(), Resp(b'{"ok": 1}')]

        def fake(*a, **k):
            r = answers.pop(0)
            if isinstance(r, Exception):
                raise r
            return r
        with patch("urllib.request.urlopen", side_effect=fake):
            self.assertEqual(fc.s2_request("r", sleep=self.sleep, clock=self.clock), {"ok": 1})
        self.assertIn(7.0, self.sleeps)
        self.assertIn(3.0, self.sleeps)

    def test_other_errors_and_exhausted_retries_raise(self):
        with patch("urllib.request.urlopen", side_effect=urllib.error.HTTPError("u", 500, "x", {}, None)):
            with self.assertRaises(urllib.error.HTTPError):
                fc.s2_request("r", sleep=self.sleep, clock=self.clock)
        with patch("urllib.request.urlopen", side_effect=lambda *a, **k: (_ for _ in ()).throw(too_many())):
            with self.assertRaises(urllib.error.HTTPError):
                fc.s2_request("r", max_attempts=3, sleep=self.sleep, clock=self.clock)


if __name__ == "__main__":
    unittest.main()
