"""Build a small demo GeoPackage, so the app can be tried without real data.

The file is written with plain ``sqlite3`` and mirrors what GDAL produces: typed
columns with ``CHECK`` constraints, an RTree spatial index with its triggers,
and a coded-value domain through the GeoPackage Schema extension.
"""

from __future__ import annotations

import sqlite3
import struct
from pathlib import Path
from typing import Sequence

from gpkg_changetracker.validation import utc_now_iso

TABLE = "waterways"
GEOMETRY_COLUMN = "geom"
SRS_ID = 28992

#: Rijksdriehoek coordinates, roughly around Zwolle.
_FEATURES: tuple[tuple[dict[str, object], list[list[tuple[float, float]]]], ...] = (
    (
        {
            "code": "WG-0001",
            "name": "Grote Wetering",
            "depth": 1.2,
            "category": 1,
            "width": 6.5,
            "inspected": "2025-04-18",
            "accessible": 1,
            "notes": "Noordelijke tak, oever recent gemaaid.",
        },
        [[(202000.0, 502000.0), (202150.0, 502080.0), (202320.0, 502090.0)]],
    ),
    (
        {
            "code": "WG-0002",
            "name": "Kleine Wetering",
            "depth": 0.8,
            "category": 2,
            "width": 3.0,
            "inspected": "2025-05-02",
            "accessible": 1,
            "notes": None,
        },
        [[(202320.0, 502090.0), (202400.0, 501900.0), (202480.0, 501750.0)]],
    ),
    (
        {
            "code": "WG-0003",
            "name": "Molensloot",
            "depth": 0.55,
            "category": 2,
            "width": 2.2,
            "inspected": None,
            "accessible": 0,
            "notes": "Duiker onder de weg is vernauwd.",
        },
        [
            [(201800.0, 501850.0), (201950.0, 501890.0)],
            [(201950.0, 501890.0), (202100.0, 501820.0), (202230.0, 501760.0)],
        ],
    ),
    (
        {
            "code": "WG-0004",
            "name": "Oude Vaart",
            "depth": 1.75,
            "category": 3,
            "width": 9.0,
            "inspected": "2024-11-27",
            "accessible": 1,
            "notes": None,
        },
        [
            [
                (201700.0, 502200.0),
                (201900.0, 502250.0),
                (202100.0, 502210.0),
                (202300.0, 502300.0),
            ]
        ],
    ),
    (
        {
            "code": "WG-0005",
            "name": "Zandwetering",
            "depth": 0.95,
            "category": 1,
            "width": 4.4,
            "inspected": "2025-06-11",
            "accessible": 0,
            "notes": "Stuw aan de zuidzijde.",
        },
        [[(202500.0, 502400.0), (202650.0, 502300.0), (202800.0, 502120.0)]],
    ),
)

_CATEGORY_DOMAIN = (
    (1, "hoofdwatergang"),
    (2, "schouwsloot"),
    (3, "kanaal"),
)


def encode_multilinestring(
    parts: Sequence[Sequence[tuple[float, float]]], srs_id: int
) -> bytes:
    """Encode line parts as a GeoPackageBinary MultiLineString blob."""
    header = b"GP" + bytes([0, 0x01]) + struct.pack("<i", srs_id)
    body = struct.pack("<BII", 1, 5, len(parts))  # little endian, MultiLineString
    for part in parts:
        body += struct.pack("<BII", 1, 2, len(part))
        for x, y in part:
            body += struct.pack("<dd", x, y)
    return header + body


def _bounds(
    features: Sequence[tuple[dict[str, object], list[list[tuple[float, float]]]]],
) -> tuple[float, float, float, float]:
    xs = [x for _, parts in features for part in parts for x, _ in part]
    ys = [y for _, parts in features for part in parts for _, y in part]
    return min(xs), min(ys), max(xs), max(ys)


