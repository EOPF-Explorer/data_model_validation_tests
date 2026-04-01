# EOPF / GeoZarr Validation Tests

Validates GDAL's GeoZarr/EOPF Zarr support against a remote dataset accessed over HTTP.
All GDAL interaction goes through CLI tools (`gdalinfo`, `gdal_translate`, `gdalwarp`, `gdalbuildvrt`) — no Python bindings.
Produces a structured markdown report covering environment info, per-task results with copy-pasteable CLI commands, screenshots, network efficiency, issues, and a conclusion.

Designed to be reusable across dataset types (Sentinel-2, Sentinel-1, Sentinel-3, …) and GDAL versions.

Tracks tasks from [EOPF-Explorer/coordination#235](https://github.com/EOPF-Explorer/coordination/issues/235).

## What it tests

| Task | CLI tool(s) | Description |
|------|-------------|-------------|
| 1 | `gdalinfo` + `CPL_VSIL_SHOW_NETWORK_STATS` | CRS, pixel size, overview count, block size, band metadata (Scale/Offset/NoData/units), consolidated HEAD request count |
| 2 | `gdal_translate -srcwin` + `CPL_VSIL_SHOW_NETWORK_STATS` | Single-chunk read, network bytes within configured limit |
| 3 | `gdal_translate` → GeoTIFF + `gdalinfo` | Export band → GeoTIFF, verify CRS preserved |
| 4 | `gdalwarp -t_srs EPSG:4326` + `gdalinfo` + `gdal_translate -of PNG` | Reproject to EPSG:4326, render PNG thumbnail with vis_scale, visual quality check |
| 5 | `gdalbuildvrt -separate` + `gdal_translate -of PNG` | RGB composite rendered to PNG |
| 6 | `gdal_translate -ovr 0` + `CPL_VSIL_SHOW_NETWORK_STATS` | Read coarsest overview, report size and network bytes |
| 7 | `gdalinfo` × 3 bands | Pixel sizes for r10m / r20m / r60m bands |
| 8 | `gdalinfo -json` + `gdalinfo -mdd all` | GeoZarr conventions: driver=Zarr, CRS present, non-default GeoTransform, spatial/proj extension keys, grid mapping |

All dataset-specific values (band paths, expected CRS, pixel sizes, block size, …) are read from
`configs/sentinel2_l2a.toml`.

## Requirements

- Docker (recommended) — no local install needed beyond Docker
- Or locally:
  - GDAL CLI tools in PATH
  - Python ≥ 3.11 + pytest ≥ 9

## Quick start

```bash
make docker-build

# In Docker — uses the default dataset URL already set in the Makefile
make test

# Override the dataset URL if needed
EOPF_DATASET_URL=https://host/path/S2A_MSIL2A_....zarr make test

# Locally (requires GDAL CLI + Python ≥ 3.11 + pytest)
make test-local
```

Results:
- `output/report.md` — markdown report with copy-pasteable CLI commands for each task
- `output/images/rgb_composite.png` — true-colour composite (Task 5)

## Report structure

| # | Section | Content |
|---|---------|---------|
| 1 | **Environment** | GDAL version and formats, Python version, platform, dataset URL, run date |
| 2 | **Test Results** | Pass/fail table + per-task detail blocks with reference CLI commands and collapsible command output |
| 3 | **Screenshots / Images** | Embedded PNG artifacts (RGB composite, reprojected image) |
| 4 | **Network Efficiency** | Downloaded bytes vs budget for Tasks 2 and 6, using `CPL_VSIL_SHOW_NETWORK_STATS` |
| 5 | **Issues Found** | Auto-populated from failed tasks; "No issues found" when all pass |
| 6 | **Conclusion** | Per-task capability summary; confirms contracted scope is delivered when all tests pass |

The CLI commands in Section 2 use the actual dataset URL and band paths, so any reader can copy-paste and replicate a test independently without running pytest.

## Configuration

Reads `configs/sentinel2_l2a.toml` by default. Override with `EOPF_DATASET_CONFIG`:

```bash
EOPF_DATASET_CONFIG=configs/my_dataset.toml make test-local
```

## Project structure

```
validation_tests/
├── helpers.py              # Subprocess wrappers, config loading, report generation
├── conftest.py             # Root pytest fixtures (config, URL, report)
├── pyproject.toml          # pytest config, dependencies
├── Makefile
├── configs/
│   └── sentinel2_l2a.toml  # Dataset config (band paths, CRS, thresholds, …)
├── docker/
│   └── Dockerfile.gdal     # GDAL + pytest image
├── scripts/
│   └── gdal-docker.sh      # Run GDAL CLI tools from Docker without building the full image
└── gdal/
    ├── conftest.py          # GDAL version fixture
    ├── test_metadata.py     # Task 1: CRS, overviews, block size
    ├── test_partial_read.py # Task 2: Single-chunk read budget
    ├── test_export.py       # Task 3: Export → GeoTIFF
    ├── test_reproject.py    # Task 4: Reproject → EPSG:4326
    ├── test_composite.py    # Task 5: RGB composite → PNG
    ├── test_overviews.py    # Task 6: Overview reading
    ├── test_resolutions.py  # Task 7: Multi-resolution pixel sizes
    └── test_conventions.py  # Task 8: GeoZarr conventions
```

## Makefile targets

```
make docker-build    Build the Docker image
make test            Run pytest in Docker
make test-local      Run pytest locally
make clean           Remove output/
```
