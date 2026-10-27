"""Configuration errors fail before requests or checkpoint arithmetic."""
import os
import unittest
from dataclasses import replace
from unittest.mock import patch

from app.config import PROJECT_DIR, load_settings


class ConfigurationTests(unittest.TestCase):
    def load(self, values=None):
        with patch.dict(os.environ, values or {}, clear=True), \
                patch("app.config.load_dotenv"):
            return load_settings()

    def test_default_and_explicit_csv_paths(self):
        self.assertEqual(self.load().max_workers, 3)
        self.assertEqual(self.load({"MAX_WORKERS": "1"}).max_workers, 1)
        self.assertEqual(self.load().csv_file, PROJECT_DIR / "data/product_ids.csv")
        self.assertEqual(self.load({"CSV_FILE": "data/custom.csv"}).csv_file,
                         PROJECT_DIR / "data/custom.csv")
        self.assertEqual(str(self.load({"CSV_FILE": "/custom/ids.csv"}).csv_file),
                         "/custom/ids.csv")

    def test_invalid_environment_values(self):
        for name, value in [
            ("BATCH_SIZE", "0"), ("BATCH_SIZE", "not-a-number"),
            ("MAX_WORKERS", "0"), ("MAX_WORKERS", "-1"), ("MAX_WORKERS", "1.5"),
            ("CHECKPOINT_EVERY", "0"), ("REQUEST_TIMEOUT", "0"),
            ("REQUEST_DELAY", "-1"), ("SECURITY_RETRY_DELAY", "nan"),
            ("RETRY_BASE_DELAY", "inf"), ("MAX_SECURITY_RETRIES", "-1"),
            ("MAX_NETWORK_RETRIES", "-1"), ("CSV_FILE", " "),
            ("API_URL", ""), ("API_URL", "ftp://example.com/products"),
        ]:
            with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                self.load({name: value})

    def test_direct_construction_cannot_bypass_validation(self):
        settings = self.load()
        for name, value in [
            ("batch_size", 0), ("checkpoint_every", 0), ("request_timeout", -1),
            ("max_workers", True), ("max_workers", 1.5),
            ("max_network_retries", 1.5), ("max_security_retries", True),
            ("request_delay", float("nan")), ("security_retry_delay", float("inf")),
            ("retry_base_delay", -1),
        ]:
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, name.upper()):
                replace(settings, **{name: value})

    def test_zero_delays_and_retry_counts_are_valid(self):
        settings = self.load({
            "REQUEST_DELAY": "0", "SECURITY_RETRY_DELAY": "0",
            "RETRY_BASE_DELAY": "0", "MAX_SECURITY_RETRIES": "0",
            "MAX_NETWORK_RETRIES": "0",
        })
        self.assertEqual(settings.request_delay, 0)
        self.assertEqual(settings.max_network_retries, 0)
