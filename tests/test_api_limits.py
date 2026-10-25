"""The solver routes' rate limit, fpl.api.limits (#83)."""

import unittest
from types import SimpleNamespace

from fpl.api import limits


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class TestParse(unittest.TestCase):

    def test_a_rate_is_requests_over_seconds(self):
        self.assertEqual(limits.parse("10/60"), (10, 60.0))

    def test_off_is_no_limit(self):
        for setting in ("off", "OFF", "0", ""):
            with self.subTest(setting=setting):
                self.assertIsNone(limits.parse(setting))


class TestSlidingWindow(unittest.TestCase):

    def setUp(self):
        self.clock = Clock()
        self.window = limits.SlidingWindow((2, 60), clock=self.clock)

    def test_up_to_the_rate_goes_ahead_and_the_next_waits(self):
        self.assertIsNone(self.window.allow("a"))
        self.assertIsNone(self.window.allow("a"))
        self.assertEqual(self.window.allow("a"), 60)

    def test_the_wait_is_until_the_oldest_request_leaves_the_window(self):
        self.window.allow("a")
        self.clock.now += 20
        self.window.allow("a")
        self.clock.now += 10
        self.assertEqual(self.window.allow("a"), 30)
        self.clock.now += 30
        self.assertIsNone(self.window.allow("a"))

    def test_clients_are_counted_apart(self):
        self.window.allow("a")
        self.window.allow("a")
        self.assertIsNone(self.window.allow("b"))

    def test_a_refused_request_does_not_count(self):
        self.window.allow("a")
        self.window.allow("a")
        self.window.allow("a")
        self.clock.now += 60
        self.assertIsNone(self.window.allow("a"))
        self.assertIsNone(self.window.allow("a"))

    def test_no_limit_lets_everything_through(self):
        window = limits.SlidingWindow(None)
        self.assertTrue(all(window.allow("a") is None for _ in range(100)))


class TestClient(unittest.TestCase):

    def request(self, header=None):
        headers = {"fly-client-ip": header} if header else {}
        return SimpleNamespace(headers=headers, client=SimpleNamespace(host="10.0.0.1"))

    def test_on_fly_the_client_is_flys_header(self):
        self.assertEqual(limits.client_of(self.request("203.0.113.9"), {"FPL_HOSTED": "1"}),
                         "203.0.113.9")

    def test_elsewhere_the_header_is_not_trusted(self):
        self.assertEqual(limits.client_of(self.request("203.0.113.9"), {}), "10.0.0.1")


if __name__ == "__main__":
    unittest.main()
