"""Task 4: Reproject to EPSG:4326 via gdalwarp."""

import re
import time
from pathlib import Path

import pytest

from helpers import (
    TaskResult,
    band_statistics,
    make_zarr_url,
    png_max_pixel_value,
    run_gdal_translate,
    run_gdalinfo,
    run_gdalwarp,
)


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

    # Collect source band statistics to diagnose auto-scale behaviour.
    # Using the reprojected TIF (same data, already local) is faster than
    # re-fetching from the remote Zarr URL.
    src_stats = band_statistics(out_file)
    scale_min, scale_max = cfg.vis_scale

    # Thumbnail with explicit bounds
    png_file = str(output_dir / "images" / "b02_4326.png")
    run_gdal_translate(
        out_file, png_file,
        extra_args=["-of", "PNG", "-scale", str(scale_min), str(scale_max), "0", "255",
                    "-outsize", "10%", "10%"],
    )

    max_val = png_max_pixel_value(png_file) if Path(png_file).exists() else None
    if max_val is not None:
        assert max_val > 5, (
            f"Reprojection thumbnail appears all-black (max pixel value={max_val}). "
            "Check vis_scale bounds in the dataset config."
        )

    # Build diagnosis note (same logic as Task 5)
    if src_stats:
        src_min = src_stats.get("MINIMUM", "?")
        src_max = src_stats.get("MAXIMUM", "?")
        src_mean = src_stats.get("MEAN", "?")
        if isinstance(src_max, float) and src_max > 10000:
            scale_note = (
                f"Source band actual range: min={src_min:.0f} max={src_max:.0f} "
                f"mean={src_mean:.0f} — very high max DN (e.g. clouds/saturation) widens "
                f"the min–max stretch; bare `-scale` maps typical reflectance near-black. "
                f"Explicit vis_scale avoids that."
            )
        else:
            scale_note = (
                f"Source band actual range: min={src_min:.0f} max={src_max:.0f} "
                f"mean={src_mean:.0f}; thumbnail uses explicit vis_scale."
            )
    else:
        scale_note = "Source band statistics not available."

    duration = time.monotonic() - start
    artifacts = [png_file] if Path(png_file).exists() else []
    thumbnail_note = f", thumbnail max pixel={max_val:.0f}/255" if max_val is not None else ""

    def ck(flag: bool) -> str:
        return "x" if flag else " "

    visual_ok = max_val is not None and max_val > 5
    subchecks = [
        "[x] Output correctly georeferenced: EPSG:4326",
        f"[{ck(visual_ok)}] Visually appealing result: thumbnail max pixel={max_val:.0f}/255 (expect > 5)" if max_val is not None else "[ ] Visually appealing result: thumbnail not generated",
    ]

    report.add(TaskResult(
        name="4. Reproject -> EPSG:4326",
        passed=True,
        duration=duration,
        details=(
            f"Reprojected to EPSG:4326, output={Path(out_file).name}{thumbnail_note}. "
            f"{scale_note}"
        ),
        subchecks=subchecks,
        artifacts=artifacts,
        cli_commands=[
            f"gdalwarp -t_srs EPSG:4326 '{url}' b02_4326.tif -q",
            f"gdal_translate -of PNG -scale {scale_min} {scale_max} 0 255 -outsize 10% 10% b02_4326.tif b02_4326.png -q",
        ],
        output_snippet=info.stdout,
    ))
