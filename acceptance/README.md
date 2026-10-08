# eopf-accept: acceptance checks for freshly built EOPF GeoZarr stores

Does a new store work for every consumer, before and after it is registered? This package
checks the store itself, titiler-eopf on both deployments (`/rstaging`, 0.12 code; `/raster`,
0.11.1), and the registered STAC item. It sits beside the GDAL suite in this repo and
doesn't touch it.

Slice 1 of the plan. Benchmarks and the OpenLayers browser runner come in later slices.

## Install

```bash
cd acceptance
uv sync                   # core
uv sync --extra reader    # + titiler-eopf 0.12.0's own reader, for TR01 and the integration tests
```

## Use

```bash
# 1. Dry run: what would run, the URL forms per endpoint, and the request bound. Sends nothing.
uv run eopf-accept plan --collection sentinel-3-olci-l1-efr-staging --stage registered \
  --store https://s3.explorer.eopf.copernicus.eu/esa-zarr-sentinel-explorer-fra/tests-output/sentinel-3-olci-l1-efr-staging/<item>.zarr \
  --item <item> --endpoint rstaging --endpoint raster

# 2. Scratch store, before registration (AWS_* env for the -tests bucket):
uv run eopf-accept run --collection sentinel-3-olci-l1-efr-staging --stage scratch \
  --store s3://esa-zarr-sentinel-explorer-tests/<run>/sentinel-3-olci-l1-efr-staging/<item>.zarr --groups store,reader

# 3. Registered item, read-only against production (prefer the s3:// origin: the gateway caches for 1 h):
uv run eopf-accept run …same as plan…

# A local or ad-hoc endpoint: NAME=URL@REPORTED_VERSION:API (API 0.11 or 0.12)
uv run eopf-accept run … --endpoint local=http://127.0.0.1:8000@0.12.0:0.12
```

Reports go to `~/DevDS/EOPF/acceptance_runs/<UTC>_<collection>_<stage>_<label>/` (`report.md`,
`run.json`, `requests.jsonl`), outside this repo. Exit codes: 0 no FAIL, 1 FAIL, 2 usage or
budget refusal, 3 VOID (a cold request was a cache HIT, or a check crashed: no verdict).

### Products from an unmerged data-model branch

Branch products published to a public bucket (e.g. EODC's `continuous-integration` bucket,
one prefix per data-model PR branch) are scratch stores, read without credentials:

```bash
B=https://objects.eodc.eu/continuous-integration/eopf-geozarr/branches/<branch>
uv run eopf-accept plan --collection sentinel-2-l2a --stage scratch --store $B/<product>.zarr --groups store
uv run eopf-accept run  --collection sentinel-2-l2a --stage scratch --store $B/<product>.zarr --groups store --label <branch>-<product>
```

- The https form reads unsigned and ignores every `AWS_*` variable. An `s3://` store follows
  `AWS_ENDPOINT_URL[_S3]` instead, which a shell may still hold from another run: `plan` and the
  report say where reads go (`store reads:`). Unsigned s3:// needs `AWS_SKIP_SIGNATURE=true`.
- At `--stage scratch`, an unsigned reader counts a 403 as a missing key: a bucket without
  list rights answers 403, not 404. The report lists each such key, because a key refused for
  another reason looks the same. A signed reader, and any registered run, still fails on 403.
- `--collection` only picks the config. Products without multiscales (`generic_rechunker`
  output) have no matching config yet; with a config that lists no `multiscales_groups`,
  ST03, ST07, ST08 and ST09 report SKIP, never PASS.

## Safety

- GET only. No code path writes to a store, a STAC API or a cache.
- **The request bound is a feature of the tool.** Each check declares an upper bound. `run`
  refuses to start if the sum exceeds `--max-requests` (default 300), and every request
  (HTTP or store) spends the budget *before* it is sent, so request N+1 never leaves the
  process. `tests/test_budget_and_cli.py` proves both against a local server.
- Every titiler request carries a fresh `cb=<run nonce>-<seq>`, so nothing is served from
  the tile cache. A cache HIT on a cold request makes the run `VOID`.
- Physical requests are what's counted: obstore runs with `max_retries=0` and httpx follows no
  redirects (tested against a server answering 503 and 302).
- Titiler checks run only against endpoints named with `--endpoint`. `--stage scratch` refuses
  production hosts. TI00 refuses an endpoint whose reported version or route fingerprint
  (per-asset routes ⇔ the 0.12 API) differs from the config (fail-closed).
- Side effect at the registered stage: each cache-busted render makes titiler write one tile
  entry to the shared Redis/S3 cache. `plan` says so.

## Checks

| id | checks | catches |
|---|---|---|
| ST01 | consolidated metadata on the root, opened and multiscales groups; levels listed | unconsolidated groups → 0.12 500 (data-pipeline#446); the validator blind spot |
| ST03 | what titiler 0.12 reads without fallback: conventions, bbox, CRS, `spatial:dimensions`, shape+transform for min/max zoom, a transform per level; coarsening order | OLCI tilejson 500 (data-model#303); level tiles 500 |
| ST04 | opened groups visible to titiler-eopf (`_get_groups` rule); no undeclared `spatial:`/`proj:` keys | invisible groups (`scl`) |
| ST11 | `zarr_conventions` declarations equal the v0.1 schema consts (WARN, or FAIL with `strict_declarations`) | stale names/URLs (inspect.geozarr.org) |
| ST07 | chunk/shard layout; tile size `ol/source/GeoZarr` will pick, per consumer ol version | the ≤10.10 64 px fallback |
| ST08 | dtype allow-list; compression ratio of the centre chunk | heavy float64 stores |
| ST09 | the finest level has data where the coarsest level does | empty conversions |
| HT02 | each opened group, rooted where titiler roots it, opens with listing forbidden | PROPFIND 405 over HTTP |
| TR01 | titiler-eopf 0.12's own `GeoZarrReader`: open, zooms, tiles (scratch only) | anything the server would 500 on |
| TI00–TI09 | per endpoint: version + route fingerprint, /info, tilejson without zoom params, decoded tiles scaled by footprint coverage, RGB channels, cold cache, viewer, URL-form contract, fit-bounds vs minzoom | C1, C8, C13, C14 |
| RG04/05/07/08 | the item's links render; do they also work on 0.12 (flip readiness); item newer than its store; asset hrefs point exactly at this store's groups | broken links, the /raster flip, stale registrations |

Per-collection settings live in `configs/*.toml`. The S2 and S1 configs are drafts.

## Tests

```bash
uv run pytest            # fixtures only; tests/test_real_titiler.py also runs the real 0.12 app when the reader extra is installed
```
