"""Bounded fetch-ahead; callers consume and persist only in input order."""
from concurrent.futures import CancelledError, ThreadPoolExecutor

from .client import FetchError, RetryExhaustedError, SecurityChallengeError


# These are per-product failures the existing pipeline logs and skips.
PRODUCT_ERRORS = (FetchError, TypeError, ValueError, KeyError, AttributeError)
STOP_ERRORS = (SecurityChallengeError, RetryExhaustedError)


class OrderedFetcher:
    def __init__(self, client, product_ids, start, end, max_workers, stop_event):
        self.client = client
        self.product_ids = product_ids
        self.end = end
        self.max_workers = max_workers
        self.stop_event = stop_event
        self.executor = ThreadPoolExecutor(max_workers=max_workers)
        self.futures = {}
        self.next_submit = start

    def _fetch(self, index):
        if self.stop_event.is_set():
            raise CancelledError()
        try:
            return self.client.fetch(self.product_ids[index])
        except PRODUCT_ERRORS as error:
            if isinstance(error, STOP_ERRORS):
                self.stop_event.set()
            raise
        except BaseException:
            self.stop_event.set()
            raise

    def _drain(self):
        """Quiesce before a cooldown/reset; no worker may still be retrying."""
        for future in self.futures.values():
            future.cancel()
        errors = []
        for future in self.futures.values():
            try:
                future.result()
            except BaseException as error:
                errors.append(error)
        return errors

    def fetch(self, index):
        # A bounded sliding window avoids an unbounded queue/results buffer.
        self.next_submit = max(index, self.next_submit)
        while (self.next_submit < self.end
               and len(self.futures) < self.max_workers
               and not self.stop_event.is_set()):
            position = self.next_submit
            self.futures[position] = self.executor.submit(self._fetch, position)
            self.next_submit += 1
        try:
            return self.futures[index].result()
        except BaseException as error:
            if not isinstance(error, PRODUCT_ERRORS) or isinstance(error, STOP_ERRORS):
                self.stop_event.set()
                errors = self._drain()
                self.futures.clear()
                self.next_submit = index
                self.stop_event.clear()
                # Security takes precedence over retry-exhaustion/restart paths.
                for failure in errors:
                    if isinstance(failure, SecurityChallengeError):
                        raise failure
                if isinstance(error, CancelledError):
                    # A later fatal result may have cancelled an earlier retry.
                    # Replay from this earliest gap, never past it.
                    for failure in errors:
                        if (isinstance(failure, STOP_ERRORS)
                                or not isinstance(failure, PRODUCT_ERRORS + (CancelledError,))):
                            raise failure
            raise
        finally:
            self.futures.pop(index, None)

    def close(self):
        self.stop_event.set()
        for future in self.futures.values():
            future.cancel()
        self.executor.shutdown(wait=True, cancel_futures=True)
