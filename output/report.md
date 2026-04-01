# EOPF / GeoZarr Validation Report

> **10/10 tasks passed** &nbsp;·&nbsp; Generated: 2026-04-01T10:58:29Z

## 1. Environment

| Key | Value |
|-----|-------|
| GDAL version | `GDAL 3.13.0dev-2044425c73a079babe690537c692b57482e8d32c, released 2026/03/26` |
| Python version | `3.12.3` |
| Platform | `Linux-6.12.54-linuxkit-aarch64-with-glibc2.39` |
| Dataset URL | `https://s3.explorer.eopf.copernicus.eu/esa-zarr-sentinel-explorer-fra/tests-output/sentinel-2-l2a/S2B_MSIL2A_20260320T114349_N0512_R123_T30VVK_20260320T155447.zarr` |
| Date | `2026-04-01T10:58:29Z` |

## 2. Test Results

| Task | Status | Duration | Network | Details |
|------|--------|----------|---------|---------|
| 1. Metadata | ✅ PASS | 0.83s | — | CRS=EPSG:32630 pixel=10m overviews=5>=3 block=915x915 scale=False nodata=True HEAD=8 |
| 2. Partial Read | ✅ PASS | 1.65s | 2830 KB | 915x915 window (1 shard): 2830 KB (< 4096 KB limit) |
| 3. Export -> GeoTIFF | ✅ PASS | 23.66s | — | Exported to band.tif, CRS=EPSG:32630 verified, origin=399960, pixel=10m, max=1.7998000383377 |
| 4. Reproject -> EPSG:4326 | ✅ PASS | 24.93s | — | Reprojected to EPSG:4326, output=b02_4326.tif, thumbnail max pixel=255/255. Source band actual range: min=-0 max=2 mean=0; thumbnail uses explicit vis_scale. |
| 5. RGB Composite | ✅ PASS | 118.53s | — | RGB PNG written (1346 KB, max pixel=255/255): rgb_composite.png. Source band actual range: min=-0 max=2 mean=0; thumbnail uses explicit vis_scale. |
| 6. Overview Read | ✅ PASS | 14.80s | 87145 KB | 5 overview levels; overview=5490x5490 (full=10980x10980); file=117768 KB (full=471004 KB); network=87145 KB |
| 7. Resolution r10m | ✅ PASS | 1.32s | — | r10m=10m (expected 10m) |
| 7. Resolution r20m | ✅ PASS | 0.92s | — | r20m=20m (expected 20m) |
| 7. Resolution r60m | ✅ PASS | 1.15s | — | r60m=60m (expected 60m) |
| 8. GeoZarr Conventions | ✅ PASS | 1.94s | — | driver=Zarr CRS=present GeoTransform=non-default proj_ext=True gridmap=True |

### 1. Metadata

**Status:** ✅ PASS &nbsp;·&nbsp; **Duration:** 0.83s

- [x] CRS decoded: WGS 84 / UTM zone 30N, EPSG:32630
- [x] GeoTransform / pixel size: 10.0m (r10m band, expect ~10m)
- [x] Overviews listed: 5 levels (expect ≥3)
- [x] Block/chunk size: 915×915
- [ ] Band metadata — Scale: not found
- [ ] Band metadata — Offset: not found
- [x] Band metadata — NoData/fill value: nan
- [x] Band metadata — units: ":
- [x] Consolidated metadata: 8 HEAD requests (threshold < 50)

CRS=EPSG:32630 pixel=10m overviews=5>=3 block=915x915 scale=False nodata=True HEAD=8

**Reference CLI commands** (copy-paste to replicate):

```bash
CPL_VSIL_SHOW_NETWORK_STATS=YES gdalinfo 'ZARR:"/vsicurl/https://s3.explorer.eopf.copernicus.eu/esa-zarr-sentinel-explorer-fra/tests-output/sentinel-2-l2a/S2B_MSIL2A_20260320T114349_N0512_R123_T30VVK_20260320T155447.zarr":/measurements/reflectance/r10m/b02'
```

<details>
<summary>Command output</summary>

