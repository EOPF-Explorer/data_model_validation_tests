"""Footprint geometry without dependencies: where to sample tiles, and how much of a tile has data.

Bounds over-state a footprint: an S2 store's bbox is the whole MGRS grid while the scene
may cover 5 % of it, and an OLCI swath is a slanted strip inside its bbox. Tiles are
therefore chosen inside the STAC item geometry when there is one, and the expected
share of valid pixels is the share of the tile inside that geometry.
"""

import math


def _rings(geometry: dict) -> list[list[list[float]]]:
    """Outer rings of a GeoJSON Polygon or MultiPolygon."""
    if geometry["type"] == "Polygon":
        return [geometry["coordinates"][0]]
    if geometry["type"] == "MultiPolygon":
        return [poly[0] for poly in geometry["coordinates"]]
    raise ValueError(f"unsupported geometry type {geometry['type']}")


def bbox_polygon(bounds) -> dict:
    w, s, e, n = bounds
    return {"type": "Polygon", "coordinates": [[[w, s], [e, s], [e, n], [w, n], [w, s]]]}


def contains(geometry: dict, lon: float, lat: float) -> bool:
    inside = False
    for ring in _rings(geometry):
        for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
            if (y1 > lat) != (y2 > lat) and lon < x1 + (lat - y1) * (x2 - x1) / (y2 - y1):
                inside = not inside
    return inside


def interior_point(geometry: dict) -> tuple[float, float]:
    """A point inside the largest ring: its centroid if inside, else the ring point nearest it."""
    ring = max(_rings(geometry), key=lambda r: abs(_area(r)))
    cx, cy = _centroid(ring)
    if contains(geometry, cx, cy):
        return cx, cy
    # concave ring: walk a grid over its bbox and take the inside point nearest the centroid
    xs, ys = [p[0] for p in ring], [p[1] for p in ring]
    best = None
    for i in range(1, 40):
        for j in range(1, 40):
            x = min(xs) + (max(xs) - min(xs)) * i / 40
            y = min(ys) + (max(ys) - min(ys)) * j / 40
            if contains(geometry, x, y):
                d = (x - cx) ** 2 + (y - cy) ** 2
                if best is None or d < best[0]:
                    best = (d, x, y)
    if best is None:
        raise ValueError("no interior point found")
    return best[1], best[2]


def _area(ring) -> float:
    return sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(ring, ring[1:])) / 2


def _centroid(ring) -> tuple[float, float]:
    a = _area(ring)
    if a == 0:
        return sum(p[0] for p in ring) / len(ring), sum(p[1] for p in ring) / len(ring)
    cx = sum((x1 + x2) * (x1 * y2 - x2 * y1) for (x1, y1), (x2, y2) in zip(ring, ring[1:])) / (6 * a)
    cy = sum((y1 + y2) * (x1 * y2 - x2 * y1) for (x1, y1), (x2, y2) in zip(ring, ring[1:])) / (6 * a)
    return cx, cy


def coverage(geometry: dict, z: int, x: int, y: int, samples: int = 16) -> float:
    """Share of the tile inside the geometry, sampled on a samples×samples grid in mercator."""
    n = 2**z
    hits = 0
    for i in range(samples):
        lon = (x + (i + 0.5) / samples) / n * 360 - 180
        for j in range(samples):
            yy = y + (j + 0.5) / samples
            lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * yy / n))))
            hits += contains(geometry, lon, lat)
    return hits / samples**2
