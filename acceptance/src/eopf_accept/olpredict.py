"""Predict the tile size `ol/source/GeoZarr` picks for a level, from metadata alone.

Ported from openlayers `src/ol/source/GeoZarr.js` at v10.10.0 (`legacy`) and at v10.11.0
(`current`), both read on 6 Oct 2026. `legacy` falls back to 64 px tiles when the inner
chunk is larger than 512 px; each 64 px tile then decodes a whole chunk. ol 10.11.0
replaced that with the largest divisor of the chunk that is ≤ 512.
"""

MIN_TILE, MAX_TILE, DEFAULT_TILE, MAX_CHUNK_TILE = 64, 512, 256, 2048


def _shard_legacy(shard: int, inner: int) -> int:
    max_chunks = MAX_TILE // inner
    for n in range(max_chunks, 0, -1):
        c = n * inner
        if c >= MIN_TILE and shard % c == 0:
            return c
    if MIN_TILE <= shard <= MAX_TILE:
        return shard
    if shard < MIN_TILE:
        return MIN_TILE
    return max(max_chunks * inner, MIN_TILE)


def _shard_current(shard: int, inner: int) -> int:
    if inner > MAX_TILE:
        for size in range(MAX_TILE, MIN_TILE - 1, -1):
            if inner % size == 0:
                return size
        return MAX_TILE
    return _shard_legacy(shard, inner)


def _chunk_current(chunk: int, array: int) -> int:
    size = min(chunk, MAX_CHUNK_TILE) if chunk >= MAX_TILE else (MAX_TILE // chunk) * chunk
    return max(MIN_TILE, min(size, array))


def rule_for(version: str) -> str:
    major, minor, *_ = (int(p) for p in version.split("."))
    return "current" if (major, minor) >= (10, 11) else "legacy"


def tile_size(meta: dict, version: str) -> tuple[int, int]:
    """(width, height) for a 2-D or N-D array whose last two axes are y, x."""
    rule = rule_for(version)
    grid = (meta.get("chunk_grid") or {}).get("configuration", {}).get("chunk_shape")
    shard_codec = next((c for c in meta.get("codecs", []) if c.get("name") == "sharding_indexed"), None)
    if grid and shard_codec:
        inner = shard_codec["configuration"]["chunk_shape"]
        f = _shard_current if rule == "current" else _shard_legacy
        return f(grid[-1], inner[-1]), f(grid[-2], inner[-2])
    if rule == "current" and grid and max(grid[-2], grid[-1]) > DEFAULT_TILE:
        shape = meta["shape"]
        return _chunk_current(grid[-1], shape[-1]), _chunk_current(grid[-2], shape[-2])
    return DEFAULT_TILE, DEFAULT_TILE


def decode_chunk_shape(meta: dict) -> tuple[int, int]:
    """The unit a reader must decode: the inner chunk if sharded, else the chunk."""
    grid = meta["chunk_grid"]["configuration"]["chunk_shape"]
    shard_codec = next((c for c in meta.get("codecs", []) if c.get("name") == "sharding_indexed"), None)
    inner = shard_codec["configuration"]["chunk_shape"] if shard_codec else grid
    return inner[-2], inner[-1]


def decode_ratio(meta: dict, version: str) -> float:
    """Decoded pixels per drawn pixel for one tile aligned to the chunk grid."""
    tw, th = tile_size(meta, version)
    ch, cw = decode_chunk_shape(meta)
    # chunks touched by an aligned tile, times the chunk area, over the tile area
    touched = (-(-tw // cw)) * (-(-th // ch))
    return touched * cw * ch / (tw * th)
