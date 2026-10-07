"""The zoom range titiler-eopf should advertise for each multiscales group, computed from
the store's own metadata (the TI02 oracle, 7 Oct review).

Same arithmetic as titiler-eopf 5fbea81 `_get_zoom`: a level's array bounds, reprojected
to WebMercatorQuad with calculate_default_transform, the coarser pixel side, then
`zoom_for_res`. Shape and transform come from the layout entry when it has both, else
from the level group (titiler's fallback in get_minzoom/get_maxzoom). Unlike titiler,
the range spans every level, not layout[0] and layout[-1], so a mis-ordered layout shows
up as a mismatch instead of agreeing with titiler's wrong answer.
"""

import morecantile
from affine import Affine
from rasterio.crs import CRS
from rasterio.transform import array_bounds, from_bounds
from rasterio.warp import calculate_default_transform

TMS = morecantile.tms.get("WebMercatorQuad")


def level_zoom(crs: CRS, shape, transform) -> int:
    h, w = shape
    bounds = array_bounds(h, w, Affine(*transform[:6]))
    if crs != TMS.rasterio_crs:
        tr, _, _ = calculate_default_transform(crs, TMS.rasterio_crs, w, h, *bounds)
    else:
        tr = from_bounds(*bounds, w, h)
    return TMS.zoom_for_res(max(abs(tr.a), abs(tr.e)))


def oracle(ctx) -> dict[str, dict | str]:
    """group -> {"levels": [(level path, zoom)], "range": (lo, hi)}, or why it can't be computed."""
    out: dict[str, dict | str] = {}
    for g, levels in ctx.levels.items():
        a = (ctx.nodes.get(g) or {}).get("attributes") or {}
        proj = next((a[k] for k in ("proj:code", "proj:wkt2", "proj:projjson") if k in a), None)
        if proj is None or not levels:
            out[g] = "no proj:code/wkt2/projjson on the group" if proj is None else "no layout"
            continue
        try:
            crs = CRS.from_user_input(proj)
            zs = []
            for entry, path, node in levels:
                la = (node or {}).get("attributes") or {}
                src = entry if "spatial:shape" in entry and "spatial:transform" in entry else la
                if "spatial:shape" not in src or "spatial:transform" not in src:
                    raise ValueError(f"{path}: no spatial:shape+spatial:transform in the layout entry or the group")
                zs.append((path, level_zoom(crs, src["spatial:shape"], src["spatial:transform"])))
        except Exception as exc:  # a broken store is ST03's FAIL; here it only means "not checked"
            out[g] = f"{type(exc).__name__}: {exc}"
            continue
        out[g] = {"levels": zs, "range": (min(z for _, z in zs), max(z for _, z in zs))}
    return out
