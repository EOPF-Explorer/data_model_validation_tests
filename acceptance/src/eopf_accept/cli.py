"""eopf-accept: is a freshly built GeoZarr store fit for every consumer?

    eopf-accept plan --collection <id> --stage scratch|registered --store <url> [--item <id>] [--endpoint ...]
    eopf-accept run  …same arguments…

`plan` prints what would run, the URL forms per endpoint and the request bound, and
sends nothing. `run` refuses to start if the planned bound exceeds --max-requests, and
stops at the cap regardless (every physical request spends the budget first).
Titiler checks run only against endpoints named with --endpoint: no target is implied.
Exit codes: 0 no FAIL, 1 at least one FAIL, 2 usage/config/budget refusal, 3 VOID (re-run).
"""

import argparse
import datetime as dt
import sys
import tomllib
import traceback
from pathlib import Path
from urllib.parse import urlparse

from . import registration as rg
from . import report
from .budget import Budget, BudgetExceeded, Http
from .model import FAIL, VOID, Result, apply_known_issues
from .store_checks import CHECKS, REQUEST_BOUNDS, StoreContext
from .storeio import StoreReader
from .titiler_checks import TitilerBattery, urls

CONFIG_DIR = Path(__file__).resolve().parents[2] / "configs"
DEFAULT_OUT = Path.home() / "DevDS" / "EOPF" / "acceptance_runs"
PRODUCTION_HOSTS = {"api.explorer.eopf.copernicus.eu", "s3.explorer.eopf.copernicus.eu"}
PRODUCTION_BUCKETS = {"esa-zarr-sentinel-explorer-fra"}  # the registered stores, in any URL form
GROUPS = ("store", "reader", "titiler", "registration")
GATEWAY_NOTE = ("Read path: the S3 gateway caches objects for up to 1 h and ignores query strings, so a store "
                "rewritten less than an hour ago may be read as its previous version. Prefer the s3:// origin.")
SIDE_EFFECT = ("Side effect: each cache-busted titiler request that renders (a MISS) makes titiler write "
               "one tile entry to the shared Redis and S3 cache bucket. Nothing else is written.")


def load_config(collection: str, config: str | None, config_dir: Path) -> dict:
    paths = [Path(config)] if config else sorted(config_dir.glob("*.toml"))
    for p in paths:
        cfg = tomllib.loads(p.read_text())
        if config or collection in cfg.get("collections", []):
            cfg["collection"] = collection
            cfg["_config_path"] = str(p)
            return cfg
    raise SystemExit(f"no config in {config_dir} lists collection {collection!r} (use --config)")


def parse_endpoints(specs: list[str], cfg: dict) -> dict[str, dict]:
    """`NAME` (from the config) or `NAME=URL@VERSION:API` (ad hoc, e.g. a local server)."""
    out = {}
    for s in specs:
        if "=" in s:
            name, rest = s.split("=", 1)
            base, _, tail = rest.rpartition("@")
            version, _, api = tail.partition(":")
            if not (base and version and api in ("0.11", "0.12")):
                raise SystemExit(f"--endpoint {s!r}: want NAME=URL@VERSION:API, API being 0.11 or 0.12")
            out[name] = {"base": base, "expect_version": version, "api": api}
        elif s in (cfg.get("endpoints") or {}):
            out[s] = cfg["endpoints"][s]
        else:
            raise SystemExit(f"--endpoint {s!r}: not in the config's [endpoints]")
    return out


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="eopf-accept", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("plan", "run"):
        s = sub.add_parser(name)
        s.add_argument("--collection", required=True)
        s.add_argument("--config", help="a collection config TOML (default: the one in --config-dir listing --collection)")
        s.add_argument("--config-dir", type=Path, default=CONFIG_DIR, help="where collection configs live (e.g. a private directory)")
        s.add_argument("--store", required=True, help="s3://… or a local path (origin), or the https:// gateway URL")
        s.add_argument("--item", help="STAC item id (titiler and registration checks)")
        s.add_argument("--stage", choices=("scratch", "registered"), required=True)
        s.add_argument("--endpoint", action="append", default=[], help="NAME from the config, or NAME=URL@VERSION:API")
        s.add_argument("--groups", default=",".join(GROUPS), help=f"comma list of {GROUPS}")
        s.add_argument("--center", help="lon,lat for tiles (default: the item config, else inside the footprint)")
        s.add_argument("--max-requests", type=int, default=300)
        s.add_argument("--out", type=Path, default=DEFAULT_OUT)
        s.add_argument("--label", default="run")
    return p


