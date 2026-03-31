"""Task 5: RGB composite — gdalbuildvrt + gdal_translate to PNG."""

import time
from pathlib import Path

import pytest

from helpers import (
    TestResult,
    make_zarr_url,
    run_gdalbuildvrt,
    run_gdal_translate,
)


def test_composite(dataset_url, dataset_config, output_dir, report, gdal_version):
    cfg = dataset_config
    if cfg.rgb_composite is None:
        pytest.skip("No [rgb_composite] section in config")

    rgb = cfg.rgb_composite
    url_r = make_zarr_url(dataset_url, rgb.red)
    url_g = make_zarr_url(dataset_url, rgb.green)
    url_b = make_zarr_url(dataset_url, rgb.blue)
    vrt_file = str(output_dir / "rgb.vrt")
    png_file = str(output_dir / "images" / "rgb_composite.png")
    start = time.monotonic()

    # Build VRT with separate bands
    vrt = run_gdalbuildvrt(vrt_file, [url_r, url_g, url_b], extra_args=["-separate"])
    assert vrt.returncode == 0, f"gdalbuildvrt failed: {vrt.stderr[:200]}"

    # Translate to PNG with auto-stretch
    tr = run_gdal_translate(
        vrt_file, png_file,
        extra_args=["-of", "PNG", "-scale", "-outsize", "10%", "10%"],
    )
    assert tr.returncode == 0, f"gdal_translate to PNG failed: {tr.stderr[:200]}"

    png = Path(png_file)
    assert png.exists() and png.stat().st_size > 0, "PNG file missing or empty"

    duration = time.monotonic() - start
    size_kb = png.stat().st_size // 1024
    report.add(TestResult(
        name="5. RGB Composite",
        passed=True,
        duration=duration,
        details=f"RGB PNG written ({size_kb} KB): {png.name}",
        artifacts=[png_file],
    ))
