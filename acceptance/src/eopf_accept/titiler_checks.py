"""titiler-eopf checks (TI*), run once per endpoint.

The URL syntax differs by API generation (failure class C8). `urls()` is the only place
that knows it, and it keys on the endpoint's `api` ("0.12" or "0.11"), not on the
version string, which has lagged the code (until 8 Oct /rstaging ran 0.12 code that reported "0.11.0").
- 0.12: STAC item route `?assets=<asset>|bands=a,b,c`, or asset route
  `/assets/<asset>/…?variables=a&variables=b`, plus optional extras such as `bidx=1`.
- 0.11: item route only, `?variables=/<group>:a`. The server ignores asset hrefs and
  reads `{store_url}/{collection}/{item}.zarr`.
"""

import io
import math

import httpx
import numpy as np
from PIL import Image

from . import geometry as geo
from .budget import Http
from .model import FAIL, PASS, SKIP, VOID, WARN, XFAIL, XPASS, Result

TMS = "WebMercatorQuad"
ASSET_ROUTE = "/collections/{collection_id}/items/{item_id}/assets/{asset_id}"
MAX_TILES = 8  # TI03 renders one tile per pyramid level, at most this many


def resolve_group(path: str, oracle: dict) -> tuple[str | None, str | None]:
    """A render's group path → (multiscales group, level path or None); (None, None) if neither."""
    path = path.strip("/")
    if path in oracle:
        return path, None
    parent = path.rpartition("/")[0]
    if isinstance(oracle.get(parent), dict) and path in dict(oracle[parent]["levels"]):
        return parent, path
    return None, None


def expected_zooms(cfg: dict, api: str, oracle: dict | None):
    """What the store says this endpoint's render should advertise: (range, group, level,
    the level's zoom, every level's zoom), or the reason it can't say (a str)."""
    if oracle is None:
        return "no store metadata read"
    spec = cfg["render"].get(api) or {}
    if api == "0.11":
        path = spec.get("group", "")
    elif spec.get("route") == "asset":
        path = (cfg.get("asset_group") or {}).get(spec.get("asset"), "")
    else:
        return "the 0.12 STAC item route takes its zooms from rio-tiler's STAC reader, not from the store"
    g, level = resolve_group(path, oracle)
    if g is None:
        return f"render group {path!r} is not a configured multiscales group or one of its levels"
    if isinstance(oracle[g], str):
        return f"{g}: {oracle[g]}"
    levels = dict(oracle[g]["levels"])
    return oracle[g]["range"], g, level, levels.get(level), sorted(set(levels.values()))


def urls(base: str, collection: str, item: str, render: dict, api: str, n_bands: int | None = None, item_extra: dict | None = None):
    """(path prefix, query params) for a render on this API generation. `item_extra` is the
    item config's `render_extra`, e.g. an S1 cube's `sel = "time=…"`, added after the spec's."""
    spec = render.get(api)
    if spec is None:
        raise KeyError(f"no render spec for titiler API {api} in the collection config")
    variables = render["variables"][: n_bands or len(render["variables"])]
    rescale = render.get("rescale", [])[: len(variables)]
    item_prefix = f"{base}/collections/{collection}/items/{item}"
    params: list[tuple[str, str]] = []
    if api == "0.11":
        prefix = item_prefix
        params += [("variables", f"{spec['group']}:{var}") for var in variables]
    elif spec.get("route") == "asset":
        prefix = f"{item_prefix}/assets/{spec['asset']}"
        params += [("variables", var) for var in variables]
    else:
        prefix = item_prefix
        params.append(("assets", f"{spec['asset']}|bands={','.join(variables)}"))
    params += [("rescale", r) for r in rescale]
    params += [(k, str(val)) for k, val in {**(spec.get("extra") or {}), **(item_extra or {})}.items()]
    return prefix, params


def lonlat_to_tile(lon: float, lat: float, z: int) -> tuple[int, int]:
    n = 2**z
    lat = max(min(lat, 85.0511), -85.0511)
    x = int((lon + 180.0) / 360.0 * n)
    y = int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)
    return min(max(x, 0), n - 1), min(max(y, 0), n - 1)


# The smallest browser window TI09 expects map.html to open in: a 1280×720 laptop screen
# (4–5 % of desktops, StatCounter Sep 2026) minus ~120 px of browser chrome (an estimate).
# A collection config can set `floor_viewport = [w, h]`.
FLOOR_VIEWPORT = (1280, 600)


