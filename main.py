import json
import re
import subprocess
import time
from pathlib import Path

import pandas as pd
from bs4 import BeautifulSoup


API_URL = "https://api.tiki.vn/product-detail/api/v1/products"
BATCH_SIZE = 1000
REQUEST_DELAY = 1
CHECKPOINT_EVERY = 50
SECURITY_RETRY_DELAY = 120  # seconds
MAX_SECURITY_RETRIES = 2   # extra attempts after the first challenge


class SecurityChallengeError(Exception):
    pass


class FetchError(Exception):
    pass


# EXTRACT
def fetch_product(product_id):
    result = subprocess.run(
        [
            "curl",
            "-sS",
            "--max-time", "15",
            "-w", "\n%{http_code}",
            "-H", "User-Agent: Mozilla/5.0",
            "-H", "Accept: application/json",
            f"{API_URL}/{product_id}",
        ],
        capture_output=True,
        text=True,
    )

    body, separator, status_text = result.stdout.rpartition("\n")
    if not separator or not status_text.isdigit():
        raise FetchError(f"Invalid cURL response: {result.stderr.strip()}")

    status = int(status_text)
    is_html = body.lstrip().lower().startswith(("<!doctype html", "<html"))

    if is_html or status in (403, 429):
        if is_html:
            Path("debug_response.html").write_text(body, encoding="utf-8")
        raise SecurityChallengeError(
            f"HTTP {status}: access/security check for product {product_id}"
        )

    if result.returncode != 0:
        raise FetchError(f"cURL error: {result.stderr.strip()}")

    if status >= 400:
        raise FetchError(f"HTTP {status} for product {product_id}")

    try:
        return json.loads(body)
    except json.JSONDecodeError as e:
        raise FetchError(f"Invalid JSON for product {product_id}") from e


# TRANSFORM
def clean_description(html):
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    return re.sub(r"\s+", " ", soup.get_text(separator=" ", strip=True))


def transform_product(data):
    return {
        "id": data.get("id"),
        "name": data.get("name"),
        "url_key": data.get("url_key"),
        "price": data.get("price"),
        "description": clean_description(data.get("description")),
        "images_url": [
            image["base_url"]
            for image in (data.get("images") or [])
            if image.get("base_url")
        ],
    }