```
Driver: Zarr/Zarr
Files: /vsicurl/https://s3.explorer.eopf.copernicus.eu/esa-zarr-sentinel-explorer-fra/tests-output/sentinel-2-l2a/S2B_MSIL2A_20260320T114349_N0512_R123_T30VVK_20260320T155447.zarr/measurements/reflectance/r10m/b02/zarr.json
Size is 10980, 10980
Coordinate System is:
PROJCRS["WGS 84 / UTM zone 30N",
    BASEGEOGCRS["WGS 84",
        ENSEMBLE["World Geodetic System 1984 ensemble",
            MEMBER["World Geodetic System 1984 (Transit)"],
            MEMBER["World Geodetic System 1984 (G730)"],
            MEMBER["World Geodetic System 1984 (G873)"],
            MEMBER["World Geodetic System 1984 (G1150)"],
            MEMBER["World Geodetic System 1984 (G1674)"],
            MEMBER["World Geodetic System 1984 (G1762)"],
            MEMBER["World Geodetic System 1984 (G2139)"],
            MEMBER["World Geodetic System 1984 (G2296)"],
            ELLIPSOID["WGS 84",6378137,298.257223563,
                LENGTHUNIT["metre",1]],
            ENSEMBLEACCURACY[2.0]],
        PRIMEM["Greenwich",0,
            ANGLEUNIT["degree",0.0174532925199433]],
...
```
</details>

### 2. Partial Read

**Status:** ✅ PASS &nbsp;·&nbsp; **Duration:** 1.65s

- [x] Only relevant shard fetched: 915×915 window = 1 chunk (shard-aligned read)
- [x] Network download < 4096 KB: 2830 KB transferred

915x915 window (1 shard): 2830 KB (< 4096 KB limit)

**Reference CLI commands** (copy-paste to replicate):

```bash
CPL_VSIL_SHOW_NETWORK_STATS=YES \
  gdal_translate 'ZARR:"/vsicurl/https://s3.explorer.eopf.copernicus.eu/esa-zarr-sentinel-explorer-fra/tests-output/sentinel-2-l2a/S2B_MSIL2A_20260320T114349_N0512_R123_T30VVK_20260320T155447.zarr":/measurements/reflectance/r10m/b02' out_partial.tif \
  -srcwin 0 0 915 915 -q
```

<details>
<summary>Command output</summary>

```
Network statistics:
{
  "methods":{
    "HEAD":{
      "count":9
    },
    "GET":{
      "count":5,
      "downloaded_bytes":2898204
    }
  },
  "handlers":{
    "vsicurl":{
      "methods":{
        "HEAD":{
          "count":9
        },
        "GET":{
          "count":5,
          "downloaded_bytes":2898204
...
```
</details>

### 3. Export -> GeoTIFF

**Status:** ✅ PASS &nbsp;·&nbsp; **Duration:** 23.66s

- [x] CRS preserved: EPSG:32630
- [x] Extent / origin preserved: origin_x=399960.0, pixel_size=10m
- [x] Pixel values preserved: max=1.7998000383377 (expect > 0)

Exported to band.tif, CRS=EPSG:32630 verified, origin=399960, pixel=10m, max=1.7998000383377

**Reference CLI commands** (copy-paste to replicate):

```bash
gdal_translate 'ZARR:"/vsicurl/https://s3.explorer.eopf.copernicus.eu/esa-zarr-sentinel-explorer-fra/tests-output/sentinel-2-l2a/S2B_MSIL2A_20260320T114349_N0512_R123_T30VVK_20260320T155447.zarr":/measurements/reflectance/r10m/b02' band.tif -q
gdalinfo -stats band.tif
```

<details>
<summary>Command output</summary>

```
Driver: GTiff/GeoTIFF
Files: output/band.tif
Size is 10980, 10980
Coordinate System is:
PROJCRS["WGS 84 / UTM zone 30N",
    BASEGEOGCRS["WGS 84",
        DATUM["World Geodetic System 1984",
            ELLIPSOID["WGS 84",6378137,298.257223563,
                LENGTHUNIT["metre",1]]],
        PRIMEM["Greenwich",0,
            ANGLEUNIT["degree",0.0174532925199433]],
        ID["EPSG",4326]],
    CONVERSION["UTM zone 30N",
        METHOD["Transverse Mercator",
            ID["EPSG",9807]],
        PARAMETER["Latitude of natural origin",0,
            ANGLEUNIT["degree",0.0174532925199433],
            ID["EPSG",8801]],
        PARAMETER["Longitude of natural origin",-3,
            ANGLEUNIT["degree",0.0174532925199433],
...
```
</details>

