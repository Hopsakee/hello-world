"""SQLite-level GeoPackage operations: working copies, the changedate column, export.

Everything here uses plain ``sqlite3``. A GeoPackage is a SQLite database, so
adding a column, updating attributes and deleting rows need no GDAL - and
keeping the file untouched otherwise means CRS definitions, metadata and the
spatial index survive exactly as the source had them.
"""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path
from typing import Any, Iterable, Sequence

from gpkg_changetracker import geometry as geom
from gpkg_changetracker.validation import utc_now_iso

#: Name of the column this app adds to track when a row was last edited.
CHANGEDATE_COLUMN = "changedate"

#: Table the app writes its per-field history to, inside the working copy.
LOG_TABLE = "changetracker_log"

_GPKG_APPLICATION_ID = 0x47504B47  # 'GPKG'


def register_gpkg_functions(conn: sqlite3.Connection) -> None:
    """Provide the ``ST_*`` functions the spatial-index triggers call.

    GDAL writes RTree triggers that call ``ST_MinX``/``ST_IsEmpty``. Those are
    GDAL SQL functions, and without them plain SQLite refuses *any* UPDATE or
    DELETE on the feature table - the trigger's WHEN clause is evaluated even
    when it can only be false. Implementing them on top of the blob reader
    keeps the spatial index correct instead of merely silencing the error.
    """

    def _bounds(blob: Any) -> tuple[float, float, float, float] | None:
        if blob is None:
            return None
        try:
            return geom.envelope_of(blob)
        except geom.GeometryError:
            return None

    def _corner(index: int):
        def getter(blob: Any) -> float | None:
            bounds = _bounds(blob)
            return None if bounds is None else bounds[index]

        return getter

    def st_is_empty(blob: Any) -> int:
        bounds = _bounds(blob)
        return 1 if bounds is None else 0

    def st_srid(blob: Any) -> int | None:
        if blob is None:
            return None
        try:
            decoded = geom.decode(blob)
        except geom.GeometryError:
            return None
        return None if decoded is None else decoded.srs_id

    conn.create_function("ST_MinX", 1, _corner(0))
    conn.create_function("ST_MinY", 1, _corner(1))
    conn.create_function("ST_MaxX", 1, _corner(2))
    conn.create_function("ST_MaxY", 1, _corner(3))
    conn.create_function("ST_IsEmpty", 1, st_is_empty)
    conn.create_function("ST_SRID", 1, st_srid)


def open_gpkg(path: str | Path, *, read_only: bool = False) -> sqlite3.Connection:
    """Open a GeoPackage with name-based row access and the ``ST_*`` helpers."""
    path = Path(path)
    if read_only:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    else:
        conn = sqlite3.connect(path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = OFF")
    register_gpkg_functions(conn)
    return conn


def is_geopackage(path: str | Path) -> bool:
    """True when ``path`` looks like a GeoPackage rather than a plain database."""
    path = Path(path)
    if not path.is_file():
        return False
    conn = None
    try:
        conn = open_gpkg(path, read_only=True)
        application_id = conn.execute("PRAGMA application_id").fetchone()[0]
        has_contents = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'gpkg_contents'"
        ).fetchone()
        return application_id == _GPKG_APPLICATION_ID or has_contents is not None
    except sqlite3.DatabaseError:
        return False
    finally:
        if conn is not None:
            conn.close()