def extent_px(bounds, z: int) -> tuple[float, float]:
    """Width and height in pixels of `bounds` drawn at zoom z in WebMercatorQuad."""
    w, s, e, n = bounds

    def merc_y(lat):
        lat = max(min(lat, 85.0511), -85.0511)
        return math.asinh(math.tan(math.radians(lat)))

    size = 256 * 2**z
    return (e - w) / 360.0 * size, (merc_y(n) - merc_y(s)) / (2 * math.pi) * size


def png_stats(content: bytes) -> dict:
    img = Image.open(io.BytesIO(content))
    a = np.asarray(img)
    if a.ndim == 2:
        a = a[..., None]
    has_alpha = img.mode in ("LA", "RGBA", "PA")
    alpha = a[..., -1] if has_alpha else np.full(a.shape[:2], 255, np.uint8)
    color = a[..., :-1] if has_alpha else a
    valid = alpha > 0
    vals = color[valid]
    distinct = int(len(np.unique(vals.reshape(len(vals), -1), axis=0))) if len(vals) else 0
    same_channels = bool(color.shape[-1] >= 3 and len(vals) and (vals[:, 0] == vals[:, 1]).all() and (vals[:, 1] == vals[:, 2]).all())
    return {"mode": img.mode, "size": img.size, "valid": float(valid.mean()), "distinct": distinct, "same_channels": same_channels}


def api_fingerprint(openapi: dict) -> str:
    """0.12 mounts per-asset routes; 0.11 has item routes only (main.py on each branch)."""
    return "0.12" if any(p.startswith(ASSET_ROUTE) for p in openapi.get("paths", {})) else "0.11"