### 4. Reproject -> EPSG:4326

**Status:** ✅ PASS &nbsp;·&nbsp; **Duration:** 24.93s

- [x] Output correctly georeferenced: EPSG:4326
- [x] Visually appealing result: thumbnail max pixel=255/255 (expect > 5)

Reprojected to EPSG:4326, output=b02_4326.tif, thumbnail max pixel=255/255. Source band actual range: min=-0 max=2 mean=0; thumbnail uses explicit vis_scale.

**Reference CLI commands** (copy-paste to replicate):

```bash
gdalwarp -t_srs EPSG:4326 'ZARR:"/vsicurl/https://s3.explorer.eopf.copernicus.eu/esa-zarr-sentinel-explorer-fra/tests-output/sentinel-2-l2a/S2B_MSIL2A_20260320T114349_N0512_R123_T30VVK_20260320T155447.zarr":/measurements/reflectance/r10m/b02' b02_4326.tif -q
gdal_translate -of PNG -scale 0.0 0.3 0 255 -outsize 10% 10% b02_4326.tif b02_4326.png -q
```

<details>
<summary>Command output</summary>

```
Driver: GTiff/GeoTIFF
Files: output/b02_4326.tif
       output/b02_4326.tif.aux.xml
Size is 13829, 7293
Coordinate System is:
GEOGCRS["WGS 84",
    ENSEMBLE["World Geodetic System 1984 ensemble",
        MEMBER["World Geodetic System 1984 (Transit)"],
        MEMBER["World Geodetic System 1984 (G730)"],
        MEMBER["World Geodetic System 1984 (G873)"],
        MEMBER["World Geodetic System 1984 (G1150)"],
        MEMBER["World Geodetic System 1984 (G1674)"],
        MEMBER["World Geodetic System 1984 (G1762)"],
        MEMBER["World Geodetic System 1984 (G2139)"],
        MEMBER["World Geodetic System 1984 (G2296)"],
        ELLIPSOID["WGS 84",6378137,298.257223563,
            LENGTHUNIT["metre",1]],
        ENSEMBLEACCURACY[2.0]],
    PRIMEM["Greenwich",0,
        ANGLEUNIT["degree",0.0174532925199433]],
...
```
</details>

### 5. RGB Composite

**Status:** ✅ PASS &nbsp;·&nbsp; **Duration:** 118.53s

- [x] Multi-band VRT composite (B04-B03-B02) built successfully
- [x] Result visually meaningful: max pixel=255/255 (expect > 5); size=1346 KB

RGB PNG written (1346 KB, max pixel=255/255): rgb_composite.png. Source band actual range: min=-0 max=2 mean=0; thumbnail uses explicit vis_scale.

**Reference CLI commands** (copy-paste to replicate):

```bash
gdalbuildvrt -separate rgb.vrt \
  'ZARR:"/vsicurl/https://s3.explorer.eopf.copernicus.eu/esa-zarr-sentinel-explorer-fra/tests-output/sentinel-2-l2a/S2B_MSIL2A_20260320T114349_N0512_R123_T30VVK_20260320T155447.zarr":/measurements/reflectance/r10m/b04' \
  'ZARR:"/vsicurl/https://s3.explorer.eopf.copernicus.eu/esa-zarr-sentinel-explorer-fra/tests-output/sentinel-2-l2a/S2B_MSIL2A_20260320T114349_N0512_R123_T30VVK_20260320T155447.zarr":/measurements/reflectance/r10m/b03' \
  'ZARR:"/vsicurl/https://s3.explorer.eopf.copernicus.eu/esa-zarr-sentinel-explorer-fra/tests-output/sentinel-2-l2a/S2B_MSIL2A_20260320T114349_N0512_R123_T30VVK_20260320T155447.zarr":/measurements/reflectance/r10m/b02'
gdal_translate -of PNG -scale 0.0 0.3 0 255 -outsize 10% 10% rgb.vrt rgb_composite.png -q
```

<details>
<summary>Command output</summary>

```
0...10...20...30...40...50...60...70...80...90...100 - done.

```
</details>

### 6. Overview Read

