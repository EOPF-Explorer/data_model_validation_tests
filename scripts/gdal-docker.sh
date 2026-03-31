# Run GDAL CLI tools from Docker (same base image as docker/Dockerfile.gdal).
# Usage:
#   source /path/to/validation_tests/scripts/gdal-docker.sh
#   gdal-docker gdalinfo /data/foo.tif
#
# Paths: the current directory is mounted at /data, so use /data/... for files
# under your cwd (or relative paths like ./foo.tif → /data/foo.tif).
#
# Override image (e.g. after `make docker-build`):
#   export GDAL_DOCKER_IMAGE=eopf-validation-gdal

: "${GDAL_DOCKER_IMAGE:=ghcr.io/osgeo/gdal:ubuntu-full-latest}"

gdal-docker() {
	docker run --rm \
		-v "${PWD}:/data" \
		-w /data \
		"${GDAL_DOCKER_IMAGE}" \
		"$@"
}
