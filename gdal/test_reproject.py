"""Task 4: Reproject to EPSG:4326 via gdalwarp."""

import re
import time
from pathlib import Path

import pytest

from helpers import TestResult, make_zarr_url, run_gdalinfo, run_gdalwarp


def test_reproject(dataset_url, dataset_config, output_dir, report, gdal_version):
    cfg = dataset_config
    url = make_zarr_url(dataset_url, cfg.default_band_path)
    out_file = str(output_dir / "b02_4326.tif")
    start = time.monotonic()

    result = run_gdalwarp(url, out_file, extra_args=["-t_srs", "EPSG:4326"])
    assert result.returncode == 0, f"gdalwarp failed: {result.stderr[:200]}"
    assert Path(out_file).exists(), "Output file not created"

    info = run_gdalinfo(out_file)
    assert info.returncode == 0
    assert re.search(r'EPSG[",]+4326', info.stdout), (
        "EPSG:4326 not found in reprojected output"
    )

    duration = time.monotonic() - start
    report.add(TestResult(
        name="4. Reproject -> EPSG:4326",
        passed=True,
        duration=duration,
        details=f"Reprojected to EPSG:4326, output={Path(out_file).name}",
    ))
