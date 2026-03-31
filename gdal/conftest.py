"""GDAL-specific fixtures for CLI validation tests."""

import subprocess

import pytest


@pytest.fixture(scope="session")
def gdal_version():
    result = subprocess.run(
        ["gdalinfo", "--version"], capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        pytest.skip("gdalinfo not found in PATH")
    return result.stdout.strip()
