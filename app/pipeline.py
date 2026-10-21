"""Orchestrate batches without knowing cURL, JSON serialization or .env details."""
import time
from enum import Enum, auto

import pandas as pd

from .client import FetchError, ProductClient, SecurityChallengeError, RetryExhaustedError
from .config import Settings
from .storage import (
    finish_batch,
    fingerprint_ids,
    validate_previous_batch,
    load_batch,
    load_checkpoint,
    log_failure,
    save_progress,
)
from .transform import transform_product


class PipelineResult(Enum):
    """Outcome used by the CLI / process supervisor (not HTTP error metadata)."""
    COMPLETED = auto()
    SECURITY_PAUSED = auto()
    TRANSIENT_EXHAUSTED = auto()
    INTERRUPTED = auto()


def run_pipeline(settings: Settings) -> PipelineResult:
    product_ids = pd.read_csv(settings.csv_file, dtype={"id": str})["id"].tolist()
    total_ids = len(product_ids)
    ids_fingerprint = fingerprint_ids(product_ids)
    checkpoint_index = load_checkpoint(settings, total_ids, ids_fingerprint)
    validate_previous_batch(settings, checkpoint_index, total_ids, ids_fingerprint, product_ids)
    if checkpoint_index == total_ids:
        print("Finished all product IDs.")
        return PipelineResult.COMPLETED
    first_batch_start = (checkpoint_index // settings.batch_size) * settings.batch_size
    client = ProductClient(settings)

    for batch_start in range(first_batch_start, total_ids, settings.batch_size):
        batch_end = min(batch_start + settings.batch_size, total_ids)
        batch_number = batch_start // settings.batch_size + 1
        next_index, products = load_batch(
            settings, batch_number, batch_start, batch_end, checkpoint_index,
            ids_fingerprint, product_ids
        )
        start_time = time.perf_counter()

        try:
            for index in range(next_index, batch_end):
                product_id = product_ids[index]
                batch_position = index - batch_start + 1
                global_position = index + 1
                stage = "extract"

                print(
                    f"Batch {batch_number}: {batch_position}/{batch_end - batch_start} "
                    f"| Total: {global_position}/{total_ids} | ID: {product_id}",
                    flush=True,
                )

                try:
                    security_retries = 0
                    while True:
                        try:
                            data = client.fetch(product_id)
                            break
                        except SecurityChallengeError as error:
                            log_failure(settings, product_id, batch_number,
                                        batch_position, global_position, "security", error)
                            save_progress(settings, batch_number, batch_start,
                                          index, products, total_ids, ids_fingerprint)
                            if security_retries >= settings.max_security_retries:
                                print(
                                    f"Security check persisted after {security_retries + 1} attempts. "
                                    f"Saved progress; resume at ID #{global_position} later.",
                                    flush=True,
                                )
                                return PipelineResult.SECURITY_PAUSED
                            security_retries += 1
                            print(
                                f"BytePlus detected at ID #{global_position}. "
                                f"Waiting {settings.security_retry_delay}s, then retrying "
                                f"the same ID ({security_retries}/{settings.max_security_retries})...",
                                flush=True,
                            )
                            time.sleep(settings.security_retry_delay)

                    stage = "transform"
                    product = transform_product(data)
                    if str(product["id"]) != str(product_id) or not product["name"]:
                        raise ValueError("Invalid product ID or name")
                    products.append(product)

                except RetryExhaustedError as error:
                    # Do not treat repeated transient failures as a missing product.
                    # Save successfully processed IDs and resume at THIS ID next run.
                    log_failure(settings, product_id, batch_number,
                                batch_position, global_position, "extract", error)
                    save_progress(settings, batch_number, batch_start,
                                  index, products, total_ids, ids_fingerprint)
                    print(
                        f"Connection/server problem persists at ID #{global_position}. "
                        "Saved progress; stopping without skipping this ID.",
                        flush=True,
                    )
                    return PipelineResult.TRANSIENT_EXHAUSTED

                except (FetchError, TypeError, ValueError, KeyError, AttributeError) as error:
                    log_failure(settings, product_id, batch_number,
                                batch_position, global_position, stage, error)

                # A completed ID (or ordinary failure) advances the cursor.
                next_index = index + 1
                if ((next_index - batch_start) % settings.checkpoint_every == 0
                        and next_index < batch_end):
                    save_progress(settings, batch_number, batch_start,
                                  next_index, products, total_ids, ids_fingerprint)

        except KeyboardInterrupt:
            if next_index == batch_end:
                finish_batch(settings, batch_number, batch_start, batch_end,
                             products, total_ids, ids_fingerprint)
                print(f"\nStopped after completing batch {batch_number}; batch saved.")
            else:
                save_progress(settings, batch_number, batch_start,
                              next_index, products, total_ids, ids_fingerprint)
                print(f"\nStopped by Ctrl+C. Saved progress; resume at ID #{next_index + 1}.")
            return PipelineResult.INTERRUPTED

        finish_batch(settings, batch_number, batch_start, batch_end,
                             products, total_ids, ids_fingerprint)
        elapsed = time.perf_counter() - start_time
        print(
            f"\nCompleted batch {batch_number} | Successful: {len(products)} "
            f"| Time: {elapsed:.2f}s\n",
            flush=True,
        )
        checkpoint_index = batch_end

    print("Finished all product IDs.")
    return PipelineResult.COMPLETED
