"""Retrieve Tiki products with request pacing and bounded transient-network retries.

The caller owns security-challenge handling and checkpoint persistence.
"""
import json
import subprocess
import time
from concurrent.futures import CancelledError
from threading import Lock

from .config import Settings


# Errors that can reasonably resolve without changing the request.
TRANSIENT_CURL_EXIT_CODES = {6, 7, 18, 28, 35, 52, 56}


class FetchError(Exception):
    def __init__(self, message, http_status=None, curl_exit_code=None):
        super().__init__(message)
        self.http_status = http_status
        self.curl_exit_code = curl_exit_code


class SecurityChallengeError(FetchError):
    """HTML security response or existing HTTP 403/429 handling."""

    def __init__(self, *args, response_body=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.response_body = response_body


class TransientFetchError(FetchError):
    """Network or server error that may succeed when retried."""


class RetryExhaustedError(FetchError):
    """Temporary failure persisted; pipeline must STOP, not skip this ID."""


class ProductClient:
    def __init__(self, settings: Settings, stop_event=None):
        self.settings = settings
        self.stop_event = stop_event
        self.last_request_start = None
        self._pace_lock = Lock()

    def _check_stopped(self):
        if self.stop_event is not None and self.stop_event.is_set():
            raise CancelledError("Request stopped before another attempt")

    def _wait(self, delay):
        if self.stop_event is None:
            time.sleep(delay)
        elif self.stop_event.wait(delay):
            raise CancelledError("Request stopped while waiting")

    def _pace(self):
        # At least request_delay between the start of ALL attempts, even retries.
        # One shared client/lock for the whole crawl, not one limiter per worker.
        with self._pace_lock:
            self._check_stopped()
            if self.last_request_start is not None:
                remaining = self.settings.request_delay - (
                    time.perf_counter() - self.last_request_start
                )
                if remaining > 0:
                    self._wait(remaining)
            self._check_stopped()
            self.last_request_start = time.perf_counter()

    def _fetch_once(self, product_id: str) -> dict:
        self._pace()

        try:
            result = subprocess.run(
                [
                    "curl", "-sS",
                    "--max-time", str(self.settings.request_timeout),
                    "-w", "\n%{http_code}",
                    "-H", "User-Agent: Mozilla/5.0",
                    "-H", "Accept: application/json",
                    f"{self.settings.api_url}/{product_id}",
                ],
                capture_output=True,
                text=True,
            )
        except OSError as exc:
            raise TransientFetchError(f"Could not start cURL: {exc}") from exc

        body, separator, status_text = result.stdout.rpartition("\n")
        if not separator or not status_text.isdigit():
            message = f"Invalid cURL response: {result.stderr.strip()}"
            error_class = (
                TransientFetchError
                if result.returncode in TRANSIENT_CURL_EXIT_CODES
                else FetchError
            )
            raise error_class(message, curl_exit_code=result.returncode)

        status = int(status_text)
        http_status = status if status != 0 else None
        is_html = body.lstrip().lower().startswith(("<!doctype html", "<html"))

        # Preserve the existing BytePlus / access-check path in pipeline.py.
        if is_html or status in (403, 429):
            raise SecurityChallengeError(
                f"HTTP {status}: access/security check for product {product_id}",
                http_status=http_status,
                curl_exit_code=result.returncode,
                response_body=body if is_html else None,
            )

        if status >= 500:
            raise TransientFetchError(
                f"HTTP {status} for product {product_id}",
                http_status=http_status,
                curl_exit_code=result.returncode,
            )

        if result.returncode != 0:
            error_class = (
                TransientFetchError
                if result.returncode in TRANSIENT_CURL_EXIT_CODES
                else FetchError
            )
            raise error_class(
                f"cURL error: {result.stderr.strip()}",
                http_status=http_status,
                curl_exit_code=result.returncode,
            )

        if status >= 400:
            raise FetchError(
                f"HTTP {status} for product {product_id}",
                http_status=http_status,
                curl_exit_code=result.returncode,
            )

        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise FetchError(
                f"Invalid JSON for product {product_id}",
                http_status=http_status,
                curl_exit_code=result.returncode,
            ) from exc

    def fetch(self, product_id: str) -> dict:
        """Retry transient errors only; no retry for 404, invalid JSON or BytePlus."""
        for attempt in range(self.settings.max_network_retries + 1):
            try:
                return self._fetch_once(product_id)
            except TransientFetchError as error:
                if attempt == self.settings.max_network_retries:
                    raise RetryExhaustedError(
                        f"Product {product_id}: network/server error after "
                        f"{attempt + 1} attempts: {error}",
                        http_status=error.http_status,
                        curl_exit_code=error.curl_exit_code,
                    ) from error

                delay = self.settings.retry_base_delay * (2 ** attempt)
                print(
                    f"Temporary error at ID {product_id}: {error}. "
                    f"Retry {attempt + 1}/{self.settings.max_network_retries} "
                    f"in {delay:g}s...",
                    flush=True,
                )
                self._wait(delay)
