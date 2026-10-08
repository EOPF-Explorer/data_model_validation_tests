"""Each store check passes on a healthy store and fails on the failure it exists for."""

import copy
import datetime as dt
import json
import re

import pytest

from eopf_accept.budget import Budget
from eopf_accept.model import FAIL, KNOWN, PASS, SKIP, WARN, Result, apply_known_issues
from eopf_accept.store_checks import CHECKS, StoreContext
from eopf_accept.storeio import StoreReader

from .geozarr_fixture import CFG, build


def run(path, cfg=CFG, budget=500):
    ctx = StoreContext(StoreReader(path, Budget(budget)), cfg)
    return {cid: fn(ctx) for cid, fn in CHECKS.items()}


def test_healthy_store_passes_everything(tmp_path):
    res = run(build(tmp_path / "s.zarr"))
    want = {k: SKIP if k.startswith("GR") else PASS for k in CHECKS}  # GR*: only for configs with a [generic] table
    assert {k: r.status for k, r in res.items()} == want, {k: (r.status, r.evidence[:3]) for k, r in res.items()}


def test_c2_unconsolidated_fails_st01_and_ht02(tmp_path):
    """The validator blind spot (F3): an unconsolidated store must FAIL, not pass silently."""
    res = run(build(tmp_path / "s.zarr", consolidated="none"))
    assert res["ST01"].status == FAIL
    assert res["HT02"].status == FAIL
    assert "PROPFIND" in res["HT02"].summary


def test_c2_root_only_consolidation_still_fails_the_opened_group(tmp_path):
    """titiler opens the asset href (`measurements`), so a consolidated root is not enough."""
    res = run(build(tmp_path / "s.zarr", consolidated="root-only"))
    assert res["ST01"].status == FAIL
    assert any("measurements: no consolidated_metadata" in e for e in res["ST01"].evidence)
    assert res["HT02"].status == FAIL


def test_c1_no_spatial_shape_anywhere_fails_st03(tmp_path):
    """The 6 Oct OLCI tilejson 500: neither the layout nor the level groups had spatial:shape."""
    res = run(build(tmp_path / "s.zarr", layout_shape=False, group_shape=False))
    assert res["ST03"].status == FAIL
    assert "tilejson without zoom params returns 500" in " ".join(res["ST03"].evidence)


def test_c1_shape_on_groups_only_is_a_warning(tmp_path):
    """Layout entries keep spatial:transform but not spatial:shape: titiler falls back to the
    level group's shape (get_maxzoom/get_minzoom, _get_variable), so this renders: WARN."""
    res = run(build(tmp_path / "s.zarr", layout_shape=False))
    assert res["ST03"].status == WARN, res["ST03"].evidence


def test_w1_layout_without_transform_fails_st03_even_when_the_group_has_one(tmp_path):
    """7 Oct review (W1): titiler 0.12 reads layout["spatial:transform"] with no fallback, so
    every tile 500s although the level groups carry a transform. It used to WARN."""
    res = run(build(tmp_path / "s.zarr", layout_transform=False))
    assert res["ST03"].status == FAIL
    assert any("layout entry has no spatial:transform" in e for e in res["ST03"].evidence), res["ST03"].evidence


def test_malformed_transform_fails_st03_instead_of_crashing_the_store_checks(tmp_path):
    """7 Oct code review: np.allclose raised on a non-numeric level transform, so one bad
    level ended every store check as CRASH/VOID instead of an ST03 FAIL."""
    store = build(tmp_path / "s.zarr")
    level = tmp_path / "s.zarr" / "measurements" / StoreContext(StoreReader(store, Budget(500)), CFG).levels["measurements"][0][0]["asset"]
    meta = json.loads((level / "zarr.json").read_text())
    meta["attributes"]["spatial:transform"] = ["x"] * 6
    (level / "zarr.json").write_text(json.dumps(meta))
    res = run(store)
    assert res["ST03"].status == FAIL
    assert any("layout spatial:transform" in e and "!= group" in e for e in res["ST03"].evidence), res["ST03"].evidence


