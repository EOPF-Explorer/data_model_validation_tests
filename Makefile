.PHONY: test test-docker test-local validate validate-docker validate-local docker-build clean help

IMAGE_NAME := eopf-validation-gdal

# --- pytest (primary) ---

## Run pytest suite in Docker
test test-docker:
	mkdir -p output/images
	docker run --rm \
		-e EOPF_DATASET_URL="$${EOPF_DATASET_URL:-}" \
		-v "$(PWD)/output:/workspace/output" \
		$(IMAGE_NAME) pytest -v

## Run pytest suite locally (requires GDAL CLI + Python ≥ 3.11 + pytest)
test-local:
	EOPF_DATASET_URL="$${EOPF_DATASET_URL}" python -m pytest -v

# --- bash script (standalone alternative) ---

## Run bash validation in Docker
validate validate-docker:
	mkdir -p output/images
	docker run --rm \
		-e EOPF_DATASET_URL="$${EOPF_DATASET_URL:-}" \
		-v "$(PWD)/output:/workspace/output" \
		$(IMAGE_NAME) bash validate_gdal.sh

## Run bash validation locally (requires GDAL CLI in PATH)
validate-local:
	bash validate_gdal.sh

# --- common ---

## Build the Docker image
docker-build:
	docker build -f docker/Dockerfile.gdal -t $(IMAGE_NAME) .

## Remove generated output
clean:
	rm -rf output/

help:
	@grep -E '^##' Makefile | sed 's/## //'
