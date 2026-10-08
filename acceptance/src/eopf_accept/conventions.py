"""zarr-conventions v0.1 declaration constants, and titiler-eopf 0.12's visibility rule.

Constants are copied from `$defs.conventionMetadata.properties.*.const` of each
schema.json at refs/tags/v0.1. On 6 Oct 2026 refs/heads/main was byte-identical:
  spatial     sha256 4436913e3ebc2f98301d81d42b04340e7a55dc0eb60672af869a6015cb431f3e
  proj        sha256 957e190e1a2bdb7c157f48bc1d135e30f86989cfcdcbfbadc8efed2dcdfaf834
  multiscales sha256 716f2c6a7696a49f46cd6919960672ae158bcd6a24572ae8a153139900572845
inspect.geozarr.org (metazarr) applies these consts, so any mismatch fails there.
"""

SPATIAL, PROJ, MULTISCALES = (
    "689b58e2-cf7b-45e0-9fff-9cfc0883d6b4",
    "f17cb550-5864-4468-aeb7-f3180cfb622f",
    "d35379db-88df-4056-af3a-620245f8e347",
)

CONSTS = {
    SPATIAL: {
        "name": "spatial",
        "schema_url": "https://raw.githubusercontent.com/zarr-conventions/spatial/refs/tags/v0.1/schema.json",
        "spec_url": "https://github.com/zarr-conventions/spatial/blob/v0.1/README.md",
        "description": "Spatial coordinate information",
    },
    PROJ: {
        "name": "proj",
        "schema_url": "https://raw.githubusercontent.com/zarr-conventions/proj/refs/tags/v0.1/schema.json",
        "spec_url": "https://github.com/zarr-conventions/proj/blob/v0.1/README.md",
        "description": "Coordinate reference system information for geospatial data",
    },
    MULTISCALES: {
        "name": "multiscales",
        "schema_url": "https://raw.githubusercontent.com/zarr-conventions/multiscales/refs/tags/v0.1/schema.json",
        "spec_url": "https://github.com/zarr-conventions/multiscales/blob/v0.1/README.md",
        "description": "Multiscale layout of zarr datasets",
    },
}
NAME = {SPATIAL: "spatial", PROJ: "proj", MULTISCALES: "multiscales"}


def declared(attrs: dict) -> set[str]:
    """uuids declared in `zarr_conventions` (titiler-eopf matches on uuid only)."""
    return {c.get("uuid") for c in attrs.get("zarr_conventions") or [] if isinstance(c, dict)}


def used(attrs: dict) -> set[str]:
    """uuids whose keys the node actually uses."""
    out = set()
    if any(k.startswith("spatial:") for k in attrs):
        out.add(SPATIAL)
    if any(k.startswith("proj:") for k in attrs):
        out.add(PROJ)
    if "multiscales" in attrs:
        out.add(MULTISCALES)
    return out


def declaration_problems(attrs: dict) -> list[str]:
    """Stale or misspelt declarations (wrong const values, unknown extra keys)."""
    problems = []
    for entry in attrs.get("zarr_conventions") or []:
        if not isinstance(entry, dict) or entry.get("uuid") not in CONSTS:
            continue
        consts = CONSTS[entry["uuid"]]
        name = NAME[entry["uuid"]]
        bad = sorted(k for k, v in consts.items() if k in entry and entry[k] != v)
        extra = sorted(set(entry) - set(consts) - {"uuid"})
        for k in bad:
            problems.append(f"{name}: {k}={entry[k]!r}, schema wants {consts[k]!r}")
        if extra:
            problems.append(f"{name}: keys not in the schema: {extra}")
    return problems


def titiler_visible(group_attrs: dict, array_attrs: list[dict]) -> bool:
    """titiler-eopf 0.12 `GeoZarrReader._get_groups` for one group (reader.py, v0.12.0 to v0.12.2).

    A group with `zarr_conventions` is visible only if it declares both spatial and proj.
    A group without them is visible only if one of its arrays declares both.
    """
    if group_attrs.get("zarr_conventions"):
        return {SPATIAL, PROJ} <= declared(group_attrs)
    return any({SPATIAL, PROJ} <= declared(a) for a in array_attrs)
