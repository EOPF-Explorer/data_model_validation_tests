"""The titiler battery against a fake server: each check fails when it should."""

import datetime as dt

import pytest

from eopf_accept.budget import Budget, Http
from eopf_accept.model import FAIL, KNOWN, PASS, VOID, WARN, XFAIL, XPASS, apply_known_issues
from eopf_accept.titiler_checks import TitilerBattery, fit_zoom, urls

from .fake_titiler import State, serve
from .geozarr_fixture import CFG

ITEM = "S3B_TEST"
RSTAGING = {"api": "0.12", "expect_version": "0.11.0"}  # 0.12 code that reports 0.11.0
RASTER = {"api": "0.11", "expect_version": "0.11.0"}  # the fake reports 0.11.0 for both
# zooms.oracle for an OLCI-shaped store whose multiscales give 5–9 (the fake tilejson's default)
ORACLE = {"measurements": {"levels": [("measurements/r0", 9), ("measurements/r2", 8), ("measurements/r4", 7),
                                      ("measurements/r8", 6), ("measurements/r16", 5)], "range": (5, 9)}}


@pytest.fixture
def server():
    state = State()
    srv, base = serve(state)
    yield state, base
    srv.shutdown()


def battery(base, ep=RSTAGING, cfg=CFG, budget=100, footprint=None, oracle=ORACLE):
    http = Http(Budget(budget))
    return TitilerBattery(http, "fake", {"base": base, **ep}, cfg, ITEM, footprint=footprint, zoom_oracle=oracle)


def by_id(results):
    return {r.id: r for r in results}


def test_healthy_endpoint_passes(server):
    state, base = server
    res = by_id(battery(base).run())
    for cid in ("TI00", "TI01", "TI02", "TI03", "TI04", "TI06", "TI05", "TI09"):
        assert res[cid].status == PASS, (cid, res[cid].summary, res[cid].evidence)


def test_every_request_is_cache_busted_uniquely(server):
    state, base = server
    battery(base).run()
    busters = [p.split("cb=")[1] for p in state.requests]
    assert len(busters) == len(state.requests) == len(set(busters))


def test_version_guard_is_fail_closed(server):
    state, base = server
    state.version = ""
    res = battery(base).run()
    assert [r.id for r in res] == ["TI00"] and res[0].status == FAIL
    state.version = "0.12.0"  # config expects 0.11.0
    assert battery(base).run()[0].status == FAIL


def test_version_guard_fingerprints_the_routes(server):
    """A 0.11 deployment that happens to report the expected string must still be refused."""
    state, base = server
    state.api = "0.11"
    res = battery(base).run()
    assert len(res) == 1 and res[0].status == FAIL and "routes look like the 0.11 API" in res[0].summary


def test_zoom_mismatch_shows_the_zooms_and_a_known_issue_is_scoped_to_its_endpoint(server):
    """6 Oct: /raster (0.11.1) gave zooms 9–9 without the zoom workaround; /rstaging gave 5–9.
    A known issue for titiler:raster must not hide the same failure on another endpoint."""
    state, base = server
    cfg = {**CFG, "items": {ITEM: {"zooms": [4, 9]}}}  # the fake tilejson says 5–9
    results = [
        TitilerBattery(Http(Budget(100)), name, {"base": base, **RSTAGING}, cfg, ITEM).ti02_tilejson()
        for name in ("raster", "rstaging")
    ]
    assert all(r.status == FAIL and "minzoom=5 maxzoom=9" in r.evidence[0] for r in results)
    known = [{"check": "TI02", "group": "titiler:raster", "match": "zooms 5–9", "ref": "x", "until": "2026-12-31"}]
    apply_known_issues(results, known, dt.date(2026, 10, 6))
    assert [r.status for r in results] == [KNOWN, FAIL]
    # a new problem reported next to the known one keeps the result a FAIL
    state.bounds = [-36.0, 34.0, -36.0, 36.0]  # west == east
    both = TitilerBattery(Http(Budget(100)), "raster", {"base": base, **RSTAGING}, cfg, ITEM).ti02_tilejson()
    assert "bounds=" in both.summary and "zooms 5–9" in both.summary
    apply_known_issues([both], known, dt.date(2026, 10, 6))
    assert both.status == FAIL

