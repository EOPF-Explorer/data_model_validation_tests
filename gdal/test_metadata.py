"""Task 1: Metadata — CRS, overview count, block size via gdalinfo."""

import re
import time

import pytest

from helpers import TestResult, make_zarr_url, run_gdalinfo


def test_metadata(dataset_url, dataset_config, report, gdal_version):
    cfg = dataset_config
    url = make_zarr_url(dataset_url, cfg.default_band_path)
    start = time.monotonic()

    result = run_gdalinfo(url)
    assert result.returncode == 0, f"gdalinfo failed: {result.stderr[:200]}"
    out = result.stdout

    # CRS
    if cfg.crs_authority_code:
        assert re.search(rf'EPSG[",]+{cfg.crs_authority_code}', out), (
            f"Expected EPSG:{cfg.crs_authority_code} in gdalinfo output"
        )

    # Overview count
    ovr_match = re.search(r"Overviews:\s*(.+)", out)
    if cfg.min_overview_count:
        assert ovr_match, "No 'Overviews:' line found in gdalinfo output"
        ovr_count = len(re.findall(r"\d+x\d+", ovr_match.group(1)))
        assert ovr_count >= cfg.min_overview_count, (
            f"Found {ovr_count} overviews, expected >= {cfg.min_overview_count}"
        )

    # Block size
    if cfg.block_size:
        bx, by = cfg.block_size
        assert f"Block={bx}x{by}" in out, (
            f"Expected Block={bx}x{by} in gdalinfo output"
        )

    duration = time.monotonic() - start
    details = []
    if cfg.crs_authority_code:
        details.append(f"CRS=EPSG:{cfg.crs_authority_code}")
    if ovr_match and cfg.min_overview_count:
        details.append(f"overviews={ovr_count}>={cfg.min_overview_count}")
    if cfg.block_size:
        details.append(f"block={bx}x{by}")

    report.add(TestResult(
        name="1. Metadata",
        passed=True,
        duration=duration,
        details=" ".join(details),
    ))
