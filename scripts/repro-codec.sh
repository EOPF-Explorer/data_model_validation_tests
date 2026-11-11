#!/usr/bin/env bash
# Reproduce GDAL's failure to read the Zarr v3 scale_offset / cast_value codecs.
#
# Needs only Docker — the fixture is committed under fixtures/ and the GDAL CLI
# comes from the official image. Override the image to test another GDAL build:
#   GDAL_IMAGE=ghcr.io/osgeo/gdal:ubuntu-full-3.11.0 ./scripts/repro-codec.sh
set -uo pipefail

GDAL_IMAGE="${GDAL_IMAGE:-ghcr.io/osgeo/gdal:ubuntu-full-latest}"
FIXTURE="$(cd "$(dirname "$0")/.." && pwd)/fixtures/codec_scale_offset.zarr"

[ -d "$FIXTURE" ] || { echo "fixture not found: $FIXTURE" >&2; exit 2; }

echo "GDAL image: $GDAL_IMAGE"
docker run --rm "$GDAL_IMAGE" gdalinfo --version

rc=0
for ARRAY in b02_plain b02_sharded; do
  echo
  echo "=============================================================="
  echo "  /$ARRAY   (float32 on the wire, uint16 on disk)"
  echo "=============================================================="
  docker run --rm -v "$FIXTURE:/fx.zarr:ro" "$GDAL_IMAGE" \
    gdalinfo "ZARR:\"/fx.zarr\":/$ARRAY"
  status=$?
  echo "gdalinfo exit code = $status"
  [ $status -ne 0 ] && rc=1
done

echo
if [ $rc -ne 0 ]; then
  echo "RESULT: GDAL CANNOT read these codecs (expected today — see EOPF-Explorer/data-pipeline#181)."
else
  echo "RESULT: GDAL read both arrays. The codecs are supported in $GDAL_IMAGE."
fi
exit $rc