def test_issue1_r0_collapse_fails_with_its_mechanism_without_any_item_config(server):
    """7 Oct: the same /raster collapse read KNOWN, PASS or FAIL depending on per-item
    config. The store now gives every item an expectation; the r0 link form gets a text
    that names the cause (the level group), whatever its value (9–9 or 7–7)."""
    state, base = server
    state.api = "0.11"
    state.zooms = (9, 9)  # r0's own zoom, as /raster returned for the canary and S3B 073033
    res = by_id(battery(base, ep=RASTER).run())  # CFG has no [items] entry: the S3B 073033 case
    assert res["TI02"].status == FAIL
    assert "the render reads level group measurements/r0, not the multiscales group measurements (the store gives 5–9)" in res["TI02"].summary
    # E: a usable tilejson still feeds TI03 (one tile per level), TI04 and TI09
    assert res["TI03"].status != "SKIP" and res["TI09"].status != "SKIP" and res["TI04"].status != "SKIP"
    tiles = sorted({int(p.split("/tiles/WebMercatorQuad/")[1].split("/")[0]) for p in state.requests if "/tiles/" in p})
    assert tiles == [5, 6, 7, 8, 9], tiles


def test_issue1_other_mismatches_get_the_generic_text(server):
    """8–8 when r0 sits at 9 is not the r0 mechanism; neither is 9–9 on the asset route."""
    state, base = server
    state.api, state.zooms = "0.11", (8, 8)
    assert "the store's multiscales give 5–9" in by_id(battery(base, ep=RASTER).run())["TI02"].summary
    state.api, state.zooms = "0.12", (9, 9)
    r = by_id(battery(base).run())["TI02"]
    assert r.status == FAIL and "zooms 9–9, the store's multiscales give 5–9" in r.summary, r.summary


def test_issue1_anchor_and_store_must_agree(server):
    """The per-item zooms stay as an anchor that doesn't come from the store's transforms."""
    state, base = server
    cfg = {**CFG, "items": {ITEM: {"zooms": [5, 9]}}}
    assert by_id(battery(base, cfg=cfg).run())["TI02"].status == PASS
    oracle = {"measurements": {**ORACLE["measurements"], "range": (5, 8)}}
    r = by_id(battery(base, cfg=cfg, oracle=oracle).run())["TI02"]
    assert r.status == FAIL and "the store gives 5–8 but the item config anchors 5–9" in r.summary, r.summary


def test_issue1_unchecked_zooms_warn_instead_of_passing(server):
    """No store metadata, or the 0.12 STAC item route (S2): never a silent PASS."""
    state, base = server
    assert by_id(battery(base, oracle=None).run())["TI02"].status == WARN
    cfg = {**CFG, "render": {**CFG["render"], "0.12": {"route": "item", "asset": "radianceData"}}}
    r = by_id(battery(base, cfg=cfg).run())["TI02"]
    assert r.status == WARN and any("rio-tiler's STAC reader" in e for e in r.evidence), r.evidence


def test_item_render_extra_reaches_every_render(server):
    """An S1 cube renders one time slice: the item config's `sel` must ride on every titiler request."""
    state, base = server
    cfg = {**CFG, "items": {ITEM: {"render_extra": {"sel": "time=2026-07-07T17:05:31"}}}}
    TitilerBattery(Http(Budget(100)), "fake", {"base": base, **RSTAGING}, cfg, ITEM).run()
    renders = [p for p in state.requests if "/tiles/" in p or "tilejson.json" in p]
    assert renders and all("sel=time%3D2026-07-07T17%3A05%3A31" in p for p in renders), renders[:2]


def test_c1_tilejson_500_fails_ti02(server):
    state, base = server
    state.tilejson_status = 500
    res = by_id(battery(base).run())
    assert res["TI02"].status == FAIL and "500" in res["TI02"].summary