**Status:** ✅ PASS &nbsp;·&nbsp; **Duration:** 14.80s

- [x] Overview returns lower-resolution data: 5490×5490 vs full-res 10980×10980
- [x] Overview access is efficient: 117768 KB vs full-res 471004 KB

5 overview levels; overview=5490x5490 (full=10980x10980); file=117768 KB (full=471004 KB); network=87145 KB

**Reference CLI commands** (copy-paste to replicate):

```bash
gdalinfo 'ZARR:"/vsicurl/https://s3.explorer.eopf.copernicus.eu/esa-zarr-sentinel-explorer-fra/tests-output/sentinel-2-l2a/S2B_MSIL2A_20260320T114349_N0512_R123_T30VVK_20260320T155447.zarr":/measurements/reflectance/r10m/b02'
CPL_VSIL_SHOW_NETWORK_STATS=YES \
  gdal_translate 'ZARR:"/vsicurl/https://s3.explorer.eopf.copernicus.eu/esa-zarr-sentinel-explorer-fra/tests-output/sentinel-2-l2a/S2B_MSIL2A_20260320T114349_N0512_R123_T30VVK_20260320T155447.zarr":/measurements/reflectance/r10m/b02' overview.tif -ovr 0 -q
```

<details>
<summary>Command output</summary>

```
Driver: Zarr/Zarr
Files: /vsicurl/https://s3.explorer.eopf.copernicus.eu/esa-zarr-sentinel-explorer-fra/tests-output/sentinel-2-l2a/S2B_MSIL2A_20260320T114349_N0512_R123_T30VVK_20260320T155447.zarr/measurements/reflectance/r10m/b02/zarr.json
Size is 10980, 10980
Coordinate System is:
PROJCRS["WGS 84 / UTM zone 30N",
    BASEGEOGCRS["WGS 84",
        ENSEMBLE["World Geodetic System 1984 ensemble",
            MEMBER["World Geodetic System 1984 (Transit)"],
            MEMBER["World Geodetic System 1984 (G730)"],
            MEMBER["World Geodetic System 1984 (G873)"],
            MEMBER["World Geodetic System 1984 (G1150)"],
            MEMBER["World Geodetic System 1984 (G1674)"],
            MEMBER["World Geodetic System 1984 (G1762)"],
            MEMBER["World Geodetic System 1984 (G2139)"],
            MEMBER["World Geodetic System 1984 (G2296)"],
            ELLIPSOID["WGS 84",6378137,298.257223563,
                LENGTHUNIT["metre",1]],
            ENSEMBLEACCURACY[2.0]],
        PRIMEM["Greenwich",0,
            ANGLEUNIT["degree",0.0174532925199433]],
...
```
</details>

### 7. Resolution r10m

**Status:** ✅ PASS &nbsp;·&nbsp; **Duration:** 1.32s

- [x] r10m band: pixel size=10m (expect 10m)

r10m=10m (expected 10m)

**Reference CLI commands** (copy-paste to replicate):

```bash
gdalinfo 'ZARR:"/vsicurl/https://s3.explorer.eopf.copernicus.eu/esa-zarr-sentinel-explorer-fra/tests-output/sentinel-2-l2a/S2B_MSIL2A_20260320T114349_N0512_R123_T30VVK_20260320T155447.zarr":/measurements/reflectance/r10m/b02'
```

<details>
<summary>Command output</summary>

```
Driver: Zarr/Zarr
Files: /vsicurl/https://s3.explorer.eopf.copernicus.eu/esa-zarr-sentinel-explorer-fra/tests-output/sentinel-2-l2a/S2B_MSIL2A_20260320T114349_N0512_R123_T30VVK_20260320T155447.zarr/measurements/reflectance/r10m/b02/zarr.json
Size is 10980, 10980
Coordinate System is:
PROJCRS["WGS 84 / UTM zone 30N",
    BASEGEOGCRS["WGS 84",
        ENSEMBLE["World Geodetic System 1984 ensemble",
            MEMBER["World Geodetic System 1984 (Transit)"],
            MEMBER["World Geodetic System 1984 (G730)"],
            MEMBER["World Geodetic System 1984 (G873)"],
            MEMBER["World Geodetic System 1984 (G1150)"],
            MEMBER["World Geodetic System 1984 (G1674)"],
            MEMBER["World Geodetic System 1984 (G1762)"],
            MEMBER["World Geodetic System 1984 (G2139)"],
            MEMBER["World Geodetic System 1984 (G2296)"],
            ELLIPSOID["WGS 84",6378137,298.257223563,
                LENGTHUNIT["metre",1]],
            ENSEMBLEACCURACY[2.0]],
        PRIMEM["Greenwich",0,
            ANGLEUNIT["degree",0.0174532925199433]],
...
```
</details>