def test_transforms_must_agree_with_the_group_bbox(tmp_path):
    """titiler takes zooms from the transforms and bounds from spatial:bbox; consistently
    wrong transforms would make the zoom oracle agree with titiler, so ST03 ties them."""
    store = build(tmp_path / "s.zarr")
    path = tmp_path / "s.zarr" / "measurements" / "zarr.json"
    meta = json.loads(path.read_text())
    meta["attributes"]["spatial:bbox"] = [-36.0, 34.0, -30.0, 36.0]  # 3x wider than the levels
    path.write_text(json.dumps(meta))
    res = run(store)
    assert res["ST03"].status == FAIL and any("more than a pixel from the group's spatial:bbox" in e for e in res["ST03"].evidence)


def test_c15_level_without_spatial_dimensions_fails_st03(tmp_path):
    """Found by running the real 0.12 app: a level that declares spatial without
    spatial:dimensions makes every tile from it 500 (KeyError in _get_variable)."""
    res = run(build(tmp_path / "s.zarr", level_dims=False))
    assert res["ST03"].status == FAIL
    assert "spatial:dimensions" in " ".join(res["ST03"].evidence)


def test_c5_stale_declarations_warn_st11_but_titiler_still_sees_the_group(tmp_path):
    res = run(build(tmp_path / "s.zarr", conventions="stale"))
    assert res["ST11"].status == WARN
    assert any("schema wants" in e for e in res["ST11"].evidence)
    # titiler matches on uuid only, so the stale store is still readable by it
    assert res["ST03"].status == PASS and res["ST04"].status == PASS
    strict = copy.deepcopy(CFG) | {"strict_declarations": True}
    assert run(build(tmp_path / "s2.zarr", conventions="stale"), strict)["ST11"].status == FAIL


def test_c4_undeclared_conventions_make_the_group_invisible(tmp_path):
    res = run(build(tmp_path / "s.zarr", conventions="none"))
    assert res["ST04"].status == FAIL
    assert any("invisible to titiler-eopf" in e for e in res["ST04"].evidence)
    assert res["ST03"].status == FAIL  # no multiscales/spatial/proj declared


def test_st12_flags_invalid_convention_contents(tmp_path):
    """geozarr-toolkit's models catch what ST03 only checks for presence: a 5-coefficient transform."""
    store = build(tmp_path / "s.zarr", consolidated="none")
    level = tmp_path / "s.zarr" / "measurements" / StoreContext(StoreReader(store, Budget(500)), CFG).levels["measurements"][0][0]["asset"]
    meta = json.loads((level / "zarr.json").read_text())
    meta["attributes"]["spatial:transform"] = meta["attributes"]["spatial:transform"][:5]
    (level / "zarr.json").write_text(json.dumps(meta))
    res = run(store)
    assert res["ST12"].status == WARN and "exactly 6 coefficients" in res["ST12"].evidence[0], res["ST12"].evidence
    group = tmp_path / "s.zarr" / "measurements" / "zarr.json"  # multiscales not a dict: a WARN, not a crash
    gmeta = json.loads(group.read_text())
    gmeta["attributes"]["multiscales"] = None
    group.write_text(json.dumps(gmeta))
    res = run(store)
    assert res["ST12"].status == WARN and any("multiscales: attributes: Input should be a valid dictionary" in e for e in res["ST12"].evidence), res["ST12"].evidence


def test_unconsolidated_levels_are_read_once(tmp_path):
    """Review: without consolidated metadata, every check re-read each level's arrays."""
    reader = StoreReader(build(tmp_path / "s.zarr", consolidated="none"), Budget(500))
    ctx = StoreContext(reader, CFG)
    g = ctx.ms_groups[0]
    path = ctx.levels[g][0][1]
    first = ctx.arrays(g, path)
    used = reader.budget.used
    assert first and ctx.arrays(g, path) == first and reader.budget.used == used


