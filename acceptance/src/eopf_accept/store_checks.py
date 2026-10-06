"""Store checks (ST*) and the no-listing open (HT02).

The groups to check come from the collection config (`open_groups`,
`multiscales_groups`) and each multiscales layout. They are never derived by walking
the consolidated tree: an unconsolidated root would then yield nothing to check and
"pass" (the conformance harness blind spot, F3 in the 24 Sep findings).
"""

import math
import re

import numpy as np
import zarr

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


class StoreContext:
    """Metadata read once and shared by the checks."""

    def __init__(self, reader: StoreReader, cfg: dict):
        self.reader, self.cfg = reader, cfg
        self.root = reader.node("")
        self.nodes: dict[str, dict | None] = {"": self.root}
        # A trailing "?" marks a group as optional (an S1 cube may have one orbit only).
        # Absent optional groups are dropped; if every listed group is optional and
        # absent, ST01 fails, because then there is nothing to open.
        self.absent_optional: list[str] = []
        names = [g.rstrip("?") for g in cfg.get("open_groups", []) + cfg.get("multiscales_groups", [])]
        optional = {g.rstrip("?") for g in cfg.get("open_groups", []) + cfg.get("multiscales_groups", []) if g.endswith("?")}
        for g in dict.fromkeys(names):
            node = reader.node(g)
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

    def arrays(self, g: str, level_path: str) -> dict[str, dict]:
        """Data arrays of a level: from consolidated metadata, else the configured variables."""
        rel = level_path[len(g) + 1:]
        found = _data_arrays(rel, _consolidated(self.nodes[g]))
        if found:
            return found
        out = {}
        for v in self.cfg.get("sample_variables", []):
            meta = self.reader.node(f"{level_path}/{v}")
            if meta:
                out[v] = meta
        return out


def st01_consolidated(ctx: StoreContext) -> Result:
    fails, warns = [], []
    if not ctx.open_groups:
        fails.append(f"no group to open: every configured group is absent ({ctx.absent_optional})")
    for g in dict.fromkeys(["", *ctx.open_groups, *ctx.ms_groups]):
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
    if fails:
        return Result("ST01", "store", FAIL, f"{len(fails)} group(s) not consolidated or incomplete", fails + warns)
    if warns:
        return Result("ST01", "store", WARN, f"{len(warns)} level group(s) without their own consolidated metadata", warns)
    return Result("ST01", "store", PASS, "root, opened and multiscales groups are consolidated")


def st03_multiscales(ctx: StoreContext) -> Result:
    """Emulates what titiler-eopf 0.12 needs for tilejson without zoom params (C1)."""
    fails, warns = [], []
    min_levels = int(ctx.cfg.get("min_levels", 2))
    for g in ctx.ms_groups:
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
            elif not in_layout:
                warns.append(f"{path}: layout entry lacks spatial:shape/spatial:transform (the group has them)")
            if "spatial:shape" not in la:
                warns.append(f"{path}: group has no spatial:shape")
            if cv.SPATIAL in cv.declared(la) and "spatial:dimensions" not in la:
                fails.append(f"{path}: declares spatial but has no spatial:dimensions; titiler-eopf 0.12 reads it unguarded (reader.py _get_variable), so tiles from this level return 500")
            if "spatial:transform" not in entry and "spatial:transform" not in la:
                fails.append(f"{path}: no spatial:transform in the group or the layout entry; a tile that selects this level fails")
            if "spatial:shape" in entry and "spatial:shape" in la and list(entry["spatial:shape"]) != list(la["spatial:shape"]):
                fails.append(f"{path}: layout spatial:shape {entry['spatial:shape']} != group {la['spatial:shape']}")
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
    nodes = [(g or "/", ctx.nodes[g]) for g in ctx.nodes]
    for g, levels in ctx.levels.items():
        for _, path, node in levels:
            nodes.append((path, node))
            nodes += [(f"{path}/{n}", m) for n, m in ctx.arrays(g, path).items()]
    return nodes


def st04_visibility(ctx: StoreContext) -> Result:
    """titiler-eopf 0.12 sees a group only through its declared conventions (C4: `scl`)."""
    fails = []
    for path, node in _all_nodes(ctx):
        a = _attrs(node)
        undeclared = [cv.NAME[u] for u in cv.used(a) - cv.declared(a)]
        if undeclared and node and node.get("node_type") == "group":
            fails.append(f"{path}: uses {undeclared} keys without declaring them in zarr_conventions")
    for g in dict.fromkeys(ctx.open_groups + list(ctx.cfg.get("visible_groups", []))):
        node = ctx.nodes.get(g) or ctx.reader.node(g)
        if node is None:
            fails.append(f"{g}: no zarr.json")
            continue
        arrays = [m.get("attributes") or {} for m in (_consolidated(node) or {}).values() if m.get("node_type") == "array"]
        if not cv.titiler_visible(_attrs(node), arrays):
            fails.append(f"{g}: invisible to titiler-eopf 0.12 (_get_groups needs spatial+proj declared on the group, or on an array of a group without conventions)")
    if fails:
        return Result("ST04", "store", FAIL, f"{len(fails)} visibility problem(s)", fails[:40] + ([f"... {len(fails) - 40} more"] if len(fails) > 40 else []))
    return Result("ST04", "store", PASS, "every group meant to render is visible to titiler-eopf 0.12")


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


