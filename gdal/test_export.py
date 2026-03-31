"""Task 3: Export band to GeoTIFF — verify CRS preserved."""

import re
import time
from pathlib import Path

import pytest

from helpers import TestResult, make_zarr_url, run_gdal_translate, run_gdalinfo


def test_export(dataset_url, dataset_config, output_dir, report, gdal_version):
    cfg = dataset_config
    url = make_zarr_url(dataset_url, cfg.default_band_path)
    out_file = str(output_dir / "band.tif")
    start = time.monotonic()

    result = run_gdal_translate(url, out_file)
    assert result.returncode == 0, f"gdal_translate failed: {result.stderr[:200]}"
    assert Path(out_file).exists(), "Output file not created"

    # Verify CRS on the output
    if cfg.crs_authority_code:
        info = run_gdalinfo(out_file)
        assert info.returncode == 0
        assert re.search(rf'EPSG[",]+{cfg.crs_authority_code}', info.stdout), (
            f"Expected EPSG:{cfg.crs_authority_code} in exported GeoTIFF"
        )

    duration = time.monotonic() - start
    report.add(TestResult(
        name="3. Export -> GeoTIFF",
        passed=True,
        duration=duration,
        details=f"Exported to {Path(out_file).name}, CRS=EPSG:{cfg.crs_authority_code} verified",
    ))