def write_sample(path: str | Path) -> Path:
    """Write the demo GeoPackage to ``path`` (overwriting it) and return the path."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()

    conn = sqlite3.connect(path, isolation_level=None)
    try:
        conn.executescript(
            f"""
            PRAGMA application_id = 1196444487;   -- 'GPKG'
            PRAGMA user_version = 10400;

            CREATE TABLE gpkg_spatial_ref_sys (
                srs_name TEXT NOT NULL,
                srs_id INTEGER NOT NULL PRIMARY KEY,
                organization TEXT NOT NULL,
                organization_coordsys_id INTEGER NOT NULL,
                definition TEXT NOT NULL,
                description TEXT
            );

            CREATE TABLE gpkg_contents (
                table_name TEXT NOT NULL PRIMARY KEY,
                data_type TEXT NOT NULL,
                identifier TEXT UNIQUE,
                description TEXT DEFAULT '',
                last_change DATETIME NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                min_x DOUBLE, min_y DOUBLE, max_x DOUBLE, max_y DOUBLE,
                srs_id INTEGER,
                CONSTRAINT fk_gc_r_srs_id FOREIGN KEY (srs_id)
                    REFERENCES gpkg_spatial_ref_sys(srs_id)
            );

            CREATE TABLE gpkg_geometry_columns (
                table_name TEXT NOT NULL,
                column_name TEXT NOT NULL,
                geometry_type_name TEXT NOT NULL,
                srs_id INTEGER NOT NULL,
                z TINYINT NOT NULL,
                m TINYINT NOT NULL,
                CONSTRAINT pk_geom_cols PRIMARY KEY (table_name, column_name)
            );

            CREATE TABLE gpkg_extensions (
                table_name TEXT, column_name TEXT, extension_name TEXT NOT NULL,
                definition TEXT NOT NULL, scope TEXT NOT NULL,
                CONSTRAINT ge_tce UNIQUE (table_name, column_name, extension_name)
            );

            CREATE TABLE gpkg_data_columns (
                table_name TEXT NOT NULL, column_name TEXT NOT NULL,
                name TEXT, title TEXT, description TEXT,
                mime_type TEXT, constraint_name TEXT,
                CONSTRAINT pk_gdc PRIMARY KEY (table_name, column_name)
            );

            CREATE TABLE gpkg_data_column_constraints (
                constraint_name TEXT NOT NULL, constraint_type TEXT NOT NULL,
                value TEXT, min NUMERIC, min_is_inclusive BOOLEAN,
                max NUMERIC, max_is_inclusive BOOLEAN, description TEXT
            );

            CREATE TABLE "{TABLE}" (
                fid INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
                "{GEOMETRY_COLUMN}" MULTILINESTRING,
                code TEXT(8) CHECK(typeof(code) = 'text' AND NOT length(code) > 8) NOT NULL,
                name TEXT(60) CHECK((typeof(name) = 'text' OR typeof(name) = 'null')
                    AND NOT length(name) > 60),
                depth FLOAT CHECK((typeof(depth) = 'real' OR typeof(depth) = 'null')
                    AND depth >= -100.0 AND depth <= 100.0),
                category MEDIUMINT CHECK((typeof(category) = 'integer' OR typeof(category) = 'null')
                    AND category >= -2147483648 AND category <= 2147483647),
                width DOUBLE CHECK(typeof(width) = 'real' OR typeof(width) = 'null'),
                inspected DATE,
                accessible BOOLEAN CHECK((typeof(accessible) = 'integer'
                    OR typeof(accessible) = 'null') AND accessible IN (0, 1)),
                notes TEXT(500) CHECK((typeof(notes) = 'text' OR typeof(notes) = 'null')
                    AND NOT length(notes) > 500)
            );

            CREATE VIRTUAL TABLE "rtree_{TABLE}_{GEOMETRY_COLUMN}" USING rtree(id, minx, maxx, miny, maxy);

            CREATE TRIGGER "rtree_{TABLE}_{GEOMETRY_COLUMN}_insert"
            AFTER INSERT ON "{TABLE}"
            WHEN (new."{GEOMETRY_COLUMN}" NOT NULL AND NOT ST_IsEmpty(NEW."{GEOMETRY_COLUMN}"))
            BEGIN
                INSERT OR REPLACE INTO "rtree_{TABLE}_{GEOMETRY_COLUMN}" VALUES (
                    NEW.fid,
                    ST_MinX(NEW."{GEOMETRY_COLUMN}"), ST_MaxX(NEW."{GEOMETRY_COLUMN}"),
                    ST_MinY(NEW."{GEOMETRY_COLUMN}"), ST_MaxY(NEW."{GEOMETRY_COLUMN}"));
            END;

            CREATE TRIGGER "rtree_{TABLE}_{GEOMETRY_COLUMN}_delete"
            AFTER DELETE ON "{TABLE}" WHEN old."{GEOMETRY_COLUMN}" NOT NULL
            BEGIN
                DELETE FROM "rtree_{TABLE}_{GEOMETRY_COLUMN}" WHERE id = OLD.fid;
            END;

            CREATE TRIGGER "rtree_{TABLE}_{GEOMETRY_COLUMN}_update5"
            AFTER UPDATE ON "{TABLE}"
            WHEN OLD.fid != NEW.fid AND (NEW."{GEOMETRY_COLUMN}" NOTNULL
                AND NOT ST_IsEmpty(NEW."{GEOMETRY_COLUMN}"))
            BEGIN
                DELETE FROM "rtree_{TABLE}_{GEOMETRY_COLUMN}" WHERE id = OLD.fid;
                INSERT OR REPLACE INTO "rtree_{TABLE}_{GEOMETRY_COLUMN}" VALUES (
                    NEW.fid,
                    ST_MinX(NEW."{GEOMETRY_COLUMN}"), ST_MaxX(NEW."{GEOMETRY_COLUMN}"),
                    ST_MinY(NEW."{GEOMETRY_COLUMN}"), ST_MaxY(NEW."{GEOMETRY_COLUMN}"));
            END;
            """
        )

        conn.executemany(
            "INSERT INTO gpkg_spatial_ref_sys "
            "(srs_name, srs_id, organization, organization_coordsys_id, definition) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                ("Undefined cartesian SRS", -1, "NONE", -1, "undefined"),
                ("Undefined geographic SRS", 0, "NONE", 0, "undefined"),
                ("WGS 84 geodetic", 4326, "EPSG", 4326, 'GEOGCS["WGS 84"]'),
                (
                    "Amersfoort / RD New",
                    SRS_ID,
                    "EPSG",
                    SRS_ID,
                    'PROJCS["Amersfoort / RD New"]',
                ),
            ],
        )
        minimum_x, minimum_y, maximum_x, maximum_y = _bounds(_FEATURES)
        conn.execute(
            "INSERT INTO gpkg_contents "
            "(table_name, data_type, identifier, description, last_change, "
            " min_x, min_y, max_x, max_y, srs_id) VALUES (?, 'features', ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                TABLE,
                TABLE,
                "Demo waterways for the GeoPackage change tracker",
                utc_now_iso(),
                minimum_x,
                minimum_y,
                maximum_x,
                maximum_y,
                SRS_ID,
            ),
        )
        conn.execute(
            "INSERT INTO gpkg_geometry_columns VALUES (?, ?, 'MULTILINESTRING', ?, 0, 0)",
            (TABLE, GEOMETRY_COLUMN, SRS_ID),
        )
        conn.execute(
            "INSERT INTO gpkg_extensions VALUES (?, ?, 'gpkg_rtree_index', "
            "'http://www.geopackage.org/spec120/#extension_rtree', 'write-only')",
            (TABLE, GEOMETRY_COLUMN),
        )
        conn.execute(
            "INSERT INTO gpkg_extensions VALUES (NULL, NULL, 'gpkg_schema', "
            "'http://www.geopackage.org/spec120/#extension_schema', 'read-write')"
        )
        conn.execute(
            "INSERT INTO gpkg_data_columns "
            "(table_name, column_name, name, title, description, constraint_name) "
            "VALUES (?, 'category', 'category', 'Watergang categorie', "
            "'Coded value domain', 'category_domain')",
            (TABLE,),
        )
        conn.executemany(
            "INSERT INTO gpkg_data_column_constraints "
            "(constraint_name, constraint_type, value, description) VALUES (?, 'enum', ?, ?)",
            [
                ("category_domain", str(value), label)
                for value, label in _CATEGORY_DOMAIN
            ],
        )

        from gpkg_changetracker.gpkg_io import register_gpkg_functions

        register_gpkg_functions(conn)
        conn.executemany(
            f'INSERT INTO "{TABLE}" ("{GEOMETRY_COLUMN}", code, name, depth, category, '
            "width, inspected, accessible, notes) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    encode_multilinestring(parts, SRS_ID),
                    attributes["code"],
                    attributes["name"],
                    attributes["depth"],
                    attributes["category"],
                    attributes["width"],
                    attributes["inspected"],
                    attributes["accessible"],
                    attributes["notes"],
                )
                for attributes, parts in _FEATURES
            ],
        )
    finally:
        conn.close()
    return path


if __name__ == "__main__":  # pragma: no cover
    import sys

    target = Path(sys.argv[1] if len(sys.argv) > 1 else "sample_waterways.gpkg")
    print(f"wrote {write_sample(target)}")
