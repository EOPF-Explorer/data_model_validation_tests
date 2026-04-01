"""Task 3: Export band to GeoTIFF — verify CRS, extent, and pixel values preserved."""

import re
import time
from pathlib import Path

import pytest

from helpers import (
    TaskResult,
    band_statistics,
    make_zarr_url,
    run_gdal_translate,
    run_gdalinfo,
)


def test_export(dataset_url, dataset_config, output_dir, report, gdal_version):
    cfg = dataset_config
    url = make_zarr_url(dataset_url, cfg.default_band_path)
    out_file = str(output_dir / "band.tif")
    start = time.monotonic()

    result = run_gdal_translate(url, out_file)
    assert result.returncode == 0, f"gdal_translate failed: {result.stderr[:200]}"
    assert Path(out_file).exists(), "Output file not created"

    info = run_gdalinfo(out_file)
    assert info.returncode == 0

    def ck(flag: bool) -> str:
        return "x" if flag else " "

    # ------------------------------------------------------------------
    # CRS preserved
    # ------------------------------------------------------------------
    crs_ok = False
    if cfg.crs_authority_code:
        crs_ok = bool(re.search(rf'EPSG[",]+{cfg.crs_authority_code}', info.stdout))
        assert crs_ok, f"Expected EPSG:{cfg.crs_authority_code} in exported GeoTIFF"

    # ------------------------------------------------------------------
    # Extent: origin must be non-zero (real georeferencing, not identity default)
    # ------------------------------------------------------------------
    origin_m = re.search(r"Origin = \(([0-9.]+)", info.stdout)
    pix_m    = re.search(r"Pixel Size = \(([0-9.]+)", info.stdout)
    origin_x = float(origin_m.group(1)) if origin_m else None
    pix_x    = float(pix_m.group(1)) if pix_m else None
    origin_ok = origin_x is not None and origin_x != 0.0
    pix_ok    = pix_x is not None

    # ------------------------------------------------------------------
    # Pixel values: statistics max > 0 (non-empty raster)
    # ------------------------------------------------------------------
    stats    = band_statistics(out_file)
    vals_ok  = stats is not None and stats.get("MAXIMUM", 0) > 0
    stats_max = stats.get("MAXIMUM", "?") if stats else "not available"

    # ------------------------------------------------------------------
    # Per-sub-bullet checklist (mirrors requirement §3)
    # ------------------------------------------------------------------
    subchecks = [
        f"[{ck(crs_ok)}] CRS preserved: EPSG:{cfg.crs_authority_code}",
        f"[{ck(origin_ok)}] Extent / origin preserved: origin_x={origin_x if origin_x is not None else '?'}, pixel_size={pix_x:g}m" if pix_x else f"[{ck(origin_ok)}] Extent / origin preserved: origin_x={origin_x if origin_x is not None else '?'}",
        f"[{ck(vals_ok)}] Pixel values preserved: max={stats_max} (expect > 0)",
    ]

    duration = time.monotonic() - start
    details_parts = [f"Exported to {Path(out_file).name}"]
    if cfg.crs_authority_code:
        details_parts.append(f"CRS=EPSG:{cfg.crs_authority_code} verified")
    if origin_x is not None:
        details_parts.append(f"origin={origin_x:.0f}")
    if pix_x is not None:
        details_parts.append(f"pixel={pix_x:g}m")
    details_parts.append(f"max={stats_max}")

    report.add(TaskResult(
        name="3. Export -> GeoTIFF",
        passed=True,
        duration=duration,
        details=", ".join(details_parts),
        subchecks=subchecks,
        cli_commands=[
            f"gdal_translate '{url}' band.tif -q",
            "gdalinfo -stats band.tif",
        ],
        output_snippet=info.stdout,
    ))
