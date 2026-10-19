"""Offline retry tests: never send HTTP requests."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.client import FetchError, ProductClient, RetryExhaustedError
from app.config import Settings
from app.pipeline import run_pipeline
from app.storage import fingerprint_ids, load_checkpoint, load_batch


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
    def test_http_503_retries_then_succeeds(self, run, sleep):
        run.side_effect = [
            self.response("unavailable", 503),
            self.response('{"id":101,"name":"ok"}', 200),
        ]
        product = ProductClient(self.settings).fetch("101")
        self.assertEqual(product["id"], 101)
        self.assertEqual(run.call_count, 2)
        self.assertEqual(sleep.call_count, 1)

    @patch("app.client.time.sleep")
    @patch("app.client.subprocess.run")
    def test_timeout_retries_then_succeeds(self, run, sleep):
        run.side_effect = [
            self.response("", "000", 28, "Operation timed out"),
            self.response('{"id":101,"name":"ok"}', 200),
        ]
        self.assertEqual(ProductClient(self.settings).fetch("101")["id"], 101)
        self.assertEqual(run.call_count, 2)

    @patch("app.client.time.sleep")
    @patch("app.client.subprocess.run")
    def test_404_does_not_retry(self, run, sleep):
        run.return_value = self.response("Not Found", 404)
        with self.assertRaises(FetchError) as ctx:
            ProductClient(self.settings).fetch("101")
        self.assertEqual(ctx.exception.http_status, 404)
        self.assertEqual(run.call_count, 1)
        sleep.assert_not_called()

    @patch("app.client.time.sleep")
    @patch("app.client.subprocess.run")
    def test_three_failures_stop_not_skip(self, run, sleep):
        run.return_value = self.response("bad gateway", 502)
        with self.assertRaises(RetryExhaustedError) as ctx:
            ProductClient(self.settings).fetch("101")
        self.assertEqual(ctx.exception.http_status, 502)
        self.assertEqual(run.call_count, 3)
        self.assertEqual(sleep.call_count, 2)

    @patch("app.pipeline.ProductClient")
    def test_pipeline_resume_same_id_after_exhaustion(self, client_factory):
        ids = ["101", "102", "103"]
        self.settings.csv_file.write_text("id\n101\n102\n103\n", encoding="utf-8")
        client = client_factory.return_value
        client.fetch.side_effect = [
            {"id": 101, "name": "p101", "description": "", "images": []},
            RetryExhaustedError("HTTP 503", http_status=503, curl_exit_code=0),
        ]
        run_pipeline(self.settings)
        self.assertEqual(load_checkpoint(self.settings, 3, fingerprint_ids(ids)), 1)
        index, products = load_batch(
            self.settings, 1, 0, 3, 1, fingerprint_ids(ids), ids
        )
        self.assertEqual(index, 1)
        self.assertEqual([p["id"] for p in products], [101])

        client.fetch.side_effect = None
        client.fetch.reset_mock()
        client.fetch.side_effect = lambda product_id: {
            "id": int(product_id), "name": f"p{product_id}", "description": "", "images": []
        }
        run_pipeline(self.settings)
        self.assertEqual(client.fetch.call_args_list[0].args[0], "102")
        self.assertEqual(load_checkpoint(self.settings, 3, fingerprint_ids(ids)), 3)
        final = json.loads((self.settings.output_dir / "products_001.json").read_text())
        self.assertEqual([p["id"] for p in final], [101, 102, 103])


if __name__ == "__main__":
    unittest.main()