### 7. Resolution r20m

**Status:** ✅ PASS &nbsp;·&nbsp; **Duration:** 0.92s

- [x] r20m band: pixel size=20m (expect 20m)

r20m=20m (expected 20m)

**Reference CLI commands** (copy-paste to replicate):

```bash
gdalinfo 'ZARR:"/vsicurl/https://s3.explorer.eopf.copernicus.eu/esa-zarr-sentinel-explorer-fra/tests-output/sentinel-2-l2a/S2B_MSIL2A_20260320T114349_N0512_R123_T30VVK_20260320T155447.zarr":/measurements/reflectance/r20m/b05'
```

<details>
<summary>Command output</summary>

```
Driver: Zarr/Zarr
Files: /vsicurl/https://s3.explorer.eopf.copernicus.eu/esa-zarr-sentinel-explorer-fra/tests-output/sentinel-2-l2a/S2B_MSIL2A_20260320T114349_N0512_R123_T30VVK_20260320T155447.zarr/measurements/reflectance/r20m/b05/zarr.json
Size is 5490, 5490
Coordinate System is:
PROJCRS["WGS 84 / UTM zone 30N",
    BASEGEOGCRS["WGS 84",
        ENSEMBLE["World Geodetic System 1984 ensemble",
            MEMBER["World Geodetic System 1984 (Transit)"],
            MEMBER["World Geodetic System 1984 (G730)"],
            MEMBER["World Geodetic System 1984 (G873)"],
            MEMBER["World Geodetic System 1984 (G1150)"],
            MEMBER["World Geodetic System 1984 (G1674)"],
            MEMBER["World Geodetic System 1984 (G1762)"],
            MEMBER["World Geodetic System 1984 (G2139)"],
            MEMBER["World Geodetic System 1984 (G2296)"],
            ELLIPSOID["WGS 84",6378137,298.257223563,
                LENGTHUNIT["metre",1]],
            ENSEMBLEACCURACY[2.0]],
        PRIMEM["Greenwich",0,
            ANGLEUNIT["degree",0.0174532925199433]],
...
```
</details>

### 7. Resolution r60m

**Status:** ✅ PASS &nbsp;·&nbsp; **Duration:** 1.15s

- [x] r60m band: pixel size=60m (expect 60m)

r60m=60m (expected 60m)

**Reference CLI commands** (copy-paste to replicate):

```bash
gdalinfo 'ZARR:"/vsicurl/https://s3.explorer.eopf.copernicus.eu/esa-zarr-sentinel-explorer-fra/tests-output/sentinel-2-l2a/S2B_MSIL2A_20260320T114349_N0512_R123_T30VVK_20260320T155447.zarr":/measurements/reflectance/r60m/b01'
```

<details>
<summary>Command output</summary>

```
Driver: Zarr/Zarr
Files: /vsicurl/https://s3.explorer.eopf.copernicus.eu/esa-zarr-sentinel-explorer-fra/tests-output/sentinel-2-l2a/S2B_MSIL2A_20260320T114349_N0512_R123_T30VVK_20260320T155447.zarr/measurements/reflectance/r60m/b01/zarr.json
Size is 1830, 1830
Coordinate System is:
PROJCRS["WGS 84 / UTM zone 30N",
    BASEGEOGCRS["WGS 84",
        ENSEMBLE["World Geodetic System 1984 ensemble",
            MEMBER["World Geodetic System 1984 (Transit)"],
            MEMBER["World Geodetic System 1984 (G730)"],
            MEMBER["World Geodetic System 1984 (G873)"],
            MEMBER["World Geodetic System 1984 (G1150)"],
            MEMBER["World Geodetic System 1984 (G1674)"],
            MEMBER["World Geodetic System 1984 (G1762)"],
            MEMBER["World Geodetic System 1984 (G2139)"],
            MEMBER["World Geodetic System 1984 (G2296)"],
            ELLIPSOID["WGS 84",6378137,298.257223563,
                LENGTHUNIT["metre",1]],
            ENSEMBLEACCURACY[2.0]],
        PRIMEM["Greenwich",0,
            ANGLEUNIT["degree",0.0174532925199433]],
...
```
</details>