def test_empty_tiles_fail_ti03(server):
    state, base = server
    state.tile = "empty"
    assert by_id(battery(base).run())["TI03"].status == FAIL


def test_w10_a_collapsed_range_gets_no_minzoom_exemption(server):
    """7 Oct review (W10): with minzoom == maxzoom the only tile is the minzoom tile, and the
    exemption ("not all nodata" is enough) let a 5 % valid tile pass inside the footprint."""
    state, base = server
    state.tile = "sparse"
    assert by_id(battery(base).run())["TI03"].status == FAIL  # z7/z9 need 25 %
    state.zooms = (9, 9)
    assert by_id(battery(base).run())["TI03"].status == FAIL


def test_partial_footprint_does_not_false_fail(server):
    """A tile mostly outside a small footprint needs only its share of valid pixels."""
    state, base = server
    corner = {"type": "Polygon", "coordinates": [[[-36, 35.6], [-35.6, 35.6], [-35.6, 36], [-36, 36], [-36, 35.6]]]}
    res = by_id(battery(base, footprint=corner).run())
    assert res["TI03"].status == PASS, res["TI03"].evidence


def test_identical_rgb_channels_fail_ti04(server):
    state, base = server
    state.tile = "gray"
    assert by_id(battery(base).run())["TI04"].status == FAIL


def test_cache_hit_voids_the_run(server):
    state, base = server
    state.x_cache = "HIT"
    assert by_id(battery(base).run())["TI05"].status == VOID


def test_contract_matrix_maps_every_mismatch_to_fail(server):
    state, base = server
    cfg = dict(CFG)
    cfg["contract"] = [
        {"api": "0.12", "name": "ok", "path": "assets/radianceData/WebMercatorQuad/tilejson.json", "params": [["variables", "oa08_radiance"]], "expect": 200},
        {"api": "0.12", "name": "0.11 syntax", "path": "WebMercatorQuad/tilejson.json", "params": [["variables", "/measurements/r0:oa08_radiance"]], "expect": 422},
        {"api": "0.12", "name": "documented 500, now 200", "path": "assets/radianceData/WebMercatorQuad/tilejson.json", "params": [["variables", "oa08_radiance"]], "expect": 500},
    ]
    r = by_id(battery(base, cfg=cfg).run())["TI07"]
    assert [e.split(":")[0] for e in r.evidence] == [PASS, XFAIL, XPASS] and r.status == XPASS
    # a documented 200 form that regresses to a 4xx is a FAIL, not a warning
    cfg["contract"] = [{"api": "0.12", "name": "regressed", "path": "WebMercatorQuad/tilejson.json", "params": [["variables", "/x:y"]], "expect": 200}]
    assert by_id(battery(base, cfg=cfg).run())["TI07"].status == FAIL


def test_url_forms_per_api():
    prefix, params = urls("B", "c", "i", CFG["render"], "0.11")
    assert prefix == "B/collections/c/items/i"
    assert ("variables", "/measurements/r0:oa08_radiance") in params and ("bidx", "1") in params
    prefix, params = urls("B", "c", "i", CFG["render"], "0.12", 1)
    assert prefix == "B/collections/c/items/i/assets/radianceData"
    assert params == [("variables", "oa08_radiance"), ("rescale", "10,300")]
    render = {"variables": ["b04", "b03", "b02"], "0.12": {"route": "item", "asset": "reflectance"}}
    assert urls("B", "c", "i", render, "0.12")[1] == [("assets", "reflectance|bands=b04,b03,b02")]


def test_c13_fit_zoom_below_minzoom_warns(server):
    """The S3A 76.6°N item: a wide high-latitude swath fits below minzoom, so map.html opens blank."""
    state, base = server
    assert by_id(battery(base).run())["TI09"].status == PASS
    state.bounds = [-60.0, 60.0, 10.0, 85.0]
    assert fit_zoom(state.bounds) == 3
    assert by_id(battery(base).run())["TI09"].status == WARN
