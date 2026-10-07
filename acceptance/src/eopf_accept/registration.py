"""Registration checks (RG*): does the registered STAC item point at this store and render?

Registered stage only. Reads the item once from the STAC API, then its own links.
"""

import datetime as dt
import re
from urllib.parse import parse_qsl, urlsplit

import httpx

from . import geometry as geo
from .budget import Http
from .model import FAIL, PASS, SKIP, WARN, Result
from .storeio import StoreReader
from .titiler_checks import lonlat_to_tile, png_stats, resolve_group, urls

LINK_RELS = ("viewer", "tilejson", "xyz")
REQUEST_BOUND = 13  # item + 3 links + thumbnail, each once more for RG05, RG07's source item, plus slack
GROUP_TOKEN = re.compile(r"(/[^:;()\s]+):")  # "/measurements/r0:oa08_radiance", "(/ascending:vv)"


def link_groups(href: str) -> set[str]:
    """The zarr groups a 0.11 link reads, from `variables=/g:var` or the `/g:var` operands of `expression=`."""
    return {g for k, v in parse_qsl(urlsplit(href).query) if k in ("variables", "expression") for g in GROUP_TOKEN.findall(v)}


def _expected_range(links: dict, oracle: dict | None):
    """The store's zoom range for the multiscales group the links read (zooms.oracle), if known."""
    for href in links.values():
        for g in sorted(link_groups(href)):
            ms, _ = resolve_group(g, oracle or {})
            if ms and isinstance(oracle[ms], dict):
                return oracle[ms]["range"]
    return None


def fetch_item(http: Http, stac: str, collection: str, item: str) -> dict:
    r = http.get(f"{stac.rstrip('/')}/collections/{collection}/items/{item}", bust=False)
    r.raise_for_status()
    return r.json()


def _parse_time(value) -> dt.datetime | None:
    if not value:
        return None
    try:
        t = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=dt.UTC)  # STAC times are UTC; compare aware


GATEWAY_HOSTS = ("s3.explorer.eopf.copernicus.eu", "s3.de.io.cloud.ovh.net")


def bucket_key(url: str) -> str:
    """`s3://b/k`, `https://<gateway>/b/k` and `https://<ovh endpoint>/b/k` all become `b/k`,
    so the s3:// origin (preferred, D8) compares equal to the gateway hrefs in the item."""
    u = urlsplit(url.rstrip("/"))
    if u.scheme == "s3":
        return f"{u.netloc}{u.path}"
    if u.hostname in GATEWAY_HOSTS:
        return u.path.lstrip("/")
    return url.rstrip("/")


def rg08_hrefs(item: dict, store: str, cfg: dict, ctx=None) -> Result:
    """Each configured asset points exactly at its group of THIS store (C9: a failed
    registration leaves the previous item, whose hrefs still point at the old layout), and
    that group exists (from the StoreContext already read; a group it doesn't hold costs one
    zarr.json read). When the item
    config declares its groups (a single-orbit S1 cube), only their assets are required."""
    root = bucket_key(store)
    fails, rows = [], []
    store_link = next((link["href"] for link in item.get("links", []) if link.get("rel") == "store"), None)
    if store_link and bucket_key(store_link) != root:
        fails.append(f"item `store` link {store_link} is not the store under test {store}")
    declared = ctx.expected_groups if ctx is not None else None
    for asset, group in (cfg.get("asset_group") or {}).items():
        href = (item.get("assets", {}).get(asset) or {}).get("href")
        want = f"{root}/{group}"
        if href is None:
            if declared is None or group in declared:
                fails.append(f"asset {asset!r} missing")
            else:
                rows.append(f"asset {asset!r} absent; its group {group!r} isn't in the item config's groups")
        elif bucket_key(href) != want:
            fails.append(f"asset {asset!r} → {href}, want {want}")
        elif ctx is not None and (group in ctx.absent_optional or (ctx.nodes.get(group) or ctx.reader.node(group)) is None):
            fails.append(f"asset {asset!r} → {href}, but the store has no group {group!r}")
        else:
            rows.append(f"asset {asset!r} → {href}")
    if not cfg.get("asset_group"):
        return Result("RG08", "registration", SKIP, "no asset_group in the config")
    return Result("RG08", "registration", FAIL if fails else PASS, fails[0] if fails else "asset hrefs point at this store's groups", fails + rows, problems=fails)


