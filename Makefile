.PHONY: test test-docker test-local test-v3 docker-build clean help

IMAGE_NAME    := eopf-validation-gdal
EOPF_DATASET_URL ?= https://s3.explorer.eopf.copernicus.eu/esa-zarr-sentinel-explorer-fra/tests-output/sentinel-2-l2a/S2B_MSIL2A_20260320T114349_N0512_R123_T30VVK_20260320T155447.zarr

# New Zarr v3 (sharded) datamodel: default item + config.
EOPF_V3_URL    ?= https://s3.explorer.eopf.copernicus.eu/esa-zarr-sentinel-explorer-fra/tests-output/sentinel-2-l2a/S2C_MSIL2A_20260903T135731_N0512_R010_T26WME_20260903T171314.zarr
EOPF_V3_CONFIG ?= configs/sentinel2_l2a_v3.toml

# GDAL performance env for sharded Zarr v3 over HTTP: full-band reads fetch many
# inner shard chunks as range requests, which is pathologically slow single-threaded.
# Threading + HTTP multiplexing parallelises them (full band ~4 min vs >15 min).
GDAL_PERF_ENV := -e GDAL_NUM_THREADS=ALL_CPUS -e GDAL_HTTP_MULTIPLEX=YES -e VSI_CACHE=YES

## Run pytest suite in Docker (default v2 dataset)
test test-docker:
	mkdir -p output/images
	docker run --rm \
		-e EOPF_DATASET_URL="$(EOPF_DATASET_URL)" \
		-v "$(PWD)/output:/workspace/output" \
		$(IMAGE_NAME) pytest -v

## Run pytest suite against the new Zarr v3 (sharded) datamodel in Docker
test-v3:
	mkdir -p output/images
	docker run --rm \
		-e EOPF_DATASET_URL="$(EOPF_V3_URL)" \
		-e EOPF_DATASET_CONFIG="$(EOPF_V3_CONFIG)" \
		$(GDAL_PERF_ENV) \
		-v "$(PWD)/output:/workspace/output" \
		$(IMAGE_NAME) pytest -v

## Run pytest suite locally (requires GDAL CLI + Python ≥ 3.11 + pytest)
test-local:
	EOPF_DATASET_URL="$(EOPF_DATASET_URL)" python -m pytest -v

## Build the Docker image
docker-build:
	docker build -f docker/Dockerfile.gdal -t $(IMAGE_NAME) .

## Remove generated output
clean:
	rm -rf output/

help:
	@grep -E '^##' Makefile | sed 's/## //'
