"""Read settings from .env; keep paths relative to the project, not the terminal."""
from dataclasses import dataclass
import os
from pathlib import Path

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
    csv_file = Path(os.getenv("CSV_FILE", "product_ids.csv"))
    if not csv_file.is_absolute():
        csv_file = PROJECT_DIR / csv_file

    settings = Settings(
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
    )
    if settings.batch_size <= 0 or settings.checkpoint_every <= 0:
        raise ValueError("BATCH_SIZE and CHECKPOINT_EVERY must be positive")
    if settings.request_delay < 0 or settings.request_timeout <= 0:
        raise ValueError("REQUEST_DELAY must be >= 0 and REQUEST_TIMEOUT must be > 0")
    if settings.security_retry_delay < 0 or settings.max_security_retries < 0:
        raise ValueError("Security retry configuration must be nonnegative")
    if settings.max_network_retries < 0 or settings.retry_base_delay < 0:
        raise ValueError("Network retry configuration must be nonnegative")
    return settings
