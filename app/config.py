"""Read settings from .env; keep paths relative to the project, not the terminal."""
from dataclasses import dataclass
import math
import os
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv

PROJECT_DIR = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    root: Path
    api_url: str
    csv_file: Path
    batch_size: int
    request_delay: float
    request_timeout: int
    checkpoint_every: int
    security_retry_delay: float
    max_security_retries: int
    max_network_retries: int = 2
    retry_base_delay: float = 2.0
    max_workers: int = 3

    def __post_init__(self) -> None:
        """Validate direct construction as well as environment-loaded settings."""
        for name in ("batch_size", "checkpoint_every", "request_timeout", "max_workers"):
            value = getattr(self, name)
            if type(value) is not int or value <= 0:
                raise ValueError(f"{name.upper()} must be a positive integer")
        for name in ("max_security_retries", "max_network_retries"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{name.upper()} must be a nonnegative integer")
        for name in ("request_delay", "security_retry_delay", "retry_base_delay"):
            value = getattr(self, name)
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) or value < 0):
                raise ValueError(f"{name.upper()} must be finite and nonnegative")
        url = urlsplit(self.api_url)
        if url.scheme not in ("http", "https") or not url.hostname:
            raise ValueError("API_URL must be an absolute HTTP or HTTPS URL")

    @property
    def output_dir(self) -> Path:
        return self.root / "output"

    @property
    def logs_dir(self) -> Path:
        return self.root / "logs"

    @property
    def checkpoint_file(self) -> Path:
        return self.root / "checkpoint.json"


def load_settings() -> Settings:
    load_dotenv(PROJECT_DIR / ".env")
    csv_path = os.getenv("CSV_FILE", "data/product_ids.csv").strip()
    if not csv_path:
        raise ValueError("CSV_FILE must not be empty")
    csv_file = Path(csv_path)
    if not csv_file.is_absolute():
        csv_file = PROJECT_DIR / csv_file

    return Settings(
        root=PROJECT_DIR,
        api_url=os.getenv("API_URL", "https://api.tiki.vn/product-detail/api/v1/products").rstrip("/"),
        csv_file=csv_file,
        batch_size=int(os.getenv("BATCH_SIZE", "1000")),
        request_delay=float(os.getenv("REQUEST_DELAY", "1")),
        request_timeout=int(os.getenv("REQUEST_TIMEOUT", "15")),
        checkpoint_every=int(os.getenv("CHECKPOINT_EVERY", "50")),
        security_retry_delay=float(os.getenv("SECURITY_RETRY_DELAY", "120")),
        max_security_retries=int(os.getenv("MAX_SECURITY_RETRIES", "2")),
        max_network_retries=int(os.getenv("MAX_NETWORK_RETRIES", "2")),
        retry_base_delay=float(os.getenv("RETRY_BASE_DELAY", "2")),
        max_workers=int(os.getenv("MAX_WORKERS", "3")),
    )
