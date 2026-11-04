"""The titiler battery against a fake server: each check fails when it should."""

import datetime as dt
import io
import struct
import tomllib
import zlib

import numpy as np
import pytest
from PIL import Image

from eopf_accept import cli, geometry as geo
from eopf_accept.budget import Budget, Http
from eopf_accept.cli import CONFIG_DIR
from eopf_accept.model import FAIL, KNOWN, PASS, VOID, WARN, XFAIL, XPASS, Result, apply_known_issues
from eopf_accept.titiler_checks import TitilerBattery, decoded_stats, extent_px, lonlat_to_tile, png_stats, urls

from .fake_titiler import VERSIONS, State, serve
from .geozarr_fixture import CFG

ITEM = "S3B_TEST"
RSTAGING = {"api": "0.12", "expect_version": VERSIONS["0.12"]}
RASTER = {"api": "0.11", "expect_version": VERSIONS["0.11"]}
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
    state.version = "0.0.0"  # not what the config expects
    assert battery(base).run()[0].status == FAIL


def test_version_guard_fingerprints_the_routes(server):
    """A 0.11 deployment that happens to report the expected string must still be refused."""
    state, base = server
    state.api, state.version = "0.11", RSTAGING["expect_version"]
    res = battery(base).run()
    assert len(res) == 1 and res[0].status == FAIL and res[0].summary == "routes look like the 0.11 API, config says 0.12"


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
    # every one of those tiles reads r0: the summary must not claim each level was read
    assert "all read from level group measurements/r0" in res["TI03"].summary, res["TI03"].summary


def test_r0_collapse_is_one_problem_and_the_olci_known_issue_matches_it_on_raster_only(server):
    """7 Oct, option (b): the r0 collapse stays on /raster until the titiler-eopf >0.12 flip.
    With an item anchor equal to the store's range (canary 5–9, S3A 4–7), the anchor
    comparison repeated the store comparison, so a known issue could never match every
    problem. The real OLCI config's entry must turn exactly this into KNOWN, on /raster only,
    and only until its date."""
    state, base = server
    state.api, state.zooms = "0.11", (9, 9)
    cfg = {**CFG, "items": {ITEM: {"zooms": [5, 9]}}}
    raster = TitilerBattery(Http(Budget(100)), "raster", {"base": base, **RASTER}, cfg, ITEM, zoom_oracle=ORACLE).ti02_tilejson()
    assert raster.status == FAIL and len(raster.problems) == 1, raster.problems
    assert "the render reads level group measurements/r0" in raster.problems[0]
    olci = tomllib.loads((CONFIG_DIR / "sentinel-3-olci-l1-efr.toml").read_text())
    known = [k for k in olci["known_issues"] if k["check"] == "TI02"]
    assert [k["group"] for k in known] == ["titiler:raster"]
    until = dt.date.fromisoformat(str(known[0]["until"]))
    elsewhere = Result("TI02", "titiler:rstaging", FAIL, raster.summary, problems=list(raster.problems))
    expired = Result("TI02", "titiler:raster", FAIL, raster.summary, problems=list(raster.problems))
    apply_known_issues([raster, elsewhere], known, until)
    apply_known_issues([expired], known, until + dt.timedelta(days=1))
    assert (raster.status, elsewhere.status, expired.status) == (KNOWN, FAIL, FAIL)


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


def test_ti03_reads_the_stores_levels_on_the_item_route(server):
    """The item route's tilejson says 0–24; z0 and z24 say nothing about the store (T33WWP, 3 Nov)."""
    state, base = server
    state.zooms = (0, 24)
    cfg = {**CFG, "render": {**CFG["render"], "0.12": {"route": "item", "asset": "radianceData"}}}
    b = battery(base, cfg=cfg)
    assert b.ti02_tilejson().status == WARN  # TI02 still doesn't judge the item route's range
    state.requests.clear()
    r = b.ti03_tiles()
    assert {int(p.split("/tiles/")[1].split("/")[1]) for p in state.requests} == {5, 6, 7, 8, 9}
    assert r.status == PASS and "one per pyramid level" in r.summary, r.summary


FOOTPRINT = geo.bbox_polygon([-36.0, 34.0, -34.0, 36.0])


