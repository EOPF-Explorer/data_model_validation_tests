"""The battery against the REAL titiler-eopf app at 5fbea81 (the commit /rstaging runs; it reports 0.11.0).

A one-item STAC stub stands in for /stac, so the app resolves the fixture store exactly
as /rstaging resolves a registered item. This reproduces the 6 Oct OLCI bug end to end:
without spatial:shape the real app's tilejson returns 500, and TI02 and TR01 must fail.
Skipped unless the `reader` extra and uvicorn are installed.
"""

import json
import os
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

pytest.importorskip("titiler.eopf")
pytest.importorskip("uvicorn")

from eopf_accept.budget import Budget, Http  # noqa: E402
from eopf_accept.model import FAIL, PASS  # noqa: E402
from eopf_accept.reader_check import tr01_local_reader  # noqa: E402
from eopf_accept.titiler_checks import TitilerBattery  # noqa: E402

from .geozarr_fixture import CFG, build  # noqa: E402

ITEM = "S3B_FIXTURE"
BBOX = [-36.0, 34.0, -34.0, 36.0]


def stac_stub(href: str):
    item = {
        "type": "Feature", "stac_version": "1.0.0", "id": ITEM, "collection": CFG["collection"], "bbox": BBOX,
        "geometry": {"type": "Polygon", "coordinates": [[[BBOX[0], BBOX[1]], [BBOX[2], BBOX[1]], [BBOX[2], BBOX[3]], [BBOX[0], BBOX[3]], [BBOX[0], BBOX[1]]]]},
        "properties": {"datetime": "2026-07-28T12:33:30Z"}, "links": [],
        "assets": {"radianceData": {"href": href, "type": "application/vnd.zarr; version=3", "roles": ["data"]}},
    }
    body = json.dumps({"type": "FeatureCollection", "features": [item], "links": []}).encode()

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _r(self):
            out = body if self.path.split("?")[0].rstrip("/").endswith("search") else b"{}"
            self.send_response(200)
            self.send_header("content-type", "application/geo+json")
            self.send_header("content-length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def do_GET(self):
            self._r()

        def do_POST(self):
            self.rfile.read(int(self.headers.get("content-length") or 0))
            self._r()

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


@pytest.fixture
def titiler_012(tmp_path):
    def start(store_path):
        stub, stub_url = stac_stub(f"{store_path}/measurements")
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        env = {**os.environ, "TITILER_EOPF_STAC_API_URL": stub_url}
        # Log to a file: an unread PIPE fills up and blocks the app mid-request.
        log = open(tmp_path / f"titiler-{port}.log", "w")
        proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "titiler.eopf.main:app", "--port", str(port), "--log-level", "warning"],
                                env=env, stdout=log, stderr=log)
        base = f"http://127.0.0.1:{port}"
        for _ in range(120):
            try:
                if httpx.get(f"{base}/_mgmt/ping", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.25)
        started.append((proc, stub, log))
        return base

    started = []
    yield start
    for proc, stub, log in started:
        proc.terminate()
        proc.wait(10)
        stub.shutdown()
        log.close()


def run_battery(base):
    http = Http(Budget(100))
    return {r.id: r for r in TitilerBattery(http, "local-0.12", {"base": base, "api": "0.12", "expect_version": "0.11.0"}, CFG, ITEM).run()}


def test_healthy_store_on_real_titiler_012(tmp_path, titiler_012):
    store = build(tmp_path / "s.zarr")
    res = run_battery(titiler_012(store))
    for cid in ("TI00", "TI01", "TI02", "TI03", "TI04", "TI06"):
        assert res[cid].status == PASS, (cid, res[cid].summary, res[cid].evidence)
    assert tr01_local_reader(store, CFG).status == PASS


def test_c1_reproduced_on_real_titiler_012(tmp_path, titiler_012):
    """No spatial:shape in the layout or the groups: the real app's tilejson must 500."""
    store = build(tmp_path / "s.zarr", layout_shape=False, group_shape=False)
    res = run_battery(titiler_012(store))
    assert res["TI02"].status == FAIL and "500" in res["TI02"].summary, res["TI02"].summary
    tr = tr01_local_reader(store, CFG)
    assert tr.status == FAIL, tr.evidence


def test_w1_layout_without_transform_on_real_titiler_012(tmp_path, titiler_012):
    """Why ST03 FAILs a layout entry without spatial:transform (W1) but only WARNs one without
    spatial:shape: the real app 500s every tile in the first case and renders the second."""
    no_transform = build(tmp_path / "a.zarr", layout_transform=False)
    res = run_battery(titiler_012(no_transform))
    assert res["TI03"].status == FAIL and "500" in res["TI03"].summary, res["TI03"].evidence
    no_shape = build(tmp_path / "b.zarr", layout_shape=False)
    res = run_battery(titiler_012(no_shape))
    assert res["TI03"].status == PASS, res["TI03"].evidence


def test_c15_reproduced_on_real_titiler_012(tmp_path, titiler_012):
    """No spatial:dimensions on the level groups: tilejson is fine, every tile 500s."""
    store = build(tmp_path / "s.zarr", level_dims=False)
    res = run_battery(titiler_012(store))
    assert res["TI02"].status == PASS
    assert res["TI03"].status == FAIL, res["TI03"].evidence