def rg10_link_form(item: dict, cfg: dict) -> Result:
    """The battery's 0.11 render must read the group the registered links read, or TI02/TI03
    test a URL users never get. When register_v1 changes its link form, this fails until
    the config follows. No requests."""
    spec = (cfg.get("render") or {}).get("0.11")
    hrefs = [link["href"] for link in item.get("links", []) if link.get("rel") in ("tilejson", "xyz")]
    groups = set().union(*(link_groups(h) for h in hrefs)) if hrefs else set()
    if not spec or not groups:
        return Result("RG10", "registration", SKIP, "no 0.11 render in the config or no tilejson/xyz link with variables/expression")
    sent = {k for k, _ in urls("", "", "", cfg["render"], "0.11")[1]} | set((cfg.get("items", {}).get(item.get("id")) or {}).get("render_extra") or {})
    extra = sorted({k for h in hrefs for k, _ in parse_qsl(urlsplit(h).query)} - sent - {"expression", "minzoom", "maxzoom", "tilesize"})
    ev = [f"registered tilejson/xyz links read {sorted(groups)}"] + ([f"link parameters the battery doesn't send: {extra}"] if extra else [])
    if groups != {spec["group"]}:
        problem = f"the registered links read {sorted(groups)} but the battery's 0.11 render reads {spec['group']!r}: update [render].\"0.11\" so TI02/TI03 test what users get"
        return Result("RG10", "registration", FAIL, problem, ev, problems=[problem])
    return Result("RG10", "registration", PASS, f"the battery's 0.11 render reads {spec['group']}, as the registered links do", ev)


def rg07_fresh(item: dict, reader: StoreReader, http: Http) -> Result:
    """The item must be registered after the store was written: a failed registration
    leaves an older item in place while the store is new (6 Oct canary run 1).

    register_v1 (rc8) never stamps `created`/`updated`: the item keeps its source's, so
    they date the source, not this registration (6 Oct first prod run). When the item's
    time equals its `derived_from` source's, RG07 can't tell stale from fresh: WARN.
    """
    props = item.get("properties", {})
    item_t = _parse_time(props.get("updated") or props.get("created"))
    meta = reader.head("zarr.json")
    store_t = meta.get("last_modified") if meta else None
    if not item_t or not store_t:
        return Result("RG07", "registration", SKIP, f"no item updated/created ({item_t}) or store Last-Modified ({store_t})")
    lag = (item_t - store_t).total_seconds()
    ev = [f"item updated {item_t.isoformat()}", f"store root zarr.json Last-Modified {store_t.isoformat()}"]
    if lag >= -300:
        return Result("RG07", "registration", PASS, "the item was registered after the store was written", ev)
    source = next((link["href"] for link in item.get("links", []) if link.get("rel") == "derived_from"), None)
    if source:
        try:
            r = http.get(source, bust=False)
            body = r.json() if r.status_code == 200 else {}
            sp = (body.get("properties") if isinstance(body, dict) else None) or {}
        except (httpx.HTTPError, ValueError) as exc:
            ev.append(f"source {source}: {type(exc).__name__}")
        else:
            ev.append(f"source {source}: HTTP {r.status_code}, created {sp.get('created')}, updated {sp.get('updated')}")
            if item_t in {_parse_time(sp.get("created")), _parse_time(sp.get("updated"))}:
                return Result("RG07", "registration", WARN, "the item's `updated` is copied from its source item (derived_from), so it can't date this registration; register_v1 doesn't stamp it", ev)
    return Result("RG07", "registration", FAIL, f"the item predates its store by {-lag / 60:.0f} min: this registration is stale", ev)


def _zoom_and_tile(http: Http, links: dict, footprint: dict, cfg: dict, item_id: str, expected=None):
    """The tile to render: the middle of the store's zoom range, else the item config's,
    else the tilejson link's (which carries the zoom workaround, 0–11, on OLCI)."""
    zooms = list(expected) if expected else (cfg.get("items", {}).get(item_id) or {}).get("zooms")
    if not zooms and "tilejson" in links:
        r = http.get(links["tilejson"])
        if r.status_code == 200:
            tj = r.json()
            zooms = [tj.get("minzoom"), tj.get("maxzoom")]
    lo, hi = zooms if zooms and None not in zooms else (8, 8)
    z = (lo + hi + 1) // 2
    return z, lonlat_to_tile(*geo.interior_point(footprint), z)


