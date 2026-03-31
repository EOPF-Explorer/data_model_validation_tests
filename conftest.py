"""Root-level pytest fixtures for GDAL CLI validation tests."""

import os
import subprocess
from pathlib import Path

import pytest

from helpers import DatasetConfig, ReportCollector, load_config

# ---------------------------------------------------------------------------
# Session fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def output_dir() -> Path:
    out = Path("output/images")
    out.mkdir(parents=True, exist_ok=True)
    return out.parent


@pytest.fixture(scope="session")
def dataset_config() -> DatasetConfig:
    config_path = os.environ.get("EOPF_DATASET_CONFIG", "configs/sentinel2_l2a.toml")
    return load_config(config_path)


@pytest.fixture(scope="session")
def dataset_url() -> str:
    url = os.environ.get("EOPF_DATASET_URL")
    if not url:
        pytest.skip("EOPF_DATASET_URL not set")
    return url


@pytest.fixture(scope="session")
def report(output_dir, dataset_url):
    """Collects test results and writes a markdown report at session end."""
    collector = ReportCollector()

    # Populate environment info
    result = subprocess.run(
        ["gdalinfo", "--version"], capture_output=True, text=True, check=False
    )
    collector.env_info["GDAL version"] = result.stdout.strip() if result.returncode == 0 else "unknown"
    collector.env_info["Dataset URL"] = dataset_url

    yield collector

    collector.write(output_dir / "report.md")
