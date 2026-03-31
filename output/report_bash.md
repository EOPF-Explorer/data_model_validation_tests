# EOPF / GeoZarr Validation Report (Bash)

## Environment

- **GDAL version**: GDAL 3.13.0dev-2044425c73a079babe690537c692b57482e8d32c, released 2026/03/26
- **Dataset URL**: https://s3.explorer.eopf.copernicus.eu/esa-zarr-sentinel-explorer-fra/tests-output/sentinel-2-l2a/S2B_MSIL2A_20260228T114349_N0512_R123_T30VVK_20260228T155602.zarr
- **Date**: 2026-03-31T09:31:34Z

## Results

| Task | Status | Duration | Network | Details |
|------|--------|----------|---------|----------|
| 1. Metadata | ✅ PASS | 2.67s | — | CRS=EPSG:32630 overviews=5>=3 block=244x244 |
| 2. Partial Read | ✅ PASS | 3.37s | 573 KB | 244x244 window read 573 KB (< 1024 KB limit) |
| 3. Export -> GeoTIFF | ✅ PASS | 37.76s | — | Exported to band.tif, CRS=EPSG:32630 verified |
| 4. Reproject -> 4326 | ✅ PASS | 30.19s | — | Reprojected to EPSG:4326, output=b02_4326.tif |
| 5. RGB Composite | ✅ PASS | 121.94s | — | RGB PNG written (5 KB): rgb_composite_bash.png |
| 6. Overview Read | ✅ PASS | 11.28s | 87682 KB | 5 overview levels; coarsest: 117768 KB; network: 87682 KB |
| 7. Resolutions | ✅ PASS | 8.09s | — | r10m=10.000000000000000m(ok) r20m=20.000000000000000m(ok) r60m=60.000000000000000m(ok) |
| 8. GeoZarr Conventions | ✅ PASS | 2.37s | — | driver=Zarr CRS=present GeoTransform=non-default |

## Summary

**8/8 tasks passed**

## Artifacts

### 5. RGB Composite
![rgb_composite_bash.png](output/images/rgb_composite_bash.png)

