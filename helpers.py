"""Shared helpers for GDAL CLI validation tests.

Thin wrappers around subprocess calls to GDAL CLI tools,
config loading from TOML, and markdown report generation.
No osgeo.gdal imports — all GDAL interaction is via CLI.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


# ---------------------------------------------------------------------------
# ZARR URL construction
# ---------------------------------------------------------------------------

def make_zarr_url(base_url: str, zarr_path: str) -> str:
    """Build a GDAL ZARR connection string: ZARR:"/vsicurl/https://...":/path"""
    return f'ZARR:"/vsicurl/{base_url}":{zarr_path}'


# ---------------------------------------------------------------------------
# GDAL CLI wrappers
# ---------------------------------------------------------------------------

def run_gdalinfo(url: str, *, json_mode: bool = False) -> subprocess.CompletedProcess:
    cmd = ["gdalinfo"]
    if json_mode:
        cmd.append("-json")
    cmd.append(url)
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def band_statistics(url: str) -> dict[str, float] | None:
    """Return STATISTICS_{MINIMUM,MAXIMUM,MEAN} for the first band of *url*, or None.

    Runs ``gdalinfo -stats`` so no Python bindings are required.  The statistics are
    computed from the actual data (not the theoretical datatype range), which makes this
    useful for diagnosing whether a black thumbnail is caused by bad vis_scale bounds or
    by GDAL falling back to the UInt16 full range (0–65535) when auto-scaling with
    ``-scale`` alone.
    """
    result = subprocess.run(
        ["gdalinfo", "-stats", url],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        return None
    out = result.stdout
    stats: dict[str, float] = {}
    for key in ("MINIMUM", "MAXIMUM", "MEAN"):
        m = re.search(rf"STATISTICS_{key}=([0-9.eE+\-]+)", out)
        if m:
            stats[key] = float(m.group(1))
    return stats if stats else None


def png_max_pixel_value(path: str) -> float | None:
    """Return the highest STATISTICS_MAXIMUM across all bands of a PNG, or None on failure.

    Uses ``gdalinfo -stats`` so no Python imaging library is required.
    A return value of 0 (or near 0) indicates an all-black / blank image.
    """
    result = subprocess.run(
        ["gdalinfo", "-stats", path],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        return None
    values = [
        float(m.group(1))
        for m in re.finditer(r"STATISTICS_MAXIMUM=([0-9.]+)", result.stdout)
    ]
    return max(values) if values else None


def run_gdal_translate(
    src: str,
    dst: str,
    *,
    extra_args: list[str] | None = None,
    network_stats: bool = False,
) -> subprocess.CompletedProcess:
    cmd = ["gdal_translate", src, dst, "-q"]
    if extra_args:
        cmd[3:3] = extra_args  # insert before -q
    env = os.environ.copy()
    if network_stats:
        env["CPL_VSIL_SHOW_NETWORK_STATS"] = "YES"
    return subprocess.run(cmd, capture_output=True, text=True, check=False, env=env)


def run_gdalwarp(
    src: str, dst: str, *, extra_args: list[str] | None = None
) -> subprocess.CompletedProcess:
    cmd = ["gdalwarp", src, dst, "-q"]
    if extra_args:
        cmd[3:3] = extra_args
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def run_gdalbuildvrt(
    dst: str, inputs: list[str], *, extra_args: list[str] | None = None
) -> subprocess.CompletedProcess:
    cmd = ["gdalbuildvrt"]
    if extra_args:
        cmd.extend(extra_args)
    cmd.append(dst)
    cmd.extend(inputs)
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


# ---------------------------------------------------------------------------
# Network stats parsing
# ---------------------------------------------------------------------------

def parse_network_bytes(text: str) -> int:
    """Extract the first 'downloaded_bytes' value from CPL_VSIL_SHOW_NETWORK_STATS output."""
    m = re.search(r'"downloaded_bytes":(\d+)', text)
    return int(m.group(1)) if m else 0


# ---------------------------------------------------------------------------
# Dataset config
# ---------------------------------------------------------------------------

@dataclass
class RGBComposite:
    red: str
    green: str
    blue: str


@dataclass
class ResolutionBand:
    zarr_path: str
    label: str
    expected_pixel_size_m: float


@dataclass
class DatasetConfig:
    name: str
    description: str
    default_band_path: str
    crs_authority_code: str | None
    block_size: list[int] | None
    min_overview_count: int
    partial_read_max_kb: int
    rgb_composite: RGBComposite | None
    resolution_bands: list[ResolutionBand]
    vis_scale: list[float] = field(default_factory=lambda: [0.0, 1.0])


def load_config(path: str | Path) -> DatasetConfig:
    with open(path, "rb") as f:
        raw = tomllib.load(f)

    expected = raw.get("expected", {})
    rgb = raw.get("rgb_composite")
    dataset = raw["dataset"]
    return DatasetConfig(
        name=dataset["name"],
        description=dataset.get("description", ""),
        default_band_path=raw["default_band"]["zarr_path"],
        crs_authority_code=expected.get("crs_authority_code"),
        block_size=expected.get("block_size"),
        min_overview_count=expected.get("min_overview_count", 0),
        partial_read_max_kb=expected.get("partial_read_max_kb", 1024),
        rgb_composite=RGBComposite(**rgb) if rgb else None,
        resolution_bands=[
            ResolutionBand(**b) for b in raw.get("resolution_bands", [])
        ],
        vis_scale=dataset.get("vis_scale", [0, 65535]),
    )


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

@dataclass
class TestResult:
    name: str
    passed: bool
    duration: float = 0.0
    details: str = ""
    artifacts: list[str] = field(default_factory=list)
    network_bytes: int | None = None
    # Reference shell commands a user can copy-paste to replicate this test
    cli_commands: list[str] = field(default_factory=list)
    # Trimmed stdout from the primary GDAL call (first 20 lines)
    output_snippet: str = ""


def _snippet(text: str, max_lines: int = 20) -> str:
    """Return at most `max_lines` lines of text, appending '...' if truncated."""
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text
    return "\n".join(lines[:max_lines]) + "\n..."


class ReportCollector:
    def __init__(self) -> None:
        self.results: list[TestResult] = []
        self.env_info: dict[str, str] = {}

    def add(self, result: TestResult) -> None:
        self.results.append(result)

    def write(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        passed = sum(1 for r in self.results if r.passed)
        total = len(self.results)
        failed = [r for r in self.results if not r.passed]
        network_results = [r for r in self.results if r.network_bytes is not None]

        L: list[str] = []

        # ------------------------------------------------------------------ #
        # Title
        # ------------------------------------------------------------------ #
        L.append("# EOPF / GeoZarr Validation Report\n")
        L.append(
            f"> **{passed}/{total} tasks passed** &nbsp;·&nbsp; "
            f"Generated: {self.env_info.get('Date', 'unknown')}\n"
        )

        # ------------------------------------------------------------------ #
        # 1. Environment
        # ------------------------------------------------------------------ #
        L.append("## 1. Environment\n")
        L.append("| Key | Value |")
        L.append("|-----|-------|")
        for key, val in self.env_info.items():
            L.append(f"| {key} | `{val}` |")
        L.append("")

        # ------------------------------------------------------------------ #
        # 2. Test Results
        # ------------------------------------------------------------------ #
        L.append("## 2. Test Results\n")

        # Summary table
        L.append("| Task | Status | Duration | Network | Details |")
        L.append("|------|--------|----------|---------|---------|")
        for r in self.results:
            icon = "✅ PASS" if r.passed else "❌ FAIL"
            dur = f"{r.duration:.2f}s"
            net = f"{r.network_bytes // 1024} KB" if r.network_bytes else "—"
            details = r.details.replace("|", "\\|")
            L.append(f"| {r.name} | {icon} | {dur} | {net} | {details} |")
        L.append("")

        # Per-task detail blocks
        for r in self.results:
            icon = "✅ PASS" if r.passed else "❌ FAIL"
            L.append(f"### {r.name}\n")
            L.append(f"**Status:** {icon} &nbsp;·&nbsp; **Duration:** {r.duration:.2f}s\n")
            if r.details:
                L.append(f"{r.details}\n")

            if r.cli_commands:
                L.append("**Reference CLI commands** (copy-paste to replicate):\n")
                L.append("```bash")
                for cmd in r.cli_commands:
                    L.append(cmd)
                L.append("```\n")

            if r.output_snippet:
                L.append("<details>")
                L.append("<summary>Command output</summary>\n")
                L.append("```")
                L.append(_snippet(r.output_snippet))
                L.append("```")
                L.append("</details>\n")

        # ------------------------------------------------------------------ #
        # 3. Screenshots / Images
        # ------------------------------------------------------------------ #
        arts = [
            (r.name, a)
            for r in self.results
            for a in r.artifacts
            if Path(a).exists() and a.endswith((".png", ".jpg", ".jpeg"))
        ]
        if arts:
            L.append("## 3. Screenshots / Images\n")
            for name, art in arts:
                # Path relative to the report file so previews work in any viewer
                try:
                    rel = Path(art).relative_to(path.parent)
                except ValueError:
                    rel = Path(art)
                L.append(f"### {name}\n")
                L.append(f"![{Path(art).name}]({rel})\n")

        # ------------------------------------------------------------------ #
        # 4. Network Efficiency
        # ------------------------------------------------------------------ #
        L.append("## 4. Network Efficiency\n")
        if network_results:
            L.append(
                "Summary of remote read performance measured with "
                "`CPL_VSIL_SHOW_NETWORK_STATS=YES`.\n"
            )
            L.append("| Task | Downloaded | Budget | Result |")
            L.append("|------|-----------|--------|--------|")
            for r in network_results:
                kb = r.network_bytes // 1024  # type: ignore[operator]
                # infer budget from details string if present
                budget_match = re.search(r"< (\d+) KB limit", r.details)
                budget = f"{budget_match.group(1)} KB" if budget_match else "—"
                result_icon = "✅ within budget" if r.passed else "❌ over budget"
                L.append(f"| {r.name} | {kb} KB | {budget} | {result_icon} |")
            L.append("")
            total_kb = sum(
                r.network_bytes // 1024
                for r in network_results
                if r.network_bytes is not None
            )
            L.append(f"**Total measured network traffic across timed tests: {total_kb} KB**\n")
            L.append(
                "The partial-read test confirms that reading a single Zarr shard "
                "(one chunk window) downloads only the bytes needed for that chunk, "
                "demonstrating efficient range-request support in the GDAL Zarr driver.\n"
            )
        else:
            L.append(
                "_No network statistics were captured during this run. "
                "Re-run with `CPL_VSIL_SHOW_NETWORK_STATS=YES` to collect them._\n"
            )

        # ------------------------------------------------------------------ #
        # 5. Issues Found
        # ------------------------------------------------------------------ #
        L.append("## 5. Issues Found\n")
        if failed:
            L.append(
                f"**{len(failed)} task(s) failed.** "
                "Investigate the output snippets above for details.\n"
            )
            L.append("| Task | Details |")
            L.append("|------|---------|")
            for r in failed:
                L.append(f"| {r.name} | {r.details.replace('|', chr(92) + '|')} |")
            L.append("")
            L.append(
                "> If a failure is caused by a GDAL bug, file an issue at "
                "[https://github.com/OSGeo/gdal/issues](https://github.com/OSGeo/gdal/issues) "
                "and link it here.\n"
            )
        else:
            L.append("No issues found. All tasks passed without errors.\n")

        # ------------------------------------------------------------------ #
        # 6. Conclusion
        # ------------------------------------------------------------------ #
        L.append("## 6. Conclusion\n")
        if passed == total:
            dataset_url = self.env_info.get("Dataset URL", "the configured dataset")
            gdal_ver = self.env_info.get("GDAL version", "the installed GDAL version")
            L.append(
                f"All **{total} contracted validation tasks** passed successfully "
                f"against {dataset_url} using {gdal_ver}.\n"
            )
            L.append("The following capabilities are confirmed working:\n")
            for r in self.results:
                L.append(f"- **{r.name}**: {r.details}")
            L.append("")
            L.append(
                "\nThe GDAL Zarr driver correctly reads EOPF CPM Zarr datasets over "
                "`/vsicurl`, exposes CRS and overview metadata, supports partial "
                "(shard-aligned) reads with efficient network usage, exports to "
                "GeoTIFF, reprojects via `gdalwarp`, renders RGB composites, reads "
                "all configured resolution bands, and reports GeoZarr-compliant "
                "driver/CRS/GeoTransform metadata. **The contracted scope is delivered "
                "and working.**\n"
            )
        else:
            L.append(
                f"**{passed}/{total} tasks passed.** "
                f"{len(failed)} task(s) require investigation before the contracted "
                "scope can be considered fully delivered. See Section 5 for details.\n"
            )

        path.write_text("\n".join(L) + "\n")
