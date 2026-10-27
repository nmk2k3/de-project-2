"""Offline concurrency, persistence, shutdown and latency comparisons."""
import json
import tempfile
import threading
import time
import unittest
from concurrent.futures import CancelledError, ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.client import FetchError, ProductClient, RetryExhaustedError, SecurityChallengeError
from app.config import Settings
from app.pipeline import PipelineResult, run_pipeline
from app.storage import fingerprint_ids, load_checkpoint


def response(product_id, status=200, body=None):
    if body is None:
        body = json.dumps({"id": int(product_id), "name": f"p{product_id}",
                           "description": "<p>Hello</p>", "images": []})
    return SimpleNamespace(stdout=f"{body}\n{status}", returncode=0, stderr="")


class ParallelTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.settings = Settings(
            root=root, api_url="https://example.invalid/products",
            csv_file=root / "ids.csv", batch_size=20, request_delay=0,
            request_timeout=1, checkpoint_every=1, security_retry_delay=0,
            max_security_retries=0, max_network_retries=0, retry_base_delay=0,
        )
        self.ids = ["101", "102", "103"]
        self.write_ids(self.settings, self.ids)

    @staticmethod
    def write_ids(settings, ids):
        settings.root.mkdir(parents=True, exist_ok=True)
        settings.csv_file.write_text("id\n" + "\n".join(ids) + "\n")

    def cursor(self, settings=None, ids=None):
        settings = settings or self.settings
        ids = ids or self.ids
        return load_checkpoint(settings, len(ids), fingerprint_ids(ids))

    def test_global_spacing_includes_all_worker_retries(self):
        settings = replace(self.settings, request_delay=0.025, max_network_retries=1)
        starts = []
        counts = {}
        lock = threading.Lock()

        def request(command, **kwargs):
            pid = command[-1].rsplit("/", 1)[-1]
            with lock:
                starts.append(time.perf_counter())
                counts[pid] = counts.get(pid, 0) + 1
                attempt = counts[pid]
            time.sleep(0.04)
            return response(pid, status=503 if attempt == 1 else 200)

        client = ProductClient(settings)
        with patch("app.client.subprocess.run", side_effect=request):
            with ThreadPoolExecutor(max_workers=3) as executor:
                results = list(executor.map(client.fetch, self.ids))
        self.assertEqual([p["id"] for p in results], [101, 102, 103])
        self.assertEqual(len(starts), 6)
        self.assertTrue(all(b - a >= 0.023 for a, b in zip(starts, starts[1:])))

    def test_out_of_order_failure_commits_only_prefix_and_resumes(self):
        barrier = threading.Barrier(3)
        third_done = threading.Event()
        finished = []

        def request(command, **kwargs):
            pid = command[-1].rsplit("/", 1)[-1]
            barrier.wait(timeout=2)
            if pid == "101":
                time.sleep(0.04)
            elif pid == "102":
                self.assertTrue(third_done.wait(2))
            finished.append(pid)
            if pid == "103":
                third_done.set()
            return response(pid, status=503 if pid == "102" else 200)

        with patch("app.client.subprocess.run", side_effect=request):
            self.assertEqual(run_pipeline(self.settings), PipelineResult.TRANSIENT_EXHAUSTED)
        self.assertEqual(finished[0], "103")
        self.assertEqual(self.cursor(), 1)
        partial = json.loads((self.settings.output_dir / "products_001.partial.json").read_text())
        self.assertEqual([p["id"] for p in partial["products"]], [101])
        requested = []

        def resume(command, **kwargs):
            pid = command[-1].rsplit("/", 1)[-1]
            requested.append(pid)
            return response(pid)

        with patch("app.client.subprocess.run", side_effect=resume):
            self.assertEqual(run_pipeline(self.settings), PipelineResult.COMPLETED)
        self.assertEqual(sorted(requested), ["102", "103"])
        final = json.loads((self.settings.output_dir / "products_001.json").read_text())
        self.assertEqual([p["id"] for p in final], [101, 102, 103])

    def test_security_cancels_waiting_workers_and_main_writes_debug(self):
        settings = replace(self.settings, request_delay=0.2)
        main_thread = threading.get_ident()
        original_write = Path.write_text
        writers = []

        def write(path, *args, **kwargs):
            writers.append(threading.get_ident())
            return original_write(path, *args, **kwargs)

        with patch("app.client.subprocess.run", return_value=response(
                "101", body="<!doctype html><html>BytePlus</html>")) as request, \
                patch.object(Path, "write_text", write):
            self.assertEqual(run_pipeline(settings), PipelineResult.SECURITY_PAUSED)
        self.assertEqual(request.call_count, 1)
        self.assertEqual(self.cursor(), 0)
        self.assertTrue(writers)
        self.assertEqual(set(writers), {main_thread})
        self.assertIn("BytePlus", (settings.root / "debug_response.html").read_text())

    def test_security_cancels_earlier_retry_without_skipping_gap(self):
        settings = replace(self.settings, max_network_retries=2, retry_base_delay=5)
        first_started = threading.Event()
        requests = []

        def request(command, **kwargs):
            pid = command[-1].rsplit("/", 1)[-1]
            requests.append(pid)
            if pid == "101":
                first_started.set()
                return response(pid, status=503)
            self.assertTrue(first_started.wait(2))
            return response(pid, status=429)

        started = time.perf_counter()
        with patch("app.client.subprocess.run", side_effect=request):
            self.assertEqual(run_pipeline(settings), PipelineResult.SECURITY_PAUSED)
        self.assertLess(time.perf_counter() - started, 2)
        self.assertEqual(requests.count("101"), 1)
        self.assertEqual(self.cursor(), 0)

    def test_security_retry_drains_pool_before_cooldown(self):
        settings = replace(self.settings, max_security_retries=1, request_delay=0.03)
        first = True
        active = 0
        lock = threading.Lock()

        def request(command, **kwargs):
            nonlocal first, active
            pid = command[-1].rsplit("/", 1)[-1]
            with lock:
                active += 1
                challenged = first
                first = False
            time.sleep(0.01)
            with lock:
                active -= 1
            return response(pid, status=403 if challenged else 200)

        def cooldown(delay):
            self.assertEqual(active, 0)
            self.assertEqual(self.cursor(), 0)

        # Patch only the pipeline's wait function, not the shared time module.
        with patch("app.client.subprocess.run", side_effect=request), \
                patch("app.pipeline._wait_for_security_retry", side_effect=lambda *args: (
                    cooldown(0) or True)) as wait:
            self.assertEqual(run_pipeline(settings), PipelineResult.COMPLETED)
        wait.assert_called_once()
        self.assertEqual(self.cursor(), 3)

    def test_unexpected_worker_failure_saves_prefix_and_propagates(self):
        def fetch(pid):
            if pid == "102":
                raise RuntimeError("worker broke")
            return {"id": int(pid), "name": pid}

        with patch("app.pipeline.ProductClient") as cls:
            cls.return_value.fetch.side_effect = fetch
            with self.assertRaisesRegex(RuntimeError, "worker broke"):
                run_pipeline(self.settings)
        self.assertEqual(self.cursor(), 1)

    def test_cancelled_gap_uses_fatal_error_not_unrelated_product_error(self):
        for middle, last, expected in [
            (FetchError("missing", http_status=404), RetryExhaustedError("offline"),
             PipelineResult.TRANSIENT_EXHAUSTED),
            (RetryExhaustedError("offline"), SecurityChallengeError("BytePlus"),
             PipelineResult.SECURITY_PAUSED),
        ]:
            with self.subTest(expected=expected):
                barrier = threading.Barrier(3)

                def factory(settings, stop_event):
                    def fetch(pid):
                        barrier.wait(timeout=2)
                        if pid == "101":
                            self.assertTrue(stop_event.wait(2))
                            raise CancelledError()
                        raise middle if pid == "102" else last
                    return SimpleNamespace(fetch=fetch)

                with patch("app.pipeline.ProductClient", side_effect=factory):
                    self.assertEqual(run_pipeline(self.settings), expected)
                self.assertEqual(self.cursor(), 0)

    def test_interrupt_while_waiting_stops_workers_and_saves_prefix(self):
        from app.parallel import OrderedFetcher

        original_fetch = OrderedFetcher.fetch
        started = threading.Event()
        released = threading.Event()

        def fetch(pid):
            if pid == "102":
                started.set()
                self.assertTrue(released.wait(2))
            return {"id": int(pid), "name": pid}

        def interrupt(fetcher, index):
            if index == 1:
                self.assertTrue(started.wait(2))
                released.set()
                raise KeyboardInterrupt
            return original_fetch(fetcher, index)

        with patch("app.pipeline.ProductClient") as cls, \
                patch.object(OrderedFetcher, "fetch", interrupt):
            cls.return_value.fetch.side_effect = fetch
            self.assertEqual(run_pipeline(self.settings), PipelineResult.INTERRUPTED)
        self.assertEqual(self.cursor(), 1)
        self.assertFalse(any(t.name.startswith("ThreadPoolExecutor")
                             for t in threading.enumerate()))

    def test_parallel_matches_sequential_output_and_reduces_latency(self):
        ids = [str(101 + i) for i in range(9)]
        outputs = []
        elapsed = []
        peaks = []
        writer_threads = []
        original_open = Path.open

        def open_file(path, mode="r", *args, **kwargs):
            if any(flag in mode for flag in "wax+"):
                writer_threads.append(threading.get_ident())
            return original_open(path, mode, *args, **kwargs)

        for workers in (1, 3):
            root = self.settings.root / str(workers)
            settings = replace(self.settings, root=root, csv_file=root / "ids.csv",
                               max_workers=workers, request_delay=0.02)
            self.write_ids(settings, ids)
            active = 0
            peak = 0
            lock = threading.Lock()

            def request(command, **kwargs):
                nonlocal active, peak
                with lock:
                    active += 1
                    peak = max(peak, active)
                time.sleep(0.12)
                with lock:
                    active -= 1
                return response(command[-1].rsplit("/", 1)[-1])

            started = time.perf_counter()
            with patch("app.client.subprocess.run", side_effect=request), \
                    patch.object(Path, "open", open_file):
                self.assertEqual(run_pipeline(settings), PipelineResult.COMPLETED)
            elapsed.append(time.perf_counter() - started)
            peaks.append(peak)
            outputs.append((settings.output_dir / "products_001.json").read_bytes())
        self.assertEqual(outputs[0], outputs[1])
        self.assertEqual(peaks, [1, 3])
        self.assertEqual(set(writer_threads), {threading.get_ident()})
        # Generous margin: simulated I/O latency dominates; no live API traffic.
        self.assertLess(elapsed[1], elapsed[0] * 0.8)
        print(f"Mock benchmark (9 IDs, 120ms latency, 20ms global spacing): "
              f"sequential={elapsed[0]:.3f}s, parallel={elapsed[1]:.3f}s, "
              f"speedup={elapsed[0] / elapsed[1]:.2f}x")