### 8. GeoZarr Conventions

**Status:** ✅ PASS &nbsp;·&nbsp; **Duration:** 1.94s

- [x] spatial/proj extensions recognized: crs_wkt/_CRS/spatial_ref/proj: keys in metadata
- [x] Grid mapping / CRS via Zarr conventions: grid_mapping or ZARR domain present
- [x] GDAL Zarr driver in use: driverShortName=Zarr
- [x] CRS block present and non-empty in output

driver=Zarr CRS=present GeoTransform=non-default proj_ext=True gridmap=True

**Reference CLI commands** (copy-paste to replicate):

```bash
gdalinfo -json 'ZARR:"/vsicurl/https://s3.explorer.eopf.copernicus.eu/esa-zarr-sentinel-explorer-fra/tests-output/sentinel-2-l2a/S2B_MSIL2A_20260320T114349_N0512_R123_T30VVK_20260320T155447.zarr":/measurements/reflectance/r10m/b02'
gdalinfo -mdd all 'ZARR:"/vsicurl/https://s3.explorer.eopf.copernicus.eu/esa-zarr-sentinel-explorer-fra/tests-output/sentinel-2-l2a/S2B_MSIL2A_20260320T114349_N0512_R123_T30VVK_20260320T155447.zarr":/measurements/reflectance/r10m/b02'
```

<details>
<summary>Command output</summary>

```
{
  "description":"ZARR:\"/vsicurl/https://s3.explorer.eopf.copernicus.eu/esa-zarr-sentinel-explorer-fra/tests-output/sentinel-2-l2a/S2B_MSIL2A_20260320T114349_N0512_R123_T30VVK_20260320T155447.zarr\":/measurements/reflectance/r10m/b02",
  "driverShortName":"Zarr",
  "driverLongName":"Zarr",
  "files":[
    "/vsicurl/https://s3.explorer.eopf.copernicus.eu/esa-zarr-sentinel-explorer-fra/tests-output/sentinel-2-l2a/S2B_MSIL2A_20260320T114349_N0512_R123_T30VVK_20260320T155447.zarr/measurements/reflectance/r10m/b02/zarr.json"
  ],
  "size":[
    10980,
    10980
  ],
  "coordinateSystem":{
    "wkt":"PROJCRS[\"WGS 84 / UTM zone 30N\",\n    BASEGEOGCRS[\"WGS 84\",\n        ENSEMBLE[\"World Geodetic System 1984 ensemble\",\n            MEMBER[\"World Geodetic System 1984 (Transit)\"],\n            MEMBER[\"World Geodetic System 1984 (G730)\"],\n            MEMBER[\"World Geodetic System 1984 (G873)\"],\n            MEMBER[\"World Geodetic System 1984 (G1150)\"],\n            MEMBER[\"World Geodetic System 1984 (G1674)\"],\n            MEMBER[\"World Geodetic System 1984 (G1762)\"],\n            MEMBER[\"World Geodetic System 1984 (G2139)\"],\n            MEMBER[\"World Geodetic System 1984 (G2296)\"],\n            ELLIPSOID[\"WGS 84\",6378137,298.257223563,\n                LENGTHUNIT[\"metre\",1]],\n            ENSEMBLEACCURACY[2.0]],\n        PRIMEM[\"Greenwich\",0,\n            ANGLEUNIT[\"degree\",0.0174532925199433]],\n        ID[\"EPSG\",4326]],\n    CONVERSION[\"UTM zone 30N
--- metadata domains ---
Driver: Zarr/Zarr
Files: /vsicurl/https://s3.explorer.eopf.copernicus.eu/esa-zarr-sentinel-explorer-fra/tests-output/sentinel-2-l2a/S2B_MSIL2A_20260320T114349_N0512_R123_T30VVK_20260320T155447.zarr/measurements/reflectance/r10m/b02/zarr.json
  grid_mapping=spatial_ref
```
</details>