CENTRE = lonlat_to_tile(*geo.interior_point(FOOTPRINT), 9)
ELSEWHERE = [lonlat_to_tile(*p, 9) for p in geo.spread_points(FOOTPRINT)]  # z9 tiles a flat one is compared with


def z9(*tiles, mode="flat"):
    return {(9, x, y): mode for x, y in tiles}


def test_ti03_a_flat_patch_is_not_a_constant_render(server):
    """T33WWP's centre is bright (cloud or snow) at z13 and z12 (1 and 15 distinct values); the rest has over 150."""
    state, base = server
    state.tile_at = z9(CENTRE)
    r = by_id(battery(base, footprint=FOOTPRINT).run())["TI03"]
    assert r.status == PASS, r.evidence
    assert any(e.startswith(f"z9 {CENTRE[0]}/{CENTRE[1]}: a flat patch") for e in r.evidence), r.evidence
    assert "flat at the centre at z9, varied elsewhere" in r.summary, r.summary  # never looks like a clean pass


def test_ti03_compares_a_flat_tile_with_every_spread_tile(server):
    state, base = server
    state.tile_at = z9(CENTRE, *ELSEWHERE[:3])
    assert by_id(battery(base, footprint=FOOTPRINT).run())["TI03"].status == PASS


def test_ti03_a_render_flat_everywhere_fails_and_says_what_it_compared(server):
    state, base = server
    state.tile = "flat"
    b = battery(base, footprint=FOOTPRINT)
    b.ti02_tilejson()
    state.requests.clear()
    r = b.ti03_tiles()
    assert r.status == FAIL
    assert f"z9 {CENTRE[0]}/{CENTRE[1]}: only 1 distinct values, 100% of valid pixels at PNG value 200, and none of the 4 other tile(s) tried varies with enough valid pixels (a constant render, or values outside or too narrow for the stretch?)" in r.problems, r.problems
    # at z5 the whole footprint is one tile: nothing was compared, and the report says so
    assert any(p.startswith("z5 ") and "no other sample point of the footprint falls in another tile" in p for p in r.problems), r.problems
    assert sum("/tiles/WebMercatorQuad/5/" in p for p in state.requests) == 1  # the flat tile isn't asked for twice


def test_png_stats_reports_the_commonest_value_and_its_share():
    a = np.zeros((10, 10), np.uint8)
    a[:7] = 255  # 70 pixels at 255, 30 at 0
    buf = io.BytesIO()
    Image.fromarray(a).save(buf, "PNG")
    st = png_stats(buf.getvalue())
    assert (st["modal"], round(st["modal_share"], 2)) == (255, 0.7)


def test_ti03_with_nothing_to_compare_says_where_the_pixels_sit(server):
    state, base = server
    state.tile = "flat"
    b = battery(base, footprint=FOOTPRINT)
    b.ti02_tilejson()
    z5 = next(p for p in b.ti03_tiles().problems if p.startswith("z5 "))
    assert "100% of valid pixels at PNG value 200" in z5 and "too narrow for the stretch" in z5, z5


L2A = tomllib.loads((CONFIG_DIR / "sentinel-2-l2a.toml").read_text())


def test_l2a_threshold_lets_dark_water_clear_a_flat_tile_but_not_a_flat_render(server):
    """T27SVB, a healthy ocean scene, has 11 and 10 distinct values on its best tiles at 0,1."""
    state, base = server
    cfg = {**CFG, "min_distinct": L2A["min_distinct"]}
    state.tile_at = z9(CENTRE) | z9(*ELSEWHERE, mode="dark")
    assert by_id(battery(base, cfg=cfg, footprint=FOOTPRINT).run())["TI03"].status == PASS
    state.tile_at, state.tile = {}, "flat"
    assert by_id(battery(base, cfg=cfg, footprint=FOOTPRINT).run())["TI03"].status == FAIL


EXPLORER = {"name": "the Explorer's true colour", "rescale": "0,0.5", "min_distinct": 10, "api": "0.12"}
SECOND = {**CFG, "render": {**CFG["render"], "second_stretch": EXPLORER}}


def at_second_stretch(*tiles, mode="flat"):
    return {(9, x, y, "0,0.5"): mode for x, y in tiles}


