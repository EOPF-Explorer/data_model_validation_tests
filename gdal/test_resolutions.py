"""Task 7: Multiple resolutions — pixel sizes for each configured band."""

import re
import time

import pytest

from helpers import TaskResult, make_zarr_url, run_gdalinfo


@pytest.mark.parametrize(
    "band_index",
    [0, 1, 2],
    ids=lambda i: ["r10m", "r20m", "r60m"][i] if i < 3 else str(i),
)
def test_resolution(dataset_url, dataset_config, report, gdal_version, band_index):
    cfg = dataset_config
    if band_index >= len(cfg.resolution_bands):
        pytest.skip(f"No resolution band at index {band_index}")

    band = cfg.resolution_bands[band_index]
    url = make_zarr_url(dataset_url, band.zarr_path)
    start = time.monotonic()

    result = run_gdalinfo(url)
    assert result.returncode == 0, f"gdalinfo failed for {band.label}: {result.stderr[:200]}"

    # Extract pixel size: "Pixel Size = (10.000000000,-10.000000000)"
    m = re.search(r"Pixel Size = \(([0-9.]+)", result.stdout)
    assert m, f"Pixel size not found in gdalinfo output for {band.label}"

    pixel_x = float(m.group(1))
    assert abs(pixel_x - band.expected_pixel_size_m) <= 1.0, (
        f"{band.label}: pixel size {pixel_x}m, expected {band.expected_pixel_size_m}m (±1m)"
    )

    duration = time.monotonic() - start
    res_ok = abs(pixel_x - band.expected_pixel_size_m) <= 1.0
    subchecks = [
        f"[{'x' if res_ok else ' '}] {band.label} band: pixel size={pixel_x:g}m (expect {band.expected_pixel_size_m:g}m)",
    ]

    report.add(TaskResult(
        name=f"7. Resolution {band.label}",
        passed=True,
        duration=duration,
        details=f"{band.label}={pixel_x:g}m (expected {band.expected_pixel_size_m:g}m)",
        subchecks=subchecks,
        cli_commands=[f"gdalinfo '{url}'"],
        output_snippet=result.stdout,
    ))
