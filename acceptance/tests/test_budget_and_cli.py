"""The request bound is a feature of the tool, proven against the real client stack before
anything real is run: it counts physical requests (no hidden retries or redirects)."""

import pytest

from eopf_accept import cli, olpredict
from eopf_accept.budget import Budget, BudgetExceeded, Http
from eopf_accept.storeio import StoreReader

from .fake_titiler import State, serve


@pytest.fixture
def server():
    state = State()
    srv, base = serve(state)
    yield state, base
    srv.shutdown()


def test_budget_stops_at_the_cap_and_sends_nothing_more(server):
    state, base = server
    http = Http(Budget(3))
    for _ in range(3):
        http.get(f"{base}/api")
    with pytest.raises(BudgetExceeded):
        http.get(f"{base}/api")
    assert len(state.requests) == 3  # request 4 never left the process


def test_httpx_does_not_follow_redirects(server):
    state, base = server
    state.force_status = 302
    r = Http(Budget(5)).get(f"{base}/api")
    assert r.status_code == 302 and len(state.requests) == 1


def test_obstore_does_not_retry_a_503(server):
    """object_store retries 5xx up to 10 times by default; the budget would not see those."""
    state, base = server
    state.force_status = 503
    reader = StoreReader(f"{base}/store.zarr", Budget(5))
    with pytest.raises(Exception):
        reader.node("")
    assert len(state.requests) == 1 and reader.budget.used == 1


def test_link_query_survives_the_cache_buster(server):
    state, base = server
    Http(Budget(5)).get(f"{base}/x.png?variables=a&variables=b&rescale=0,1")
    sent = state.requests[0]
    assert "variables=a&variables=b&rescale=0%2C1&cb=" in sent or "variables=a&variables=b&rescale=0,1&cb=" in sent


def test_plan_over_the_cap_is_refused():
    b = Budget(50)
    b.reserve("store", 40)
    b.reserve("titiler:rstaging", 20)
    with pytest.raises(BudgetExceeded, match="exceeds --max-requests 50"):
        b.assert_plan_fits()


def test_cli_plan_sends_nothing_and_refuses_over_budget(capsys):
    args = ["plan", "--collection", "sentinel-3-olci-l1-efr-staging", "--store", "s3://b/x.zarr", "--item", "I",
            "--stage", "registered", "--endpoint", "rstaging", "--endpoint", "raster"]
    assert cli.main(args) == 0
    out = capsys.readouterr().out
    assert "nothing was sent" in out and "Side effect" in out
    assert "/assets/radianceData/WebMercatorQuad/tilejson.json?variables=oa08_radiance" in out
    assert "variables=/measurements/r0:oa08_radiance" in out
    assert cli.main(args + ["--max-requests", "20"]) == 2
    assert "REFUSED" in capsys.readouterr().out


def test_w5_an_item_can_render_the_orbit_it_has(tmp_path, capsys):
    """7 Oct review (W5): the battery hard-wired /ascending, so a descending-only cube was
    tested on a group it doesn't have."""
    text = (cli.CONFIG_DIR / "sentinel-1-grd-rtc.toml").read_text() + (
        '\n[items."s1-rtc-DESC"]\ngroups = ["descending"]\n'
        'render = { "0.11" = { group = "/descending", extra = { bidx = "1" } }, '
        '"0.12" = { route = "asset", asset = "gamma0-rtc-backscatter-desc", extra = { bidx = "1" } } }\n')
    (tmp_path / "s1.toml").write_text(text)
    cli.main(["plan", "--collection", "sentinel-1-grd-rtc-staging", "--config", str(tmp_path / "s1.toml"), "--store", "s3://b/x.zarr",
              "--item", "s1-rtc-DESC", "--stage", "registered", "--endpoint", "rstaging", "--endpoint", "raster"])
    out = capsys.readouterr().out
    assert "variables=/descending:vv" in out and "/assets/gamma0-rtc-backscatter-desc/" in out and "/ascending" not in out


