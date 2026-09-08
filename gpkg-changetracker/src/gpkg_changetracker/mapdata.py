"""Turn features into the two small tables the map is drawn from.

Altair draws inline data, so a 25 000-feature layer with 200 000 vertices is
not an option. Every feature is therefore thinned to a handful of vertices and
reprojected to lon/lat here:

* ``vertices`` - one row per drawn vertex, for the line layer;
* ``points``   - one row per feature, its clickable handle on the map.
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

import pandas as pd

from gpkg_changetracker import geometry as geom
from gpkg_changetracker.gpkg_io import CHANGEDATE_COLUMN
from gpkg_changetracker.session import ChangeTrackingSession

#: Vertices kept per feature; enough to keep a watercourse recognisable.
DEFAULT_MAX_POINTS = 14

VERTEX_COLUMNS = ("fid", "part", "seq", "lon", "lat", "status")
POINT_COLUMNS = ("fid", "lon", "lat", "status", "label", CHANGEDATE_COLUMN)


def feature_frames(
    session: ChangeTrackingSession,
    fids: Iterable[int],
    *,
    label_columns: Sequence[str] = (),
    max_points: int = DEFAULT_MAX_POINTS,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Build the ``(vertices, points)`` frames for ``fids``."""
    fids = [int(fid) for fid in fids]
    if not fids:
        return _empty(VERTEX_COLUMNS), _empty(POINT_COLUMNS)

    geometries = session.geometries(fids)
    changed = set(session.changed_fids())
    labels = _labels(session, fids, label_columns)

    vertex_rows: list[dict[str, Any]] = []
    point_rows: list[dict[str, Any]] = []
    for fid in fids:
        geometry = geometries.get(fid)
        if geometry is None or not geometry.parts:
            continue
        status = "changed" if fid in changed else "unchanged"
        thinned = [geom.thin(part, max_points) for part in geometry.parts if part]
        projected = geom.to_wgs84(thinned, geometry.srs_id or session.schema.srs_id)
        for part_index, part in enumerate(projected):
            for seq, (lon, lat) in enumerate(part):
                vertex_rows.append(
                    {
                        "fid": fid,
                        "part": f"{fid}-{part_index}",
                        "seq": seq,
                        "lon": lon,
                        "lat": lat,
                        "status": status,
                    }
                )
        handle = _handle(projected)
        if handle is not None:
            point_rows.append(
                {
                    "fid": fid,
                    "lon": handle[0],
                    "lat": handle[1],
                    "status": status,
                    "label": labels.get(fid, {}).get("label", str(fid)),
                    CHANGEDATE_COLUMN: labels.get(fid, {}).get(CHANGEDATE_COLUMN),
                }
            )

    vertices = pd.DataFrame(vertex_rows, columns=list(VERTEX_COLUMNS))
    points = pd.DataFrame(point_rows, columns=list(POINT_COLUMNS))
    return vertices, points


def _handle(parts: Sequence[list[tuple[float, float]]]) -> tuple[float, float] | None:
    """Midpoint of the longest drawn part: the feature's handle on the map."""
    usable = [part for part in parts if part]
    if not usable:
        return None
    longest = max(usable, key=len)
    return longest[len(longest) // 2]


def _labels(
    session: ChangeTrackingSession, fids: Sequence[int], label_columns: Sequence[str]
) -> dict[int, dict[str, Any]]:
    wanted = [column for column in label_columns if session.schema.has(column)]
    columns = [session.pk_column, *wanted]
    if session.schema.has(CHANGEDATE_COLUMN):
        columns.append(CHANGEDATE_COLUMN)
    rows = session.rows(fids=fids, columns=columns, limit=None)
    out: dict[int, dict[str, Any]] = {}
    for row in rows:
        fid = int(row[session.pk_column])
        parts = [f"{session.pk_column} {fid}"]
        parts += [
            f"{column}: {row[column]}"
            for column in wanted
            if row.get(column) is not None
        ]
        out[fid] = {
            "label": " | ".join(parts),
            CHANGEDATE_COLUMN: row.get(CHANGEDATE_COLUMN),
        }
    return out


def _empty(columns: Sequence[str]) -> pd.DataFrame:
    return pd.DataFrame({column: pd.Series(dtype="object") for column in columns})


def bounds_of(frame: pd.DataFrame) -> tuple[float, float, float, float] | None:
    """``(min_lon, min_lat, max_lon, max_lat)`` of any frame with lon/lat columns."""
    if frame.empty:
        return None
    return (
        float(frame["lon"].min()),
        float(frame["lat"].min()),
        float(frame["lon"].max()),
        float(frame["lat"].max()),
    )