def test_ti10_runs_only_where_a_config_names_a_second_stretch(server):
    state, base = server
    assert "TI10" not in by_id(battery(base, footprint=FOOTPRINT).run())
    state.requests.clear()
    r = by_id(battery(base, cfg=SECOND, footprint=FOOTPRINT).run())["TI10"]
    assert r.status == PASS and "the Explorer's true colour" in r.summary and "0,0.5" in r.summary, r.summary
    assert any("rescale=0%2C0.5" in p or "rescale=0,0.5" in p for p in state.requests), state.requests[:3]


def test_ti10_warns_where_the_second_stretch_clips_and_ti03_is_unaffected(server):
    """T25WFP, 100 % cloud: varied at the links' 0,1, flat at z13 under the Explorer's 0,0.5."""
    state, base = server
    state.tile_at = at_second_stretch(CENTRE, ELSEWHERE[0])
    res = by_id(battery(base, cfg=SECOND, footprint=FOOTPRINT).run())
    assert res["TI10"].status == WARN and "z9" in res["TI10"].summary and "PNG value 200" in res["TI10"].summary, res["TI10"].summary
    assert res["TI10"].problems == []
    assert res["TI03"].status == PASS and "flat" not in res["TI03"].summary, res["TI03"].summary


def test_ti10_a_flat_patch_is_not_a_warning(server):
    state, base = server
    state.tile_at = at_second_stretch(CENTRE)
    r = by_id(battery(base, cfg=SECOND, footprint=FOOTPRINT).run())["TI10"]
    assert r.status == PASS and "flat at the centre at z9" in r.summary, r.summary


def test_ti10_judges_flatness_with_its_own_threshold(server):
    state, base = server
    state.tile_at = at_second_stretch(CENTRE, *ELSEWHERE, mode="dark")  # 11 distinct: clears 10, not 16
    assert by_id(battery(base, cfg=SECOND, footprint=FOOTPRINT).run())["TI10"].status == PASS
    strict = {**CFG, "render": {**CFG["render"], "second_stretch": {**EXPLORER, "min_distinct": 16}}}
    assert by_id(battery(base, cfg=strict, footprint=FOOTPRINT).run())["TI10"].status == WARN


def test_ti10_doesnt_call_a_tile_with_no_valid_pixels_a_flat_colour(server):
    """Nodata isn't a colour a viewer shows, and TI03 already judges valid pixels."""
    state, base = server
    state.tile_at = at_second_stretch(CENTRE, ELSEWHERE[0], mode="empty")
    r = by_id(battery(base, cfg=SECOND, footprint=FOOTPRINT).run())["TI10"]
    assert r.status == PASS and "no valid pixels at z9" in r.summary and "every level varies" not in r.summary, r.summary


def test_ti10_skips_when_no_level_has_valid_pixels(server):
    state, base = server
    b = battery(base, cfg=SECOND, footprint=FOOTPRINT)
    b.ti02_tilejson()
    state.tile = "empty"
    assert b.ti10_second_stretch().status == "SKIP"


def test_ti10_runs_on_its_configured_api_only(server):
    """Whether this data clips at this stretch doesn't depend on the titiler version."""
    state, base = server
    state.api = "0.11"
    res = by_id(battery(base, ep=RASTER, cfg=SECOND, footprint=FOOTPRINT).run())
    assert "TI03" in res and "TI10" not in res, sorted(res)
    assert TitilerBattery.request_bound(SECOND, "0.12") - TitilerBattery.request_bound(SECOND, "0.11") == 8 * 2


def test_ti10_sends_its_stretch_even_if_the_render_has_none(server):
    state, base = server
    render = {k: v for k, v in SECOND["render"].items() if k != "rescale"}
    b = battery(base, cfg={**CFG, "render": render}, footprint=FOOTPRINT)
    b.ti02_tilejson()
    state.requests.clear()
    b.ti10_second_stretch()
    tiles = [p for p in state.requests if "/tiles/" in p]
    assert tiles and all("rescale=0" in p for p in tiles), tiles[:2]


def test_decoded_stats_leaves_the_suites_own_bugs_visible(monkeypatch):
    """Only decoding is excused: a bug in the statistics must not read as a bad server response."""
    buf = io.BytesIO()
    Image.fromarray(np.zeros((4, 4), np.uint8)).save(buf, "PNG")
    assert decoded_stats(buf.getvalue())["distinct"] == 1
    monkeypatch.setattr(np, "unique", lambda *a, **k: 1 / 0)
    with pytest.raises(ZeroDivisionError):
        decoded_stats(buf.getvalue())


