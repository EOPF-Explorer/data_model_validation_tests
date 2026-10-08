"""Unsigned reads of a public bucket that answers 403 for a missing key (EODC's CI bucket,
where unmerged data-model branches publish their products), proven against a local server."""

import copy
import functools
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import pytest
from obstore.exceptions import PermissionDeniedError

from eopf_accept import cli, store_checks
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
    ctx = StoreContext(StoreReader(f"{base}/bkt/s.zarr", Budget(500)), CFG)
    res = {cid: fn(ctx) for cid, fn in CHECKS.items()}
    assert {k: r.status for k, r in res.items()} == {k: PASS for k in CHECKS}, {k: r.summary for k, r in res.items()}
    assert not [p for p in srv.paths if p.endswith((".zarray", ".zattrs", ".zgroup", ".zmetadata"))]


def test_an_absent_optional_group_is_absent_not_a_crash(bucket, monkeypatch):
    """S1 configs list `ascending?`/`descending?`: a 403 for the missing one VOIDed every S1 run."""
    _, base = bucket
    s3_env(monkeypatch, base, unsigned=True)
    cfg = copy.deepcopy(CFG) | {"open_groups": ["measurements", "ascending?"]}
    ctx = StoreContext(StoreReader("s3://bkt/s.zarr", Budget(500)), cfg)
    assert ctx.absent_optional == ["ascending"]


def test_a_signed_reader_still_raises_on_403(bucket, monkeypatch):
    """With credentials, a 403 may be a real permission problem: it must stay loud."""
    _, base = bucket
    s3_env(monkeypatch, base, unsigned=False)
    reader = StoreReader("s3://bkt/s.zarr", Budget(5))
    assert reader.node("") is not None
    with pytest.raises(PermissionDeniedError):
        reader.node("ascending")
    assert reader.access() == f"s3 via {base}, signed with the AWS_* credentials"


def test_plan_says_where_an_s3_store_is_read_from(monkeypatch, capsys):
    """A shell still holding another run's AWS_ENDPOINT_URL sends s3:// reads there."""
    monkeypatch.setenv("AWS_ENDPOINT_URL", "https://objects.example.org")
    monkeypatch.setenv("AWS_SKIP_SIGNATURE", "true")
    monkeypatch.delenv("AWS_ENDPOINT_URL_S3", raising=False)
    cli.main(["plan", "--collection", "sentinel-2-l2a", "--stage", "scratch", "--store", "s3://ci/b/x.zarr", "--groups", "store"])
    assert "store reads: s3 via https://objects.example.org, unsigned" in capsys.readouterr().out


def test_a_store_without_multiscales_skips_rather_than_passes(tmp_path):
    """A config with no multiscales groups (e.g. generic_rechunker output) made ST03, ST07
    and ST08 PASS on nothing."""
    cfg = copy.deepcopy(CFG) | {"multiscales_groups": []}
    ctx = StoreContext(StoreReader(build(tmp_path / "s.zarr"), Budget(500)), cfg)
    res = {cid: fn(ctx) for cid, fn in CHECKS.items()}
    assert {k: res[k].status for k in ("ST03", "ST07", "ST08", "ST09")} == dict.fromkeys(("ST03", "ST07", "ST08", "ST09"), SKIP)


def test_one_crashing_check_keeps_the_other_verdicts(tmp_path, monkeypatch, capsys):
    """One raising check used to replace every store verdict with a single CRASH row."""
    def boom(ctx):
        raise RuntimeError("check exploded")

    monkeypatch.setitem(store_checks.CHECKS, "ST07", boom)
    rc = cli.main(["run", "--collection", "sentinel-3-olci-l1-efr-staging", "--store", str(build(tmp_path / "s.zarr")),
                   "--stage", "scratch", "--groups", "store", "--out", str(tmp_path / "runs")])
    out = capsys.readouterr().out
    assert rc == 3  # no FAIL, one VOID
    assert f"{VOID:5s} ST07" in out and "check exploded" in out
    assert f"{PASS:5s} ST03" in out and f"{PASS:5s} HT02" in out
