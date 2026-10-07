"""TR01: open the store with titiler-eopf's own GeoZarrReader (the code `/rstaging` runs).

Scratch stage only: it reads S3 through titiler's own obstore, outside this tool's
request budget, so it never runs against a production URL. Needs the `reader` extra.
"""

import time

from .model import FAIL, PASS, SKIP, VOID, Result
from .titiler_checks import lonlat_to_tile


def tr01_local_reader(store_url: str, cfg: dict, center=None, absent_optional=()) -> Result:
    """`absent_optional`: StoreContext.absent_optional, the "?" groups this store doesn't have."""
    if store_url.startswith(("http://", "https://")):
        return Result("TR01", "reader", SKIP, "the local reader runs on scratch stores only (s3:// or a local path)")
    try:
        from titiler.eopf.reader import GeoZarrReader
    except ImportError:  # asked for and not run: no verdict, rather than a silent PASS
        return Result("TR01", "reader", VOID, "titiler-eopf not installed: run `uv sync --extra reader`, or drop `reader` from --groups")
    rows, fails, metrics = [], [], {}
    variables = cfg["render"]["variables"]
    for group in [g.rstrip("?") for g in cfg.get("open_groups", [])]:
        if group in absent_optional:
            rows.append(f"{group}: optional and absent from this store, not opened")
            continue
        url = f"{store_url.rstrip('/')}/{group}"
        t0 = time.perf_counter()
        try:
            with GeoZarrReader(input=url) as src:
                rows.append(f"{group}: opened in {time.perf_counter() - t0:.1f} s; groups {src.groups}")
                lo, hi = src.get_minzoom("/"), src.get_maxzoom("/")
                rows.append(f"{group}: zooms {lo}–{hi}")
                metrics[group] = {"minzoom": lo, "maxzoom": hi}
                lon, lat = center or [(src.bounds[0] + src.bounds[2]) / 2, (src.bounds[1] + src.bounds[3]) / 2]
                for z in sorted({lo, (lo + hi) // 2, hi}):
                    x, y = lonlat_to_tile(lon, lat, z)
                    t = time.perf_counter()
                    img = src.tile(x, y, z, variables=variables[:1])
                    valid = float((img.mask > 0).mean())
                    rows.append(f"{group}: tile z{z} {x}/{y} {time.perf_counter() - t:.1f} s, {valid:.0%} valid")
                    if z != lo and valid == 0:
                        fails.append(f"{group}: tile z{z} {x}/{y} is empty")
        except Exception as exc:  # report, never hide: this is the error titiler would 500 on
            fails.append(f"{group}: {type(exc).__name__}: {str(exc)[:200]}")
    return Result("TR01", "reader", FAIL if fails else PASS, fails[0] if fails else "titiler-eopf 0.12 reader opens the store and renders tiles", fails + rows, metrics, problems=fails)
