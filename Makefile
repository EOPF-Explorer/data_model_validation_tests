.PHONY: test test-docker test-local docker-build clean help

IMAGE_NAME    := eopf-validation-gdal
EOPF_DATASET_URL ?= https://s3.explorer.eopf.copernicus.eu/esa-zarr-sentinel-explorer-fra/tests-output/sentinel-2-l2a/S2B_MSIL2A_20260320T114349_N0512_R123_T30VVK_20260320T155447.zarr

## Run pytest suite in Docker
test test-docker:
	mkdir -p output/images
	docker run --rm \
		-e EOPF_DATASET_URL="$(EOPF_DATASET_URL)" \
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