# LOAD: atomic JSON writes
def save_json(data, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    temporary.replace(path)


# ERROR LOGGING: one file for all batches
def log_failure(product_id, batch_number, batch_position,
                global_position, stage, error):
    failure = {
        "id": product_id,
        "batch": batch_number,
        "batch_position": batch_position,
        "global_position": global_position,
        "stage": stage,
        "error_type": type(error).__name__,
        "reason": str(error),
    }
    Path("logs").mkdir(exist_ok=True)
    with Path("logs/failed.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(failure, ensure_ascii=False) + "\n")
        f.flush()
    print(f"FAILED | ID #{global_position} | {type(error).__name__}", flush=True)


# CHECKPOINT
def save_checkpoint(next_index, total_ids):
    save_json({
        "next_index": next_index,
        "batch_size": BATCH_SIZE,
        "total_ids": total_ids,
    }, "checkpoint.json")


def load_checkpoint(total_ids):
    path = Path("checkpoint.json")
    if not path.exists():
        return 0

    state = json.loads(path.read_text(encoding="utf-8"))
    next_index = state["next_index"]

    if state.get("batch_size", BATCH_SIZE) != BATCH_SIZE:
        raise ValueError("BATCH_SIZE changed since checkpoint. Do not resume with a different batch size.")
    if state.get("total_ids", total_ids) != total_ids:
        raise ValueError("CSV size changed since checkpoint.")
    if "next_batch_number" in state:
        expected = next_index // BATCH_SIZE + 1
        if state["next_batch_number"] != expected:
            raise ValueError("Legacy checkpoint uses a different batch size. Back up and migrate/reset it first.")
    if not 0 <= next_index <= total_ids:
        raise ValueError("Invalid checkpoint next_index.")

    return next_index


def save_progress(batch_number, batch_start, next_index, products, total_ids):
    # Partial data includes its own cursor: safe even if interrupted
    # between writing the partial data and checkpoint.json.
    save_json({
        "batch_start": batch_start,
        "next_index": next_index,
        "products": products,
    }, f"output/products_{batch_number:03d}.partial.json")
    save_checkpoint(next_index, total_ids)


# MAIN PIPELINE
def main():
    df = pd.read_csv("product_ids.csv", dtype={"id": str})
    product_ids = df["id"].tolist()
    total_ids = len(product_ids)
    start_index = load_checkpoint(total_ids)

    first_batch_start = (start_index // BATCH_SIZE) * BATCH_SIZE

    for batch_start in range(first_batch_start, total_ids, BATCH_SIZE):
        batch_end = min(batch_start + BATCH_SIZE, total_ids)
        batch_number = batch_start // BATCH_SIZE + 1
        partial_path = Path(f"output/products_{batch_number:03d}.partial.json")

        # Recover an incomplete batch without fetching completed IDs again.
        if partial_path.exists():
            partial = json.loads(partial_path.read_text(encoding="utf-8"))
            if not isinstance(partial, dict) or partial.get("batch_start") != batch_start:
                raise ValueError(f"Incompatible partial file: {partial_path}")
            next_index = partial["next_index"]
            if not start_index <= next_index <= batch_end:
                raise ValueError("Partial file and checkpoint do not match.")
            products = partial["products"]
            print(f"Resuming batch {batch_number} at ID #{next_index + 1}", flush=True)
        else:
            if start_index > batch_start:
                raise FileNotFoundError(
                    f"Missing {partial_path}; cannot safely resume at ID #{start_index + 1}"
                )
            next_index = batch_start
            products = []

        start_time = time.perf_counter()

        try:
            for index in range(next_index, batch_end):
                # One second between the start of consecutive requests.
                if index > 0:
                    time.sleep(REQUEST_DELAY)

                product_id = product_ids[index]
                stage = "extract"
                position = index - batch_start + 1
                global_position = index + 1

                print(
                    f"Batch {batch_number}: {position}/{batch_end - batch_start} "
                    f"| Total: {global_position}/{total_ids} | ID: {product_id}",
                    flush=True,
                )

                try:
                    security_retries = 0
                    while True:
                        try:
                            data = fetch_product(product_id)
                            break
                        except SecurityChallengeError as e:
                            log_failure(product_id, batch_number, position,
                                        global_position, "security", e)

                            # Save the current ID as unfinished before waiting.
                            # Ctrl+C during the wait will not lose earlier products.
                            save_progress(batch_number, batch_start, index,
                                          products, total_ids)

                            if security_retries >= MAX_SECURITY_RETRIES:
                                print(
                                    f"Security check persisted after "
                                    f"{security_retries + 1} attempts. "
                                    f"Saved progress; resume at ID #{global_position} later.",
                                    flush=True,
                                )
                                return

                            security_retries += 1
                            print(
                                f"BytePlus detected at ID #{global_position}. "
                                f"Waiting {SECURITY_RETRY_DELAY}s, then retrying "
                                f"the same ID ({security_retries}/{MAX_SECURITY_RETRIES})...",
                                flush=True,
                            )
                            time.sleep(SECURITY_RETRY_DELAY)

                    stage = "transform"
                    product = transform_product(data)
                    if str(product["id"]) != str(product_id) or not product["name"]:
                        raise ValueError("Invalid product ID or name")
                    products.append(product)

                except (FetchError, TypeError, ValueError, KeyError, AttributeError) as e:
                    log_failure(product_id, batch_number, position,
                                global_position, stage, e)

                # This ID was processed successfully or logged as a normal failure.
                next_index = index + 1

                if (next_index - batch_start) % CHECKPOINT_EVERY == 0 and next_index < batch_end:
                    save_progress(batch_number, batch_start, next_index,
                                  products, total_ids)

        except KeyboardInterrupt:
            if next_index == batch_end:
                # Final ID is already done: finalize, don't leave a partial-only batch.
                save_json(products, f"output/products_{batch_number:03d}.json")
                save_checkpoint(batch_end, total_ids)
                partial_path.unlink(missing_ok=True)
                print(f"\nStopped after completing batch {batch_number}; batch saved.")
            else:
                save_progress(batch_number, batch_start, next_index, products, total_ids)
                print(f"\nStopped by Ctrl+C. Saved progress; resume at ID #{next_index + 1}.")
            return

        # A completed batch: save its final JSON before advancing checkpoint.
        save_json(products, f"output/products_{batch_number:03d}.json")
        save_checkpoint(batch_end, total_ids)
        partial_path.unlink(missing_ok=True)

        elapsed = time.perf_counter() - start_time
        print(f"\nCompleted batch {batch_number} | Successful: {len(products)} "
              f"| Time: {elapsed:.2f}s\n", flush=True)
        start_index = batch_end

    print("Finished all product IDs.")


if __name__ == "__main__":
    main()
