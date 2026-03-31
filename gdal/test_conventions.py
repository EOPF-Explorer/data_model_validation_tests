"""Task 8: GeoZarr conventions — driver, CRS, GeoTransform via gdalinfo -json."""

import json
import time

import pytest

from helpers import TestResult, make_zarr_url, run_gdalinfo


def test_conventions(dataset_url, dataset_config, report, gdal_version):
    cfg = dataset_config
    url = make_zarr_url(dataset_url, cfg.default_band_path)
    start = time.monotonic()

    result = run_gdalinfo(url, json_mode=True)
    assert result.returncode == 0, f"gdalinfo -json failed: {result.stderr[:200]}"

    data = json.loads(result.stdout)

    # Driver must contain "Zarr"
    driver = data.get("driverShortName", "") + data.get("driverLongName", "")
    assert "zarr" in driver.lower(), f"Expected Zarr driver, got: {driver}"

    # CRS must be present and non-empty
    coord_sys = data.get("coordinateSystem", {})
    wkt = coord_sys.get("wkt", "")
    assert wkt, "coordinateSystem.wkt is empty — no CRS projection found"

    # GeoTransform must be non-default (origin != 0)
    gt = data.get("geoTransform", [0, 1, 0, 0, 0, 1])
    assert gt[0] != 0.0, f"GeoTransform origin X is 0 (default identity): {gt}"

    duration = time.monotonic() - start
    report.add(TestResult(
        name="8. GeoZarr Conventions",
        passed=True,
        duration=duration,
        details="driver=Zarr CRS=present GeoTransform=non-default",
    ))