class TitilerBattery:
    def __init__(self, http: Http, name: str, endpoint: dict, cfg: dict, item: str, center=None, footprint: dict | None = None,
                 zoom_oracle: dict | None = None):
        self.http, self.name, self.ep, self.cfg, self.item = http, name, endpoint, cfg, item
        self.base = endpoint["base"].rstrip("/")
        self.api = endpoint["api"]
        self.group = f"titiler:{name}"
        self.center = center
        self.footprint = footprint  # STAC item geometry, when the item was fetched
        self.expected = expected_zooms(cfg, self.api, zoom_oracle)  # zooms.oracle(StoreContext)
        # which viewer the items link: 0.11 /viewer (OLCI) or map.html (S1); 0.12 map.html
        self.viewer_page = (cfg.get("viewer_page") or {}).get(self.api, "viewer" if self.api == "0.11" else "map.html")
        self.cache_headers: list[str] = []
        self.tilejson: dict | None = None  # set when usable, even if TI02 fails on its zooms

    def get(self, url, params=None):
        """GET; a dropped connection becomes a 599 response, so the check fails instead of the run."""
        try:
            r = self.http.get(url, params)
        except httpx.TransportError as exc:
            return httpx.Response(599, text=f"transport error: {type(exc).__name__}: {exc}", request=httpx.Request("GET", url))
        if "x-cache" in r.headers:
            self.cache_headers.append(r.headers["x-cache"].upper())
        return r

    def result(self, id_, status, summary, evidence=None, metrics=None, problems=None):
        return Result(id_, self.group, status, summary, evidence or [], metrics or {}, problems=problems or [])

    def ti00_version(self) -> Result:
        """Fail-closed: the reported version must equal the config AND the routes must match
        the configured API generation. An empty version is a failure, never a match."""
        r = self.get(f"{self.base}/api")
        doc = {}
        if r.status_code == 200:
            try:
                doc = r.json()
            except ValueError:
                pass
        got = (doc.get("info") or {}).get("version")
        if not got:
            return self.result("TI00", FAIL, f"could not read the version from {self.base}/api (HTTP {r.status_code}); refusing to test an unknown deployment")
        fp = api_fingerprint(doc)
        problems = []
        if str(got) != self.ep["expect_version"]:
            problems.append(f"reports {got}, config expects {self.ep['expect_version']}")
        if fp != self.api:
            problems.append(f"routes look like the {fp} API, config says {self.api}")
        if problems:
            return self.result("TI00", FAIL, "; ".join(problems), problems=problems)
        return self.result("TI00", PASS, f"reports {got}; routes match the {fp} API")

    def _prefix(self, n_bands=None):
        item_extra = (self.cfg.get("items", {}).get(self.item) or {}).get("render_extra")
        return urls(self.base, self.cfg["collection"], self.item, self.cfg["render"], self.api, n_bands, item_extra)

    def ti01_info(self) -> Result:
        prefix, params = self._prefix()
        r = self.get(f"{prefix}/info", [p for p in params if p[0] != "rescale"])
        if r.status_code != 200:
            return self.result("TI01", FAIL, f"/info → HTTP {r.status_code}", [r.text[:300]])
        return self.result("TI01", PASS, "/info → 200", [r.text[:200]])

    def ti02_tilejson(self) -> Result:
        prefix, params = self._prefix(1)
        r = self.get(f"{prefix}/{TMS}/tilejson.json", params)
        if r.status_code != 200:
            return self.result("TI02", FAIL, f"tilejson without zoom params → HTTP {r.status_code} (missing spatial:shape/transform?)", [r.text[:300]])
        tj = r.json()
        lo, hi, b = tj.get("minzoom"), tj.get("maxzoom"), tj.get("bounds")
        probs = []
        zooms_ok = isinstance(lo, int) and isinstance(hi, int) and lo <= hi
        if not zooms_ok:
            probs.append(f"minzoom={lo!r} maxzoom={hi!r}")
        if not (isinstance(b, list) and len(b) == 4 and -180 <= b[0] < b[2] <= 180 and -90 <= b[1] < b[3] <= 90):
            probs.append(f"bounds={b!r}")
        else:
            if zooms_ok:  # usable: TI03/TI04/TI09 still run and add evidence when TI02 fails on content
                self.tilejson = tj
            if self.footprint and not geo.contains(geo.bbox_polygon(b), *geo.interior_point(self.footprint)):
                probs.append(f"bounds {b} do not contain the item footprint")
        ev = [f"minzoom={lo!r} maxzoom={hi!r} bounds={b!r}"]
        exp = self.expected
        if isinstance(exp, str):
            ev.append(f"zoom range not checked: {exp}")
        else:
            (elo, ehi), g, level, level_z, all_z = exp
            ev.append(f"store: {g} levels at zooms {all_z} → expected {elo}–{ehi}")
            if zooms_ok and (lo, hi) != (elo, ehi):
                if level and lo == hi == level_z:
                    probs.append(f"minzoom = maxzoom = {hi}: the render reads level group {level}, not the multiscales group {g} (the store gives {elo}–{ehi})")
                else:
                    probs.append(f"zooms {lo}–{hi}, the store's multiscales give {elo}–{ehi}")
        anchor = (self.cfg.get("items", {}).get(self.item) or {}).get("zooms")
        # When the store gives the anchor's range, the store comparison above already says this
        if anchor and zooms_ok and [lo, hi] != list(anchor) and (isinstance(exp, str) or list(exp[0]) != list(anchor)):
            probs.append(f"zooms {lo}–{hi}, the item config expects {anchor[0]}–{anchor[1]}")
        if anchor and not isinstance(exp, str) and list(exp[0]) != list(anchor):
            probs.append(f"the store gives {exp[0][0]}–{exp[0][1]} but the item config anchors {anchor[0]}–{anchor[1]}")
        if probs:
            return self.result("TI02", FAIL, "tilejson is incoherent: " + "; ".join(probs), ev + [str(tj)[:300]], problems=probs)
        if isinstance(exp, str):  # never a silent PASS on an unchecked range
            return self.result("TI02", WARN, f"tilejson → 200 with no zoom params; zooms {lo}–{hi}, not checked against the store", ev,
                               metrics={"minzoom": lo, "maxzoom": hi, "bounds": b})
        return self.result("TI02", PASS, f"tilejson → 200 with no zoom params; zooms {lo}–{hi}, as the store's multiscales give", ev,
                           metrics={"minzoom": lo, "maxzoom": hi, "bounds": b})

    def _footprint(self) -> dict:
        return self.footprint or geo.bbox_polygon(self.tilejson["bounds"])

    def _center(self):
        if self.center:
            return self.center
        item_center = (self.cfg.get("items", {}).get(self.item) or {}).get("center")
        if item_center:
            return item_center
        return list(geo.interior_point(self._footprint()))

    def ti03_tiles(self) -> Result:
        """One tile per pyramid level (each level's zoom from the store), so every resolution
        is read; without the store, minzoom, middle and maxzoom of the tilejson. A render that
        reads one level group (the 0.11 r0 links) reads that level at every zoom, and the
        summary says so."""
        if not self.tilejson:
            return self.result("TI03", SKIP, "no usable tilejson (TI02)")
        lo, hi = self.tilejson["minzoom"], self.tilejson["maxzoom"]
        lon, lat = self._center()
        min_valid = float(self.cfg.get("min_valid", 0.25))
        min_distinct = int(self.cfg.get("min_distinct", 16))
        prefix, params = self._prefix(1)
        fails, rows, metrics = [], [], {}
        per_level = not isinstance(self.expected, str)
        zooms = self.expected[4] if per_level else sorted({lo, (lo + hi) // 2, hi})
        if len(zooms) > MAX_TILES:
            rows.append(f"{len(zooms)} levels; rendering {MAX_TILES} of them, spread from the coarsest to the finest")
            zooms = sorted({zooms[round(i * (len(zooms) - 1) / (MAX_TILES - 1))] for i in range(MAX_TILES)})
        exempt = zooms[0] if len(zooms) > 1 else None  # the coarsest tile is mostly outside the footprint
        for z in zooms:
            x, y = lonlat_to_tile(lon, lat, z)
            r = self.get(f"{prefix}/tiles/{TMS}/{z}/{x}/{y}.png", params)
            if r.status_code != 200 or "png" not in r.headers.get("content-type", ""):
                fails.append(f"z{z} {x}/{y}: HTTP {r.status_code} {r.headers.get('content-type')} {r.text[:120] if r.status_code != 200 else ''}")
                continue
            st = png_stats(r.content)
            # Require valid pixels in proportion to the tile's share of the footprint; at
            # minzoom, where the tile is mostly outside it, only "not all nodata".
            cover = geo.coverage(self._footprint(), z, x, y)
            want = 0.0 if z == exempt else min_valid * cover  # a single tile gets no exemption (W10)
            rows.append(f"z{z} {x}/{y}: {st['mode']} {st['size'][0]}x{st['size'][1]}, {st['valid']:.0%} valid ({cover:.0%} of the tile inside the footprint), {st['distinct']} distinct values, {len(r.content)} B, {r.elapsed.total_seconds():.2f} s")
            metrics[f"z{z}"] = {"valid": st["valid"], "coverage": cover, "distinct": st["distinct"], "seconds": r.elapsed.total_seconds()}
            if st["valid"] <= want:
                fails.append(f"z{z} {x}/{y}: {st['valid']:.0%} valid pixels (want > {want:.0%})")
            elif st["distinct"] < min_distinct:
                fails.append(f"z{z} {x}/{y}: only {st['distinct']} distinct values (a constant render?)")
        level = self.expected[2] if per_level else None
        which = (f"each pyramid level's zoom, all read from level group {level}" if level else
                 "one per pyramid level" if per_level else "tilejson min, mid, max")
        return self.result("TI03", FAIL if fails else PASS, fails[0] if fails else f"tiles at z{', z'.join(map(str, zooms))} ({which}) decode with real pixels",
                           fails + rows, metrics, problems=fails)

    def ti04_rgb(self) -> Result:
        if not self.tilejson or len(self.cfg["render"]["variables"]) < 3:
            return self.result("TI04", SKIP, "no tilejson or fewer than 3 render variables")
        lo, hi = self.tilejson["minzoom"], self.tilejson["maxzoom"]
        z = (lo + hi + 1) // 2
        x, y = lonlat_to_tile(*self._center(), z)
        prefix, params = self._prefix(3)
        r = self.get(f"{prefix}/tiles/{TMS}/{z}/{x}/{y}.png", params)
        if r.status_code != 200:
            return self.result("TI04", FAIL, f"RGB tile z{z} → HTTP {r.status_code}", [r.text[:300]])
        st = png_stats(r.content)
        if st["valid"] == 0:
            return self.result("TI04", FAIL, f"RGB tile z{z} has no valid pixels")
        if st["same_channels"]:
            return self.result("TI04", FAIL, "RGB tile has three identical channels (one variable served for all three?)")
        return self.result("TI04", PASS, f"RGB tile z{z}: {st['mode']}, channels differ, {st['valid']:.0%} valid")

    def ti06_viewer(self) -> Result:
        """The viewer page the collection's items link (`viewer_page` in the config, per API)."""
        prefix, params = self._prefix(3)
        if self.viewer_page == "viewer":
            url, params = f"{self.base}/collections/{self.cfg['collection']}/items/{self.item}/viewer", []
        else:
            url = f"{prefix}/{TMS}/map.html"
        r = self.get(url, params)
        ok = r.status_code == 200 and "html" in r.headers.get("content-type", "")
        return self.result("TI06", PASS if ok else FAIL, f"{url.rsplit('/', 1)[-1]} → HTTP {r.status_code}")

    def ti07_contract(self) -> Result:
        """Every documented URL form must return exactly its documented status (C8, C14).
        A documented failure that now returns 200 is XPASS: worth knowing at the flip, but a
        200 alone doesn't prove the parameters were honoured."""
        rows, worst = [], PASS
        order = [PASS, XFAIL, XPASS, FAIL]
        entries = [c for c in self.cfg.get("contract", []) if c["api"] == self.api]
        if not entries:
            return self.result("TI07", SKIP, f"no URL contract entries for the {self.api} API")
        base_item = f"{self.base}/collections/{self.cfg['collection']}/items/{self.item}"
        for c in entries:
            r = self.get(f"{base_item}/{c['path']}", [tuple(p) for p in c.get("params", [])])
            want, got = int(c["expect"]), r.status_code
            if got == want:
                st = PASS if want == 200 else XFAIL
            elif got == 200:
                st = XPASS
            else:
                st = FAIL
            rows.append(f"{st}: {c['name']}: expected {want}, got {got} ({c.get('ref', '')})")
            worst = max(worst, st, key=order.index)
        return self.result("TI07", worst, f"{len(entries)} URL forms checked", rows)

    def ti09_fit_zoom(self) -> Result:
        """C13. map.html fills the browser window and calls Leaflet fitBounds (no padding, zoom
        floored), and its tile layer draws nothing below minzoom. So it opens blank when the
        item, drawn at minzoom, is wider or taller than the window. 1280×800 (the old rule)
        is a screen, not a window: it passed S3A 142227, whose map opened blank on 6 Oct."""
        if not self.tilejson:
            return self.result("TI09", SKIP, "no usable tilejson (TI02)")
        width, height = self.cfg.get("floor_viewport", FLOOR_VIEWPORT)
        lo = self.tilejson["minzoom"]
        ew, eh = extent_px(self.tilejson["bounds"], lo)
        margin = math.log2(min(width / ew, height / eh))
        ev = [f"at minzoom {lo} the bounds span {ew:.0f}×{eh:.0f} px; floor window {width}×{height}; margin {margin:+.2f} zoom",
              f"map.html opens blank in a window narrower than {ew:.0f} px or shorter than {eh:.0f} px"]
        if self.viewer_page != "map.html":
            ev.append(f"the item's own viewer link on this API is /{self.viewer_page}, which TI09 doesn't model; map.html is mounted here too")
        metrics = {"extent_px": [round(ew), round(eh)], "margin_zoom": round(margin, 3)}
        if margin < 0:
            return self.result("TI09", WARN, f"map.html opens blank in a {width}×{height} window: at minzoom {lo} the item spans {ew:.0f}×{eh:.0f} px (C13)", ev, metrics)
        return self.result("TI09", PASS, f"at minzoom {lo} the item ({ew:.0f}×{eh:.0f} px) fits a {width}×{height} window (margin {margin:+.2f} zoom)", ev, metrics)

    def ti05_cold(self) -> Result:
        hits = [h for h in self.cache_headers if "HIT" in h]
        if hits:
            return self.result("TI05", VOID, f"{len(hits)} of {len(self.cache_headers)} responses were cache HITs: the run measured the cache; re-run")
        return self.result("TI05", PASS, f"no cache HITs ({len(self.cache_headers)} responses carried X-Cache)")

    def run(self) -> list[Result]:
        v = self.ti00_version()
        if v.status != PASS:
            return [v]
        out = [v, self.ti01_info(), self.ti02_tilejson(), self.ti03_tiles(), self.ti04_rgb(), self.ti06_viewer(), self.ti07_contract(), self.ti09_fit_zoom()]
        return out + [self.ti05_cold()]

    @staticmethod
    def request_bound(cfg: dict, api: str) -> int:
        # TI00 TI01 TI02 TI04 TI06 one each, TI03 up to MAX_TILES, TI07 one per contract row, 2 slack
        return 7 + MAX_TILES + len([c for c in cfg.get("contract", []) if c["api"] == api])