def test_c7_float64_fails_st08_and_a_known_issue_downgrades_it(tmp_path):
    res = run(build(tmp_path / "s.zarr", dtype="float64"))
    assert res["ST08"].status == FAIL and "float64" in res["ST08"].summary
    results = list(res.values())
    apply_known_issues(results, [{"check": "ST08", "match": "float64", "ref": "data-model follow-up", "until": "2026-12-31"}], dt.date(2026, 10, 6))
    assert res["ST08"].status == KNOWN
    # after the expiry date the same failure is a FAIL again
    res2 = run(build(tmp_path / "s2.zarr", dtype="float64"))
    apply_known_issues(list(res2.values()), [{"check": "ST08", "match": "float64", "ref": "x", "until": "2026-12-31"}], dt.date(2027, 1, 1))
    assert res2["ST08"].status == FAIL


def test_c6_1024_inner_chunks_warn_for_ol_10_10_and_name_the_upgrade(tmp_path):
    """ol ≤ 10.10 draws 64 px tiles from 1024 px inner chunks; 10.11 doesn't, so it's the viewer's to fix."""
    path = build(tmp_path / "s.zarr", shape=(2048, 2048), chunks=(1024, 1024), shards=(2048, 2048), write_data=False, dtype="uint16")
    cfg = copy.deepcopy(CFG) | {"sample_variables": ["oa08_radiance"], "dtype_allow": ["uint16"]}
    cfg["consumers"] = {"openlayers": {"sentinel-explorer": "10.10.0", "upstream": "10.11.0"}}
    st07 = run(path, cfg)["ST07"]
    assert st07.status == WARN and "fixed by upgrading the viewer to ol ≥ 10.11" in st07.summary
    assert "64x64 px tiles" in st07.evidence[0] and st07.evidence[0].endswith("ol ≥ 10.11 draws 512x512 px tiles (4x): upgrade sentinel-explorer")
    cfg["consumers"] = {"openlayers": {"upstream": "10.11.0"}}
    assert run(path, cfg)["ST07"].status == PASS


def test_c6_2048_inner_chunks_fail_because_no_ol_release_avoids_it(tmp_path):
    """10.11 still decodes 16x here (512 px tiles from 2048 px chunks): the store's problem, not the viewer's."""
    path = build(tmp_path / "s.zarr", shape=(4096, 4096), chunks=(2048, 2048), shards=(4096, 4096), write_data=False, dtype="uint16")
    cfg = copy.deepcopy(CFG) | {"sample_variables": ["oa08_radiance"], "dtype_allow": ["uint16"]}
    for consumers in ({"sentinel-explorer": "10.10.0"}, {"upstream": "10.11.0"}):
        cfg["consumers"] = {"openlayers": consumers}
        st07 = run(path, cfg)["ST07"]
        assert st07.status == FAIL and "no OpenLayers release avoids it (ol 10.11: 16x)" in st07.evidence[0], st07.evidence[:1]


def test_partial_scene_does_not_false_fail_st09(tmp_path):
    """The bbox centre of a 5 % scene is nodata; ST09 samples where the coarsest level has data."""
    assert run(build(tmp_path / "s.zarr", partial=True))["ST09"].status == PASS


def test_all_fill_store_fails_st09(tmp_path):
    res = run(build(tmp_path / "s.zarr", fill=True))
    assert res["ST09"].status == FAIL


def test_optional_groups_absent_fail_only_when_none_exist(tmp_path):
    path = build(tmp_path / "s.zarr")
    cfg = copy.deepcopy(CFG) | {"open_groups": ["measurements", "other?"], "multiscales_groups": ["measurements", "other?"]}
    assert run(path, cfg)["ST01"].status == PASS
    cfg = copy.deepcopy(CFG) | {"open_groups": ["nope?"], "multiscales_groups": ["nope?"]}
    assert run(path, cfg)["ST01"].status == FAIL


