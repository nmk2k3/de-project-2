"""Run offline: python -m unittest discover -s tests -v"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.client import FetchError
from app.config import Settings
from app.pipeline import run_pipeline
from app.storage import (
    fingerprint_ids,
    finish_batch,
    load_batch,
    load_checkpoint,
    output_paths,
    save_checkpoint,
    save_json,
    save_progress,
    validate_previous_batch,
)
from app.transform import transform_product


class IntegrityTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        root = Path(self.folder.name)
        self.settings = Settings(
            root=root,
            api_url="http://example.invalid/products",
            csv_file=root / "product_ids.csv",
            batch_size=3,
            request_delay=1.0,
            request_timeout=15,
            checkpoint_every=2,
            security_retry_delay=120,
            max_security_retries=0,
        )
        self.ids = ["101", "102", "103", "104", "105"]
        self.fp = fingerprint_ids(self.ids)

    def test_fingerprint_checks_order_even_if_csv_length_unchanged(self):
        save_checkpoint(self.settings, 1, len(self.ids), self.fp)
        self.assertEqual(load_checkpoint(self.settings, len(self.ids), self.fp), 1)
        modified = ["102", "101", "103", "104", "105"]
        with self.assertRaisesRegex(ValueError, "changed"):
            load_checkpoint(self.settings, len(modified), fingerprint_ids(modified))

    def test_partial_recovers_ahead_of_checkpoint(self):
        save_checkpoint(self.settings, 1, len(self.ids), self.fp)
        partial, _ = output_paths(self.settings, 1)
        products = [{"id": 101, "name": "A"}, {"id": 102, "name": "B"}]
        # Simulate power loss just after publishing partial, before checkpoint update.
        save_json({"batch_start": 0, "next_index": 2,
                   "ids_fingerprint": self.fp, "products": products}, partial)
        index, recovered = load_batch(self.settings, 1, 0, 3, 1, self.fp, self.ids)
        self.assertEqual(index, 2)
        self.assertEqual(recovered, products)

    def test_reject_corrupted_partial_products(self):
        partial, _ = output_paths(self.settings, 1)
        save_json({"batch_start": 0, "next_index": 2,
                   "ids_fingerprint": self.fp,
                   "products": [{"id": 105, "name": "wrong product"}]}, partial)
        with self.assertRaisesRegex(ValueError, "unexpected"):
            load_batch(self.settings, 1, 0, 3, 0, self.fp, self.ids)

    def test_finish_batch_commits_and_resumes_at_next_batch(self):
        products = [{"id": 101, "name": "A"}, {"id": 103, "name": "C"}]
        finish_batch(self.settings, 1, 0, 3, products, len(self.ids), self.fp)
        partial, final = output_paths(self.settings, 1)
        self.assertFalse(partial.exists())
        self.assertEqual(json.loads(final.read_text(encoding="utf-8")), products)
        self.assertEqual(load_checkpoint(self.settings, len(self.ids), self.fp), 3)
        validate_previous_batch(self.settings, 3, len(self.ids), self.fp, self.ids)

    def test_repair_final_from_complete_partial_after_crash(self):
        products = [{"id": 101, "name": "A"}]
        save_json({"batch_start": 0, "next_index": 3,
                   "ids_fingerprint": self.fp, "products": products},
                  output_paths(self.settings, 1)[0])
        save_checkpoint(self.settings, 3, len(self.ids), self.fp)
        # Simulates missing final file at the moment checkpoint says complete.
        validate_previous_batch(self.settings, 3, len(self.ids), self.fp, self.ids)
        self.assertEqual(json.loads(output_paths(self.settings, 1)[1].read_text()), products)
        self.assertFalse(output_paths(self.settings, 1)[0].exists())

    def test_missing_completed_batch_is_detected(self):
        save_checkpoint(self.settings, 3, len(self.ids), self.fp)
        with self.assertRaises(FileNotFoundError):
            validate_previous_batch(self.settings, 3, len(self.ids), self.fp, self.ids)

    def test_reject_corrupted_completed_output(self):
        save_checkpoint(self.settings, 3, len(self.ids), self.fp)
        output_paths(self.settings, 1)[1].parent.mkdir(parents=True, exist_ok=True)
        output_paths(self.settings, 1)[1].write_text("{not-json", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Cannot read"):
            validate_previous_batch(self.settings, 3, len(self.ids), self.fp, self.ids)

    def test_recover_full_partial_with_old_checkpoint_without_refetch(self):
        save_checkpoint(self.settings, 1, len(self.ids), self.fp)
        partial, _ = output_paths(self.settings, 1)
        saved = [{"id": 101, "name": "P101"}, {"id": 102, "name": "P102"},
                 {"id": 103, "name": "P103"}]
        save_json({"batch_start": 0, "next_index": 3,
                   "ids_fingerprint": self.fp, "products": saved}, partial)
        next_index, products = load_batch(self.settings, 1, 0, 3, 1, self.fp, self.ids)
        self.assertEqual(next_index, 3)
        finish_batch(self.settings, 1, 0, 3, products, len(self.ids), self.fp)
        self.assertEqual(load_checkpoint(self.settings, len(self.ids), self.fp), 3)
        self.assertEqual(json.loads(output_paths(self.settings, 1)[1].read_text()), saved)

    def test_legacy_checkpoint_can_be_read(self):
        save_json({"next_index": 1, "next_batch_number": 1},
                  self.settings.checkpoint_file)
        self.assertEqual(load_checkpoint(self.settings, len(self.ids), self.fp), 1)

    def test_json_write_does_not_corrupt_existing_file_on_error(self):
        file = self.settings.root / "output" / "example.json"
        save_json({"good": 1}, file)
        with self.assertRaises(TypeError):
            save_json({"bad": object()}, file)
        self.assertEqual(json.loads(file.read_text()), {"good": 1})

    def test_clean_description(self):
        product = transform_product({
            "id": 101, "name": "A", "price": 10,
            "description": "<p>Hello <b>world</b></p><p>again</p>",
            "images": [{"base_url": "https://example.invalid/a.jpg"}],
        })
        self.assertEqual(product["description"], "Hello world again")
        self.assertEqual(product["images_url"], ["https://example.invalid/a.jpg"])

    def test_interrupt_resume_and_http_failure_log_without_network(self):
        self.settings.csv_file.write_text("id\n101\n102\n103\n104\n105\n", encoding="utf-8")

        class FakeClient:
            def __init__(self, settings):
                self.calls = []
                self.interrupted = False

            def fetch(self, product_id):
                self.calls.append(product_id)
                if product_id == "103" and not self.interrupted:
                    self.interrupted = True
                    raise KeyboardInterrupt
                if product_id == "104":
                    raise FetchError("HTTP 404", http_status=404, curl_exit_code=0)
                return {"id": int(product_id), "name": f"Product {product_id}"}

        fake = FakeClient(self.settings)
        with patch("app.pipeline.ProductClient", return_value=fake):
            run_pipeline(self.settings)
            self.assertEqual(load_checkpoint(self.settings, 5, self.fp), 2)
            run_pipeline(self.settings)

        self.assertEqual(fake.calls, ["101", "102", "103", "103", "104", "105"])
        self.assertEqual(load_checkpoint(self.settings, 5, self.fp), 5)
        final1 = json.loads(output_paths(self.settings, 1)[1].read_text())
        final2 = json.loads(output_paths(self.settings, 2)[1].read_text())
        self.assertEqual([p["id"] for p in final1 + final2], [101, 102, 103, 105])
        failures = [json.loads(s) for s in
                    (self.settings.logs_dir / "failed.jsonl").read_text().splitlines()]
        self.assertEqual(len(failures), 1)
        self.assertEqual((failures[0]["global_position"], failures[0]["http_status"]), (4, 404))


if __name__ == "__main__":
    unittest.main()