def _tilejson_problem(tj, expected, footprint) -> str | None:
    """A 200 tilejson can still be unusable: the 0.11 r0 form without the zoom workaround
    returns minzoom = maxzoom (W7, 7 Oct review)."""
    lo, hi, b = (tj.get("minzoom"), tj.get("maxzoom"), tj.get("bounds")) if isinstance(tj, dict) else (None, None, None)
    if not (isinstance(lo, int) and isinstance(hi, int) and lo <= hi):
        return f"200 but minzoom={lo!r} maxzoom={hi!r}"
    if expected and not (lo <= expected[0] and hi >= expected[1]):
        return f"200 but it advertises zooms {lo}–{hi}, which don't cover the store's {expected[0]}–{expected[1]}: a map built from it can't reach every level"
    if footprint and isinstance(b, list) and len(b) == 4 and not geo.contains(geo.bbox_polygon(b), *geo.interior_point(footprint)):
        return f"200 but its bounds {b} do not contain the item footprint"
    return None


def _check_link(http: Http, rel: str, href: str, ztile, expected=None, footprint=None) -> tuple[int, str]:
    z, (x, y) = ztile
    url = href.replace("{z}", str(z)).replace("{x}", str(x)).replace("{y}", str(y))
    r = http.get(url)
    if r.status_code != 200:
        return r.status_code, f"HTTP {r.status_code}"
    if rel in ("xyz", "thumbnail"):
        if "png" not in r.headers.get("content-type", ""):
            return 0, f"200 but {r.headers.get('content-type')!r}, not a PNG"
        st = png_stats(r.content)
        if st["valid"] == 0:
            return 0, "200 but no valid pixels"
        return 200, f"200, {st['valid']:.0%} valid"
    if rel == "tilejson":
        try:
            tj = r.json()
        except ValueError:
            return 0, "200 but not JSON"
        if problem := _tilejson_problem(tj, expected, footprint):
            return 0, problem
        return 200, f"200, zooms {tj['minzoom']}–{tj['maxzoom']}"
    return 200, "200"


def rg04_rg05_links(http: Http, item: dict, cfg: dict, oracle: dict | None = None) -> list[Result]:
    links = {link["rel"]: link["href"] for link in item.get("links", []) if link.get("rel") in LINK_RELS}
    thumb = (item.get("assets", {}).get("thumbnail") or {}).get("href")
    if thumb:
        links["thumbnail"] = thumb
    if not links:
        return [Result("RG04", "registration", FAIL, "the item has no viewer/tilejson/xyz links or thumbnail")]
    footprint = item.get("geometry") or geo.bbox_polygon(item["bbox"])
    expected = _expected_range(links, oracle)
    ztile = _zoom_and_tile(http, links, footprint, cfg, item["id"], expected)
    rows4, fails4, rows5, warn5 = [], [], [], []
    if expected:
        rows4.append(f"store zoom range for the linked group: {expected[0]}–{expected[1]}")
    flip = cfg.get("flip") or {}
    for rel, href in sorted(links.items()):
        code, note = _check_link(http, rel, href, ztile, expected, footprint)
        host = urlsplit(href).path.split("/")[1] if urlsplit(href).path else ""
        rows4.append(f"{rel} (/{host}): {note}")
        if code != 200:
            fails4.append(f"{rel}: {note}")
        if flip and flip["from"] in href:
            fcode, fnote = _check_link(http, rel, href.replace(flip["from"], flip["to"]), ztile, expected, footprint)
            rows5.append(f"{rel} on {flip['to']}: {fnote}")
            if code == 200 and fcode != 200:
                warn5.append(f"{rel} works on {flip['from']} but returns {fnote} on {flip['to']}: regenerate it before the flip")
    out = [Result("RG04", "registration", FAIL if fails4 else PASS, fails4[0] if fails4 else f"{len(links)} item links render (tile z{ztile[0]})",
                  fails4 + rows4, problems=fails4)]
    if flip:
        out.append(Result("RG05", "registration", WARN if warn5 else PASS,
                          warn5[0] if warn5 else f"every link also works on {flip['to']}", warn5 + rows5))
    return out
