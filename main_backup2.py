"""Run the product ETL pipeline."""
from app.config import load_settings
from app.pipeline import run_pipeline


if __name__ == "__main__":
    run_pipeline(load_settings())
