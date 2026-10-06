"""Registration checks against a fake STAC item and fake titiler links."""

import datetime as dt

import pytest

from eopf_accept import registration as rg
from eopf_accept.budget import Budget, Http
from eopf_accept.model import FAIL, PASS, WARN
from eopf_accept.storeio import StoreReader

from .fake_titiler import State, serve
from .geozarr_fixture import CFG, build

CFG_RG = {**CFG, "asset_group": {"radianceData": "measurements"}, "flip": {"from": "/raster/", "to": "/rstaging/"}}


def item_for(base: str, store: str, updated: str, href_group: str = "measurements") -> dict:
    q = "variables=%2Fmeasurements%2Fr0%3Aoa08_radiance&rescale=10,300&bidx=1"
    raster = f"{base}/raster/collections/c/items/I"
    return {
        "type": "Feature", "id": "I", "collection": "c", "bbox": [-36, 34, -34, 36],
        "geometry": {"type": "Polygon", "coordinates": [[[-36, 34], [-34, 34], [-34, 36], [-36, 36], [-36, 34]]]},
        "properties": {"updated": updated},
        "assets": {"radianceData": {"href": f"{store}/{href_group}"}, "thumbnail": {"href": f"{raster}/preview.png?{q}"}},
        "links": [
            {"rel": "store", "href": store},
            {"rel": "viewer", "href": f"{raster}/viewer"},
            {"rel": "tilejson", "href": f"{raster}/WebMercatorQuad/tilejson.json?{q}"},
            {"rel": "xyz", "href": f"{raster}/tiles/WebMercatorQuad/{{z}}/{{x}}/{{y}}.png?{q}"},
        ],
    }


@pytest.fixture
def server():
    state = State()
    state.api = "0.11"  # the links are /raster (0.11) links
    srv, base = serve(state)
    yield state, base
    srv.shutdown()


def now_iso(minutes=0):
    return (dt.datetime.now(dt.UTC) + dt.timedelta(minutes=minutes)).isoformat()


def test_links_render_and_flip_readiness_is_reported(server, tmp_path):
    state, base = server
    store = build(tmp_path / "s.zarr")
    item = item_for(base, store, now_iso(5))
    res = {r.id: r for r in rg.rg04_rg05_links(Http(Budget(50)), item, CFG_RG)}
    assert res["RG04"].status == PASS, res["RG04"].evidence
    # the 0.11-syntax tilejson/xyz/thumbnail links 422 on /rstaging: regenerate before the flip
    assert res["RG05"].status == WARN and "regenerate it before the flip" in res["RG05"].summary
    state.flipped_status = 200
    res = {r.id: r for r in rg.rg04_rg05_links(Http(Budget(50)), item, CFG_RG)}
    assert res["RG05"].status == PASS


def test_link_query_reaches_the_server(server, tmp_path):
    state, base = server
    store = build(tmp_path / "s.zarr")
    rg.rg04_rg05_links(Http(Budget(50)), item_for(base, store, now_iso(5)), CFG_RG)
    xyz = [p for p in state.requests if "/tiles/" in p and p.startswith("/raster/")]
    assert xyz and all("variables=%2Fmeasurements%2Fr0%3Aoa08_radiance" in p and "bidx=1" in p for p in xyz)


def test_c9_stale_registration_fails_rg07_and_rg08(server, tmp_path):
    """6 Oct canary run 1: the store was rewritten, registration failed, and the old item
    (registered earlier, radianceData still on measurements/r0) stayed in the catalogue."""
    state, base = server
    store = build(tmp_path / "s.zarr")
    stale = item_for(base, store, "2026-10-01T00:00:00+00:00", href_group="measurements/r0")
    assert rg.rg08_hrefs(stale, store, CFG_RG).status == FAIL
    assert rg.rg07_fresh(stale, StoreReader(store, Budget(5)), Http(Budget(5))).status == FAIL
    fresh = item_for(base, store, now_iso(5))
    assert rg.rg08_hrefs(fresh, store, CFG_RG).status == PASS
    assert rg.rg07_fresh(fresh, StoreReader(store, Budget(5)), Http(Budget(5))).status == PASS


def test_rg07_warns_when_the_item_keeps_its_source_items_time(server, tmp_path):
    """6 Oct first prod run: register_v1 keeps the source item's `updated`, so an item
    re-registered after its store looked stale. Its derived_from source has the same time."""
    state, base = server
    store = build(tmp_path / "s.zarr")
    src_t = "2026-07-28T15:40:15.158141Z"
    item = item_for(base, store, src_t)
    item["links"].append({"rel": "derived_from", "href": f"{base}/stac/collections/src/items/I"})
    state.item = {"properties": {"created": src_t, "updated": src_t}}
    res = rg.rg07_fresh(item, StoreReader(store, Budget(5)), Http(Budget(5)))
    assert res.status == WARN and "copied from its source" in res.summary, res.summary
    # a source with another time doesn't explain the item's: still stale
    state.item = {"properties": {"created": "2026-07-28T15:00:00Z", "updated": "2026-07-28T15:00:00Z"}}
    assert rg.rg07_fresh(item, StoreReader(store, Budget(5)), Http(Budget(5))).status == FAIL
    # a source whose JSON isn't an object (review): still stale, no crash
    state.item = ["not", "an", "item"]
    assert rg.rg07_fresh(item, StoreReader(store, Budget(5)), Http(Budget(5))).status == FAIL

def test_rg08_matches_the_s3_origin_against_gateway_hrefs():
    """D8 says pass the s3:// origin; the item's hrefs are gateway https URLs. Same store."""
    gw = "https://s3.explorer.eopf.copernicus.eu/esa-zarr-sentinel-explorer-fra/tests-output/c/I.zarr"
    item = {"links": [{"rel": "store", "href": gw}], "assets": {"radianceData": {"href": f"{gw}/measurements"}}}
    origin = "s3://esa-zarr-sentinel-explorer-fra/tests-output/c/I.zarr"
    assert rg.rg08_hrefs(item, origin, CFG_RG).status == PASS
    assert rg.rg08_hrefs(item, origin.replace("I.zarr", "OTHER.zarr"), CFG_RG).status == FAIL


def test_rg07_survives_a_naive_time_and_a_source_that_is_not_json(server, tmp_path):
    """Review: a time without an offset raised TypeError; a 200 HTML source raised ValueError."""
    state, base = server
    store = build(tmp_path / "s.zarr")
    item = item_for(base, store, "2026-07-28T15:40:15")  # no offset: read as UTC
    item["links"].append({"rel": "derived_from", "href": f"{base}/src/map.html"})  # 200 text/html
    res = rg.rg07_fresh(item, StoreReader(store, Budget(5)), Http(Budget(5)))
    assert res.status == FAIL and any("JSONDecodeError" in e for e in res.evidence), res.evidence


def test_link_zoom_falls_back_when_the_tilejson_has_no_zooms(server):
    """Review: zooms = [None, None] is truthy, so `None + None` crashed RG04."""
    state, base = server
    state.item = {"tilejson": "3.0.0"}  # served as JSON by the fake STAC route
    footprint = {"type": "Polygon", "coordinates": [[[-36, 34], [-34, 34], [-34, 36], [-36, 36], [-36, 34]]]}
    z, _ = rg._zoom_and_tile(Http(Budget(5)), {"tilejson": f"{base}/stac/collections/tj"}, footprint, {}, "I")
    assert z == 8
