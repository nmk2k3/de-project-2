"""Durable batch files, checkpoint validation, and append-only failure logs."""
import hashlib
import json
import os
from pathlib import Path

from .config import Settings


def fingerprint_ids(product_ids: list[str]) -> str:
    """Hash the ordered sequence of IDs (not CSV formatting)."""
    digest = hashlib.sha256()
    for product_id in product_ids:
        data = str(product_id).encode("utf-8")
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return digest.hexdigest()


def save_json(data, path: Path) -> None:
    """Replace a JSON file atomically; flush data before publishing it."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    with temp.open("w", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, separators=(",", ":"))
        stream.flush()
        os.fsync(stream.fileno())
    temp.replace(path)
    # Flush directory metadata so the rename survives a system crash on Linux.
    if hasattr(os, "O_DIRECTORY"):
        dir_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)


def output_paths(settings: Settings, batch_number: int) -> tuple[Path, Path]:
    prefix = f"products_{batch_number:03d}"
    return (
        settings.output_dir / f"{prefix}.partial.json",
        settings.output_dir / f"{prefix}.json",
    )


def _check_fingerprint(state: dict, fingerprint: str, path: Path) -> None:
    stored = state.get("ids_fingerprint")
    if stored is not None and stored != fingerprint:
        raise ValueError(
            f"Product ID list/order has changed since {path}. "
            "Restore the original CSV before resuming."
        )


def load_checkpoint(settings: Settings, total_ids: int, fingerprint: str) -> int:
    path = settings.checkpoint_file
    if not path.exists():
        return 0

    state = json.loads(path.read_text(encoding="utf-8"))
    next_index = state["next_index"]
    if state.get("batch_size", settings.batch_size) != settings.batch_size:
        raise ValueError("BATCH_SIZE changed since checkpoint. Keep the old batch size when resuming.")
    if state.get("total_ids", total_ids) != total_ids:
        raise ValueError("CSV size changed since checkpoint.")
    _check_fingerprint(state, fingerprint, path)
    if "ids_fingerprint" not in state:
        print("Legacy checkpoint: original CSV order cannot be verified. "
              "It will be recorded at the next checkpoint.", flush=True)
    if "next_batch_number" in state:
        expected = next_index // settings.batch_size + 1
        if state["next_batch_number"] != expected:
            raise ValueError("Legacy checkpoint uses a different batch size. Back it up and migrate first.")
    if not isinstance(next_index, int) or not 0 <= next_index <= total_ids:
        raise ValueError("Invalid checkpoint next_index.")
    return next_index


def save_checkpoint(settings: Settings, next_index: int, total_ids: int,
                    fingerprint: str) -> None:
    save_json({
        "next_index": next_index,
        "batch_size": settings.batch_size,
        "total_ids": total_ids,
        "ids_fingerprint": fingerprint,
    }, settings.checkpoint_file)


def _partial_state(batch_start: int, next_index: int, products: list,
                   fingerprint: str) -> dict:
    return {
        "batch_start": batch_start,
        "next_index": next_index,
        "ids_fingerprint": fingerprint,
        "products": products,
    }


def save_progress(settings: Settings, batch_number: int, batch_start: int,
                  next_index: int, products: list, total_ids: int,
                  fingerprint: str) -> None:
    partial_path, _ = output_paths(settings, batch_number)
    # The partial is published *first*; it can be ahead of checkpoint on recovery.
    save_json(_partial_state(batch_start, next_index, products, fingerprint), partial_path)
    save_checkpoint(settings, next_index, total_ids, fingerprint)


def _read_partial(path: Path, batch_start: int, batch_end: int,
                  fingerprint: str, product_ids: list[str]) -> dict:
    state = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(state, dict) or state.get("batch_start") != batch_start:
        raise ValueError(f"Incompatible partial file: {path}")
    _check_fingerprint(state, fingerprint, path)
    next_index = state.get("next_index")
    if not isinstance(next_index, int) or not batch_start <= next_index <= batch_end:
        raise ValueError(f"Invalid cursor in partial file: {path}")
    products = state.get("products")
    if not isinstance(products, list):
        raise ValueError(f"Invalid products in partial file: {path}")
    processed_ids = {str(p) for p in product_ids[batch_start:next_index]}
    saved_ids = []
    for product in products:
        if not isinstance(product, dict) or product.get("id") is None:
            raise ValueError(f"Malformed product in partial file: {path}")
        saved_ids.append(str(product["id"]))
    if len(saved_ids) != len(set(saved_ids)) or not set(saved_ids) <= processed_ids:
        raise ValueError(f"Partial file contains duplicated/unexpected product IDs: {path}")
    return state


def load_batch(settings: Settings, batch_number: int, batch_start: int,
               batch_end: int, checkpoint_index: int, fingerprint: str,
               product_ids: list[str]) -> tuple[int, list]:
    partial_path, _ = output_paths(settings, batch_number)
    if partial_path.exists():
        partial = _read_partial(partial_path, batch_start, batch_end, fingerprint, product_ids)
        next_index = partial["next_index"]
        if not checkpoint_index <= next_index <= batch_end:
            raise ValueError("Partial file and checkpoint do not match.")
        print(f"Resuming batch {batch_number} at ID #{next_index + 1}", flush=True)
        return next_index, partial["products"]

    if checkpoint_index > batch_start:
        raise FileNotFoundError(
            f"Missing {partial_path}; cannot safely resume at ID #{checkpoint_index + 1}"
        )
    return batch_start, []


def finish_batch(settings: Settings, batch_number: int, batch_start: int,
                 batch_end: int, products: list, total_ids: int,
                 fingerprint: str) -> None:
    partial_path, final_path = output_paths(settings, batch_number)
    # Commit-ready partial ensures a crash at *any* point during finalization
    # can recover without fetching already completed IDs again.
    save_json(_partial_state(batch_start, batch_end, products, fingerprint), partial_path)
    save_json(products, final_path)
    save_checkpoint(settings, batch_end, total_ids, fingerprint)
    partial_path.unlink(missing_ok=True)


def _validate_final_output(path: Path, expected_ids: list[str]) -> None:
    """Refuse to resume past a missing, unreadable, or mismatched finished file."""
    try:
        products = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read completed batch output: {path}") from exc
    if not isinstance(products, list):
        raise ValueError(f"Completed output must be a JSON array: {path}")
    saved_ids = []
    for product in products:
        if not isinstance(product, dict) or product.get("id") is None or not product.get("name"):
            raise ValueError(f"Invalid product in completed output: {path}")
        saved_ids.append(str(product["id"]))
    if len(saved_ids) != len(set(saved_ids)) or not set(saved_ids) <= set(expected_ids):
        raise ValueError(f"Completed output contains duplicated/unexpected IDs: {path}")


def validate_previous_batch(settings: Settings, checkpoint_index: int,
                            total_ids: int, fingerprint: str,
                            product_ids: list[str]) -> None:
    """Recover a completed partial at a boundary; refuse an absent final output."""
    if checkpoint_index <= 0:
        return
    last_batch_number = (checkpoint_index - 1) // settings.batch_size + 1
    batch_start = (last_batch_number - 1) * settings.batch_size
    batch_end = min(batch_start + settings.batch_size, total_ids)
    partial_path, final_path = output_paths(settings, last_batch_number)

    if checkpoint_index == batch_end and partial_path.exists():
        partial = _read_partial(partial_path, batch_start, batch_end,
                                fingerprint, product_ids)
        if partial["next_index"] == batch_end:
            # This handles a crash after checkpoint/final but before partial cleanup.
            if not final_path.exists():
                save_json(partial["products"], final_path)
            partial_path.unlink(missing_ok=True)

    # A checkpoint says the previous batch is complete: its final output must exist.
    if checkpoint_index == batch_end:
        if not final_path.exists():
            raise FileNotFoundError(f"Checkpoint indicates completed batch, but {final_path} is missing")
        _validate_final_output(final_path, product_ids[batch_start:batch_end])
    elif last_batch_number > 1:
        previous_batch_start = batch_start - settings.batch_size
        previous_final = output_paths(settings, last_batch_number - 1)[1]
        if not previous_final.exists():
            raise FileNotFoundError(f"Missing output for previously completed batch: {previous_final}")
        _validate_final_output(previous_final, product_ids[previous_batch_start:batch_start])


def log_failure(settings: Settings, product_id: str, batch_number: int,
                batch_position: int, global_position: int, stage: str,
                error: Exception) -> None:
    failure = {
        "id": product_id,
        "batch": batch_number,
        "batch_position": batch_position,
        "global_position": global_position,
        "stage": stage,
        "error_type": type(error).__name__,
        "http_status": getattr(error, "http_status", None),
        "curl_exit_code": getattr(error, "curl_exit_code", None),
        "reason": str(error),
    }
    settings.logs_dir.mkdir(parents=True, exist_ok=True)
    with (settings.logs_dir / "failed.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(failure, ensure_ascii=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    print(f"FAILED | ID #{global_position} | {type(error).__name__}", flush=True)