def column_names(conn: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')]


def create_working_copy(source: str | Path, target: str | Path) -> Path:
    """Copy the source file so the original is never written to."""
    source, target = Path(source), Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    # A copied file may carry -wal/-shm siblings; drop them so we start clean.
    for suffix in ("-wal", "-shm"):
        sibling = Path(str(target) + suffix)
        if sibling.exists():
            sibling.unlink()
    return target


def ensure_changedate_column(conn: sqlite3.Connection, table: str) -> bool:
    """Add the ``changedate`` column if it is not there yet. Returns True if added."""
    if CHANGEDATE_COLUMN in column_names(conn, table):
        return False
    conn.execute(f'ALTER TABLE "{table}" ADD COLUMN "{CHANGEDATE_COLUMN}" DATETIME')
    return True


def ensure_log_table(conn: sqlite3.Connection) -> None:
    """Create the per-field change log used to recognise reverted values."""
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS "{LOG_TABLE}" (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            table_name TEXT NOT NULL,
            fid INTEGER NOT NULL,
            column_name TEXT NOT NULL,
            original_value TEXT,
            old_value TEXT,
            new_value TEXT,
            changed_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        f'CREATE INDEX IF NOT EXISTS "{LOG_TABLE}_fid" ON "{LOG_TABLE}" (table_name, fid)'
    )


def read_row(
    conn: sqlite3.Connection,
    table: str,
    pk_column: str,
    fid: int,
    columns: Sequence[str] | None = None,
) -> dict[str, Any] | None:
    selected = ", ".join(f'"{name}"' for name in columns) if columns else "*"
    row = conn.execute(
        f'SELECT {selected} FROM "{table}" WHERE "{pk_column}" = ?', (fid,)
    ).fetchone()
    return dict(row) if row is not None else None


def update_values(
    conn: sqlite3.Connection,
    table: str,
    pk_column: str,
    fid: int,
    values: dict[str, Any],
) -> None:
    """Write attribute values for one row."""
    if not values:
        return
    assignments = ", ".join(f'"{name}" = ?' for name in values)
    conn.execute(
        f'UPDATE "{table}" SET {assignments} WHERE "{pk_column}" = ?',
        (*values.values(), fid),
    )


def export_subset(
    working_path: str | Path,
    out_path: str | Path,
    table: str,
    pk_column: str,
    fids: Iterable[int],
    *,
    geometry_column: str | None = None,
) -> int:
    """Write a new GeoPackage containing only ``fids`` of ``table``.

    The working copy is duplicated and the other rows are deleted, so the result
    keeps the original schema, CRS, metadata and spatial index. Returns the
    number of rows in the new file.
    """
    working_path, out_path = Path(working_path), Path(out_path)
    keep = sorted({int(fid) for fid in fids})
    out_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(working_path, out_path)

    conn = open_gpkg(out_path)
    try:
        conn.execute("BEGIN")
        conn.execute("CREATE TEMP TABLE _keep (fid INTEGER PRIMARY KEY)")
        conn.executemany("INSERT INTO _keep (fid) VALUES (?)", [(fid,) for fid in keep])
        conn.execute(
            f'DELETE FROM "{table}" WHERE "{pk_column}" NOT IN (SELECT fid FROM _keep)'
        )
        conn.execute("DROP TABLE _keep")
        # The log lives in the working copy only; the deliverable stays clean.
        conn.execute(f'DROP TABLE IF EXISTS "{LOG_TABLE}"')
        remaining = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        _refresh_contents(conn, table, geometry_column)
        conn.execute("COMMIT")
        conn.execute("VACUUM")
    finally:
        conn.close()
    return remaining


def _refresh_contents(
    conn: sqlite3.Connection, table: str, geometry_column: str | None
) -> None:
    """Bring ``gpkg_contents`` in line with the rows that are left."""
    has_contents = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'gpkg_contents'"
    ).fetchone()
    if not has_contents:
        return

    bounds = None
    if geometry_column:
        blobs = conn.execute(f'SELECT "{geometry_column}" FROM "{table}"').fetchall()
        bounds = geom.merge_bounds(geom.envelope_of(row[0]) for row in blobs)

    if bounds is None:
        conn.execute(
            "UPDATE gpkg_contents SET last_change = ? WHERE table_name = ?",
            (utc_now_iso(), table),
        )
        return
    conn.execute(
        "UPDATE gpkg_contents SET min_x = ?, min_y = ?, max_x = ?, max_y = ?, last_change = ? "
        "WHERE table_name = ?",
        (bounds[0], bounds[1], bounds[2], bounds[3], utc_now_iso(), table),
    )