def st07_chunk_layout(ctx: StoreContext) -> Result:
    consumers: dict = (ctx.cfg.get("consumers") or {}).get("openlayers") or {}
    fails, warns, rows = [], [], []
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
                if ratio >= 16:  # an unsharded 1024 px chunk drawn as 256 px tiles under ol ≤ 10.10 is exactly 16
                    fails.append(f"{path}/{name}: {consumer} (ol {version}) would draw {tw}x{th} px tiles and decode {ratio:.0f}x the pixels it draws")
            if n_inner == 1 and chunk_mb > 64:
                warns.append(f"{path}/{name}: single-chunk level decodes {chunk_mb:.0f} MB per read")
            rows.append(row)
    status = FAIL if fails else WARN if warns else PASS
    summary = (f"{len(fails)} OpenLayers tile-size problem(s)" if fails else
               "layout OK" + ("" if consumers else " (no OpenLayers consumers configured)"))
    return Result("ST07", "store", status, summary, fails + warns + rows)


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
    for g, levels in ctx.levels.items():
        if not levels:
            continue
        _, path, _ = levels[0]  # the finest level carries the bulk of the bytes
        arrays = ctx.arrays(g, path)
        bad = sorted({m["data_type"] for n, m in arrays.items() if pattern.search(n) and allow and m.get("data_type") not in allow})
        if bad:
            names = [n for n, m in arrays.items() if m.get("data_type") in bad and pattern.search(n)]
            fails.append(f"{path}: dtype {', '.join(map(str, bad))} not in the allow-list {sorted(allow)} ({len(names)} arrays, e.g. {names[:3]})")
        for name in [v for v in ctx.cfg.get("sample_variables", []) if v in arrays][:3]:
            meta = arrays[name]
            if not isinstance(meta.get("data_type"), str):
                continue
            key = f"{path}/{name}/{_center_chunk_key(meta)}"
            stored = ctx.reader.head_size(key)
            grid = meta["chunk_grid"]["configuration"]["chunk_shape"]
            raw = math.prod(grid) * np.dtype(meta["data_type"]).itemsize
            if not stored:
                rows.append(f"{key}: not stored (all fill?)")
                continue
            ratio = raw / stored
            rows.append(f"{path}/{name}: {meta['data_type']}, stored chunk {stored / 1e6:.2f} MB, raw {raw / 1e6:.2f} MB, ratio {ratio:.2f}")
            if meta["data_type"].startswith("float") and ratio < 1.2:
                warns.append(f"{path}/{name}: {meta['data_type']} compresses only {ratio:.2f}x")
    status = FAIL if fails else WARN if warns else PASS
    return Result("ST08", "store", status, fails[0] if fails else (warns[0] if warns else "dtypes allowed, compression OK"), fails + warns + rows)


def _is_empty(block: np.ndarray, fill) -> np.ndarray:
    empty = np.isnan(block) if np.issubdtype(block.dtype, np.floating) else np.zeros(block.shape, bool)
    if fill is not None and not (isinstance(fill, float) and math.isnan(fill)):
        empty |= block == fill
    return empty


def st09_data_present(ctx: StoreContext) -> Result:
    """The coarsest level is small: read it whole, then read the finest-level block under a
    pixel it shows as valid. A bbox centre can be nodata on a partial S2 scene."""
    fails, rows = [], []
    zstore = ctx.reader.zarr_store("")
    for g, levels in ctx.levels.items():
        if not levels:
            continue
        arrays_fine = ctx.arrays(g, levels[0][1])
        arrays_coarse = ctx.arrays(g, levels[-1][1])
        for name in [v for v in ctx.cfg.get("sample_variables", []) if v in arrays_fine and v in arrays_coarse][:1]:
            coarse = zarr.open_array(store=zstore, path=f"{levels[-1][1]}/{name}", mode="r")
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
            fine = zarr.open_array(store=zstore, path=f"{levels[0][1]}/{name}", mode="r")
            fy = int(yy[k] * fine.shape[-2] / cblock.shape[0])
            fx = int(xx[k] * fine.shape[-1] / cblock.shape[1])
            ch, cw = olpredict.decode_chunk_shape(arrays_fine[name])
            y0, x0 = fy // ch * ch, fx // cw * cw
            block = np.asarray(fine[lead + (slice(y0, y0 + ch), slice(x0, x0 + cw))])
            valid = float((~_is_empty(block, fine.fill_value)).mean())
            rows.append(f"{levels[0][1]}/{name}: block at ({y0}, {x0}) {block.shape}, {valid:.0%} valid")
            if valid == 0:
                fails.append(f"{levels[0][1]}/{name}: the finest level is empty where the coarsest level has data")
    return Result("ST09", "store", FAIL if fails else PASS if rows else SKIP,
                  fails[0] if fails else ("data present at the finest and coarsest levels" if rows else "no sample_variables found"), fails + rows)


def ht02_open_without_listing(ctx: StoreContext) -> Result:
    fails, rows = [], []
    for g in ctx.open_groups:
        try:
            grp = zarr.open_group(store=ctx.reader.zarr_store(g, allow_list=False), mode="r")
            n = len(list(grp.members(max_depth=None)))
            rows.append(f"{g}: opened with {n} members and no listing")
        except ListingNotAllowed as exc:
            fails.append(f"{g}: {exc}. Over HTTP this is a PROPFIND, which the gateway answers 405 (data-pipeline#446)")
        except FileNotFoundError as exc:
            fails.append(f"{g}: not found ({exc})")
    return Result("HT02", "host", FAIL if fails else PASS, fails[0] if fails else "every opened group opens without listing", fails + rows)


CHECKS = {
    "ST01": st01_consolidated,
    "ST03": st03_multiscales,
    "ST04": st04_visibility,
    "ST07": st07_chunk_layout,
    "ST08": st08_dtype_and_compression,
    "ST09": st09_data_present,
    "ST11": st11_declarations,
    "HT02": ht02_open_without_listing,
}
