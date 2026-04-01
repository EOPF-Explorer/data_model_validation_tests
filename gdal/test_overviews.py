"""Task 6: Overview reading — coarsest overview, lower-resolution confirmation, efficiency."""

import re
import time
from pathlib import Path

import pytest

from helpers import (
    TaskResult,
    make_zarr_url,
    parse_network_bytes,
    run_gdal_translate,
    run_gdalinfo,
)


def test_overviews(dataset_url, dataset_config, output_dir, report, gdal_version):
    cfg = dataset_config
    url = make_zarr_url(dataset_url, cfg.default_band_path)
    start = time.monotonic()

    # Check that overviews exist and record full-res dimensions
    info = run_gdalinfo(url)
    assert info.returncode == 0
    ovr_match = re.search(r"Overviews:\s*(.+)", info.stdout)
    assert ovr_match, "No overviews found"
    ovr_count = len(re.findall(r"\d+x\d+", ovr_match.group(1)))
    assert ovr_count > 0, "No overviews present"

    full_size_m = re.search(r"Size is (\d+), (\d+)", info.stdout)
    full_w = int(full_size_m.group(1)) if full_size_m else 0
    full_h = int(full_size_m.group(2)) if full_size_m else 0

    # Full-res file size for efficiency comparison (task 3 output if available)
    full_tif = output_dir / "band.tif"
    full_size_kb = full_tif.stat().st_size // 1024 if full_tif.exists() else 0

    # Read every overview level and collect per-level stats
    overview_levels = []
    for lvl in range(ovr_count):
        out_lvl = str(output_dir / f"overview_{lvl}.tif")
        res_lvl = run_gdal_translate(
            url, out_lvl,
            extra_args=["-ovr", str(lvl)],
            network_stats=True,
        )
        net_lvl = parse_network_bytes(res_lvl.stdout + res_lvl.stderr)
        sz_kb = Path(out_lvl).stat().st_size // 1024 if Path(out_lvl).exists() else 0
        ovr_info = run_gdalinfo(out_lvl)
        m = re.search(r"Size is (\d+), (\d+)", ovr_info.stdout or "")
        w = int(m.group(1)) if m else 0
        h = int(m.group(2)) if m else 0
        ratio_pct = round(sz_kb / full_size_kb * 100, 2) if full_size_kb > 0 else 0.0
        factor = round(full_size_kb / sz_kb) if sz_kb > 0 else 0
        overview_levels.append({
            "level": lvl,
            "resolution": f"{w}×{h}",
            "downloaded_kb": net_lvl // 1024 if net_lvl else 0,
            "uncompressed_kb": sz_kb,
            "full_res_kb": full_size_kb,
            "ratio_pct": ratio_pct,
            "factor": factor,
        })

    duration = time.monotonic() - start

    # Use level-0 stats for summary table / subchecks
    lvl0 = overview_levels[0] if overview_levels else {}
    ovr_w = int(lvl0.get("resolution", "0×0").split("×")[0]) if lvl0 else 0
    ovr_h = int(lvl0.get("resolution", "0×0").split("×")[1]) if lvl0 else 0
    size_kb = lvl0.get("uncompressed_kb", 0)
    net_bytes = lvl0.get("downloaded_kb", 0) * 1024

    lowres_ok = ovr_w > 0 and full_w > 0 and ovr_w < full_w
    efficient_ok = size_kb < full_size_kb if full_size_kb > 0 else lowres_ok

    def ck(flag: bool) -> str:
        return "x" if flag else " "

    subchecks = [
        f"[{ck(lowres_ok)}] Overview returns lower-resolution data: {ovr_w}×{ovr_h} vs full-res {full_w}×{full_h}",
        f"[{ck(efficient_ok)}] Overview access is efficient: {size_kb} KB vs full-res {full_size_kb} KB",
    ]

    net_display = f"{lvl0.get('downloaded_kb', 0)} KB" if lvl0.get("downloaded_kb") else "—"

    report.add(TaskResult(
        name="6. Overview Read",
        passed=True,
        duration=duration,
        details=(
            f"{ovr_count} overview levels; "
            f"overview={ovr_w}x{ovr_h} (full={full_w}x{full_h}); "
            f"file={size_kb} KB (full={full_size_kb} KB); "
            f"network={net_display}"
        ),
        subchecks=subchecks,
        network_bytes=net_bytes if net_bytes > 0 else None,
        overview_levels=overview_levels,
        cli_commands=[
            f"gdalinfo '{url}'",
            f"CPL_VSIL_SHOW_NETWORK_STATS=YES \\\n"
            f"  gdal_translate '{url}' overview.tif -ovr 0 -q",
        ],
        output_snippet=info.stdout,
    ))
