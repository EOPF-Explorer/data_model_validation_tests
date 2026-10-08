"""Store checks (ST*) and the no-listing open (HT02).

The groups to check come from the collection config (`open_groups`,
`multiscales_groups`) and each multiscales layout. They are never derived by walking
the consolidated tree: an unconsolidated root would then yield nothing to check and
"pass" (the conformance harness blind spot, F3 in the 24 Sep findings).
"""

import math
import re
from urllib.parse import urlparse

import numpy as np
import zarr
from geozarr_toolkit import Multiscales, Proj, Spatial
from pydantic import ValidationError

from . import conventions as cv
from . import olpredict
from .model import FAIL, PASS, SKIP, WARN, Result
from .storeio import ListingNotAllowed, StoreReader

# Declared upper bounds on requests, summed by the CLI before anything is sent.
# Generous: they bound the run, they are not estimates.
REQUEST_BOUNDS = {"store-metadata": 80, "ST08": 30, "ST09": 30, "HT02": 10}


def _attrs(node: dict | None) -> dict:
    return (node or {}).get("attributes") or {}


def _consolidated(node: dict | None) -> dict | None:
    cm = (node or {}).get("consolidated_metadata")
    return None if cm is None else (cm.get("metadata") or {})


def _data_arrays(level_prefix: str, cm: dict | None) -> dict[str, dict]:
    """name -> array metadata for the ≥2-D, non-coordinate arrays directly in a level group."""
    out = {}
    for key, meta in (cm or {}).items():
        if not key.startswith(level_prefix + "/") or "/" in key[len(level_prefix) + 1:]:
            continue
        name = key[len(level_prefix) + 1:]
        if meta.get("node_type") != "array" or len(meta.get("shape", [])) < 2:
            continue
        if name in (meta.get("dimension_names") or []):
            continue
        out[name] = meta
    return out


def _resolution(transform) -> float | None:
    try:
        return abs(float(transform[0]))
    except (TypeError, ValueError, IndexError):
        return None


def _same_transform(a, b) -> bool:
    try:
        return len(a) == len(b) and bool(np.allclose(a, b, rtol=1e-9, atol=0))
    except (TypeError, ValueError):  # non-numeric: an ST03 FAIL, not a crash of every store check
        return False


def _bounds_vs_bbox(transform, shape, bbox) -> str | None:
    """titiler takes zooms from a level's transform and shape but tilejson bounds from the
    group's spatial:bbox: they must describe the same extent, within one pixel of the level."""
    try:
        a, _, c, _, e, f = (float(v) for v in transform[:6])
        h, w = shape[-2:]
        west, south, east, north = (float(v) for v in bbox[:4])
    except (TypeError, ValueError, IndexError):
        return None  # missing pieces are reported by the presence checks
    xs, ys = sorted((c, c + a * w)), sorted((f, f + e * h))
    if abs(xs[0] - west) > abs(a) or abs(xs[1] - east) > abs(a) or abs(ys[0] - south) > abs(e) or abs(ys[1] - north) > abs(e):
        return (f"spatial:transform × spatial:shape gives bounds [{xs[0]:.6g}, {ys[0]:.6g}, {xs[1]:.6g}, {ys[1]:.6g}], "
                f"more than a pixel from the group's spatial:bbox {list(bbox)}: zooms and bounds would disagree")
    return None


EOPF_TOP_GROUPS = ("measurements", "conditions", "quality")


def _subroot_of(href: str) -> str | None:
    """`/S01SIWGRD_…_065517/measurements/grd` -> `S01SIWGRD_…_065517`: the group that holds a
    product's measurements/conditions/quality (a sub-root, data-model#291 Ex.2). An absolute href
    (`s3://bucket/…/P.zarr/S01…/measurements/grd`) counts from after the `.zarr` segment."""
    parts = [p for p in urlparse(href).path.split("/") if p not in ("", ".")]
    if zarr_at := [i for i, p in enumerate(parts) if p.endswith(".zarr")]:
        parts = parts[zarr_at[-1] + 1:]
    i = next((i for i, p in enumerate(parts) if p in EOPF_TOP_GROUPS), 0)
    return "/".join(parts[:i]) or None


GROUP_KEYS = ("open_groups", "multiscales_groups", "consolidated_groups", "visible_groups")


def _with_subroot(value, subroot: str):
    """`{subroot}` replaced in every string of a config (TOML dates and numbers kept as they are)."""
    if isinstance(value, str):
        return value.replace("{subroot}", subroot)
    if isinstance(value, list):
        return [_with_subroot(v, subroot) for v in value]
    if isinstance(value, dict):
        return {k: _with_subroot(v, subroot) for k, v in value.items()}
    return value