def guard_stage(args, endpoints: dict) -> None:
    """Scratch runs never touch production: /rstaging can't serve an unregistered store anyway."""
    if args.stage != "scratch":
        return
    hosts = {urlparse(e["base"]).hostname for e in endpoints.values()} | {urlparse(args.store).hostname}
    if hosts & PRODUCTION_HOSTS:
        raise SystemExit(f"--stage scratch refuses production hosts {sorted(hosts & PRODUCTION_HOSTS)}")
    if bucket := next((b for b in PRODUCTION_BUCKETS if b in args.store), None):
        raise SystemExit(f"--stage scratch refuses the production bucket {bucket}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = load_config(args.collection, args.config, args.config_dir)
    endpoints = parse_endpoints(args.endpoint, cfg)
    groups = set(args.groups.split(","))
    if unknown := groups - set(GROUPS):
        raise SystemExit(f"unknown --groups {sorted(unknown)}")
    if args.stage == "scratch":
        groups.discard("registration")
    else:
        groups.discard("reader")  # the local reader reads outside the budget: scratch only
    if not endpoints:
        groups.discard("titiler")
    if groups & {"titiler", "registration"} and not args.item:
        raise SystemExit("titiler and registration checks need --item")
    if "registration" in groups and not cfg.get("stac"):
        raise SystemExit("registration checks need `stac` in the config")
    guard_stage(args, endpoints)
    center = [float(v) for v in args.center.split(",")] if args.center else None

    budget = Budget(args.max_requests)
    if "store" in groups:
        for check_id, bound in REQUEST_BOUNDS.items():
            budget.reserve(check_id, bound)
    if "registration" in groups:
        budget.reserve("registration", rg.REQUEST_BOUND)
    if "titiler" in groups:
        for name, ep in endpoints.items():
            budget.reserve(f"titiler:{name}", TitilerBattery.request_bound(cfg, ep["api"]))

    if args.cmd == "plan":
        print(f"config: {cfg['_config_path']}")
        print(f"store: {args.store}  stage: {args.stage}  groups: {sorted(groups)}")
        if urlparse(args.store).hostname in PRODUCTION_HOSTS:
            print(GATEWAY_NOTE)
        for name, ep in endpoints.items() if "titiler" in groups else []:
            item_extra = (cfg.get("items", {}).get(args.item) or {}).get("render_extra")
            prefix, params = urls(ep["base"], args.collection, args.item or "<item>", cfg["render"], ep["api"], item_extra=item_extra)
            print(f"endpoint {name} (api {ep['api']}, reports {ep['expect_version']}): {prefix}/WebMercatorQuad/tilejson.json?" + "&".join(f"{k}={v}" for k, v in params))
        print("request upper bound: " + ", ".join(f"{k}={v}" for k, v in sorted(budget.planned.items())) + f" → {sum(budget.planned.values())} of --max-requests {budget.max}")
        if "titiler" in groups or "registration" in groups:
            print(SIDE_EFFECT)
        try:
            budget.assert_plan_fits()
        except BudgetExceeded as exc:
            print(f"REFUSED: {exc}")
            return 2
        print("fits; nothing was sent")
        return 0

    try:
        budget.assert_plan_fits()
    except BudgetExceeded as exc:
        print(f"refusing to start: {exc}", file=sys.stderr)
        return 2

    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    out_dir = args.out / f"{stamp}_{args.collection}_{args.stage}_{args.label}"
    out_dir.mkdir(parents=True, exist_ok=True)
    http = Http(budget, log_path=out_dir / "requests.jsonl")
    started = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
    results: list[Result] = []
    reader = StoreReader(args.store, budget)
    try:
        if "store" in groups:
            ctx = StoreContext(reader, cfg)
            results += [fn(ctx) for fn in CHECKS.values()]
        if "reader" in groups:
            from .reader_check import tr01_local_reader

            results.append(tr01_local_reader(args.store, cfg, center))
        item = footprint = None
        if "registration" in groups:
            item = rg.fetch_item(http, cfg["stac"], args.collection, args.item)
            footprint = item.get("geometry")
            results += [rg.rg08_hrefs(item, args.store, cfg), rg.rg07_fresh(item, reader, http)]
            results += rg.rg04_rg05_links(http, item, cfg)
        if "titiler" in groups:
            for name, ep in endpoints.items():
                results += TitilerBattery(http, name, ep, cfg, args.item, center, footprint).run()
    except BudgetExceeded as exc:
        results.append(Result("BUDGET", "run", FAIL, f"stopped: {exc}"))
    except Exception as exc:  # keep the partial results; a crash is no verdict
        results.append(Result("CRASH", "run", VOID, f"stopped by {type(exc).__name__}: {exc}; no verdict, fix and re-run",
                              traceback.format_exc().splitlines()[-12:]))
    finally:
        http.close()

    apply_known_issues(results, cfg.get("known_issues", []), dt.date.today())
    meta = {
        "collection": args.collection, "store": args.store, "item": args.item, "stage": args.stage,
        "endpoints": {n: f"{e['expect_version']} (api {e['api']})" for n, e in endpoints.items()}, "config": cfg["_config_path"],
        "started": started, "finished": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "nonce": http.nonce, "requests_used": budget.used, "max_requests": budget.max,
        "read_path": GATEWAY_NOTE if urlparse(args.store).hostname in PRODUCTION_HOSTS else "origin",
    }
    _, report_md = report.write(out_dir, meta, results)
    v = report.verdict(results)
    for r in results:
        print(f"{r.status:5s} {r.id:6s} {r.group:18s} {r.summary}")
    print(f"\n{v}: {report_md}")
    return {"PASS": 0, "FAIL": 1, "VOID": 3}[v]


if __name__ == "__main__":
    sys.exit(main())