def test_w13_st04_covers_groups_outside_the_config(tmp_path):
    """7 Oct review (W13): the 30 Sep S1 snapshot has `<orbit>/conditions` using proj:/spatial:
    with no zarr_conventions, and ST04 never looked there (only configured groups)."""
    import zarr

    store = build(tmp_path / "s.zarr")
    zarr.open_group(store, mode="a").create_group("conditions", attributes={"proj:code": "EPSG:4326", "spatial:bbox": [-36, 34, -34, 36]})
    zarr.consolidate_metadata(store)
    res = run(store)
    assert res["ST04"].status == FAIL and "1 node(s) with implicit conventions" in res["ST04"].summary, res["ST04"].summary
    assert res["ST04"].problems == ["conditions: implicit conventions: uses ['proj', 'spatial'] keys without declaring them in zarr_conventions"]


def test_w5_declared_item_groups_and_optional_groups(tmp_path):
    """A single-orbit S1 cube: the item config says which groups it has; ST01 requires exactly those."""
    store = build(tmp_path / "s.zarr")
    cfg = copy.deepcopy(CFG) | {"open_groups": ["measurements", "other?"], "multiscales_groups": ["measurements", "other?"],
                                "items": {"I": {"groups": ["measurements"]}, "J": {"groups": ["other"]}}}

    def st01(item):
        return CHECKS["ST01"](StoreContext(StoreReader(store, Budget(500)), cfg, item))

    ok = st01("I")
    assert ok.status == PASS and "optional group(s) absent: ['other']" in ok.evidence, ok.evidence
    bad = st01("J")
    assert bad.status == FAIL and any("expects this group" in p for p in bad.problems) and any("don't list it" in p for p in bad.problems)


def test_w5_rg08_requires_only_the_declared_groups_assets(tmp_path):
    from eopf_accept import registration as rg

    store = build(tmp_path / "s.zarr")
    cfg = copy.deepcopy(CFG) | {"open_groups": ["measurements", "other?"], "multiscales_groups": ["measurements", "other?"],
                                "asset_group": {"radianceData": "measurements", "otherData": "other"}, "items": {"I": {"groups": ["measurements"]}}}
    item = {"id": "I", "links": [], "assets": {"radianceData": {"href": f"{store}/measurements"}}}
    assert rg.rg08_hrefs(item, store, cfg, StoreContext(StoreReader(store, Budget(500)), cfg, "I")).status == PASS
    assert rg.rg08_hrefs(item, store, cfg).status == FAIL  # no declaration: every asset is required
    item["assets"]["otherData"] = {"href": f"{store}/other"}  # an href to a group the store doesn't have
    r = rg.rg08_hrefs(item, store, cfg, StoreContext(StoreReader(store, Budget(500)), cfg, "I"))
    assert r.status == FAIL and "the store has no group 'other'" in r.summary


def test_w5_tr01_skips_absent_optional_groups(tmp_path):
    pytest.importorskip("titiler.eopf")
    from eopf_accept.reader_check import tr01_local_reader

    store = build(tmp_path / "s.zarr")
    cfg = copy.deepcopy(CFG) | {"open_groups": ["measurements", "other?"]}
    r = tr01_local_reader(store, cfg, absent_optional=["other"])
    assert r.status == PASS and any("optional and absent" in e for e in r.evidence), r.evidence


def test_store_reads_spend_the_budget(tmp_path):
    from eopf_accept.budget import BudgetExceeded

    with pytest.raises(BudgetExceeded):
        run(build(tmp_path / "s.zarr"), budget=3)


