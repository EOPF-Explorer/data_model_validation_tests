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


def load_config(path: str | Path) -> DatasetConfig:
    with open(path, "rb") as f:
        raw = tomllib.load(f)

    expected = raw.get("expected", {})
    rgb = raw.get("rgb_composite")
    return DatasetConfig(
        name=raw["dataset"]["name"],
        description=raw["dataset"].get("description", ""),
        default_band_path=raw["default_band"]["zarr_path"],
        crs_authority_code=expected.get("crs_authority_code"),
        block_size=expected.get("block_size"),
        min_overview_count=expected.get("min_overview_count", 0),
        partial_read_max_kb=expected.get("partial_read_max_kb", 1024),
        rgb_composite=RGBComposite(**rgb) if rgb else None,
        resolution_bands=[
            ResolutionBand(**b) for b in raw.get("resolution_bands", [])
        ],
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


class ReportCollector:
    def __init__(self) -> None:
        self.results: list[TestResult] = []
        self.env_info: dict[str, str] = {}

    def add(self, result: TestResult) -> None:
        self.results.append(result)

    def write(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        lines = ["# EOPF / GeoZarr Validation Report\n"]

        # Environment
        lines.append("\n## Environment\n")
        for key, val in self.env_info.items():
            lines.append(f"- **{key}**: {val}")
        lines.append("")

        # Results table
        lines.append("\n## Results\n")
        lines.append("| Task | Status | Duration | Network | Details |")
        lines.append("|------|--------|----------|---------|---------|")
        passed = 0
        for r in self.results:
            icon = "✅ PASS" if r.passed else "❌ FAIL"
            dur = f"{r.duration:.2f}s"
            net = f"{r.network_bytes // 1024} KB" if r.network_bytes else "—"
            details = r.details.replace("|", "\\|")
            lines.append(f"| {r.name} | {icon} | {dur} | {net} | {details} |")
            if r.passed:
                passed += 1
        lines.append("")

        # Summary
        total = len(self.results)
        lines.append(f"\n## Summary\n")
        lines.append(f"**{passed}/{total} tasks passed**\n")

        # Artifacts
        arts = [
            (r.name, a)
            for r in self.results
            for a in r.artifacts
            if Path(a).exists() and a.endswith((".png", ".jpg"))
        ]
        if arts:
            lines.append("\n## Artifacts\n")
            for name, art in arts:
                lines.append(f"### {name}")
                lines.append(f"![{Path(art).name}]({art})\n")

        path.write_text("\n".join(lines))