class StoreContext:
    """Metadata read once and shared by the checks."""

    def __init__(self, reader: StoreReader, cfg: dict, item: str | None = None):
        self.reader = reader
        self.root = reader.node("")
        self.nodes: dict[str, dict | None] = {"": self.root}
        # A sub-root's name differs per product (S1: `S01SIWGRD_<start>_…_<id>`), so a config names
        # it as `{subroot}` and says which asset of the root's own stac_discovery points into it.
        self.subroot = self.subroot_problem = None
        if cfg.get("consolidation") == "subroot" and not cfg.get("subroot_asset"):
            self.subroot_problem = 'consolidation = "subroot" needs subroot_asset to find the sub-root: nothing was checked'
        if asset := cfg.get("subroot_asset"):
            href = (((_attrs(self.root).get("stac_discovery") or {}).get("assets") or {}).get(asset) or {}).get("href") or ""
            self.subroot = _subroot_of(href)
            if self.subroot is None:
                self.subroot_problem = (f"sub-root unknown: the root's stac_discovery has no asset {asset!r} whose href "
                                        f"lies under a sub-root (href {href!r})")
        if self.subroot:  # every group path in the config: open/multiscales/visible groups, items, assets, render
            cfg = _with_subroot(cfg, self.subroot)
        else:  # no request for literal `{subroot}/…` keys: ST01 reports the one cause
            cfg = {**cfg, **{k: [g for g in cfg[k] if "{subroot}" not in g] for k in GROUP_KEYS if k in cfg}}
        self.cfg = cfg
        self.consolidated_groups: list[str] = list(cfg.get("consolidated_groups", []))
        for g in [self.subroot, *self.consolidated_groups]:
            if g and g not in self.nodes:
                self.nodes[g] = reader.node(g)
        # A trailing "?" marks a group as optional (an S1 cube may have one orbit only).
        # Absent optional groups are dropped; if every listed group is optional and
        # absent, ST01 fails, because then there is nothing to open. An item can declare
        # which groups it has (`items."<id>".groups`); ST01 then requires exactly those.
        self.expected_groups: list[str] | None = (cfg.get("items", {}).get(item) or {}).get("groups") if item else None
        self.absent_optional: list[str] = []
        names = [g.rstrip("?") for g in cfg.get("open_groups", []) + cfg.get("multiscales_groups", [])]
        optional = {g.rstrip("?") for g in cfg.get("open_groups", []) + cfg.get("multiscales_groups", []) if g.endswith("?")}
        for g in dict.fromkeys(names):
            node = self.nodes[g] if g in self.nodes else reader.node(g)
            if node is None and g in optional:
                self.absent_optional.append(g)
            else:
                self.nodes[g] = node
        def present(key: str) -> list[str]:
            return [g.rstrip("?") for g in cfg.get(key, []) if g.rstrip("?") in self.nodes]

        self.open_groups, self.ms_groups = present("open_groups"), present("multiscales_groups")
        self.levels: dict[str, list[tuple[dict, str, dict | None]]] = {}
        for g in self.ms_groups:
            layout = (_attrs(self.nodes[g]).get("multiscales") or {}).get("layout") or []
            self.levels[g] = [(e, f"{g}/{e.get('asset')}", reader.node(f"{g}/{e.get('asset')}")) for e in layout]
        self._arrays: dict[str, dict[str, dict]] = {}
        self.fallback_levels: set[str] = set()  # levels seen only through sample_variables

    def arrays(self, g: str, level_path: str) -> dict[str, dict]:
        """Data arrays of a level: from consolidated metadata, else the configured variables."""
        if level_path not in self._arrays:  # six checks ask; read each zarr.json once
            found = _data_arrays(level_path[len(g) + 1:], _consolidated(self.nodes[g]))
            if not found:
                metas = {v: self.reader.node(f"{level_path}/{v}") for v in self.cfg.get("sample_variables", [])}
                found = {v: m for v, m in metas.items() if m}
                self.fallback_levels.add(level_path)
            self._arrays[level_path] = found
        return self._arrays[level_path]


def st01_consolidated(ctx: StoreContext) -> Result:
    """data-model#291: every root (Ex.1) or sub-root (Ex.2, `consolidation = "subroot"`) is
    consolidated, and so is every group a STAC asset points to; plus what titiler opens."""
    fails, warns = [], []
    at_subroot = ctx.cfg.get("consolidation") == "subroot"
    if ctx.subroot_problem:
        fails.append(ctx.subroot_problem)
    if not ctx.open_groups:
        fails.append(f"no group to open: every configured group is absent ({ctx.absent_optional})")
    if ctx.expected_groups is not None:
        present = set(ctx.open_groups) | set(ctx.ms_groups)
        fails += [f"{g}: the item config expects this group, the store doesn't have it" for g in ctx.expected_groups if g not in present]
        fails += [f"{g}: present, but the item config's groups {ctx.expected_groups} don't list it" for g in sorted(present - set(ctx.expected_groups))]
    roots = ([ctx.subroot] if ctx.subroot else []) if at_subroot else [""]
    for g in dict.fromkeys([*roots, *ctx.open_groups, *ctx.ms_groups, *ctx.consolidated_groups]):
        node = ctx.nodes.get(g)
        label = g or "/"
        if node is None:
            fails.append(f"{label}: no zarr.json")
        elif _consolidated(node) is None:
            fails.append(f"{label}: no consolidated_metadata")
    for g, levels in ctx.levels.items():
        cm = _consolidated(ctx.nodes[g])
        for entry, path, node in levels:
            asset = entry.get("asset")
            if node is None:
                fails.append(f"{path}: layout names it but it has no zarr.json")
            elif _consolidated(node) is None:
                warns.append(f"{path}: no consolidated_metadata (level group)")
            if cm is not None and asset not in cm:
                fails.append(f"{g}: consolidated metadata does not list level {asset!r}")
    rows = [f"optional group(s) absent: {ctx.absent_optional}"] if ctx.absent_optional else []
    if at_subroot:
        rows.append(f"sub-root {ctx.subroot or '?'} checked instead of the root (data-model#291 Ex.2)")
    if fails:
        return Result("ST01", "store", FAIL, f"{len(fails)} group(s) missing, not consolidated or incomplete", fails + warns + rows, problems=fails)
    if warns:
        return Result("ST01", "store", WARN, f"{len(warns)} level group(s) without their own consolidated metadata", warns + rows)
    return Result("ST01", "store", PASS, f"{'sub-root' if at_subroot else 'root'}, opened, multiscales and listed groups are consolidated", rows)