def test_ti10_a_tile_it_cant_read_fails(server):
    state, base = server
    state.tile_at = at_second_stretch(CENTRE, mode="error")
    r = by_id(battery(base, cfg=SECOND, footprint=FOOTPRINT).run())["TI10"]
    assert r.status == FAIL and "HTTP 500" in r.summary and r.problems, r.summary
    state.tile_at = at_second_stretch(CENTRE) | at_second_stretch(ELSEWHERE[0], mode="error")
    r = by_id(battery(base, cfg=SECOND, footprint=FOOTPRINT).run())["TI10"]
    assert r.status == FAIL and "HTTP 500" in r.summary, r.summary


def test_ti10_compares_a_flat_tile_with_one_other_tile_only(server):
    """A WARN doesn't justify a level's worst case of four more renders on the shared cache."""
    state, base = server
    state.tile_at = at_second_stretch(CENTRE, ELSEWHERE[0])  # the second tile tried would vary
    assert by_id(battery(base, cfg=SECOND, footprint=FOOTPRINT).run())["TI10"].status == WARN


def test_every_flat_level_stays_within_the_bound_with_a_second_stretch(server):
    state, base = server
    state.tile, state.zooms = "flat", (10, 17)
    levels = [(f"measurements/r{i}", z) for i, z in enumerate(range(17, 9, -1))]
    state.requests.clear()
    battery(base, cfg=SECOND, footprint=FOOTPRINT, oracle={"measurements": {"levels": levels, "range": (10, 17)}}, budget=500).run()
    assert sum("/tiles/" in p for p in state.requests) == (8 + 1) * 5 + 8 * 2
    assert len(state.requests) <= TitilerBattery.request_bound(SECOND, "0.12")


def test_l2a_config_tests_the_links_stretch_and_the_explorers():
    assert L2A["render"]["rescale"] == ["0,1", "0,1", "0,1"] and L2A["min_distinct"] == 5
    assert L2A["render"]["second_stretch"]["rescale"] == "0,0.5" and L2A["render"]["second_stretch"]["min_distinct"] == 10
    assert L2A["render"]["second_stretch"]["api"] == "0.12"


def test_the_registered_l2a_plan_fits_the_default_budget(capsys):
    """The README's registered form with both endpoints, the largest plan a user runs: registration
    requests count too, and a plan over budget refuses to start."""
    cli.main(["plan", "--collection", "sentinel-2-l2a", "--stage", "registered", "--store", "s3://ci/b/x.zarr", "--item", "demo",
              "--endpoint", "a=http://127.0.0.1:9@0.12.4:0.12", "--endpoint", "b=http://127.0.0.1:9@0.11.1:0.11"])
    out = capsys.readouterr().out
    assert "registration" in out and "fits" in out and "REFUSED" not in out, out


def test_ti03_a_tile_with_few_valid_pixels_cant_clear_a_flat_one(server):
    state, base = server
    state.tile_at = z9(CENTRE) | z9(*ELSEWHERE, mode="sparse")  # 5 % valid, plenty of distinct values
    r = by_id(battery(base, footprint=FOOTPRINT).run())["TI03"]
    assert r.status == FAIL and any("none of the 4 other tile(s) tried" in p for p in r.problems), r.problems


def test_ti03_reports_a_failed_tile_it_compared(server):
    state, base = server
    state.tile_at = z9(CENTRE) | z9(ELSEWHERE[0], mode="error")  # the next tile varies, the 500 still counts
    r = by_id(battery(base, footprint=FOOTPRINT).run())["TI03"]
    assert r.status == FAIL and len(r.problems) == 1 and "HTTP 500" in r.problems[0], r.problems
    assert any("a flat patch" in e for e in r.evidence), r.evidence
    state.tile_at = z9(CENTRE) | z9(*ELSEWHERE, mode="error")  # server errors, not a flat footprint
    r = by_id(battery(base, footprint=FOOTPRINT).run())["TI03"]
    assert r.status == FAIL and "HTTP 500" in r.summary and not any("constant render" in p for p in r.problems), r.problems


