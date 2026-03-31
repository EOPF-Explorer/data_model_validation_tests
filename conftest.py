"""Root-level pytest fixtures for GDAL CLI validation tests."""

import os
import platform
import subprocess
import sys
from datetime import datetime, timezone
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

    gdal_version_result = subprocess.run(
        ["gdalinfo", "--version"], capture_output=True, text=True, check=False
    )
    gdal_version = (
        gdal_version_result.stdout.strip()
        if gdal_version_result.returncode == 0
        else "unknown"
    )

    # Optional: gdal-config --formats for build info (may not be present in all envs)
    gdal_formats_result = subprocess.run(
        ["gdal-config", "--formats"], capture_output=True, text=True, check=False
    )
    gdal_formats = (
        gdal_formats_result.stdout.strip()
        if gdal_formats_result.returncode == 0
        else "unavailable"
    )

    collector.env_info["GDAL version"] = gdal_version
    collector.env_info["GDAL formats"] = gdal_formats
    collector.env_info["Python version"] = sys.version.split()[0]
    collector.env_info["Platform"] = platform.platform()
    collector.env_info["Dataset URL"] = dataset_url
    collector.env_info["Date"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    yield collector

    collector.write(output_dir / "report.md")