def st03_multiscales(ctx: StoreContext) -> Result:
    """Emulates what titiler-eopf 0.12 needs for tilejson without zoom params (C1)."""
    if not ctx.ms_groups:  # a config listing none: nothing was checked, so no PASS
        return Result("ST03", "store", SKIP, "no multiscales group to check (the config lists none, or every listed one is absent)")
    fails, warns = [], []
    min_levels = int(ctx.cfg.get("min_levels", 2))
    for g in ctx.ms_groups:
        if ctx.nodes.get(g) is None:
            fails.append(f"{g}: the multiscales group is absent (no zarr.json)")
            continue
        a = _attrs(ctx.nodes.get(g))
        decl = cv.declared(a)
        missing = [cv.NAME[u] for u in (cv.MULTISCALES, cv.SPATIAL, cv.PROJ) if u not in decl]
        if missing:
            fails.append(f"{g}: zarr_conventions lacks {missing} (titiler-eopf ignores the group)")
        if not a.get("spatial:bbox"):
            fails.append(f"{g}: no spatial:bbox (titiler-eopf get_bounds asserts on it)")
        if not any(k in a for k in ("proj:code", "proj:wkt2", "proj:projjson")):
            fails.append(f"{g}: no proj:code / proj:wkt2 / proj:projjson")
        if cv.SPATIAL in decl and "spatial:dimensions" not in a:
            fails.append(f"{g}: declares spatial but has no spatial:dimensions (titiler-eopf 0.12 tiles fail with KeyError)")
        levels = ctx.levels.get(g, [])
        if len(levels) < min_levels:
            fails.append(f"{g}: {len(levels)} layout entries, want ≥ {min_levels}")
            continue
        res = []
        for i, (entry, path, node) in enumerate(levels):
            la = _attrs(node)
            in_layout = "spatial:shape" in entry and "spatial:transform" in entry
            in_group = "spatial:shape" in la and "spatial:transform" in la
            if i in (0, len(levels) - 1) and not (in_layout or in_group):
                which = "maxzoom" if i == 0 else "minzoom"
                fails.append(f"{path}: no spatial:shape+spatial:transform in the layout entry or the group; {which} can't be derived, so tilejson without zoom params returns 500")
            # titiler-eopf 0.12 (5fbea81) reads layout["spatial:transform"] with no fallback:
            # get_multiscale_level for every level, and _get_variable's
            # `attrs.get("spatial:transform", layout["spatial:transform"])` evaluates its default
            # first. A transform on the level group alone doesn't help: every tile returns 500.
            if "spatial:transform" not in entry:
                fails.append(f"{path}: layout entry has no spatial:transform; titiler-eopf 0.12 reads it unguarded (reader.py get_multiscale_level, _get_variable), so every tile returns 500")
            elif "spatial:shape" not in entry and "spatial:shape" in la:
                warns.append(f"{path}: layout entry lacks spatial:shape (the group has it)")
            if "spatial:shape" not in la:
                warns.append(f"{path}: group has no spatial:shape")
            if cv.SPATIAL in cv.declared(la) and "spatial:dimensions" not in la:
                fails.append(f"{path}: declares spatial but has no spatial:dimensions; titiler-eopf 0.12 reads it unguarded (reader.py _get_variable), so tiles from this level return 500")
            if "spatial:shape" in entry and "spatial:shape" in la and list(entry["spatial:shape"]) != list(la["spatial:shape"]):
                fails.append(f"{path}: layout spatial:shape {entry['spatial:shape']} != group {la['spatial:shape']}")
            et, lt = entry.get("spatial:transform"), la.get("spatial:transform")
            if et and lt and not _same_transform(et, lt):
                fails.append(f"{path}: layout spatial:transform {et} != group {lt}")
            if problem := _bounds_vs_bbox(entry.get("spatial:transform") or la.get("spatial:transform"),
                                          entry.get("spatial:shape") or la.get("spatial:shape"), a.get("spatial:bbox")):
                fails.append(f"{path}: {problem}")
            shape = entry.get("spatial:shape") or la.get("spatial:shape")
            for name, meta in list(ctx.arrays(g, path).items())[:3]:
                if shape and list(meta["shape"][-2:]) != list(shape):
                    fails.append(f"{path}/{name}: array shape {meta['shape'][-2:]} != spatial:shape {shape}")
            res.append(_resolution(entry.get("spatial:transform") or la.get("spatial:transform")))
        if None not in res and any(b <= a_ for a_, b in zip(res, res[1:])):
            fails.append(f"{g}: layout resolutions {res} are not strictly coarsening (titiler takes layout[0] as the finest)")
    if fails:
        return Result("ST03", "store", FAIL, f"{len(fails)} multiscales problem(s)", fails + warns)
    if warns:
        return Result("ST03", "store", WARN, "multiscales usable by titiler, with gaps", warns)
    return Result("ST03", "store", PASS, f"{sum(len(v) for v in ctx.levels.values())} levels, each with spatial:shape and spatial:transform")


