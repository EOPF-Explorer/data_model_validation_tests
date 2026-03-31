"""Task 6: Overview reading — coarsest overview, network bytes."""

import re
import time
from pathlib import Path

import pytest

from helpers import (
    TestResult,
    make_zarr_url,
    parse_network_bytes,
    run_gdal_translate,
    run_gdalinfo,
)


def test_overviews(dataset_url, dataset_config, output_dir, report, gdal_version):
    cfg = dataset_config
    url = make_zarr_url(dataset_url, cfg.default_band_path)
    out_file = str(output_dir / "overview.tif")
    start = time.monotonic()

    # First check that overviews exist
    info = run_gdalinfo(url)
    assert info.returncode == 0
    ovr_match = re.search(r"Overviews:\s*(.+)", info.stdout)
    assert ovr_match, "No overviews found"
    ovr_count = len(re.findall(r"\d+x\d+", ovr_match.group(1)))
    assert ovr_count > 0, "No overviews present"

    # Read coarsest overview (-ovr 0)
    result = run_gdal_translate(
        url, out_file,
        extra_args=["-ovr", "0"],
        network_stats=True,
    )
    assert result.returncode == 0, f"gdal_translate -ovr 0 failed: {result.stderr[:200]}"

    net_bytes = parse_network_bytes(result.stdout + result.stderr)
    duration = time.monotonic() - start
    size_kb = Path(out_file).stat().st_size // 1024 if Path(out_file).exists() else 0
    net_display = f"{net_bytes // 1024} KB" if net_bytes > 0 else "—"

    report.add(TestResult(
        name="6. Overview Read",
        passed=True,
        duration=duration,
        details=f"{ovr_count} overview levels; coarsest: {size_kb} KB; network: {net_display}",
        network_bytes=net_bytes if net_bytes > 0 else None,
        cli_commands=[
            f"gdalinfo '{url}'",
            f"CPL_VSIL_SHOW_NETWORK_STATS=YES \\\n"
            f"  gdal_translate '{url}' overview.tif -ovr 0 -q",
        ],
        output_snippet=info.stdout,
    ))
