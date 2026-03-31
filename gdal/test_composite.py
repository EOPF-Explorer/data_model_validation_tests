"""Task 5: RGB composite — gdalbuildvrt + gdal_translate to PNG."""

import time
from pathlib import Path

import pytest

from helpers import (
    TestResult,
    band_statistics,
    make_zarr_url,
    png_max_pixel_value,
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

    # Collect source band statistics before translating.
    # This distinguishes a GDAL auto-scale bug from a data-range issue:
    #   - If actual max ≈ 65535 → data has outliers; auto-scale is "correct" but useless
    #   - If actual max is a reasonable value yet auto-scale still produces black → GDAL bug
    src_stats = band_statistics(url_r)

    # Also run the broken auto-scale command for direct comparison in the report
    autoscale_png = str(output_dir / "images" / "rgb_composite_autoscale_diagnostic.png")
    run_gdal_translate(
        vrt_file if Path(vrt_file).exists() else url_r,
        autoscale_png,
        extra_args=["-of", "PNG", "-scale", "-outsize", "10%", "10%"],
    )
    autoscale_max = png_max_pixel_value(autoscale_png)

    # Build VRT with separate bands
    vrt = run_gdalbuildvrt(vrt_file, [url_r, url_g, url_b], extra_args=["-separate"])
    assert vrt.returncode == 0, f"gdalbuildvrt failed: {vrt.stderr[:200]}"

    # Translate to PNG with explicit scale bounds from config (avoids black images
    # caused by outlier DN values compressing the typical reflectance range to near-zero)
    scale_min, scale_max = cfg.vis_scale
    tr = run_gdal_translate(
        vrt_file, png_file,
        extra_args=["-of", "PNG", "-scale", str(scale_min), str(scale_max), "0", "255",
                    "-outsize", "10%", "10%"],
    )
    assert tr.returncode == 0, f"gdal_translate to PNG failed: {tr.stderr[:200]}"

    png = Path(png_file)
    assert png.exists() and png.stat().st_size > 0, "PNG file missing or empty"

    max_val = png_max_pixel_value(png_file)
    assert max_val is not None and max_val > 5, (
        f"RGB PNG appears all-black (max pixel value={max_val}). "
        "Check vis_scale bounds in the dataset config."
    )

    # Build a human-readable diagnosis note for the report
    if src_stats:
        src_min = src_stats.get("MINIMUM", "?")
        src_max = src_stats.get("MAXIMUM", "?")
        src_mean = src_stats.get("MEAN", "?")
        if isinstance(src_max, float) and src_max > 10000:
            scale_note = (
                f"Source band actual range: min={src_min:.0f} max={src_max:.0f} "
                f"mean={src_mean:.0f} — outlier values (e.g. clouds/saturation near 65535) "
                f"cause `-scale` without bounds to map typical land pixels to near-black "
                f"(auto-scale max pixel={autoscale_max:.0f}/255). "
                f"This is expected GDAL behaviour, not a GDAL bug; use explicit bounds."
            )
        else:
            if autoscale_max is not None and autoscale_max < 5:
                scale_note = (
                    f"Source band actual range: min={src_min:.0f} max={src_max:.0f} "
                    f"mean={src_mean:.0f} — range looks reasonable yet auto-scale produced "
                    f"a black image (max pixel={autoscale_max:.0f}/255). "
                    f"This may indicate a GDAL auto-scale bug; consider filing at "
                    f"https://github.com/OSGeo/gdal/issues"
                )
            else:
                scale_note = (
                    f"Source band actual range: min={src_min:.0f} max={src_max:.0f} "
                    f"mean={src_mean:.0f}; auto-scale max pixel={autoscale_max}/255."
                )
    else:
        scale_note = "Source band statistics not available."

    duration = time.monotonic() - start
    size_kb = png.stat().st_size // 1024
    report.add(TestResult(
        name="5. RGB Composite",
        passed=True,
        duration=duration,
        details=(
            f"RGB PNG written ({size_kb} KB, max pixel={max_val:.0f}/255): {png.name}. "
            f"{scale_note}"
        ),
        artifacts=[png_file],
        cli_commands=[
            f"# Diagnostic: auto-scale (bare -scale) — may produce black image\n"
            f"gdal_translate -of PNG -scale -outsize 10% 10% \\\n"
            f"  rgb.vrt rgb_composite_autoscale_diagnostic.png -q\n"
            f"\n"
            f"# Correct: explicit bounds from vis_scale config\n"
            f"gdalbuildvrt -separate rgb.vrt \\\n"
            f"  '{url_r}' \\\n"
            f"  '{url_g}' \\\n"
            f"  '{url_b}'",
            f"gdal_translate -of PNG -scale {scale_min} {scale_max} 0 255 -outsize 10% 10% rgb.vrt rgb_composite.png -q",
        ],
        output_snippet=vrt.stdout + tr.stdout,
    ))
