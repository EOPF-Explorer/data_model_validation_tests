"""Products from data-model's generic_rechunker (data-model#292): a sub-root found through the root's
stac_discovery, consolidation as data-model#291 decided, and the GR02/GR03 layout and encoding rules."""

import copy
import tomllib
from types import SimpleNamespace

import numpy as np
import pytest
import zarr

from eopf_accept import conventions as cv
from eopf_accept.budget import Budget
from eopf_accept.cli import CONFIG_DIR
from eopf_accept.model import FAIL, PASS, SKIP, WARN
from eopf_accept.store_checks import CHECKS, StoreContext, gr02_chunks_and_shards, gr03_encoding
from eopf_accept.storeio import StoreReader

from .geozarr_fixture import decl

SUB = "S01SIWGRD_20240201T164915_0025_A299_750E_065517"
GEO = {"zarr_conventions": [decl(cv.SPATIAL), decl(cv.PROJ)], "spatial:bbox": [15.2, 39.4, 18.6, 41.3], "proj:code": "EPSG:4326"}


def s1_config(spatial_chunk=16):
    cfg = tomllib.loads((CONFIG_DIR / "sentinel-1-l1-grd.toml").read_text())
    cfg["generic"]["spatial_chunk"] = spatial_chunk  # the fixture is small
    return cfg


def build_s1(path, *, overviews=False, shards=(2, 48, 64), chunks=(2, 16, 16), grd_attrs=None, consolidate_sub=True):
    """An S1 GRD-shaped generic product: root (stac_discovery, not consolidated) → sub-root → measurements."""
    path = str(path)
    root = zarr.open_group(path, mode="w", zarr_format=3)
    root.attrs.update(GEO | {"stac_discovery": {"assets": {"grd": {"href": f"/{SUB}/measurements/grd"}}}})
    sub = root.create_group(SUB, attributes=GEO)
    m = sub.create_group("measurements")
    grd = m.create_array("grd", shape=(2, 40, 50), chunks=chunks, shards=shards, dtype="uint16", fill_value=0,
                         dimension_names=["polarization", "azimuth_time", "ground_range"], attributes=grd_attrs or {})
    grd[:] = 7
    for name, n in (("azimuth_time", 40), ("ground_range", 50)):
        m.create_array(name, shape=(n,), chunks=(n,), dtype="float64", compressors=None, dimension_names=[name])
    sub.create_group("conditions")
    if overviews:
        sub.create_group("overviews", attributes=GEO)
    if consolidate_sub:  # data-model#291: the sub-root and the measurements group
        zarr.consolidate_metadata(path, path=SUB, zarr_format=3)
        zarr.consolidate_metadata(path, path=f"{SUB}/measurements", zarr_format=3)
    return path


def run(path, cfg):
    ctx = StoreContext(StoreReader(path, Budget(500)), cfg)
    return ctx, {cid: fn(ctx) for cid, fn in CHECKS.items()}


def test_the_sub_root_comes_from_the_roots_stac_discovery(tmp_path):
    ctx, _ = run(build_s1(tmp_path / "s.zarr"), s1_config())
    assert ctx.subroot == SUB
    assert ctx.open_groups == [f"{SUB}/overviews"] and ctx.consolidated_groups == [f"{SUB}/measurements"]


def test_an_unknown_sub_root_asset_fails_st01(tmp_path):
    cfg = s1_config() | {"subroot_asset": "no_such_asset"}
    _, res = run(build_s1(tmp_path / "s.zarr"), cfg)
    assert res["ST01"].status == FAIL and any("sub-root unknown" in p for p in res["ST01"].problems)


def test_s1_consolidates_its_sub_root_not_its_root(tmp_path):
    """data-model#291 Ex.2: the root of an S1 product is not consolidated, by design."""
    _, res = run(build_s1(tmp_path / "s.zarr", overviews=True), s1_config())
    assert not any(p.startswith("/:") for p in res["ST01"].problems), res["ST01"].problems
    _, res = run(build_s1(tmp_path / "t.zarr", overviews=True, consolidate_sub=False), s1_config())
    assert f"{SUB}: no consolidated_metadata" in res["ST01"].problems
    assert f"{SUB}/measurements: no consolidated_metadata" in res["ST01"].problems


def test_missing_overviews_are_flagged_not_skipped(tmp_path):
    """The #292 S1 products have no `overviews` (data-model#249 lists them): every check that
    needs the group says so, rather than a config that stops asking."""
    _, res = run(build_s1(tmp_path / "s.zarr"), s1_config())
    assert f"{SUB}/overviews: no zarr.json" in res["ST01"].problems
    assert res["ST03"].status == FAIL and "multiscales group is absent" in res["ST03"].evidence[0]
    assert res["HT02"].status == FAIL and "absent" in res["HT02"].summary
    assert res["GR02"].status == PASS and res["GR03"].status == PASS