def _all_nodes(ctx: StoreContext) -> list[tuple[str, dict | None]]:
    """The configured groups and their levels and arrays (the floor: never derived from the
    tree, see the module docstring), plus every other node the root's consolidated metadata
    lists, so groups outside the config (S1 `<orbit>/conditions`) are checked too (W13)."""
    nodes = [(g or "/", ctx.nodes[g]) for g in ctx.nodes]
    for g, levels in ctx.levels.items():
        for _, path, node in levels:
            nodes.append((path, node))
            nodes += [(f"{path}/{n}", m) for n, m in ctx.arrays(g, path).items()]
    seen = {p for p, _ in nodes}
    nodes += [(p, m) for p, m in sorted((_consolidated(ctx.root) or {}).items()) if p not in seen]
    if ctx.subroot:
        seen = {p for p, _ in nodes}
        nodes += [(f"{ctx.subroot}/{p}", m) for p, m in sorted((_consolidated(ctx.nodes.get(ctx.subroot)) or {}).items())
                  if f"{ctx.subroot}/{p}" not in seen]
    return nodes


def st04_visibility(ctx: StoreContext) -> Result:
    """Two rules, both FAIL: (1) no node uses spatial:/proj: keys without declaring them in
    zarr_conventions (the zarr-conventions spec forbids implicit conventions); (2) every group
    meant to render is visible to titiler-eopf 0.12, which sees a group only through its
    declared conventions (C4: `scl`)."""
    implicit, invisible = [], []
    for path, node in _all_nodes(ctx):
        a = _attrs(node)
        undeclared = sorted(cv.NAME[u] for u in cv.used(a) - cv.declared(a))
        if undeclared and node:
            implicit.append(f"{path}: implicit conventions: uses {undeclared} keys without declaring them in zarr_conventions")
    for g in dict.fromkeys(ctx.open_groups + list(ctx.cfg.get("visible_groups", []))):
        node = ctx.nodes.get(g) or ctx.reader.node(g)
        if node is None:
            invisible.append(f"{g}: no zarr.json")
            continue
        arrays = [m.get("attributes") or {} for m in (_consolidated(node) or {}).values() if m.get("node_type") == "array"]
        if not cv.titiler_visible(_attrs(node), arrays):
            invisible.append(f"{g}: invisible to titiler-eopf 0.12 (_get_groups needs spatial+proj declared on the group, or on an array of a group without conventions)")
    fails = invisible + implicit
    if fails:
        parts = ([f"{len(invisible)} group(s) invisible to titiler-eopf 0.12"] if invisible else []) + \
                ([f"{len(implicit)} node(s) with implicit conventions (spatial:/proj: keys not declared)"] if implicit else [])
        return Result("ST04", "store", FAIL, "; ".join(parts), fails[:40] + ([f"... {len(fails) - 40} more"] if len(fails) > 40 else []), problems=fails)
    return Result("ST04", "store", PASS, "conventions declared wherever used; every group meant to render is visible to titiler-eopf 0.12")


def st11_declarations(ctx: StoreContext) -> Result:
    """Declarations equal the zarr-conventions v0.1 consts (C5: zarr-cm 0.4.1 names and
    URLs, which inspect.geozarr.org rejects). titiler ignores these fields (it matches on
    uuid), so this is WARN unless the config sets strict_declarations."""
    problems = []
    nodes = _all_nodes(ctx)
    for path, node in nodes:
        problems += [f"{path}: {p}" for p in cv.declaration_problems(_attrs(node))]
    if not problems:
        return Result("ST11", "store", PASS, f"{len(nodes)} nodes: declarations match the v0.1 schemas")
    status = FAIL if ctx.cfg.get("strict_declarations") else WARN
    return Result("ST11", "store", status, f"{len(problems)} stale or misspelt convention declaration(s)", problems[:40] + ([f"... {len(problems) - 40} more"] if len(problems) > 40 else []))


# Attribute contents only. geozarr-toolkit 0.1.2's own declaration constants (name
# "spatial:", schema_url refs/tags/v1, a tag that doesn't exist) disagree with the
# published v0.1 schemas (checked 6 Oct 2026), so declarations stay ST11's job.
CONTENT_MODELS = {
    cv.SPATIAL: Spatial.model_validate,
    cv.PROJ: Proj.model_validate,
    cv.MULTISCALES: lambda a: Multiscales.model_validate(a["multiscales"]),
}


def st12_convention_content(ctx: StoreContext) -> Result:
    """Each node's spatial/proj/multiscales attributes against geozarr-toolkit's models,
    the library behind inspect.geozarr.org. WARN while the toolkit is 0.1.x."""
    problems = []
    nodes = _all_nodes(ctx)
    for path, node in nodes:
        a = _attrs(node)
        for u in sorted(cv.used(a)):
            try:
                CONTENT_MODELS[u](a)
            except ValidationError as exc:
                # spatial v0.1: spatial:dimensions is "Required: Yes on arrays; optional on groups"
                # (README; the schema requires it only for node_type "array"). geozarr-toolkit's
                # model requires it everywhere. Where titiler needs it on a group, ST03 FAILs.
                errors = [e for e in exc.errors() if not (u == cv.SPATIAL and (node or {}).get("node_type") == "group"
                                                          and tuple(e["loc"]) == ("spatial:dimensions",) and e["type"] == "missing")]
                problems += [f"{path}: {cv.NAME[u]}: {'.'.join(map(str, e['loc'])) or 'attributes'}: {e['msg']}" for e in errors]
    if not problems:
        return Result("ST12", "store", PASS, f"{len(nodes)} nodes: spatial/proj/multiscales attributes valid (geozarr-toolkit)")
    return Result("ST12", "store", WARN, f"{len(problems)} invalid convention attribute(s) (geozarr-toolkit)", problems[:40] + ([f"... {len(problems) - 40} more"] if len(problems) > 40 else []))


