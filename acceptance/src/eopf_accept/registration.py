"""Registration checks (RG*): does the registered STAC item point at this store and render?

Registered stage only. Reads the item once from the STAC API, then its own links.
"""

import datetime as dt
from urllib.parse import urlsplit

import httpx

from . import geometry as geo
from .budget import Http
from .model import FAIL, PASS, SKIP, WARN, Result
from .storeio import StoreReader
from .titiler_checks import lonlat_to_tile, png_stats

LINK_RELS = ("viewer", "tilejson", "xyz")
REQUEST_BOUND = 13  # item + 3 links + thumbnail, each once more for RG05, RG07's source item, plus slack


def fetch_item(http: Http, stac: str, collection: str, item: str) -> dict:
    r = http.get(f"{stac.rstrip('/')}/collections/{collection}/items/{item}", bust=False)
    r.raise_for_status()
    return r.json()


def _parse_time(value) -> dt.datetime | None:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


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


def rg08_hrefs(item: dict, store: str, cfg: dict) -> Result:
    """Each configured asset points exactly at its group of THIS store (C9: a failed
    registration leaves the previous item, whose hrefs still point at the old layout)."""
    root = bucket_key(store)
    fails, rows = [], []
    store_link = next((link["href"] for link in item.get("links", []) if link.get("rel") == "store"), None)
    if store_link and bucket_key(store_link) != root:
        fails.append(f"item `store` link {store_link} is not the store under test {store}")
    for asset, group in (cfg.get("asset_group") or {}).items():
        href = (item.get("assets", {}).get(asset) or {}).get("href")
        want = f"{root}/{group}"
        if href is None:
            fails.append(f"asset {asset!r} missing")
        elif bucket_key(href) != want:
            fails.append(f"asset {asset!r} → {href}, want {want}")
        else:
            rows.append(f"asset {asset!r} → {href}")
    if not cfg.get("asset_group"):
        return Result("RG08", "registration", SKIP, "no asset_group in the config")
    return Result("RG08", "registration", FAIL if fails else PASS, fails[0] if fails else "asset hrefs point at this store's groups", fails + rows)


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
        except httpx.HTTPError as exc:
            ev.append(f"source {source}: {type(exc).__name__}")
        else:
            sp = r.json().get("properties", {}) if r.status_code == 200 else {}
            ev.append(f"source {source}: HTTP {r.status_code}, created {sp.get('created')}, updated {sp.get('updated')}")
            if item_t in {_parse_time(sp.get("created")), _parse_time(sp.get("updated"))}:
                return Result("RG07", "registration", WARN, "the item's `updated` is copied from its source item (derived_from), so it can't date this registration; register_v1 doesn't stamp it", ev)
    return Result("RG07", "registration", FAIL, f"the item predates its store by {-lag / 60:.0f} min: this registration is stale", ev)


def _zoom_and_tile(http: Http, links: dict, footprint: dict, cfg: dict, item_id: str):
    zooms = (cfg.get("items", {}).get(item_id) or {}).get("zooms")
    if not zooms and "tilejson" in links:
        r = http.get(links["tilejson"])
        if r.status_code == 200:
            tj = r.json()
            zooms = [tj.get("minzoom"), tj.get("maxzoom")]
    lo, hi = zooms or (8, 8)
    z = (lo + hi + 1) // 2
    return z, lonlat_to_tile(*geo.interior_point(footprint), z)


def _check_link(http: Http, rel: str, href: str, ztile) -> tuple[int, str]:
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
    return 200, "200"


def rg04_rg05_links(http: Http, item: dict, cfg: dict) -> list[Result]:
    links = {link["rel"]: link["href"] for link in item.get("links", []) if link.get("rel") in LINK_RELS}
    thumb = (item.get("assets", {}).get("thumbnail") or {}).get("href")
    if thumb:
        links["thumbnail"] = thumb
    if not links:
        return [Result("RG04", "registration", FAIL, "the item has no viewer/tilejson/xyz links or thumbnail")]
    footprint = item.get("geometry") or geo.bbox_polygon(item["bbox"])
    ztile = _zoom_and_tile(http, links, footprint, cfg, item["id"])
    rows4, fails4, rows5, warn5 = [], [], [], []
    flip = cfg.get("flip") or {}
    for rel, href in sorted(links.items()):
        code, note = _check_link(http, rel, href, ztile)
        host = urlsplit(href).path.split("/")[1] if urlsplit(href).path else ""
        rows4.append(f"{rel} (/{host}): {note}")
        if code != 200:
            fails4.append(f"{rel}: {note}")
        if flip and flip["from"] in href:
            fcode, fnote = _check_link(http, rel, href.replace(flip["from"], flip["to"]), ztile)
            rows5.append(f"{rel} on {flip['to']}: {fnote}")
            if code == 200 and fcode != 200:
                warn5.append(f"{rel} works on {flip['from']} but returns {fnote} on {flip['to']}: regenerate it before the flip")
    out = [Result("RG04", "registration", FAIL if fails4 else PASS, fails4[0] if fails4 else f"{len(links)} item links render (tile z{ztile[0]})", fails4 + rows4)]
    if flip:
        out.append(Result("RG05", "registration", WARN if warn5 else PASS,
                          warn5[0] if warn5 else f"every link also works on {flip['to']}", warn5 + rows5))
    return out
