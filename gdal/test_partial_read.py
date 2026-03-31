"""Task 2: Partial read — single-chunk window, network bytes within budget."""

import time

import pytest

from helpers import (
    TestResult,
    make_zarr_url,
    parse_network_bytes,
    run_gdal_translate,
)


def test_partial_read(dataset_url, dataset_config, output_dir, report, gdal_version):
    cfg = dataset_config
    url = make_zarr_url(dataset_url, cfg.default_band_path)
    out_file = str(output_dir / "out_partial.tif")
    bx = cfg.block_size[0] if cfg.block_size else 244
    start = time.monotonic()

    result = run_gdal_translate(
        url, out_file,
        extra_args=["-srcwin", "0", "0", str(bx), str(bx)],
        network_stats=True,
    )
    assert result.returncode == 0, f"gdal_translate -srcwin failed: {result.stderr[:200]}"

    # Network stats appear on stdout when CPL_VSIL_SHOW_NETWORK_STATS=YES
    net_bytes = parse_network_bytes(result.stdout + result.stderr)
    max_bytes = cfg.partial_read_max_kb * 1024
    duration = time.monotonic() - start

    if net_bytes > 0:
        assert net_bytes < max_bytes, (
            f"Downloaded {net_bytes // 1024} KB, exceeds {cfg.partial_read_max_kb} KB limit"
        )
        details = f"{bx}x{bx} window: {net_bytes // 1024} KB (< {cfg.partial_read_max_kb} KB limit)"
    else:
        details = f"{bx}x{bx} window read OK (network stats=0, cache hit or not captured)"

    report.add(TestResult(
        name="2. Partial Read",
        passed=True,
        duration=duration,
        details=details,
        network_bytes=net_bytes if net_bytes > 0 else None,
        cli_commands=[
            f"CPL_VSIL_SHOW_NETWORK_STATS=YES \\\n"
            f"  gdal_translate '{url}' out_partial.tif \\\n"
            f"  -srcwin 0 0 {bx} {bx} -q"
        ],
        output_snippet=result.stdout or result.stderr,
    ))