def test_w3_st08_reports_each_dtype_so_a_known_issue_cannot_cover_another(tmp_path):
    """7 Oct review (W3): "float16, float64" in one problem matched a "float64" known issue."""
    res = run(build(tmp_path / "s.zarr", dtype="float64", dtype_at={"r0/oa04_radiance": "float16"}))
    assert res["ST08"].status == FAIL
    assert sum("dtype float16" in p for p in res["ST08"].problems) == 1, res["ST08"].problems
    apply_known_issues([res["ST08"]], [{"check": "ST08", "match": "float64", "ref": "x", "until": "2026-12-31"}], dt.date(2026, 10, 7))
    assert res["ST08"].status == FAIL


def test_w4_st08_checks_every_level_not_only_the_finest(tmp_path):
    """7 Oct review (W4): float64 only at a coarser level used to PASS."""
    res = run(build(tmp_path / "s.zarr", dtype_at={"r2/oa08_radiance": "float64"}))
    assert res["ST08"].status == FAIL and res["ST08"].problems[0].startswith("measurements/r2: dtype float64"), res["ST08"].problems
    # the OLCI known issue still covers float64 on every level
    res = run(build(tmp_path / "s2.zarr", dtype="float64"))
    assert len(res["ST08"].problems) == 2
    apply_known_issues([res["ST08"]], [{"check": "ST08", "match": "float64", "ref": "x", "until": "2026-12-31"}], dt.date(2026, 10, 7))
    assert res["ST08"].status == KNOWN


def test_w4_s2_dtype_pattern_covers_all_twelve_bands():
    from eopf_accept.cli import CONFIG_DIR, load_config

    pattern = re.compile(load_config("sentinel-2-l2a", None, CONFIG_DIR)["dtype_check_pattern"])
    bands = [f"b{i:02d}" for i in range(1, 13)] + ["b8a"]
    assert all(pattern.search(b) for b in bands)
    assert not any(pattern.search(n) for n in ("b13", "b00", "scl", "b02_detector"))


def test_w11_a_band_missing_from_the_coarsest_level_fails_st09(tmp_path):
    """7 Oct review (W11): ST09 skipped the variable and the store verdict stayed PASS."""
    res = run(build(tmp_path / "s.zarr", missing=("r2/oa08_radiance",)))
    assert res["ST09"].status == FAIL
    assert "missing from the coarsest level" in res["ST09"].summary


def test_w11_tr01_without_the_reader_extra_is_void_not_skip(tmp_path, monkeypatch):
    """Asked for and not run: no verdict, instead of a SKIP that leaves a scratch run PASS."""
    import sys

    from eopf_accept.model import VOID
    from eopf_accept.reader_check import tr01_local_reader

    monkeypatch.setitem(sys.modules, "titiler.eopf.reader", None)  # import raises ImportError
    assert tr01_local_reader(build(tmp_path / "s.zarr"), CFG).status == VOID


def test_w2_a_fail_without_problems_is_never_downgraded():
    """7 Oct review (W2): a count summary ("12 visibility problem(s)") would also cover a
    later, different failure of the same check."""
    r = Result("ST04", "store", FAIL, "12 visibility problem(s)")
    apply_known_issues([r], [{"check": "ST04", "match": "visibility", "ref": "x", "until": "2026-12-31"}], dt.date(2026, 10, 7))
    assert r.status == FAIL and "known issue not applied" in r.evidence[-1]


def test_a_known_issue_cannot_hide_a_second_failure():
    """Review: ST08's summary is its first failure only; a new int64 failure in another group must stay visible."""
    known = [{"check": "ST08", "match": "float64", "ref": "x", "until": "2026-12-31"}]
    one = Result("ST08", "store", FAIL, "r0: dtype float64 not allowed", problems=["r0: dtype float64 not allowed"])
    two = Result("ST08", "store", FAIL, "r0: dtype float64 not allowed", problems=["r0: dtype float64 not allowed", "r2: dtype int64 not allowed"])
    apply_known_issues([one, two], known, dt.date(2026, 10, 6))
    assert (one.status, two.status) == (KNOWN, FAIL)
