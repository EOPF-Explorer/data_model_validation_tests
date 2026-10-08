"""A programmable stand-in for titiler-eopf (and a one-item STAC API), so checks can be shown to fail."""

import io
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl, urlsplit

import numpy as np
from PIL import Image

ASSET_ROUTE = "/collections/{collection_id}/items/{item_id}/assets/{asset_id}"


class State:
    def __init__(self):
        self.version = "0.12.2"  # what /rstaging reports
        self.api = "0.12"  # which routes /api lists
        self.tilejson_status = 200
        self.zooms = (5, 9)  # tilejson minzoom, maxzoom
        self.tile = "good"  # good | empty | gray | sparse (5 % valid)
        self.x_cache = "MISS"
        self.bounds = [-36.0, 34.0, -34.0, 36.0]
        self.force_status: int | None = None  # answer everything with this (503, 302)
        self.flipped_status = 422  # what /rstaging/ answers for links in 0.11 syntax
        self.item: dict | None = None
        self.requests: list[str] = []


def _png(n_bands: int, mode: str) -> bytes:
    rng = np.random.default_rng(1)
    h = w = 256
    alpha = np.full((h, w), 0 if mode == "empty" else 255, np.uint8)
    if mode == "sparse":
        alpha[h // 20:, :] = 0
    base = rng.integers(0, 255, (h, w), dtype=np.uint8)
    if n_bands >= 3:
        bands = [base] * 3 if mode == "gray" else [base, np.roll(base, 1), np.roll(base, 2)]
        img = Image.fromarray(np.dstack([*bands, alpha]), "RGBA")
    else:
        img = Image.fromarray(np.dstack([base, alpha]), "LA")
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def serve(state: State):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def send(self, code, body: bytes, ctype, extra=None):
            self.send_response(code)
            self.send_header("content-type", ctype)
            self.send_header("x-cache", state.x_cache)
            self.send_header("content-length", str(len(body)))
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            state.requests.append(self.path)
            if state.force_status:
                return self.send(state.force_status, b"forced", "text/plain", {"location": "/elsewhere"})
            u = urlsplit(self.path)
            q = parse_qsl(u.query)
            keys = [k for k, _ in q]
            n = keys.count("variables") or next((len(v.split("bands=")[1].split(",")) for k, v in q if k == "assets"), 1)
            if "/stac/collections/" in u.path:
                return self.send(200 if state.item else 404, json.dumps(state.item or {}).encode(), "application/geo+json")
            if u.path.startswith("/rstaging/") and state.flipped_status != 200 and any(k == "variables" and v.startswith("/") for k, v in q):
                return self.send(state.flipped_status, b'{"detail":"bad variables"}', "application/json")
            if u.path.endswith("/api"):
                paths = {"/collections/{collection_id}/items/{item_id}/info": {}}
                if state.api == "0.12":
                    paths[ASSET_ROUTE + "/info"] = {}
                return self.send(200, json.dumps({"info": {"version": state.version}, "paths": paths}).encode(), "application/json")
            if any(k == "variables" and v.startswith("/") for k, v in q) and state.api == "0.12" and not u.path.startswith("/rstaging/"):
                return self.send(422, b'{"detail":"bad variables"}', "application/json")
            if "bands" in keys and "/assets/" in u.path:
                return self.send(500, b"Internal Server Error", "text/plain")
            if u.path.endswith("tilejson.json"):
                if state.tilejson_status != 200:
                    return self.send(state.tilejson_status, b"Internal Server Error", "text/plain")
                tj = {"minzoom": state.zooms[0], "maxzoom": state.zooms[1], "bounds": state.bounds, "tiles": []}
                return self.send(200, json.dumps(tj).encode(), "application/json")
            if u.path.endswith(".png"):
                return self.send(200, _png(n, state.tile), "image/png")
            if u.path.endswith("/info"):
                return self.send(200, json.dumps({"bounds": [-36, 34, -34, 36]}).encode(), "application/json")
            if u.path.endswith("map.html") or u.path.endswith("/viewer"):
                return self.send(200, b"<html></html>", "text/html; charset=utf-8")
            return self.send(404, b"not found", "text/plain")

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"