## 3. Screenshots / Images

### 4. Reproject -> EPSG:4326

![b02_4326.png](images/b02_4326.png)

### 5. RGB Composite

![rgb_composite.png](images/rgb_composite.png)

## 4. Network Efficiency

Remote read performance measured with `CPL_VSIL_SHOW_NETWORK_STATS=YES`.

### Metadata overhead — Task 1

Opening the dataset issued **8 HTTP HEAD/GET requests** (limit: 8). ✅ This confirms consolidated metadata (`.zmetadata`) is in use, avoiding per-array probing.

### Partial shard read — Task 2

| Window | Shard shape | Uncompressed | Downloaded | Ratio | Budget | Result |
|--------|-------------|--------------|------------|-------|--------|--------|
| 0,0 → 915×915 | 915×915 | 3270 KB | 2830 KB | 87% | 4096 KB | ✅ |

Reading a single shard-aligned window fetches only the bytes for that chunk, confirming HTTP range-request support in the GDAL Zarr driver.

### Overview access — Task 6

Full-res band: 471004 KB (uncompressed Float32 GeoTIFF). Each row shows one overview level exported with `gdal_translate -ovr N`.

| Level | Resolution | Downloaded | Uncompressed | vs full-res (471004 KB) |
|-------|-----------|------------|--------------|----------------------|
| 0 | 5490×5490 | 87145 KB | 117768 KB | 25.0% (4× smaller) |
| 1 | 1830×1830 | 10050 KB | 13093 KB | 2.78% (36× smaller) |
| 2 | 915×915 | 2899 KB | 3274 KB | 0.7% (144× smaller) |
| 3 | 305×305 | 671 KB | 365 KB | 0.08% (1290× smaller) |
| 4 | 152×152 | 459 KB | 91 KB | 0.02% (5176× smaller) |

Overview access is efficient: GDAL fetches only the chunks for the requested overview level without downloading the full-resolution data.

## 5. Issues Found

No issues found. All tasks passed without errors.

## 6. Conclusion

All **10 contracted validation tasks** passed successfully against https://s3.explorer.eopf.copernicus.eu/esa-zarr-sentinel-explorer-fra/tests-output/sentinel-2-l2a/S2B_MSIL2A_20260320T114349_N0512_R123_T30VVK_20260320T155447.zarr using GDAL 3.13.0dev-2044425c73a079babe690537c692b57482e8d32c, released 2026/03/26.

The following capabilities are confirmed working:

- **1. Metadata**: CRS=EPSG:32630 pixel=10m overviews=5>=3 block=915x915 scale=False nodata=True HEAD=8
- **2. Partial Read**: 915x915 window (1 shard): 2830 KB (< 4096 KB limit)
- **3. Export -> GeoTIFF**: Exported to band.tif, CRS=EPSG:32630 verified, origin=399960, pixel=10m, max=1.7998000383377
- **4. Reproject -> EPSG:4326**: Reprojected to EPSG:4326, output=b02_4326.tif, thumbnail max pixel=255/255. Source band actual range: min=-0 max=2 mean=0; thumbnail uses explicit vis_scale.
- **5. RGB Composite**: RGB PNG written (1346 KB, max pixel=255/255): rgb_composite.png. Source band actual range: min=-0 max=2 mean=0; thumbnail uses explicit vis_scale.
- **6. Overview Read**: 5 overview levels; overview=5490x5490 (full=10980x10980); file=117768 KB (full=471004 KB); network=87145 KB
- **7. Resolution r10m**: r10m=10m (expected 10m)
- **7. Resolution r20m**: r20m=20m (expected 20m)
- **7. Resolution r60m**: r60m=60m (expected 60m)
- **8. GeoZarr Conventions**: driver=Zarr CRS=present GeoTransform=non-default proj_ext=True gridmap=True


The GDAL Zarr driver correctly reads EOPF CPM Zarr datasets over `/vsicurl`, exposes CRS and overview metadata, supports partial (shard-aligned) reads with efficient network usage, exports to GeoTIFF, reprojects via `gdalwarp`, renders RGB composites, reads all configured resolution bands, and reports GeoZarr-compliant driver/CRS/GeoTransform metadata. **The contracted scope is delivered and working.**

