"""Offline failure simulations: no live requests or service mutations."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.client import SecurityChallengeError
from app.health import Progress, STATUS_FILE, assess
from app.config import Settings
from app.pipeline import PipelineResult, run_pipeline
from app.storage import fingerprint_ids, load_checkpoint
from scripts.check_health import main


class HealthTests(unittest.TestCase):
    def setUp(self):
        self.service = dict(ActiveState="active", MainPID="42", Result="success")
        self.status = dict(pid=42, state="fetching", updated_at=100,
                           last_progress_at=100, next_index=3, expected_until=200,
                           fetch_budget=100)

    def check(self, now=150, **kwargs):
        return assess(self.service, self.status, now=now, free_bytes=10000,
                      min_free_bytes=kwargs.pop("min_free_bytes", 100),
                      stall_after=300, grace=20, **kwargs)

    def test_stall_and_live_process_without_cursor_progress(self):
        self.assertEqual(self.check()[0], 0)
        self.assertIn("STALLED", self.check(now=500)[1])
        self.status.update(updated_at=490, state="processing", expected_until=490,
                           fetch_budget=0)
        self.assertIn("NO_PROGRESS", self.check(now=550)[1])

    def test_network_retry_budget_and_cooldown_are_bounded(self):
        self.status.update(expected_until=1000, fetch_budget=900)
        self.assertEqual(self.check(now=800)[0], 0)
        self.assertEqual(self.check(now=1100)[0], 2)
        self.status.update(state="cooldown", expected_until=2000)
        self.assertEqual(self.check(now=1800)[0], 0)
        self.assertEqual(self.check(now=2100)[0], 2)

    def test_stops_restart_wait_and_actual_failure(self):
        for state in ("inactive", "deactivating", "activating"):
            self.service.update(ActiveState=state, ExecMainStatus="143", Result="exit-code")
            self.status = {}  # Old/missing telemetry must not turn a stop into a stall.
            self.assertEqual(self.check(now=99999)[0], 0)
        self.service.update(ActiveState="failed", ExecMainStatus="78")
        self.assertEqual(self.check()[0], 2)
        self.service.update(ActiveState="active")
        self.status = dict(pid=42, state="failed", updated_at=100,
                           last_progress_at=100, expected_until=100, fetch_budget=100)
        self.assertEqual(self.check()[0], 2)

    def test_low_disk_even_when_stopped(self):
        self.service["ActiveState"] = "inactive"
        self.assertIn("LOW_DISK", self.check(min_free_bytes=20000)[1])

    def test_missing_stale_process_and_corrupt_telemetry_are_unknown(self):
        for changes in ({"pid": 7}, {"updated_at": "bad"},
                        {"expected_until": float("nan")}, {"state": "invalid"}):
            with self.subTest(changes=changes):
                original = self.status.copy()
                self.status.update(changes)
                self.assertEqual(self.check()[0], 3)
                self.status = original
        self.status = {}
        self.assertEqual(self.check()[0], 3)

    def test_atomic_throttled_tracking_and_write_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = SimpleNamespace(root=Path(directory), max_workers=3,
                                       max_network_retries=2, request_timeout=15,
                                       request_delay=1, retry_base_delay=2)
            tracker = Progress(settings)
            with patch("app.health.time.time", return_value=100):
                tracker.update("fetching", next_index=0, wait=tracker.fetch_budget)
            path = settings.root / STATUS_FILE
            before = path.read_text()
            with patch("app.health.time.time", return_value=101):
                tracker.update("processing", next_index=1, force=False)
            self.assertEqual(path.read_text(), before)
            with patch("app.health.time.time", return_value=111):
                tracker.update("processing", next_index=2, force=False)
            self.assertEqual(json.loads(path.read_text())["next_index"], 2)
            self.assertFalse(path.with_suffix(".json.tmp").exists())
            with patch.object(Path, "write_text", side_effect=OSError("disk full")), \
                    self.assertLogs(level="WARNING"):
                tracker.update("failed")  # Must not propagate into crawler logic.

    def test_cli_systemd_unavailable_and_malformed_status(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch("sys.argv", ["check_health", "--root", directory, "--min-free-mib", "0"]):
            with patch("scripts.check_health.subprocess.run", side_effect=OSError("no systemd")):
                self.assertEqual(main(), 3)
            (Path(directory) / STATUS_FILE).write_text("broken json")
            result = SimpleNamespace(stdout="LoadState=loaded\nActiveState=active\nMainPID=42\n")
            with patch("scripts.check_health.subprocess.run", return_value=result) as run:
                self.assertEqual(main(), 3)
                self.assertEqual(run.call_args.args[0],
                                 ["systemctl", "show", "--", "de-project-2-etl.service"])

    def test_pipeline_progress_does_not_change_checkpoint_or_security_stop(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = Settings(root, "http://example.invalid", root / "ids.csv",
                                3, 0, 15, 2, 120, 1, max_workers=1)
            settings.csv_file.write_text("id\n101\n102\n")
            responses = [{"id": 101, "name": "one", "description": ""},
                         SecurityChallengeError("BytePlus"),
                         SecurityChallengeError("BytePlus")]

            def cooldown(_delay):
                status = json.loads((root / STATUS_FILE).read_text())
                self.assertEqual(status["state"], "cooldown")
                self.assertEqual(status["next_index"], 1)
                self.assertGreaterEqual(status["expected_until"] - status["updated_at"], 120)

            with patch("app.pipeline.ProductClient") as client, \
                    patch("app.pipeline.time.sleep", side_effect=cooldown):
                client.return_value.fetch.side_effect = responses
                self.assertEqual(run_pipeline(settings), PipelineResult.SECURITY_PAUSED)
            status = json.loads((root / STATUS_FILE).read_text())
            self.assertEqual(status["state"], "security_paused")
            self.assertEqual(load_checkpoint(settings, 2, fingerprint_ids(["101", "102"])), 1)


if __name__ == "__main__":
    unittest.main()
