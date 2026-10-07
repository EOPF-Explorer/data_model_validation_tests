"""The TI02 zoom oracle: the store's own metadata must give what titiler-eopf computes."""

import json

import pytest
from rasterio.crs import CRS

from eopf_accept.budget import Budget
from eopf_accept.store_checks import StoreContext
from eopf_accept.storeio import StoreReader
from eopf_accept.zooms import level_zoom, oracle

from .geozarr_fixture import CFG, build


def olci_levels(bbox, shapes):
    w, s, e, n = bbox
    return [level_zoom(CRS.from_epsg(4326), (h, wd), [(e - w) / wd, 0, w, 0, -(n - s) / h, n]) for h, wd in shapes]


def test_oracle_reproduces_the_ranges_titiler_served_on_7_oct():
    """Level shapes and transforms of the real stores (S1 from the 7 Oct ST07/TI01 evidence;
    OLCI rebuilt from the bounds by the 7 Oct review) give the ranges /rstaging returned."""
    utm = CRS.from_epsg(32632)
    s1 = [level_zoom(utm, (n, n), [r, 0, 699960, 0, -r, 5100000]) for n, r in
          [(10980, 10), (5490, 20), (1830, 60), (915, 120), (305, 360), (153, 720)]]
    assert (min(s1), max(s1)) == (7, 13)
    canary = olci_levels([-43.909883, 29.06358382206983, -26.94228641777719, 41.970559],
                         [(4717, 6201), (2358, 3100), (1179, 1550), (589, 775), (294, 387)])
    assert canary == [9, 8, 7, 6, 5]
    s3a = olci_levels([-68.08853799999999, 69.39235226554399, -4.5299534496021465, 83.776383],
                      [(2429, 10733), (1214, 5366), (607, 2683), (303, 1341)])
    assert s3a == [7, 6, 5, 4]


def test_oracle_equals_the_real_reader_on_a_fixture(tmp_path):
    pytest.importorskip("titiler.eopf")
    from titiler.eopf.reader import GeoZarrReader

    store = build(tmp_path / "s.zarr", shape=(1200, 1800), bbox=(-40.0, 30.0, -20.0, 42.0), write_data=False)
    got = oracle(StoreContext(StoreReader(store, Budget(500)), CFG))["measurements"]["range"]
    with GeoZarrReader(input=f"{store}/measurements") as src:
        assert got == (src.get_minzoom("/"), src.get_maxzoom("/"))


def test_oracle_reports_why_it_cannot_compute(tmp_path):
    store = build(tmp_path / "s.zarr", layout_shape=False, group_shape=False)
    assert "no spatial:shape+spatial:transform" in oracle(StoreContext(StoreReader(store, Budget(500)), CFG))["measurements"]
    store = build(tmp_path / "t.zarr")
    meta_path = tmp_path / "t.zarr" / "measurements" / "zarr.json"
    meta = json.loads(meta_path.read_text())
    del meta["attributes"]["proj:code"]
    meta_path.write_text(json.dumps(meta))
    assert oracle(StoreContext(StoreReader(store, Budget(500)), CFG))["measurements"] == "no proj:code/wkt2/projjson on the group"
