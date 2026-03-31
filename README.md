# EOPF / GeoZarr Validation Tests

Validates GDAL's GeoZarr/EOPF Zarr support against a remote dataset accessed over HTTP.
All GDAL interaction goes through CLI tools (`gdalinfo`, `gdal_translate`, `gdalwarp`, `gdalbuildvrt`) — no Python bindings.
Produces a structured markdown report covering environment info, per-task results with copy-pasteable CLI commands, screenshots, network efficiency, issues, and a conclusion.

Two runners are provided:

- **pytest** (primary) — calls GDAL CLI via subprocess, generates `output/report.md`
- **bash script** (standalone alternative) — self-contained `validate_gdal.sh`, generates `output/report_bash.md`

Designed to be reusable across dataset types (Sentinel-2, Sentinel-1, Sentinel-3, …) and GDAL versions.

Tracks tasks from [EOPF-Explorer/coordination#235](https://github.com/EOPF-Explorer/coordination/issues/235).

## What it tests

| Task | CLI tool(s) | Description |
|------|-------------|-------------|
| 1 | `gdalinfo` | CRS, overview count, block size |
| 2 | `gdal_translate -srcwin` + `CPL_VSIL_SHOW_NETWORK_STATS` | Single-chunk read, network bytes within configured limit |
| 3 | `gdal_translate` → GeoTIFF + `gdalinfo` | Export band → GeoTIFF, verify CRS preserved |
| 4 | `gdalwarp -t_srs EPSG:4326` + `gdalinfo` | Reproject to EPSG:4326 |
| 5 | `gdalbuildvrt -separate` + `gdal_translate -of PNG` | RGB composite rendered to PNG |
| 6 | `gdal_translate -ovr 0` + `CPL_VSIL_SHOW_NETWORK_STATS` | Read coarsest overview, report size and network bytes |
| 7 | `gdalinfo` × 3 bands | Pixel sizes for r10m / r20m / r60m bands |
| 8 | `gdalinfo -json` | GeoZarr conventions: driver=Zarr, CRS present, non-default GeoTransform |

All dataset-specific values (band paths, expected CRS, pixel sizes, block size, …) are read from
`configs/sentinel2_l2a.toml` (pytest) or environment variables (bash script).

## Requirements

- Docker (recommended) — no local install needed beyond Docker
- Or locally:
  - GDAL CLI tools in PATH
  - Python ≥ 3.11 + pytest ≥ 9 (for the pytest runner)

## Quick start

### pytest (primary)

```bash
make docker-build

# In Docker
EOPF_DATASET_URL=https://host/path/S2A_MSIL2A_....zarr make test

# Locally
EOPF_DATASET_URL=https://host/path/S2A_MSIL2A_....zarr make test-local
```

Results:
- `output/report.md` — markdown report
- `output/images/rgb_composite.png` — true-colour composite (Task 5)

### bash script (standalone alternative)

```bash
# In Docker
EOPF_DATASET_URL=https://host/path/S2A_MSIL2A_....zarr make validate

# Locally
export EOPF_DATASET_URL=https://host/path/S2A_MSIL2A_....zarr
make validate-local
```

Results:
- `output/report_bash.md`
- `output/images/rgb_composite_bash.png`

## Report structure

Both runners produce a report with the same six sections:

| # | Section | Content |
|---|---------|---------|
| 1 | **Environment** | GDAL version and formats, Python version, platform, dataset URL, run date |
| 2 | **Test Results** | Pass/fail table + per-task detail blocks with reference CLI commands and collapsible command output |
| 3 | **Screenshots / Images** | Embedded PNG artifacts (RGB composite, reprojected image) |
| 4 | **Network Efficiency** | Downloaded bytes vs budget for Tasks 2 and 6, using `CPL_VSIL_SHOW_NETWORK_STATS` |
| 5 | **Issues Found** | Auto-populated from failed tasks; "No issues found" when all pass |
| 6 | **Conclusion** | Per-task capability summary; confirms contracted scope is delivered when all tests pass |

The CLI commands in Section 2 use the actual dataset URL and band paths, so any reader can copy-paste and replicate a test independently.

## Configuration

### pytest

Reads `configs/sentinel2_l2a.toml` by default. Override with `EOPF_DATASET_CONFIG`:

```bash
EOPF_DATASET_CONFIG=configs/my_dataset.toml make test-local
```

### bash script

All values are set via environment variables with sensible defaults for the reference Sentinel-2 dataset.

| Variable | Default | Description |
|----------|---------|-------------|
| `EOPF_DATASET_URL` | S2B reference URL | HTTP(S) URL to an EOPF Zarr store |
| `BAND_DEFAULT` | `/measurements/reflectance/r10m/b02` | Band used for Tasks 1–4, 6, 8 |
| `EXPECTED_CRS` | `32630` | Expected EPSG authority code |
| `EXPECTED_BLOCK_SIZE` | `244` | Expected block/chunk size |
| `PARTIAL_READ_MAX_KB` | `1024` | Network budget for Task 2 |
| `MIN_OVERVIEW_COUNT` | `3` | Minimum overview levels required |
| `VIS_SCALE_MIN` | `0.0` | Lower bound for `-scale` when generating PNG thumbnails (Float32 reflectance) |
| `VIS_SCALE_MAX` | `0.3` | Upper bound for `-scale` (0.3 = 30 % reflectance, typical S2 L2A land range) |

## Project structure

```
validation_tests/
├── helpers.py              # Subprocess wrappers, config loading, report generation
├── conftest.py             # Root pytest fixtures (config, URL, report)
├── pyproject.toml          # pytest config, dependencies
├── validate_gdal.sh        # Standalone bash validation suite (8 tasks)
├── Makefile
├── configs/
│   └── sentinel2_l2a.toml  # Dataset config (shared reference)
├── docker/
│   └── Dockerfile.gdal     # GDAL + pytest image
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
make test            Run pytest in Docker (primary)
make test-local      Run pytest locally
make validate        Run bash script in Docker
make validate-local  Run bash script locally
make clean           Remove output/
```
