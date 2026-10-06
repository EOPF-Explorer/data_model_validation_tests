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


@pytest.fixture
def server():
    state = State()
    srv, base = serve(state)
    yield state, base
    srv.shutdown()


def battery(base, ep=RSTAGING, cfg=CFG, budget=100, footprint=None):
    http = Http(Budget(budget))
    return TitilerBattery(http, "fake", {"base": base, **ep}, cfg, ITEM, footprint=footprint)


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

def test_c1_tilejson_500_fails_ti02(server):
    state, base = server
    state.tilejson_status = 500
    res = by_id(battery(base).run())
    assert res["TI02"].status == FAIL and "500" in res["TI02"].summary


def test_empty_tiles_fail_ti03(server):
    state, base = server
    state.tile = "empty"
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
