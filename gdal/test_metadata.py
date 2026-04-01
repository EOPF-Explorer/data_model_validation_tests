"""Task 1: Metadata — CRS, pixel size, overview count, block size, band metadata,
and consolidated network requests via gdalinfo."""

import re
import time

import pytest

from helpers import (
    TaskResult,
    make_zarr_url,
    parse_head_count,
    run_gdalinfo,
    split_network_stats,
)


def test_metadata(dataset_url, dataset_config, report, gdal_version):
    cfg = dataset_config
    url = make_zarr_url(dataset_url, cfg.default_band_path)
    start = time.monotonic()

    # Run with network stats so we can measure HEAD request count (consolidated metadata)
    result = run_gdalinfo(url, network_stats=True)
    assert result.returncode == 0, f"gdalinfo failed: {result.stderr[:200]}"

    # Split gdalinfo text from the trailing network-stats JSON block
    out, net_stats = split_network_stats(result.stdout)

    # ------------------------------------------------------------------
    # CRS
    # ------------------------------------------------------------------
    crs_ok = False
    if cfg.crs_authority_code:
        crs_ok = bool(re.search(rf'EPSG[",]+{cfg.crs_authority_code}', out))
        assert crs_ok, f"Expected EPSG:{cfg.crs_authority_code} in gdalinfo output"
    crs_name = (re.search(r'PROJCRS\["([^"]+)"', out) or re.search(r'GEOGCRS\["([^"]+)"', out))
    crs_label = crs_name.group(1) if crs_name else "unknown"

    # ------------------------------------------------------------------
    # GeoTransform / pixel size (expect ~10 m for r10m band)
    # ------------------------------------------------------------------
    pix_m = re.search(r"Pixel Size = \(([0-9.]+)", out)
    pixel_x = float(pix_m.group(1)) if pix_m else None
    pixel_ok = pixel_x is not None and abs(pixel_x - 10.0) <= 1.0

    # ------------------------------------------------------------------
    # Overview count
    # ------------------------------------------------------------------
    ovr_count = 0
    ovr_match = re.search(r"Overviews:\s*(.+)", out)
    if cfg.min_overview_count:
        assert ovr_match, "No 'Overviews:' line found in gdalinfo output"
        ovr_count = len(re.findall(r"\d+x\d+", ovr_match.group(1)))
        assert ovr_count >= cfg.min_overview_count, (
            f"Found {ovr_count} overviews, expected >= {cfg.min_overview_count}"
        )
    elif ovr_match:
        ovr_count = len(re.findall(r"\d+x\d+", ovr_match.group(1)))
    ovr_ok = ovr_count >= cfg.min_overview_count

    # ------------------------------------------------------------------
    # Block/chunk size
    # ------------------------------------------------------------------
    block_ok = False
    if cfg.block_size:
        bx, by = cfg.block_size
        block_ok = f"Block={bx}x{by}" in out
        assert block_ok, f"Expected Block={bx}x{by} in gdalinfo output"

    # ------------------------------------------------------------------
    # Band metadata: Scale, Offset, NoData/fill value, units
    # ------------------------------------------------------------------
    scale_m    = re.search(r"^\s+Scale=([0-9.eE+\-]+)", out, re.M)
    offset_m   = re.search(r"^\s+Offset=([0-9.eE+\-]+)", out, re.M)
    nodata_m   = re.search(r"NoData Value=(\S+)|FILL_VALUE=(\S+)", out, re.I)
    units_m    = re.search(r"\bunit[s]?\b[= ]*(\S+)", out, re.I)

    scale_ok   = scale_m is not None
    offset_ok  = offset_m is not None
    nodata_ok  = nodata_m is not None
    units_ok   = units_m is not None

    scale_disp  = scale_m.group(1)  if scale_m  else "not found"
    offset_disp = offset_m.group(1) if offset_m else "not found"
    nodata_disp = (nodata_m.group(1) or nodata_m.group(2)) if nodata_m else "not found"
    units_disp  = units_m.group(1)  if units_m  else "not found"

    # ------------------------------------------------------------------
    # Consolidated metadata: HEAD request count
    # ------------------------------------------------------------------
    head_count = parse_head_count(net_stats)
    consol_ok  = head_count < cfg.consolidated_head_max

    # ------------------------------------------------------------------
    # Per-sub-bullet checklist (mirrors requirement §1)
    # ------------------------------------------------------------------
    def ck(flag: bool) -> str:
        return "x" if flag else " "

    subchecks = [
        f"[{ck(crs_ok)}] CRS decoded: {crs_label}, EPSG:{cfg.crs_authority_code}",
        f"[{ck(pixel_ok)}] GeoTransform / pixel size: {pixel_x}m (r10m band, expect ~10m)",
        f"[{ck(ovr_ok)}] Overviews listed: {ovr_count} levels (expect ≥{cfg.min_overview_count})",
        f"[{ck(block_ok)}] Block/chunk size: {cfg.block_size[0]}×{cfg.block_size[1]}" if cfg.block_size else "[ ] Block/chunk size: not configured",
        f"[{ck(scale_ok)}] Band metadata — Scale: {scale_disp}",
        f"[{ck(offset_ok)}] Band metadata — Offset: {offset_disp}",
        f"[{ck(nodata_ok)}] Band metadata — NoData/fill value: {nodata_disp}",
        f"[{ck(units_ok)}] Band metadata — units: {units_disp}",
        f"[{ck(consol_ok)}] Consolidated metadata: {head_count} HEAD requests (threshold < {cfg.consolidated_head_max})",
    ]

    # ------------------------------------------------------------------
    # Summary details string
    # ------------------------------------------------------------------
    duration = time.monotonic() - start
    details_parts = []
    if cfg.crs_authority_code:
        details_parts.append(f"CRS=EPSG:{cfg.crs_authority_code}")
    if pixel_x is not None:
        details_parts.append(f"pixel={pixel_x:g}m")
    if ovr_match and cfg.min_overview_count:
        details_parts.append(f"overviews={ovr_count}>={cfg.min_overview_count}")
    if cfg.block_size:
        details_parts.append(f"block={bx}x{by}")
    details_parts.append(f"scale={scale_ok} nodata={nodata_ok} HEAD={head_count}")

    report.add(TaskResult(
        name="1. Metadata",
        passed=True,
        duration=duration,
        details=" ".join(details_parts),
        subchecks=subchecks,
        head_count=head_count,
        cli_commands=[f"CPL_VSIL_SHOW_NETWORK_STATS=YES gdalinfo '{url}'"],
        output_snippet=out,
    ))