def test_no_endpoint_means_no_titiler_traffic(capsys):
    cli.main(["plan", "--collection", "sentinel-3-olci-l1-efr-staging", "--store", "s3://b/x.zarr", "--stage", "scratch"])
    out = capsys.readouterr().out
    assert "titiler" not in out.split("groups:")[1].split("\n")[0]


def test_scratch_stage_refuses_production_hosts():
    with pytest.raises(SystemExit, match="refuses production hosts"):
        cli.main(["plan", "--collection", "sentinel-3-olci-l1-efr-staging", "--store", "s3://b/x.zarr", "--item", "I",
                  "--stage", "scratch", "--endpoint", "rstaging"])


@pytest.mark.parametrize("store", [
    "s3://esa-zarr-sentinel-explorer-fra/tests-output/c/I.zarr",
    "https://s3.de.io.cloud.ovh.net/esa-zarr-sentinel-explorer-fra/tests-output/c/I.zarr",
    "https://esa-zarr-sentinel-explorer-fra.s3.de.io.cloud.ovh.net/tests-output/c/I.zarr",  # virtual-hosted
    "https://s3.gra.io.cloud.ovh.net/esa-zarr-sentinel-explorer-fra/tests-output/c/I.zarr",  # another endpoint
])
def test_scratch_stage_refuses_the_production_bucket(store):
    """Review: the guard compared hostnames only, and an s3:// URL's host is the bucket.
    The scratch-only reader (TR01) reads outside the budget, so it must never get here."""
    with pytest.raises(SystemExit, match="refuses the production bucket"):
        cli.main(["plan", "--collection", "sentinel-3-olci-l1-efr-staging", "--store", store, "--stage", "scratch"])
    cli.main(["plan", "--collection", "sentinel-3-olci-l1-efr-staging", "--stage", "scratch",
              "--store", store.replace("explorer-fra", "explorer-tests")])


def test_a_crash_still_writes_the_report_as_void(tmp_path, monkeypatch, capsys):
    """Review: only BudgetExceeded was caught, so any other error lost every result and exited 1 like a FAIL."""
    from .geozarr_fixture import build

    store = build(tmp_path / "s.zarr")

    def boom(*a, **k):
        raise RuntimeError("store exploded")

    monkeypatch.setattr(cli, "StoreContext", boom)
    rc = cli.main(["run", "--collection", "sentinel-3-olci-l1-efr-staging", "--store", str(store), "--stage", "scratch",
                   "--groups", "store", "--out", str(tmp_path / "runs")])
    assert rc == 3
    report_md = next((tmp_path / "runs").glob("*/report.md")).read_text()
    assert "CRASH" in report_md and "store exploded" in report_md


@pytest.mark.parametrize(
    "version, shard, inner, want",
    [
        ("10.10.0", 4096, 1024, 64),  # the silent fallback (EODC 1024 px chunks)
        ("10.11.0", 4096, 1024, 512),  # largest divisor ≤ 512
        ("10.9.0", 1830, 366, 366),
        ("10.11.0", 915, 915, 305),  # single-chunk overview: 915 = 3 x 305
    ],
)
def test_ol_tile_size_ports(version, shard, inner, want):
    meta = {"shape": [shard, shard], "chunk_grid": {"configuration": {"chunk_shape": [shard, shard]}},
            "codecs": [{"name": "sharding_indexed", "configuration": {"chunk_shape": [inner, inner]}}]}
    assert olpredict.tile_size(meta, version) == (want, want)


def test_ol_unsharded_large_chunks_differ_by_version():
    """10.11 also changed unsharded tiling: a 1024 px chunk gets 1024 px tiles instead of 256."""
    meta = {"shape": [4096, 4096], "chunk_grid": {"configuration": {"chunk_shape": [1024, 1024]}}, "codecs": [{"name": "bytes"}]}
    assert olpredict.tile_size(meta, "10.10.0") == (256, 256)
    assert olpredict.decode_ratio(meta, "10.10.0") == 16.0  # ST07 fails at ≥ 16
    assert olpredict.tile_size(meta, "10.11.0") == (1024, 1024)
    assert olpredict.decode_ratio(meta, "10.11.0") == 1.0