def test_spatial_dimensions_are_optional_on_groups_not_on_arrays(tmp_path):
    """spatial v0.1: "Required: Yes on arrays; optional on groups". The root and sub-root declare
    spatial with a bbox only; an array doing the same is reported."""
    path = build_s1(tmp_path / "s.zarr", grd_attrs=GEO)
    _, res = run(path, s1_config())
    assert res["ST12"].status == WARN
    assert [e.split(":")[0] for e in res["ST12"].evidence] == [f"{SUB}/measurements/grd"]


def test_ht02_skips_when_there_is_nothing_to_open(tmp_path):
    _, res = run(build_s1(tmp_path / "s.zarr"), s1_config() | {"open_groups": []})
    assert res["HT02"].status == SKIP


def test_gr02_wants_min_spatial_chunk_and_one_shard_per_array(tmp_path):
    _, res = run(build_s1(tmp_path / "s.zarr", shards=(2, 32, 32)), s1_config())
    assert res["GR02"].status == FAIL
    assert any("shard [2, 32, 32], want [2, 48, 64] (one shard per array)" in p for p in res["GR02"].problems)
    _, res = run(build_s1(tmp_path / "t.zarr", chunks=(1, 16, 16)), s1_config())
    assert any("chunk [1, 16, 16], want [2, 16, 16]" in p for p in res["GR02"].problems), res["GR02"].problems
    _, res = run(build_s1(tmp_path / "u.zarr", shards=None), s1_config())
    assert any(p.endswith("measurements/grd: not sharded") for p in res["GR02"].problems)


def test_gr_checks_skip_without_a_generic_table(tmp_path):
    cfg = copy.deepcopy(s1_config())
    del cfg["generic"]
    _, res = run(build_s1(tmp_path / "s.zarr"), cfg)
    assert res["GR02"].status == SKIP and res["GR03"].status == SKIP


def fake_ctx(arrays: dict):
    """GR02/GR03 read only the consolidation root's metadata: craft it directly."""
    cm = {name: {"node_type": "array", "chunk_grid": {"configuration": {"chunk_shape": m.pop("chunks")}}} | m for name, m in arrays.items()}
    return SimpleNamespace(cfg={"generic": {"spatial_chunk": 16, "sharding": False}}, subroot=None,
                           nodes={"": {"consolidated_metadata": {"metadata": cm}}})


def test_gr02_flags_an_undeclared_coordinate_and_large_uncompressed_coordinates():
    """S3 OLCI EFR: `altitude`/`time_stamp` written like coordinates that nothing declares; 2-D lat/lon uncompressed."""
    ctx = fake_ctx({
        "radiance": {"shape": [40, 50], "chunks": [16, 16], "data_type": "uint16", "dimension_names": ["rows", "columns"],
                     "codecs": [{"name": "bytes"}, {"name": "zstd"}], "attributes": {"coordinates": "latitude"}},
        "latitude": {"shape": [40, 50], "chunks": [16, 16], "data_type": "int32", "codecs": [{"name": "bytes"}]},
        "altitude": {"shape": [40, 50], "chunks": [16, 16], "data_type": "int16", "codecs": [{"name": "bytes"}]},
    })
    res = gr02_chunks_and_shards(ctx)
    assert res.status == WARN and not res.problems
    assert any(e.startswith("latitude: 2-D coordinate") for e in res.evidence)
    assert any(e.startswith("altitude: written like a coordinate") for e in res.evidence)


@pytest.mark.parametrize("attrs, codecs, status, text", [
    ({"scale_factor": 0.01, "_FillValue": 65535}, [{"name": "scale_offset"}], FAIL, "decoded twice"),
    ({"_FillValue": 70000}, [], FAIL, "_FillValue 70000 does not fit uint16"),
    ({"valid_range": [0, 70000]}, [], FAIL, "valid_range [0, 70000] does not fit uint16"),
    ({"scale_factor": 0.01, "_FillValue": 65535}, [], WARN, "zarr fill_value 0 != CF _FillValue 65535"),
    ({"scale_factor": 0.01, "_FillValue": 0}, [], PASS, "consistent"),
])
def test_gr03_cf_packing(attrs, codecs, status, text):
    ctx = fake_ctx({"oa08": {"shape": [40, 50], "chunks": [16, 16], "data_type": "uint16", "fill_value": 0,
                             "codecs": [{"name": "bytes"}, *codecs, {"name": "zstd"}], "attributes": attrs}})
    res = gr03_encoding(ctx)
    assert res.status == status and text in res.summary + " ".join(res.evidence), (res.summary, res.evidence)


def test_gr03_nan_fills_agree():
    ctx = fake_ctx({"wind": {"shape": [40, 50], "chunks": [16, 16], "data_type": "float32", "fill_value": "NaN",
                             "codecs": [{"name": "bytes"}], "attributes": {"_FillValue": np.nan}}})
    assert gr03_encoding(ctx).status == PASS