def test_a_tile_that_cant_be_decoded_fails_its_check_and_the_battery_goes_on(server):
    """Uncaught, the decode error stopped the run at the CLI's outer handler and lost every later check."""
    state, base = server
    state.tile_at = z9(CENTRE, mode="corrupt")
    res = by_id(battery(base, footprint=FOOTPRINT).run())
    assert res["TI03"].status == FAIL and "not a decodable PNG" in res["TI03"].summary, res["TI03"].summary
    assert res["TI06"].status == PASS  # the battery carried on
    state.tile_at, state.tile = {}, "corrupt"
    r = by_id(battery(base, footprint=FOOTPRINT).run())["TI04"]
    assert r.status == FAIL and "not a decodable PNG" in r.summary and r.evidence == [], (r.summary, r.evidence)


def test_decoded_stats_survives_whatever_pillow_raises():
    ihdr = b"\x00\x00\x00\x05IHDR" + bytes(5)  # Pillow: ValueError, Truncated IHDR chunk
    short = b"\x89PNG\r\n\x1a\n" + ihdr + struct.pack(">I", zlib.crc32(ihdr[4:]))
    assert decoded_stats(short) is None and decoded_stats(b"<html></html>") is None and decoded_stats(b"") is None


def test_a_body_that_cant_be_read_fails_its_check_and_the_battery_goes_on(server):
    """A gateway's bad Content-Encoding raises httpx.DecodingError, which isn't a TransportError."""
    state, base = server
    state.tile_at = z9(CENTRE, mode="badgzip")
    res = by_id(battery(base, footprint=FOOTPRINT).run())
    assert res["TI03"].status == FAIL and "HTTP 599" in res["TI03"].summary and "DecodingError" in res["TI03"].summary, res["TI03"].summary
    assert res["TI06"].status == PASS


def test_ti03_says_why_it_has_no_store_levels_on_the_item_route(server):
    """Unusable store metadata must not bring back z0 and z24, and the report gives the real reason."""
    state, base = server
    state.zooms = (0, 24)
    cfg = {**CFG, "render": {**CFG["render"], "0.12": {"route": "item", "asset": "radianceData"}}}
    b = battery(base, cfg=cfg, oracle={"measurements": "no proj:code"})
    r2 = b.ti02_tilejson()
    assert r2.status == WARN and any("no proj:code" in e for e in r2.evidence), r2.evidence
    state.requests.clear()
    r = b.ti03_tiles()
    assert {int(p.split("/tiles/")[1].split("/")[1]) for p in state.requests} == {12}
    assert r.status == PASS and "the tilejson's middle zoom" in r.summary and any("no proj:code" in e for e in r.evidence), (r.summary, r.evidence)


def test_ti03_the_coarsest_levels_exemption_also_covers_the_tiles_it_compares(server):
    """The coarsest tile only needs "not all nodata", and so do the tiles a flat one is compared with."""
    state, base = server
    wide = geo.bbox_polygon([-60.0, 10.0, -10.0, 50.0])  # several tiles at z5
    centre = lonlat_to_tile(*geo.interior_point(wide), 5)
    others = {lonlat_to_tile(*p, 5) for p in geo.spread_points(wide)} - {centre}
    assert others
    state.tile_at = {(5, *centre): "flat"} | {(5, *t): "sparse" for t in others}
    r = by_id(battery(base, footprint=wide).run())["TI03"]
    assert r.status == PASS and "flat at the centre at z5" in r.summary, (r.summary, r.evidence)


def test_ti04_takes_its_zoom_from_the_stores_levels(server):
    state, base = server
    state.zooms = (0, 24)
    cfg = {**CFG, "render": {**CFG["render"], "0.12": {"route": "item", "asset": "radianceData"}}}
    r = by_id(battery(base, cfg=cfg).run())["TI04"]
    assert r.status == PASS and r.summary.startswith("RGB tile z7"), r.summary


