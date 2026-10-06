"""Build a tiny GeoZarr v1 store shaped like the OLCI rc8 store, healthy or broken on purpose.

Each keyword argument breaks one thing that broke for real (see the plan's failure
classes), so each check can be shown to fire on its failure and stay quiet otherwise.
"""

import numpy as np
import zarr

from eopf_accept import conventions as cv


def decl(uuid: str, stale: bool = False) -> dict:
    d = {"uuid": uuid, **cv.CONSTS[uuid]}
    if stale:  # the zarr-cm 0.4.1 / titiler fixture spelling: trailing colon, refs/tags/v1
        d["name"] += ":" if uuid != cv.MULTISCALES else ""
        d["schema_url"] = d["schema_url"].replace("v0.1", "v1")
    return d


def build(
    path,
    *,
    consolidated: str = "all",  # all | none | root-only
    layout_shape: bool = True,  # spatial:shape + spatial:transform in the layout entries
    group_shape: bool = True,  # spatial:shape on the level groups
    level_dims: bool = True,  # spatial:dimensions on the level groups (titiler 0.12 tiles KeyError without it)
    conventions: str = "v0.1",  # v0.1 | stale | none
    dtype: str = "float32",
    shape: tuple[int, int] = (64, 96),
    chunks: tuple[int, int] = (32, 32),
    shards: tuple[int, int] | None = None,
    write_data: bool = True,
    fill: bool = False,
    partial: bool = False,  # data only in the top-left corner, like a 5 % S2 scene
    bbox=(-36.0, 34.0, -34.0, 36.0),
) -> str:
    root = zarr.open_group(str(path), mode="w")
    west, south, east, north = bbox
    stale = conventions == "stale"
    conv_all = [] if conventions == "none" else [decl(u, stale) for u in (cv.SPATIAL, cv.PROJ, cv.MULTISCALES)]
    conv_level = [] if conventions == "none" else [decl(u, stale) for u in (cv.SPATIAL, cv.PROJ)]

    levels = []
    for i, factor in enumerate((1, 2)):
        h, w = shape[0] // factor, shape[1] // factor
        transform = [(east - west) / w, 0.0, west, 0.0, -(north - south) / h, north]
        levels.append((f"r{factor if factor > 1 else 0}", h, w, transform))

    layout = []
    for name, h, w, tr in levels:
        entry = {"asset": name}
        if layout_shape:
            entry |= {"spatial:shape": [h, w], "spatial:transform": tr}
        layout.append(entry)
    ms = root.create_group("measurements", attributes={
        "zarr_conventions": conv_all,
        "multiscales": {"layout": layout},
        "spatial:bbox": list(bbox),
        "spatial:dimensions": ["y", "x"],
        "proj:code": "EPSG:4326",
    })
    rng = np.random.default_rng(0)
    for name, h, w, tr in levels:
        attrs = {"zarr_conventions": conv_level, "spatial:transform": tr, "proj:code": "EPSG:4326", "spatial:bbox": list(bbox)}
        if group_shape:
            attrs["spatial:shape"] = [h, w]
        if level_dims:
            attrs["spatial:dimensions"] = ["y", "x"]
        lvl = ms.create_group(name, attributes=attrs)
        lvl.create_array("x", shape=(w,), dtype="float64", dimension_names=["x"])[:] = west + (np.arange(w) + 0.5) * tr[0]
        lvl.create_array("y", shape=(h,), dtype="float64", dimension_names=["y"])[:] = north + (np.arange(h) + 0.5) * tr[4]
        for var in ("oa08_radiance", "oa06_radiance", "oa04_radiance"):
            arr = lvl.create_array(
                var, shape=(h, w), dtype=dtype, chunks=chunks if shards is None else chunks, shards=shards,
                fill_value=float("nan") if dtype.startswith("float") else 0, dimension_names=["y", "x"],
                attributes={"zarr_conventions": conv_level, "proj:code": "EPSG:4326"} if conventions != "none" else {},
            )
            if write_data:
                yy, xx = np.mgrid[0:h, 0:w] / max(h, w)
                smooth = 10 + 200 * (0.5 + 0.25 * np.sin(6 * yy) + 0.25 * np.cos(5 * xx)) + rng.random((h, w))
                data = np.full((h, w), np.nan, dtype) if fill else np.round(smooth).astype(dtype)
                if partial:
                    data[h // 5:, :] = np.nan
                    data[:, w // 5:] = np.nan
                arr[:] = data

    if consolidated == "all":
        for name, *_ in levels:
            zarr.consolidate_metadata(str(path), path=f"measurements/{name}")
        zarr.consolidate_metadata(str(path), path="measurements")
        zarr.consolidate_metadata(str(path))
    elif consolidated == "root-only":
        zarr.consolidate_metadata(str(path))
    return str(path)


CFG = {
    "collection": "sentinel-3-olci-l1-efr-staging",
    "open_groups": ["measurements"],
    "multiscales_groups": ["measurements"],
    "min_levels": 2,
    "sample_variables": ["oa08_radiance", "oa06_radiance", "oa04_radiance"],
    "dtype_allow": ["uint16", "float32"],
    "dtype_check_pattern": "_radiance$",
    "consumers": {"openlayers": {}},
    "render": {
        "variables": ["oa08_radiance", "oa06_radiance", "oa04_radiance"],
        "rescale": ["10,300", "10,300", "10,300"],
        "0.12": {"route": "asset", "asset": "radianceData"},
        "0.11": {"group": "/measurements/r0", "extra": {"bidx": "1"}},
    },
}
