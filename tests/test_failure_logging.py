"""Verify HTTP 404 and other failures are written to separate JSONL files."""
import json
import tempfile
import unittest
from pathlib import Path

from app.client import FetchError, RetryExhaustedError, SecurityChallengeError
from app.config import Settings
from app.storage import log_failure


class SplitFailureLogTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        root = Path(self.temp_dir.name)
        self.settings = Settings(
            root=root,
            api_url="https://example.invalid/products",
            csv_file=root / "product_ids.csv",
            batch_size=1000,
            request_delay=1,
            request_timeout=15,
            checkpoint_every=50,
            security_retry_delay=120,
            max_security_retries=2,
        )

    def record(self, product_id, position, error):
        log_failure(self.settings, product_id, 1, position, position, "extract", error)

    def read_records(self, name):
        path = self.settings.logs_dir / name
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def test_http_404_only_in_404_file(self):
        self.record("101", 1, FetchError("missing", http_status=404, curl_exit_code=0))
        self.record("102", 2, FetchError("missing", http_status=404, curl_exit_code=22))
        errors = self.read_records("failed_404.jsonl")
        self.assertEqual([record["id"] for record in errors], ["101", "102"])
        self.assertEqual([record["http_status"] for record in errors], [404, 404])
        self.assertFalse((self.settings.logs_dir / "failed_other.jsonl").exists())

    def test_other_errors_never_in_404_file(self):
        self.record("103", 3, FetchError("timeout", curl_exit_code=28))
        self.record("104", 4, RetryExhaustedError("server", http_status=503, curl_exit_code=0))
        self.record("105", 5, SecurityChallengeError("BytePlus", http_status=200, curl_exit_code=0))
        self.record("106", 6, ValueError("invalid data"))
        errors = self.read_records("failed_other.jsonl")
        self.assertEqual([record["id"] for record in errors], ["103", "104", "105", "106"])
        self.assertEqual(errors[0]["curl_exit_code"], 28)
        self.assertFalse((self.settings.logs_dir / "failed_404.jsonl").exists())

    def test_appends_without_overwriting(self):
        self.record("101", 1, FetchError("not found", http_status=404))
        self.record("102", 2, FetchError("not found", http_status=404))
        self.assertEqual(len(self.read_records("failed_404.jsonl")), 2)


if __name__ == "__main__":
    unittest.main()
