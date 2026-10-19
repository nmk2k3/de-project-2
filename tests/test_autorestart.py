"""Offline verification of service-friendly exit codes and checkpoint recovery."""
import fcntl
import json
import signal
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import main as entrypoint
from app.client import RetryExhaustedError, SecurityChallengeError
from app.config import Settings
from app.pipeline import PipelineResult, run_pipeline
from app.storage import fingerprint_ids, load_checkpoint


class AutoRestartTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.settings = Settings(
            root=root,
            api_url="http://example.invalid/products",
            csv_file=root / "product_ids.csv",
            batch_size=3,
            request_delay=1,
            request_timeout=15,
            checkpoint_every=2,
            security_retry_delay=0,
            max_security_retries=0,
        )
        self.ids = ["101", "102", "103"]
        self.settings.csv_file.write_text("id\n101\n102\n103\n", encoding="utf-8")
        entrypoint._sigterm_requested = False
        self.addCleanup(setattr, entrypoint, "_sigterm_requested", False)

    def test_transient_exhausted_return_signals_supervisor_and_resume(self):
        with patch("app.pipeline.ProductClient") as cls:
            client = cls.return_value
            client.fetch.side_effect = [
                {"id": 101, "name": "First", "description": ""},
                RetryExhaustedError("offline", curl_exit_code=28),
            ]
            outcome = run_pipeline(self.settings)
            self.assertEqual(outcome, PipelineResult.TRANSIENT_EXHAUSTED)
            self.assertEqual(load_checkpoint(self.settings, 3, fingerprint_ids(self.ids)), 1)
            partial = json.loads((self.settings.output_dir / "products_001.partial.json").read_text())
            self.assertEqual([p["id"] for p in partial["products"]], [101])

            client.fetch.side_effect = lambda pid: {"id": int(pid), "name": f"p{pid}", "description": ""}
            client.fetch.reset_mock()
            outcome = run_pipeline(self.settings)
            self.assertEqual(outcome, PipelineResult.COMPLETED)
            self.assertEqual(client.fetch.call_args_list[0].args[0], "102")
            self.assertEqual(load_checkpoint(self.settings, 3, fingerprint_ids(self.ids)), 3)

    def test_access_challenge_does_not_signal_auto_restart(self):
        with patch("app.pipeline.ProductClient") as cls:
            cls.return_value.fetch.side_effect = SecurityChallengeError(
                "BytePlus", http_status=200, curl_exit_code=0
            )
            outcome = run_pipeline(self.settings)
        self.assertEqual(outcome, PipelineResult.SECURITY_PAUSED)
        self.assertEqual(load_checkpoint(self.settings, 3, fingerprint_ids(self.ids)), 0)

    def test_service_exit_codes(self):
        with patch("main.load_settings", return_value=self.settings), patch("main.run_pipeline") as run:
            for state, expected in [
                (PipelineResult.COMPLETED, 0),
                (PipelineResult.SECURITY_PAUSED, 0),
                (PipelineResult.INTERRUPTED, 0),
                (PipelineResult.TRANSIENT_EXHAUSTED, 75),
            ]:
                with self.subTest(state=state):
                    run.return_value = state
                    self.assertEqual(entrypoint.main(), expected)
            run.side_effect = ValueError("Corrupt checkpoint")
            self.assertEqual(entrypoint.main(), 78)

    def test_sigterm_saves_progress_and_requests_restart(self):
        with patch("main.load_settings", return_value=self.settings), \
             patch("app.pipeline.ProductClient") as cls:
            def simulate_request(pid):
                if pid == "102":
                    entrypoint._handle_sigterm(signal.SIGTERM, None)
                return {"id": int(pid), "name": f"p{pid}", "description": ""}
            cls.return_value.fetch.side_effect = simulate_request
            self.assertEqual(entrypoint.main(), 143)
            self.assertEqual(load_checkpoint(self.settings, 3, fingerprint_ids(self.ids)), 1)

    def test_second_instance_is_rejected(self):
        lock_file = self.settings.root / ".pipeline.lock"
        with lock_file.open("a+") as locked:
            fcntl.flock(locked.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            with patch("main.load_settings", return_value=self.settings), \
                 patch("main.run_pipeline") as run:
                self.assertEqual(entrypoint.main(), 78)
                run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
