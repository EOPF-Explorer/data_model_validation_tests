#!/usr/bin/env python3
"""Generate the Zarr v3 scale_offset/cast_value fixture used by the codec repro.

The generated store is committed under fixtures/, so reproducing the GDAL failure
needs nothing but Docker. Re-run this only to regenerate or adapt the fixture.

Requires: pip install "zarr[cast-value-rs]>=3.2.0"
Usage:    python scripts/make_codec_fixture.py fixtures/codec_scale_offset.zarr
"""
import shutil
import sys

import numpy as np
import zarr
from zarr.codecs import BloscCodec, CastValue, ScaleOffset

# Same encoding the EOPF converter emits for Sentinel-2 reflectance:
# CF scale_factor=0.0001 / add_offset=-0.1 pushed into the codec pipeline.
SCALE_FACTOR, ADD_OFFSET = 0.0001, -0.1


def codecs():
    """Return (scale_offset, cast_value) exactly as eopf-geozarr builds them."""
    so = ScaleOffset(offset=ADD_OFFSET, scale=1.0 / SCALE_FACTOR)
    cv = CastValue(
        data_type="uint16",
        rounding="nearest-even",
        scalar_map={"encode": [("NaN", 0)], "decode": [(0, "NaN")]},
    )
    return so, cv


def main(path: str) -> None:
    shutil.rmtree(path, ignore_errors=True)
    root = zarr.open_group(path, mode="w", zarr_format=3)
    data = (np.arange(256 * 256, dtype="float32").reshape(256, 256) % 3000) * SCALE_FACTOR
    so, cv = codecs()
    blosc = BloscCodec(cname="zstd", clevel=3)

    # 1. Plain: scale_offset + cast_value only — the minimal failure.
    plain = root.create_array(
        "b02_plain", shape=(256, 256), chunks=(256, 256), dtype="float32",
        filters=[so, cv], compressors=[blosc], fill_value=float("nan"),
    )
    plain[:] = data

    # 2. Sharded: the same codecs nested inside sharding_indexed, as production
    #    writes them. GDAL's failure cascades through the shard codec here.
    sharded = root.create_array(
        "b02_sharded", shape=(256, 256), chunks=(64, 64), shards=(256, 256),
        dtype="float32", filters=[so, cv], compressors=[blosc],
        fill_value=float("nan"),
    )
    sharded[:] = data

    print(f"wrote {path}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "fixtures/codec_scale_offset.zarr")
