"""Each store check passes on a healthy store and fails on the failure it exists for."""

import copy
import datetime as dt

import pytest

from eopf_accept.budget import Budget
from eopf_accept.model import FAIL, KNOWN, PASS, WARN, apply_known_issues
from eopf_accept.store_checks import CHECKS, StoreContext
from eopf_accept.storeio import StoreReader

from .geozarr_fixture import CFG, build


def run(path, cfg=CFG, budget=500):
    ctx = StoreContext(StoreReader(path, Budget(budget)), cfg)
    return {cid: fn(ctx) for cid, fn in CHECKS.items()}


def test_healthy_store_passes_everything(tmp_path):
    res = run(build(tmp_path / "s.zarr"))
    assert {k: r.status for k, r in res.items()} == {k: PASS for k in CHECKS}, {k: (r.status, r.evidence[:3]) for k, r in res.items()}


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
    """titiler falls back to the level group's attrs, so this renders: WARN, not FAIL."""
    res = run(build(tmp_path / "s.zarr", layout_shape=False))
    assert res["ST03"].status == WARN


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


def test_c6_1024_inner_chunks_fail_for_ol_10_10_but_not_10_11(tmp_path):
    path = build(tmp_path / "s.zarr", shape=(2048, 2048), chunks=(1024, 1024), shards=(2048, 2048), write_data=False, dtype="uint16")
    cfg = copy.deepcopy(CFG) | {"sample_variables": ["oa08_radiance"], "dtype_allow": ["uint16"]}
    cfg["consumers"] = {"openlayers": {"sentinel-explorer": "10.10.0"}}
    res = run(path, cfg)
    assert res["ST07"].status == FAIL
    assert "64x64 px tiles" in res["ST07"].evidence[0]
    cfg["consumers"] = {"openlayers": {"upstream": "10.11.0"}}
    assert run(path, cfg)["ST07"].status == PASS


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


def test_store_reads_spend_the_budget(tmp_path):
    from eopf_accept.budget import BudgetExceeded

    with pytest.raises(BudgetExceeded):
        run(build(tmp_path / "s.zarr"), budget=3)
