"""Offline retry tests: never send HTTP requests."""
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import call, patch

from app.client import FetchError, ProductClient, RetryExhaustedError, SecurityChallengeError
from app.config import Settings


class NetworkRetryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.settings = Settings(
            root=root,
            api_url="https://example.invalid/products",
            csv_file=root / "product_ids.csv",
            batch_size=3,
            request_delay=0,
            request_timeout=15,
            checkpoint_every=1,
            security_retry_delay=120,
            max_security_retries=0,
            max_network_retries=2,
            retry_base_delay=0,
        )

    @staticmethod
    def response(body, status, exit_code=0, stderr=""):
        return SimpleNamespace(
            stdout=f"{body}\n{status}", returncode=exit_code, stderr=stderr
        )

    @patch("app.client.time.sleep")
    @patch("app.client.subprocess.run")
    def test_transient_errors_retry_then_succeed(self, run, sleep):
        for failure in (
            self.response("unavailable", 503),
            self.response("", "000", 28, "Operation timed out"),
            OSError("could not start curl"),
        ):
            with self.subTest(failure=failure):
                run.reset_mock()
                sleep.reset_mock()
                run.side_effect = [failure, self.response('{"id":101,"name":"ok"}', 200)]
                self.assertEqual(ProductClient(self.settings).fetch("101")["id"], 101)
                self.assertEqual(run.call_count, 2)
                sleep.assert_called_once_with(0)

    @patch("app.client.time.sleep")
    @patch("app.client.subprocess.run")
    def test_permanent_and_security_errors_do_not_retry(self, run, sleep):
        for body, status, error in (
            ("Not Found", 404, FetchError),
            ("not json", 200, FetchError),
            ("Forbidden", 403, SecurityChallengeError),
            ("Too Many Requests", 429, SecurityChallengeError),
            ("<!doctype html><html>BytePlus</html>", 200, SecurityChallengeError),
        ):
            with self.subTest(body=body, status=status):
                run.reset_mock()
                sleep.reset_mock()
                run.return_value = self.response(body, status)
                with self.assertRaises(error) as ctx:
                    ProductClient(self.settings).fetch("101")
                self.assertEqual(type(ctx.exception), error)
                self.assertEqual(ctx.exception.http_status, status)
                self.assertEqual(run.call_count, 1)
                sleep.assert_not_called()

    @patch("app.client.time.sleep")
    @patch("app.client.subprocess.run")
    def test_retry_budget_and_exponential_backoff(self, run, sleep):
        run.return_value = self.response("bad gateway", 502)
        with self.assertRaises(RetryExhaustedError) as ctx:
            ProductClient(replace(self.settings, retry_base_delay=2)).fetch("101")
        self.assertEqual(ctx.exception.http_status, 502)
        self.assertEqual(run.call_count, 3)
        self.assertEqual(sleep.call_args_list, [call(2), call(4)])


if __name__ == "__main__":
    unittest.main()
