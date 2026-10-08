"""Unsigned reads of a public bucket that answers 403 for a missing key (EODC's CI bucket,
where unmerged data-model branches publish their products), proven against a local server."""

import copy
import functools
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import pytest
from obstore.exceptions import PermissionDeniedError

from eopf_accept import cli, store_checks
from eopf_accept.cli import CONFIG_DIR
from eopf_accept.budget import Budget
from eopf_accept.model import PASS, SKIP, VOID
from eopf_accept.store_checks import CHECKS, StoreContext
from eopf_accept.storeio import StoreReader

from .geozarr_fixture import CFG, build


class Forbidding(SimpleHTTPRequestHandler):
    """Static files; a missing key is 403, as on a bucket without list rights."""

    def log_message(self, *a):
        pass

    def send_head(self):
        self.server.paths.append(self.path)
        return super().send_head()

    def send_error(self, code, message=None, explain=None):
        super().send_error(403 if code == 404 else code, message, explain)

    def end_headers(self):
        self.send_header("ETag", '"fixture"')
        super().end_headers()


@pytest.fixture
def bucket(tmp_path):
    """`<base>/bkt/s.zarr` is a healthy fixture store; the server root is tmp_path."""
    build(tmp_path / "bkt" / "s.zarr")
    srv = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Forbidding, directory=str(tmp_path)))
    srv.paths = []
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv, f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def s3_env(monkeypatch, base, *, unsigned):
    for k, v in {"AWS_ENDPOINT_URL": base, "AWS_ALLOW_HTTP": "true", "AWS_REGION": "us-east-1",
                 "AWS_ACCESS_KEY_ID": "test", "AWS_SECRET_ACCESS_KEY": "test"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("AWS_ENDPOINT_URL_S3", raising=False)
    if unsigned:
        monkeypatch.setenv("AWS_SKIP_SIGNATURE", "true")
    else:
        monkeypatch.delenv("AWS_SKIP_SIGNATURE", raising=False)


def test_a_healthy_store_passes_over_https_that_answers_403(bucket):
    """Every anonymous run on EODC was VOID: zarr probed v2 keys (.zarray, .zattrs), the
    bucket answered 403, and only FileNotFoundError meant "missing"."""
    srv, base = bucket
    ctx = StoreContext(StoreReader(f"{base}/bkt/s.zarr", Budget(500), forbidden_is_missing=True), CFG)
    res = {cid: fn(ctx) for cid, fn in CHECKS.items()}
    assert {k: r.status for k, r in res.items()} == {k: PASS for k in CHECKS}, {k: r.summary for k, r in res.items()}
    assert not [p for p in srv.paths if p.endswith((".zarray", ".zattrs", ".zgroup", ".zmetadata"))]


def test_an_absent_optional_group_is_absent_not_a_crash(bucket, monkeypatch):
    """S1 configs list `ascending?`/`descending?`: a 403 for the missing one VOIDed every S1 run."""
    _, base = bucket
    s3_env(monkeypatch, base, unsigned=True)
    cfg = copy.deepcopy(CFG) | {"open_groups": ["measurements", "ascending?"]}
    reader = StoreReader("s3://bkt/s.zarr", Budget(500), forbidden_is_missing=True)
    ctx = StoreContext(reader, cfg)
    assert ctx.absent_optional == ["ascending"]
    assert reader.forbidden == ["ascending/zarr.json"]  # recorded: a refused key looks the same


def test_only_scratch_runs_count_403_as_missing(bucket):
    """A registered run may read the production gateway over https: a 403 there could be an
    ACL problem on a real group, so it must not quietly become "optional group absent"."""
    _, base = bucket
    with pytest.raises(PermissionDeniedError):
        StoreReader(f"{base}/bkt/s.zarr", Budget(5)).node("ascending")


def test_a_scratch_run_reports_the_keys_it_counted_as_missing(bucket, tmp_path, capsys):
    _, base = bucket
    cfg = (CONFIG_DIR / "sentinel-3-olci-l1-efr.toml").read_text().replace(
        'open_groups = ["measurements"]', 'open_groups = ["measurements", "ascending?"]')
    (tmp_path / "olci.toml").write_text(cfg)
    rc = cli.main(["run", "--collection", "sentinel-3-olci-l1-efr-staging", "--config", str(tmp_path / "olci.toml"),
                   "--store", f"{base}/bkt/s.zarr", "--stage", "scratch", "--groups", "store", "--out", str(tmp_path / "runs")])
    report_md = next((tmp_path / "runs").glob("*/report.md")).read_text()
    assert rc in (0, 1), capsys.readouterr().out  # a verdict, not VOID
    assert "- store reads: http, unsigned" in report_md
    assert "- answered 403, counted as missing (1): `ascending/zarr.json`" in report_md


def test_a_signed_reader_still_raises_on_403(bucket, monkeypatch):
    """With credentials, a 403 may be a real permission problem: it must stay loud."""
    _, base = bucket
    s3_env(monkeypatch, base, unsigned=False)
    reader = StoreReader("s3://bkt/s.zarr", Budget(5))
    assert reader.node("") is not None
    with pytest.raises(PermissionDeniedError):
        reader.node("ascending")
    assert reader.access() == f"s3 via {base}, signed with the AWS_* credentials"


@pytest.mark.parametrize("endpoint_var, skip", [("AWS_ENDPOINT_URL", "true"), ("AWS_ENDPOINT", "y")])
def test_plan_says_where_an_s3_store_is_read_from(monkeypatch, capsys, endpoint_var, skip):
    """A shell still holding another run's endpoint sends s3:// reads there. obstore also
    reads AWS_ENDPOINT, and takes "y" as true (both checked against obstore 0.11)."""
    for k in ("AWS_ENDPOINT_URL_S3", "AWS_ENDPOINT_URL", "AWS_ENDPOINT"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv(endpoint_var, "https://objects.example.org")
    monkeypatch.setenv("AWS_SKIP_SIGNATURE", skip)
    cli.main(["plan", "--collection", "sentinel-2-l2a", "--stage", "scratch", "--store", "s3://ci/b/x.zarr", "--groups", "store"])
    assert "store reads: s3 via https://objects.example.org, unsigned" in capsys.readouterr().out


def test_a_store_without_multiscales_skips_rather_than_passes(tmp_path):
    """A config with no multiscales groups (e.g. generic_rechunker output) made ST03, ST07
    and ST08 PASS on nothing."""
    cfg = copy.deepcopy(CFG) | {"multiscales_groups": []}
    ctx = StoreContext(StoreReader(build(tmp_path / "s.zarr"), Budget(500)), cfg)
    res = {cid: fn(ctx) for cid, fn in CHECKS.items()}
    assert {k: res[k].status for k in ("ST03", "ST07", "ST08", "ST09")} == dict.fromkeys(("ST03", "ST07", "ST08", "ST09"), SKIP)


def test_st08_skips_when_no_array_matches_its_pattern(tmp_path):
    """Levels without a matching array left ST08 at "dtypes allowed on every level" after 0 arrays."""
    cfg = copy.deepcopy(CFG) | {"dtype_check_pattern": "^no_such_variable$"}
    ctx = StoreContext(StoreReader(build(tmp_path / "s.zarr"), Budget(500)), cfg)
    assert store_checks.st08_dtype_and_compression(ctx).status == SKIP


def test_one_crashing_check_keeps_the_other_verdicts(tmp_path, monkeypatch, capsys):
    """One raising check used to replace every store verdict with a single CRASH row."""
    def boom(ctx):
        raise RuntimeError("check exploded")

    monkeypatch.setitem(store_checks.CHECKS, "HT02", boom)
    rc = cli.main(["run", "--collection", "sentinel-3-olci-l1-efr-staging", "--store", str(build(tmp_path / "s.zarr")),
                   "--stage", "scratch", "--groups", "store", "--out", str(tmp_path / "runs")])
    out = capsys.readouterr().out
    assert rc == 3  # no FAIL, one VOID
    assert f"{VOID:5s} HT02   host" in out and "check exploded" in out  # HT02 keeps its own group
    assert f"{PASS:5s} ST03" in out and f"{PASS:5s} ST07" in out
    assert "- store reads: local path" in next((tmp_path / "runs").glob("*/report.md")).read_text()