def st07_chunk_layout(ctx: StoreContext) -> Result:
    """A tile-size problem that upgrading the viewer fixes is a WARN naming the upgrade
    (Loïc, 8 Oct); one that no OpenLayers release avoids is the store's, a FAIL."""
    consumers: dict = (ctx.cfg.get("consumers") or {}).get("openlayers") or {}
    fixed_in = ".".join(map(str, olpredict.CURRENT_SINCE))
    fails, upgrades, warns, rows = [], [], [], []
    for g, levels in ctx.levels.items():
        for _, path, _node in levels:
            arrays = ctx.arrays(g, path)
            name = next((v for v in ctx.cfg.get("sample_variables", []) if v in arrays), next(iter(arrays), None))
            if not name:
                continue
            meta = arrays[name]
            ch, cw = olpredict.decode_chunk_shape(meta)
            itemsize = np.dtype(meta["data_type"]).itemsize if isinstance(meta.get("data_type"), str) else 0
            chunk_mb = ch * cw * itemsize / 1e6
            grid = meta["chunk_grid"]["configuration"]["chunk_shape"]
            n_inner = math.ceil(meta["shape"][-2] / ch) * math.ceil(meta["shape"][-1] / cw)
            row = f"{path}/{name}: shape {meta['shape'][-2:]}, chunk {grid[-2:]}, decode unit {ch}x{cw} ({chunk_mb:.1f} MB), {n_inner} decode unit(s)"
            for consumer, version in consumers.items():
                tw, th = olpredict.tile_size(meta, version)
                ratio = olpredict.decode_ratio(meta, version)
                row += f"; {consumer} ol {version}: {tw}x{th} px tiles, {ratio:.1f} decoded px per drawn px"
                if ratio < 16:  # an unsharded 1024 px chunk drawn as 256 px tiles under ol ≤ 10.10 is exactly 16
                    continue
                problem = f"{path}/{name}: {consumer} (ol {version}) would draw {tw}x{th} px tiles and decode {ratio:.0f}x the pixels it draws"
                fixed_ratio = olpredict.decode_ratio(meta, fixed_in)
                if olpredict.rule_for(version) == "legacy" and fixed_ratio < 16:
                    ftw, fth = olpredict.tile_size(meta, fixed_in)
                    upgrades.append(f"{problem}; ol ≥ {fixed_in} draws {ftw}x{fth} px tiles ({fixed_ratio:.0f}x): upgrade {consumer}")
                else:
                    fails.append(f"{problem}; no OpenLayers release avoids it (ol {fixed_in}: {fixed_ratio:.0f}x)")
            if n_inner == 1 and chunk_mb > 64:
                warns.append(f"{path}/{name}: single-chunk level decodes {chunk_mb:.0f} MB per read")
            rows.append(row)
    if not rows:
        return Result("ST07", "store", SKIP, "no multiscales level with a data array to measure", warns)
    status = FAIL if fails else WARN if upgrades or warns else PASS
    summary = (f"{len(fails)} OpenLayers tile-size problem(s) no release avoids" if fails else
               f"{len(upgrades)} OpenLayers tile-size problem(s), fixed by upgrading the viewer to ol ≥ {fixed_in}" if upgrades else
               "layout OK" + ("" if consumers else " (no OpenLayers consumers configured)"))
    return Result("ST07", "store", status, summary, fails + upgrades + warns + rows)


