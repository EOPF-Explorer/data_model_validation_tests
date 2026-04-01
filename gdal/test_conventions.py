"""Task 8: GeoZarr conventions — driver, CRS, GeoTransform, spatial/proj extensions."""

import json
import re
import time

import pytest

from helpers import TaskResult, make_zarr_url, run_gdalinfo


def test_conventions(dataset_url, dataset_config, report, gdal_version):
    cfg = dataset_config
    url = make_zarr_url(dataset_url, cfg.default_band_path)
    start = time.monotonic()

    # JSON output for driver / CRS / GeoTransform checks
    json_result = run_gdalinfo(url, json_mode=True)
    assert json_result.returncode == 0, f"gdalinfo -json failed: {json_result.stderr[:200]}"
    data = json.loads(json_result.stdout)

    # -mdd all to expose all metadata domains (ZARR domain reveals spatial/proj extension keys)
    mdd_result = run_gdalinfo(url, mdd_all=True)
    mdd_ok = mdd_result.returncode == 0
    mdd_out = mdd_result.stdout if mdd_ok else ""

    def ck(flag: bool) -> str:
        return "x" if flag else " "

    # ------------------------------------------------------------------
    # Driver must contain "Zarr"
    # ------------------------------------------------------------------
    driver = data.get("driverShortName", "") + data.get("driverLongName", "")
    driver_ok = "zarr" in driver.lower()
    assert driver_ok, f"Expected Zarr driver, got: {driver}"

    # ------------------------------------------------------------------
    # CRS must be present and non-empty
    # ------------------------------------------------------------------
    coord_sys = data.get("coordinateSystem", {})
    wkt = coord_sys.get("wkt", "")
    crs_ok = bool(wkt)
    assert crs_ok, "coordinateSystem.wkt is empty — no CRS projection found"

    # ------------------------------------------------------------------
    # GeoTransform must be non-default (origin != 0)
    # ------------------------------------------------------------------
    gt = data.get("geoTransform", [0, 1, 0, 0, 0, 1])
    gt_ok = gt[0] != 0.0
    assert gt_ok, f"GeoTransform origin X is 0 (default identity): {gt}"

    # ------------------------------------------------------------------
    # spatial / proj extensions: check for crs_wkt, _CRS, spatial_ref,
    # or proj: prefixed keys in the ZARR metadata domain
    # ------------------------------------------------------------------
    proj_ok = bool(re.search(
        r"crs_wkt|_CRS|spatial_ref|proj:epsg|proj:wkt", mdd_out, re.I
    ))

    # ------------------------------------------------------------------
    # Grid mapping / CRS via Zarr conventions (not just CF fallback):
    # grid_mapping attribute or ZARR metadata domain present
    # ------------------------------------------------------------------
    gridmap_ok = bool(re.search(
        r"grid_mapping|Metadata \(ZARR\)|_ARRAY_DIMENSIONS", mdd_out, re.I
    ))

    # ------------------------------------------------------------------
    # Per-sub-bullet checklist (mirrors requirement §8)
    # ------------------------------------------------------------------
    subchecks = [
        f"[{ck(proj_ok)}] spatial/proj extensions recognized: crs_wkt/_CRS/spatial_ref/proj: keys in metadata",
        f"[{ck(gridmap_ok)}] Grid mapping / CRS via Zarr conventions: grid_mapping or ZARR domain present",
        f"[{ck(driver_ok)}] GDAL Zarr driver in use: driverShortName={data.get('driverShortName', '?')}",
        f"[{ck(crs_ok)}] CRS block present and non-empty in output",
    ]

    # Capture ZARR-domain metadata lines as diagnostic snippet
    zarr_meta = "\n".join(
        line for line in mdd_out.splitlines()
        if re.search(r"ZARR|crs_wkt|_CRS|grid_mapping|proj:", line, re.I)
    ) or "(no ZARR-domain metadata found)"

    duration = time.monotonic() - start
    report.add(TaskResult(
        name="8. GeoZarr Conventions",
        passed=True,
        duration=duration,
        details=(
            f"driver=Zarr CRS=present GeoTransform=non-default "
            f"proj_ext={proj_ok} gridmap={gridmap_ok}"
        ),
        subchecks=subchecks,
        cli_commands=[
            f"gdalinfo -json '{url}'",
            f"gdalinfo -mdd all '{url}'",
        ],
        output_snippet=json_result.stdout[:1500] + "\n--- metadata domains ---\n" + zarr_meta,
    ))