def test_ti04_a_grey_patch_is_not_one_variable_served_three_times(server):
    """A cloud that clips in all three bands is grey there; a render with one band is grey everywhere."""
    state, base = server
    state.zooms = (8, 10)
    oracle = {"measurements": {"levels": [("measurements/r0", 10), ("measurements/r2", 9), ("measurements/r4", 8)], "range": (8, 10)}}
    state.tile_at = z9(CENTRE, mode="gray")
    r = by_id(battery(base, footprint=FOOTPRINT, oracle=oracle).run())["TI04"]
    assert r.status == PASS and "grey at the centre" in r.summary, r.summary
    state.tile_at = z9(CENTRE, mode="gray") | z9(ELSEWHERE[0], mode="error")
    r = by_id(battery(base, footprint=FOOTPRINT, oracle=oracle).run())["TI04"]
    assert r.status == FAIL and "HTTP 500" in r.summary, r.summary
    state.tile_at, state.tile = {}, "gray"
    r = by_id(battery(base, footprint=FOOTPRINT, oracle=oracle).run())["TI04"]
    assert r.status == FAIL and "as have the 4 other tile(s) tried" in r.summary, r.summary


def test_request_bound_covers_every_flat_tile_compared_elsewhere(server):
    """`plan` promises an upper bound: the worst case is MAX_TILES levels, all flat, RGB flat too."""
    state, base = server
    state.tile, state.zooms = "flat", (10, 17)
    levels = [(f"measurements/r{i}", z) for i, z in enumerate(range(17, 9, -1))]
    state.requests.clear()
    battery(base, footprint=FOOTPRINT, oracle={"measurements": {"levels": levels, "range": (10, 17)}}, budget=500).run()
    assert sum("/tiles/" in p for p in state.requests) == (8 + 1) * 5  # TI03's levels and TI04, each with 4 more
    assert len(state.requests) <= TitilerBattery.request_bound(CFG, "0.12")


def test_spread_points_stay_inside_the_footprint():
    triangle = {"type": "Polygon", "coordinates": [[[0, 0], [4, 0], [0, 4], [0, 0]]]}
    points = geo.spread_points(triangle)
    assert (3, 3) not in points and points and all(geo.contains(triangle, *p) for p in points)


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


S3A_BOUNDS = [-68.08853799999999, 69.39235226554399, -4.5299534496021465, 83.776383]
CANARY_BOUNDS = [-43.909883, 29.06358382206983, -26.94228641777719, 41.970559]
S1_BOUNDS = [11.538706863467372, 44.99758761196769, 12.999320560002849, 46.02436507520001]


def test_issue2_extent_at_minzoom_against_the_floor_window(server):
    """S3A 142227 (76.6°N) opened blank on 6 Oct; the 1280×800 rule passed it at "z4 ≥ 4".
    At minzoom 4 it spans 723×787 px, taller than a 1280×600 window."""
    w, h = extent_px(S3A_BOUNDS, 4)
    assert (round(w), round(h)) == (723, 787)
    state, base = server
    state.zooms, state.bounds = (4, 7), S3A_BOUNDS
    r = by_id(battery(base, oracle=None).run())["TI09"]
    assert r.status == WARN and "723×787 px" in r.summary and "shorter than 787 px" in r.evidence[1], (r.summary, r.evidence)
    for bounds, zooms in ((CANARY_BOUNDS, (5, 9)), (S1_BOUNDS, (7, 13))):  # their maps open fine
        state.bounds, state.zooms = bounds, zooms
        r = by_id(battery(base, oracle=None).run())["TI09"]
        assert r.status == PASS and r.metrics["margin_zoom"] > 0.5, (bounds, r.summary)
    cfg = {**CFG, "floor_viewport": [1920, 1080]}  # a config can move the floor
    state.zooms, state.bounds = (4, 7), S3A_BOUNDS
    assert by_id(battery(base, cfg=cfg, oracle=None).run())["TI09"].status == PASS


def test_issue2_collapsed_raster_tilejson_warns_with_the_linked_page(server):
    """TI09 ran only on a TI02 PASS, so the canary's /raster map.html blank was a SKIP. It now
    runs on any usable tilejson and says which page the items actually link."""
    state, base = server
    state.api, state.zooms, state.bounds = "0.11", (9, 9), CANARY_BOUNDS
    r = by_id(battery(base, ep=RASTER).run())["TI09"]
    assert r.status == WARN and any("/viewer, which TI09 doesn't model" in e for e in r.evidence), r.evidence
    cfg = {**CFG, "viewer_page": {"0.11": "map.html"}}  # S1 links map.html: no caveat, and TI06 opens it
    res = by_id(battery(base, ep=RASTER, cfg=cfg).run())
    assert not any("doesn't model" in e for e in res["TI09"].evidence)
    assert res["TI06"].summary.startswith("map.html")