def _center_chunk_key(meta: dict) -> str:
    grid = meta["chunk_grid"]["configuration"]["chunk_shape"]
    shape = meta["shape"]
    idx = [math.ceil(s / c) - 1 for s, c in zip(shape[:-2], grid[:-2])]  # last index of leading dims
    idx += [(shape[-2] // 2) // grid[-2], (shape[-1] // 2) // grid[-1]]
    enc = meta.get("chunk_key_encoding") or {"name": "default"}
    sep = (enc.get("configuration") or {}).get("separator", "/" if enc.get("name") == "default" else ".")
    joined = sep.join(str(i) for i in idx)
    return f"c{sep}{joined}" if enc.get("name") == "default" else joined


def st08_dtype_and_compression(ctx: StoreContext) -> Result:
    allow = set(ctx.cfg.get("dtype_allow", []))
    pattern = re.compile(ctx.cfg.get("dtype_check_pattern", ".*"))
    fails, warns, rows = [], [], []
    n_checked = 0
    for g, levels in ctx.levels.items():
        if not levels:
            continue
        # dtypes on every level (metadata only); one problem per level and dtype, so a known
        # issue for "float64" can't also cover a float16 next to it
        for _, lpath, _ in levels:
            arrays = ctx.arrays(g, lpath)
            checked = [n for n in arrays if pattern.search(n)]
            n_checked += len(checked)
            for dtype in sorted({str(arrays[n].get("data_type")) for n in checked} - set(allow) if allow else set()):
                names = [n for n in checked if str(arrays[n].get("data_type")) == dtype]
                fails.append(f"{lpath}: dtype {dtype} not in the allow-list {sorted(allow)} ({len(names)} arrays, e.g. {names[:3]})")
            fallback = " (no consolidated listing: configured sample_variables only)" if lpath in ctx.fallback_levels else ""
            rows.append(f"{lpath}: dtype checked on {len(checked)} arrays{fallback}")
        _, path, _ = levels[0]  # compression: the finest level carries the bulk of the bytes
        arrays = ctx.arrays(g, path)
        for name in [v for v in ctx.cfg.get("sample_variables", []) if v in arrays][:3]:
            meta = arrays[name]
            if not isinstance(meta.get("data_type"), str):
                continue
            key = f"{path}/{name}/{_center_chunk_key(meta)}"
            stored = ctx.reader.head_size(key)
            grid = meta["chunk_grid"]["configuration"]["chunk_shape"]
            raw = math.prod(grid) * np.dtype(meta["data_type"]).itemsize
            if not stored:
                warns.append(f"{key}: centre chunk not stored (all fill?), compression not measured")
                continue
            ratio = raw / stored
            rows.append(f"{path}/{name}: {meta['data_type']}, stored chunk {stored / 1e6:.2f} MB, raw {raw / 1e6:.2f} MB, ratio {ratio:.2f}")
            if meta["data_type"].startswith("float") and ratio < 1.2:
                warns.append(f"{path}/{name}: {meta['data_type']} compresses only {ratio:.2f}x")
    if not n_checked:  # no level, or no array matching dtype_check_pattern: nothing was checked, so no PASS
        return Result("ST08", "store", SKIP, "no array to check: no multiscales level, or none matching dtype_check_pattern", warns + rows)
    status = FAIL if fails else WARN if warns else PASS
    summary = (fails[0] + (f" (+{len(fails) - 1} more)" if len(fails) > 1 else "") if fails else
               warns[0] if warns else "dtypes allowed on every level, compression OK")
    return Result("ST08", "store", status, summary, fails + warns + rows, problems=fails)


def _is_empty(block: np.ndarray, fill) -> np.ndarray:
    empty = np.isnan(block) if np.issubdtype(block.dtype, np.floating) else np.zeros(block.shape, bool)
    if fill is not None and not (isinstance(fill, float) and math.isnan(fill)):
        empty |= block == fill
    return empty


def st09_data_present(ctx: StoreContext) -> Result:
    """The coarsest level is small: read it whole, then read the finest-level block under a
    pixel it shows as valid. A bbox centre can be nodata on a partial S2 scene.

    Fails only on all-fill (it does not apply `min_valid`, which is TI03's), and when a
    configured sample variable at the finest level is missing from the coarsest: a
    pyramid that drops a band would otherwise skip the check and leave the run green."""
    fails, rows = [], []
    zstore = ctx.reader.zarr_store("")
    for g, levels in ctx.levels.items():
        if not levels:
            continue
        arrays_fine = ctx.arrays(g, levels[0][1])
        arrays_coarse = ctx.arrays(g, levels[-1][1])
        for v in ctx.cfg.get("sample_variables", []):
            if v in arrays_fine and v not in arrays_coarse:
                fails.append(f"{levels[-1][1]}/{v}: sample variable is at the finest level {levels[0][1]} but missing from the coarsest level")
        for name in [v for v in ctx.cfg.get("sample_variables", []) if v in arrays_fine and v in arrays_coarse][:1]:
            # zarr_format=3: without it zarr also probes the v2 keys (.zarray, .zattrs), one
            # wasted request each, and a public bucket answers them 403 rather than 404
            coarse = zarr.open_array(store=zstore, path=f"{levels[-1][1]}/{name}", mode="r", zarr_format=3)
            lead = (-1,) * (coarse.ndim - 2)
            cblock = np.asarray(coarse[lead + (slice(None), slice(None))])
            cvalid = ~_is_empty(cblock, coarse.fill_value)
            rows.append(f"{levels[-1][1]}/{name}: whole level {cblock.shape}, {cvalid.mean():.0%} valid")
            if not cvalid.any():
                fails.append(f"{levels[-1][1]}/{name}: the coarsest level is entirely fill/NaN")
                continue
            # the valid coarse pixel nearest the centre, mapped to the finest level
            yy, xx = np.nonzero(cvalid)
            k = np.argmin((yy - cblock.shape[0] / 2) ** 2 + (xx - cblock.shape[1] / 2) ** 2)
            fine = zarr.open_array(store=zstore, path=f"{levels[0][1]}/{name}", mode="r", zarr_format=3)
            fy = int(yy[k] * fine.shape[-2] / cblock.shape[0])
            fx = int(xx[k] * fine.shape[-1] / cblock.shape[1])
            ch, cw = olpredict.decode_chunk_shape(arrays_fine[name])
            y0, x0 = fy // ch * ch, fx // cw * cw
            block = np.asarray(fine[lead + (slice(y0, y0 + ch), slice(x0, x0 + cw))])
            valid = float((~_is_empty(block, fine.fill_value)).mean())
            rows.append(f"{levels[0][1]}/{name}: block at ({y0}, {x0}) {block.shape}, {valid:.0%} valid")
            if valid == 0:
                fails.append(f"{levels[0][1]}/{name}: the finest level is empty where the coarsest level has data")
    summary = (fails[0] if fails else
               f"not all fill at the finest and coarsest levels ({len(rows)} arrays sampled; fails only on all-fill)" if rows else
               "no configured sample_variables at both the finest and coarsest levels")
    return Result("ST09", "store", FAIL if fails else PASS if rows else SKIP, summary, fails + rows, problems=fails)


def ht02_open_without_listing(ctx: StoreContext) -> Result:
    if not ctx.open_groups:
        return Result("HT02", "host", SKIP, "no group to open (see ST01)")
    fails, rows = [], []
    for g in ctx.open_groups:
        if ctx.nodes.get(g) is None:
            fails.append(f"{g}: absent (no zarr.json; see ST01)")
            continue
        try:
            grp = zarr.open_group(store=ctx.reader.zarr_store(g, allow_list=False), mode="r", zarr_format=3)
            n = len(list(grp.members(max_depth=None)))
            rows.append(f"{g}: opened with {n} members and no listing")
        except ListingNotAllowed as exc:
            fails.append(f"{g}: {exc}. Over HTTP this is a PROPFIND, which the gateway answers 405 (data-pipeline#446)")
        except FileNotFoundError as exc:
            fails.append(f"{g}: not found ({exc})")
    return Result("HT02", "host", FAIL if fails else PASS, fails[0] if fails else "every opened group opens without listing", fails + rows, problems=fails)


# GR*: products from data-model's generic_rechunker (data-model#292): no multiscales; every leaf
# group rechunked, sharded and encoded with one rule (eopf_geozarr conversion/utils.py
# create_uniform_encoding). They run for configs with a `[generic]` table, on the arrays the
# consolidation root lists (the root, or the sub-root with `consolidation = "subroot"`).
COMPRESSORS = {"blosc", "zstd", "gzip", "zlib", "bz2", "lzma", "lz4"}  # also as numcodecs.<name>


def _compressed(codec_names: list) -> bool:
    return any((n or "").removeprefix("numcodecs.") in COMPRESSORS for n in codec_names)


def _generic_arrays(ctx: StoreContext, check_id: str) -> tuple[dict, str, dict] | Result:
    """(the [generic] table, the consolidation root, its consolidated metadata), or the SKIP."""
    gen = ctx.cfg.get("generic")
    if not gen:
        return Result(check_id, "store", SKIP, "not a generic_rechunker config (no [generic] table)")
    g = ctx.subroot if ctx.cfg.get("consolidation") == "subroot" else ""
    cm = _consolidated(ctx.nodes.get(g)) if g is not None else None
    if not cm:
        where = "the sub-root" if g is None else (g or "/")
        return Result(check_id, "store", SKIP, f"{where}: no consolidated metadata lists the arrays (see ST01)")
    return gen, g, cm


def _layout(meta: dict) -> tuple[list | None, list, list[str]]:
    """(shard shape or None, chunk shape inside the shard, codec names including the shard's own)."""
    grid = list(meta["chunk_grid"]["configuration"]["chunk_shape"])
    codecs = meta.get("codecs") or []
    names = [c.get("name") for c in codecs]
    shard = next((c for c in codecs if c.get("name") == "sharding_indexed"), None)
    if shard is None:
        return None, grid, names
    return grid, list(shard["configuration"]["chunk_shape"]), names + [c.get("name") for c in shard["configuration"].get("codecs", [])]


def _coordinates(cm: dict) -> set[str]:
    """Paths of coordinate arrays: dimension names, and the CF `coordinates` an array or its group
    declares (xarray reads both)."""
    out = set()
    for path, m in cm.items():
        own = str((m.get("attributes") or {}).get("coordinates") or "").split()
        if m.get("node_type") == "array":
            parent = path.rpartition("/")[0]
            names = set(m.get("dimension_names") or []) | set(own)
        else:
            parent, names = path, set(own)
        out |= {f"{parent}/{n}" if parent else n for n in names}
    return out


def gr02_chunks_and_shards(ctx: StoreContext) -> Result:
    """generic_rechunker's layout: every dimension chunked to min(spatial_chunk, size) (_rechunk_ds);
    with sharding, "exactly one shard per array", each dimension the smallest multiple of its chunk
    that covers the array (create_uniform_encoding). Coordinates stay unsharded and uncompressed
    unless chunk_and_shard_coords is set; the 2-D ones (swath lat/lon) are reported."""
    got = _generic_arrays(ctx, "GR02")
    if isinstance(got, Result):
        return got
    gen, root, cm = got
    size, sharded = int(gen["spatial_chunk"]), bool(gen.get("sharding", True))
    coords = _coordinates(cm)
    # multiscales groups (S1 `overviews`) are written by another path, for tiling: not this rule
    pyramids = tuple(f"{g.rstrip('?')}/" for g in ctx.cfg.get("multiscales_groups", []))
    fails, warns, n, undeclared = [], [], 0, 0
    for path, m in sorted(cm.items()):
        if m.get("node_type") != "array" or not m.get("shape"):
            continue
        label = f"{root}/{path}" if root else path
        if label.startswith(pyramids):
            continue
        shard, inner, names = _layout(m)
        if path in coords:
            if len(m["shape"]) >= 2 and not _compressed(names):
                try:
                    size_mb = f"{math.prod(m['shape']) * np.dtype(m['data_type']).itemsize / 1e6:.0f} MB"
                except (TypeError, ValueError, KeyError):
                    size_mb = f"dtype {m.get('data_type')!r}"
                warns.append(f"{label}: {len(m['shape'])}-D coordinate {m['shape']} stored uncompressed ({size_mb}) and unsharded (chunk_and_shard_coords off)")
            continue
        if shard is None and not _compressed(names):
            # the writer's coordinate encoding, on an array nothing declares as a coordinate
            undeclared += 1
            warns.append(f"{label}: written like a coordinate (unsharded, uncompressed) but no CF `coordinates` attribute "
                         f"names it, so readers see a data variable")
            continue
        n += 1
        want = [min(size, d) for d in m["shape"]]
        if inner != want:
            fails.append(f"{label}: chunk {inner}, want {want} (min(spatial_chunk={size}, size) per dimension)")
        if sharded:
            want_shard = [math.ceil(d / c) * c for d, c in zip(m["shape"], inner)]
            if shard is None:
                fails.append(f"{label}: not sharded")
            elif shard != want_shard:
                fails.append(f"{label}: shard {shard}, want {want_shard} (one shard per array)")
    noted = (f"{len(warns) - undeclared} coordinate(s) stored uncompressed, "
             f"{undeclared} array(s) written like coordinates that nothing declares")
    if not n:
        return Result("GR02", "store", WARN if warns else SKIP,
                      f"{root or '/'}: no data array to check" + (f"; {noted}" if warns else ""), warns)
    status = FAIL if fails else WARN if warns else PASS
    summary = (f"{len(fails)} array(s) off the generic_rechunker layout" if fails else
               f"{n} data arrays on the layout; {noted}" if warns else
               f"{n} data arrays chunked to {size}" + (", one shard each" if sharded else ""))
    return Result("GR02", "store", status, summary, fails[:40] + warns[:20], problems=fails)


def _fits(value, dtype: np.dtype) -> bool:
    if dtype.kind not in "iu":
        return True
    if isinstance(value, (str, bool)):
        return False  # "65535" is not a valid CF _FillValue for a uint16 array
    if isinstance(value, int):
        v = value  # as an int: a float can't hold uint64's max exactly
    else:
        try:
            f = float(value)
        except (TypeError, ValueError):
            return False
        if not f.is_integer():
            return False
        v = int(f)
    info = np.iinfo(dtype)
    return info.min <= v <= info.max


def _same_fill(cf, zarr_fill) -> bool:
    try:
        a, b = float(cf), float(zarr_fill)
    except (TypeError, ValueError):
        return True  # complex or structured fills: not compared
    return a == b or (math.isnan(a) and math.isnan(b))


def gr03_encoding(ctx: StoreContext) -> Result:
    """CF packing as create_uniform_encoding writes it: a packed variable keeps its integer dtype
    with CF scale_factor/add_offset/_FillValue, or is packed by the scale_offset codec with no CF
    scale attributes, never both (that decodes twice). _FillValue and the valid range must fit the
    dtype. A zarr fill_value that differs from the CF _FillValue is reported: missing chunks read
    as the zarr fill_value, and readers that take it as nodata mask a different value."""
    got = _generic_arrays(ctx, "GR03")
    if isinstance(got, Result):
        return got
    _, root, cm = got
    fails, warns, n = [], [], 0
    for path, m in sorted(cm.items()):
        if m.get("node_type") != "array" or not isinstance(m.get("data_type"), str):
            continue
        try:
            dtype = np.dtype(m["data_type"])
        except TypeError:
            continue
        n += 1
        label, a = (f"{root}/{path}" if root else path), m.get("attributes") or {}
        _, _, names = _layout(m)
        cf_scale = "scale_factor" in a or "add_offset" in a
        if cf_scale and "scale_offset" in names:
            fails.append(f"{label}: CF scale_factor/add_offset AND the scale_offset codec: decoded twice")
        if cf_scale and dtype.kind == "f":
            warns.append(f"{label}: CF scale attributes on a {dtype} array (packed variables keep their integer dtype)")
        for key in ("_FillValue", "valid_min", "valid_max"):
            if key in a and not _fits(a[key], dtype):
                fails.append(f"{label}: {key} {a[key]!r} does not fit {dtype}")
        if "valid_range" in a:
            vr = a["valid_range"]
            if not (isinstance(vr, list) and len(vr) == 2):
                fails.append(f"{label}: valid_range {vr!r} is not a [min, max] pair")
            elif not all(_fits(v, dtype) for v in vr):
                fails.append(f"{label}: valid_range {vr!r} does not fit {dtype}")
        if "_FillValue" in a and not _same_fill(a["_FillValue"], m.get("fill_value")):
            warns.append(f"{label}: zarr fill_value {m.get('fill_value')!r} != CF _FillValue {a['_FillValue']!r}")
    if not n:
        return Result("GR03", "store", SKIP, f"{root or '/'}: no array to check")
    status = FAIL if fails else WARN if warns else PASS
    summary = (fails[0] + (f" (+{len(fails) - 1} more)" if len(fails) > 1 else "") if fails else
               f"{len(warns)} array(s) whose zarr fill_value or packing disagrees with CF" if warns else
               f"{n} arrays: CF packing and fill values consistent")
    shown = fails + warns
    return Result("GR03", "store", status, summary, shown[:40] + ([f"... {len(shown) - 40} more"] if len(shown) > 40 else []),
                  problems=fails)


CHECKS = {
    "ST01": st01_consolidated,
    "ST03": st03_multiscales,
    "ST04": st04_visibility,
    "ST07": st07_chunk_layout,
    "ST08": st08_dtype_and_compression,
    "ST09": st09_data_present,
    "ST11": st11_declarations,
    "ST12": st12_convention_content,
    "HT02": ht02_open_without_listing,
    "GR02": gr02_chunks_and_shards,
    "GR03": gr03_encoding,
}
