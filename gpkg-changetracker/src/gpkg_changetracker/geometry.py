"""Read GeoPackage geometry blobs without a GDAL dependency.

Only reading is needed: the app never edits geometry, so blobs are copied
verbatim on export. Decoding exists to draw features on the map and to compute
the bounding box of an exported subset.

A blob is a GeoPackageBinary header (magic ``GP``, flags, srs_id, optional
envelope) followed by standard WKB, as described in the GeoPackage spec.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from functools import lru_cache
from typing import Iterable, Sequence

Coord = tuple[float, float]
Part = list[Coord]

_ENVELOPE_DOUBLES = {0: 0, 1: 4, 2: 6, 3: 6, 4: 8}
_WKB_POINT = 1
_WKB_LINESTRING = 2
_WKB_POLYGON = 3
_WKB_MULTIPOINT = 4
_WKB_MULTILINESTRING = 5
_WKB_MULTIPOLYGON = 6
_WKB_GEOMETRYCOLLECTION = 7


class GeometryError(ValueError):
    """Raised for blobs that are not readable GeoPackage geometry."""


@dataclass(frozen=True)
class Geometry:
    """A decoded geometry, flattened to a list of coordinate sequences."""

    srs_id: int
    kind: int
    parts: tuple[Part, ...]
    is_point_like: bool = False

    @property
    def bounds(self) -> tuple[float, float, float, float] | None:
        """``(min_x, min_y, max_x, max_y)`` or ``None`` for an empty geometry."""
        xs = [x for part in self.parts for x, _ in part]
        ys = [y for part in self.parts for _, y in part]
        if not xs:
            return None
        return min(xs), min(ys), max(xs), max(ys)

    @property
    def representative_point(self) -> Coord | None:
        """A point on the geometry, used as its clickable handle on the map."""
        parts = [part for part in self.parts if part]
        if not parts:
            return None
        longest = max(parts, key=len)
        return longest[len(longest) // 2]


def decode(blob: bytes | memoryview | None) -> Geometry | None:
    """Decode a GeoPackage geometry blob. ``None`` in, ``None`` out."""
    if blob is None:
        return None
    data = bytes(blob)
    if len(data) < 8 or data[:2] != b"GP":
        raise GeometryError("not a GeoPackage geometry blob (missing 'GP' magic)")

    flags = data[3]
    little_endian = bool(flags & 0x01)
    order = "<" if little_endian else ">"
    srs_id = struct.unpack_from(f"{order}i", data, 4)[0]
    envelope_code = (flags >> 1) & 0x07
    if envelope_code not in _ENVELOPE_DOUBLES:
        raise GeometryError(f"unsupported envelope indicator {envelope_code}")
    offset = 8 + _ENVELOPE_DOUBLES[envelope_code] * 8

    if flags & 0x10:  # empty geometry flag
        return Geometry(srs_id=srs_id, kind=0, parts=())

    parts: list[Part] = []
    kind, offset = _read_wkb(data, offset, parts)
    return Geometry(
        srs_id=srs_id,
        kind=kind,
        parts=tuple(parts),
        is_point_like=kind in (_WKB_POINT, _WKB_MULTIPOINT),
    )


def _read_wkb(data: bytes, offset: int, parts: list[Part]) -> tuple[int, int]:
    """Read one WKB geometry at ``offset``, appending its rings to ``parts``."""
    if offset + 5 > len(data):
        raise GeometryError("truncated WKB geometry")
    order = "<" if data[offset] == 1 else ">"
    raw_type = struct.unpack_from(f"{order}I", data, offset + 1)[0]
    offset += 5

    # ISO WKB encodes Z/M by adding 1000/2000/3000; EWKB uses high bits instead.
    base_type = raw_type & 0x0FFFFFFF
    has_z = bool(raw_type & 0x80000000) or base_type // 1000 in (1, 3)
    has_m = bool(raw_type & 0x40000000) or base_type // 1000 in (2, 3)
    if raw_type & 0x20000000:  # EWKB SRID flag: skip the embedded srid
        offset += 4
    geom_type = base_type % 1000

    if geom_type == _WKB_POINT:
        coords, offset = _read_coords(data, offset, 1, order, has_z, has_m)
        parts.append(coords)
    elif geom_type == _WKB_LINESTRING:
        count = struct.unpack_from(f"{order}I", data, offset)[0]
        offset += 4
        coords, offset = _read_coords(data, offset, count, order, has_z, has_m)
        parts.append(coords)
    elif geom_type == _WKB_POLYGON:
        rings = struct.unpack_from(f"{order}I", data, offset)[0]
        offset += 4
        for _ in range(rings):
            count = struct.unpack_from(f"{order}I", data, offset)[0]
            offset += 4
            coords, offset = _read_coords(data, offset, count, order, has_z, has_m)
            parts.append(coords)
    elif geom_type in (
        _WKB_MULTIPOINT,
        _WKB_MULTILINESTRING,
        _WKB_MULTIPOLYGON,
        _WKB_GEOMETRYCOLLECTION,
    ):
        count = struct.unpack_from(f"{order}I", data, offset)[0]
        offset += 4
        for _ in range(count):
            _, offset = _read_wkb(data, offset, parts)
    else:
        raise GeometryError(f"unsupported WKB geometry type {geom_type}")
    return geom_type, offset


def _read_coords(
    data: bytes, offset: int, count: int, order: str, has_z: bool, has_m: bool
) -> tuple[Part, int]:
    stride = 2 + int(has_z) + int(has_m)
    needed = count * stride * 8
    if offset + needed > len(data):
        raise GeometryError("truncated coordinate list in WKB geometry")
    values = struct.unpack_from(f"{order}{count * stride}d", data, offset)
    coords = [(values[i], values[i + 1]) for i in range(0, len(values), stride)]
    return coords, offset + needed


def envelope_of(
    blob: bytes | memoryview | None,
) -> tuple[float, float, float, float] | None:
    """Bounding box of a blob, from its header envelope when it has one."""
    if blob is None:
        return None
    data = bytes(blob)
    if len(data) < 8 or data[:2] != b"GP":
        raise GeometryError("not a GeoPackage geometry blob (missing 'GP' magic)")
    flags = data[3]
    order = "<" if flags & 0x01 else ">"
    envelope_code = (flags >> 1) & 0x07
    if envelope_code in (1, 2, 3, 4):
        min_x, max_x, min_y, max_y = struct.unpack_from(f"{order}4d", data, 8)
        return min_x, min_y, max_x, max_y
    geometry = decode(data)
    return geometry.bounds if geometry else None


def merge_bounds(
    boxes: Iterable[tuple[float, float, float, float] | None],
) -> tuple[float, float, float, float] | None:
    """Union of bounding boxes, ignoring ``None``."""
    result: tuple[float, float, float, float] | None = None
    for box in boxes:
        if box is None:
            continue
        if result is None:
            result = box
        else:
            result = (
                min(result[0], box[0]),
                min(result[1], box[1]),
                max(result[2], box[2]),
                max(result[3], box[3]),
            )
    return result


def thin(coords: Sequence[Coord], max_points: int) -> Part:
    """Drop intermediate vertices so a long line stays cheap to draw."""
    if max_points < 2 or len(coords) <= max_points:
        return list(coords)
    step = (len(coords) - 1) / (max_points - 1)
    picked = [
        coords[min(int(round(index * step)), len(coords) - 1)]
        for index in range(max_points)
    ]
    return picked


@lru_cache(maxsize=32)
def _transformer(srs_id: int):
    from pyproj import Transformer

    return Transformer.from_crs(f"EPSG:{srs_id}", "EPSG:4326", always_xy=True)


def to_wgs84(parts: Sequence[Part], srs_id: int | None) -> tuple[Part, ...]:
    """Reproject coordinate sequences to lon/lat, so they can go on a web map."""
    if not parts:
        return ()
    if srs_id in (None, 0, -1, 4326, 4979):
        return tuple(list(part) for part in parts)
    transformer = _transformer(int(srs_id))
    out: list[Part] = []
    for part in parts:
        if not part:
            out.append([])
            continue
        xs, ys = zip(*part)
        lons, lats = transformer.transform(xs, ys)
        out.append(list(zip(lons, lats)))
    return tuple(out)
