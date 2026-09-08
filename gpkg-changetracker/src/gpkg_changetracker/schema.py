"""Derive per-field definitions from a GeoPackage so edits can be validated.

Three sources are combined, each refining the previous one:

1. ``PRAGMA table_info`` - column name, declared type, NOT NULL, primary key.
2. The ``CREATE TABLE`` statement - GDAL writes ``CHECK`` constraints that carry
   the real value range and text length of every column.
3. The GeoPackage *Schema* extension (``gpkg_data_columns`` /
   ``gpkg_data_column_constraints``) - column titles plus enum, range and glob
   domains, when the file happens to define them.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, replace
from typing import Any, Literal

Storage = Literal["integer", "real", "text", "blob", "datetime", "boolean", "geometry"]

#: Ranges implied by the integer type names GDAL/OGR writes into GeoPackages.
_INT_RANGES: dict[str, tuple[int, int]] = {
    "TINYINT": (-128, 127),
    "SMALLINT": (-32768, 32767),
    "MEDIUMINT": (-2147483648, 2147483647),
    "INT": (-(2**63), 2**63 - 1),
    "INTEGER": (-(2**63), 2**63 - 1),
    "BIGINT": (-(2**63), 2**63 - 1),
}

#: Largest finite value a 32-bit float can hold; ``FLOAT`` columns are limited to it.
FLOAT32_MAX = 3.4028234663852886e38

_GEOMETRY_TYPES = frozenset(
    {
        "GEOMETRY",
        "POINT",
        "LINESTRING",
        "POLYGON",
        "MULTIPOINT",
        "MULTILINESTRING",
        "MULTIPOLYGON",
        "GEOMETRYCOLLECTION",
        "CIRCULARSTRING",
        "COMPOUNDCURVE",
        "CURVEPOLYGON",
        "MULTICURVE",
        "MULTISURFACE",
        "CURVE",
        "SURFACE",
    }
)

_TYPE_RE = re.compile(
    r"^\s*([A-Za-z_][A-Za-z_0-9 ]*?)\s*(?:\(\s*(\d+)\s*(?:,\s*\d+\s*)?\))?\s*$"
)


@dataclass(frozen=True)
class FieldSpec:
    """Everything the app needs to render an input for a column and validate it."""

    name: str
    declared_type: str
    storage: Storage
    nullable: bool = True
    primary_key: bool = False
    max_length: int | None = None
    minimum: float | None = None
    maximum: float | None = None
    enum: tuple[tuple[Any, str], ...] | None = None
    glob: str | None = None
    title: str | None = None
    description: str | None = None

    @property
    def editable(self) -> bool:
        """Geometry, blobs and the feature id are managed by the app, not the user."""
        return self.storage not in ("geometry", "blob") and not self.primary_key

    @property
    def label(self) -> str:
        return self.title or self.name

    def describe(self) -> str:
        """Short, human readable summary of the constraints on this field."""
        parts = [self.declared_type or self.storage]
        if not self.nullable:
            parts.append("required")
        if self.max_length is not None:
            parts.append(f"max {self.max_length} characters")
        if self.enum:
            values = ", ".join(str(value) for value, _ in self.enum[:12])
            parts.append(f"one of: {values}{', ...' if len(self.enum) > 12 else ''}")
        elif self.storage == "boolean":
            parts.append("true or false")
        elif self.minimum is not None or self.maximum is not None:
            low = "-inf" if self.minimum is None else _fmt_number(self.minimum)
            high = "inf" if self.maximum is None else _fmt_number(self.maximum)
            parts.append(f"{low} .. {high}")
        if self.glob:
            parts.append(f"pattern {self.glob}")
        return ", ".join(parts)


@dataclass(frozen=True)
class TableSchema:
    """The columns of one GeoPackage feature table."""

    name: str
    fields: tuple[FieldSpec, ...]
    pk_column: str
    geometry_column: str | None = None
    srs_id: int | None = None

    def by_name(self, name: str) -> FieldSpec:
        for spec in self.fields:
            if spec.name == name:
                return spec
        raise KeyError(f"{self.name!r} has no column {name!r}")

    def has(self, name: str) -> bool:
        return any(spec.name == name for spec in self.fields)

    @property
    def editable_fields(self) -> tuple[FieldSpec, ...]:
        return tuple(spec for spec in self.fields if spec.editable)

    @property
    def column_names(self) -> tuple[str, ...]:
        return tuple(spec.name for spec in self.fields)


def _fmt_number(value: float) -> str:
    if isinstance(value, int) or float(value).is_integer():
        return str(int(value))
    return f"{value:g}"


def _split_type(declared: str) -> tuple[str, int | None]:
    """``"TEXT(24)"`` -> ``("TEXT", 24)``."""
    match = _TYPE_RE.match(declared or "")
    if not match:
        return (declared or "").strip().upper(), None
    base = match.group(1).strip().upper()
    length = int(match.group(2)) if match.group(2) else None
    return base, length


def _storage_of(base: str) -> Storage:
    if base in _GEOMETRY_TYPES:
        return "geometry"
    if base == "BOOLEAN":
        return "boolean"
    if base in ("DATE", "DATETIME", "TIMESTAMP"):
        return "datetime"
    if base in _INT_RANGES:
        return "integer"
    if base in ("FLOAT", "REAL", "DOUBLE", "DOUBLE PRECISION", "NUMERIC", "DECIMAL"):
        return "real"
    if base in ("TEXT", "CLOB", "CHAR", "VARCHAR", "CHARACTER", "NVARCHAR", "STRING"):
        return "text"
    if base in ("BLOB", "BINARY", "VARBINARY", ""):
        return "blob"
    # Unknown declared types get SQLite's own affinity rules, defaulting to text.
    if "INT" in base:
        return "integer"
    if any(token in base for token in ("REAL", "FLOA", "DOUB")):
        return "real"
    if any(token in base for token in ("CHAR", "TEXT")):
        return "text"
    return "text"


def _spec_from_column(name: str, declared: str, notnull: int, pk: int) -> FieldSpec:
    base, length = _split_type(declared)
    storage = _storage_of(base)
    minimum: float | None = None
    maximum: float | None = None
    if storage == "integer":
        minimum, maximum = _INT_RANGES.get(base, _INT_RANGES["INTEGER"])
    elif storage == "real" and base == "FLOAT":
        minimum, maximum = -FLOAT32_MAX, FLOAT32_MAX
    elif storage == "boolean":
        minimum, maximum = 0, 1
    return FieldSpec(
        name=name,
        declared_type=(declared or "").strip(),
        storage=storage,
        # An INTEGER PRIMARY KEY is an alias of rowid and is never really null.
        nullable=not (notnull or pk),
        primary_key=bool(pk),
        max_length=length if storage == "text" else None,
        minimum=minimum,
        maximum=maximum,
    )


def _column_checks(create_sql: str, column: str) -> str:
    """Return the part of ``create_sql`` that mentions ``column`` in a CHECK clause."""
    fragments = []
    for match in re.finditer(r"check\s*\(", create_sql, flags=re.IGNORECASE):
        start = match.end()
        depth = 1
        index = start
        while index < len(create_sql) and depth:
            if create_sql[index] == "(":
                depth += 1
            elif create_sql[index] == ")":
                depth -= 1
            index += 1
        body = create_sql[start : index - 1]
        if re.search(rf"[\[\"`]?{re.escape(column)}[\]\"`]?", body):
            fragments.append(body)
    return " and ".join(fragments)


def _refine_with_checks(spec: FieldSpec, create_sql: str) -> FieldSpec:
    """Pick up range and length limits from the table's CHECK constraints."""
    if not create_sql or spec.storage in ("geometry", "blob"):
        return spec
    body = _column_checks(create_sql, spec.name)
    if not body:
        return spec
    col = rf"[\[\"`]?{re.escape(spec.name)}[\]\"`]?"
    updates: dict[str, Any] = {}

    lower = re.search(rf"{col}\s*>=\s*(-?[\d.eE+]+)", body)
    upper = re.search(rf"{col}\s*<=\s*(-?[\d.eE+]+)", body)
    if spec.storage in ("integer", "real", "boolean"):
        if lower:
            value = float(lower.group(1))
            if spec.minimum is None or value > spec.minimum:
                updates["minimum"] = int(value) if spec.storage != "real" else value
        if upper:
            value = float(upper.group(1))
            if spec.maximum is None or value < spec.maximum:
                updates["maximum"] = int(value) if spec.storage != "real" else value

    length = re.search(rf"length\s*\(\s*{col}\s*\)\s*>\s*(\d+)", body)
    if length and spec.storage == "text":
        limit = int(length.group(1))
        if spec.max_length is None or limit < spec.max_length:
            updates["max_length"] = limit

    # ``typeof(col) = 'text'`` without an ``or ... = 'null'`` branch means NOT NULL.
    typeof = re.search(
        rf"typeof\s*\(\s*{col}\s*\)\s*=\s*'(\w+)'", body, flags=re.IGNORECASE
    )
    if typeof and "'null'" not in body.lower():
        updates["nullable"] = False

    return replace(spec, **updates) if updates else spec


def _domains(conn: sqlite3.Connection, table: str) -> dict[str, dict[str, Any]]:
    """Read the optional GeoPackage Schema extension for one table."""
    if not _table_exists(conn, "gpkg_data_columns"):
        return {}
    # A private cursor keeps name-based row access working on plain connections.
    cursor = conn.cursor()
    cursor.row_factory = sqlite3.Row
    rows = cursor.execute(
        "SELECT column_name, name, title, description, constraint_name "
        "FROM gpkg_data_columns WHERE table_name = ?",
        (table,),
    ).fetchall()
    if not rows:
        return {}

    constraints: dict[str, list[sqlite3.Row]] = {}
    if _table_exists(conn, "gpkg_data_column_constraints"):
        for row in cursor.execute(
            "SELECT * FROM gpkg_data_column_constraints"
        ).fetchall():
            constraints.setdefault(row["constraint_name"], []).append(row)

    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        column = row["column_name"]
        info: dict[str, Any] = {}
        if row["title"]:
            info["title"] = row["title"]
        if row["description"]:
            info["description"] = row["description"]
        name = row["constraint_name"] or row["name"]
        enum: list[tuple[Any, str]] = []
        for constraint in constraints.get(name, []):
            kind = (constraint["constraint_type"] or "").lower()
            if kind == "enum":
                enum.append(
                    (
                        constraint["value"],
                        constraint["description"] or str(constraint["value"]),
                    )
                )
            elif kind == "range":
                if constraint["min"] is not None:
                    info["minimum"] = constraint["min"]
                if constraint["max"] is not None:
                    info["maximum"] = constraint["max"]
            elif kind == "glob" and constraint["value"]:
                info["glob"] = constraint["value"]
        if enum:
            info["enum"] = tuple(enum)
        if info:
            out[column] = info
    return out


def _normalise_enum(spec: FieldSpec) -> FieldSpec:
    """Cast domain values to the column's own type.

    ``gpkg_data_column_constraints.value`` is a TEXT column, so the enum of an
    integer field arrives as ``"2"``. Left as text it would never match the
    ``2`` in the table, and the value would look like "not in the domain".
    """
    if not spec.enum:
        return spec
    cast = (
        int
        if spec.storage in ("integer", "boolean")
        else float
        if spec.storage == "real"
        else None
    )
    if cast is None:
        return spec
    values: list[tuple[Any, str]] = []
    for value, label in spec.enum:
        try:
            values.append((cast(value), label))
        except (TypeError, ValueError):
            values.append((value, label))
    return replace(spec, enum=tuple(values))


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name = ?",
        (name,),
    ).fetchone()
    return row is not None


def list_feature_tables(conn: sqlite3.Connection) -> list[str]:
    """Feature tables registered in ``gpkg_contents``, falling back to any table."""
    if _table_exists(conn, "gpkg_contents"):
        rows = conn.execute(
            "SELECT table_name FROM gpkg_contents WHERE data_type = 'features' ORDER BY table_name"
        ).fetchall()
        if rows:
            return [row[0] for row in rows]
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' "
        "AND name NOT LIKE 'gpkg_%' AND name NOT LIKE 'rtree_%' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return [row[0] for row in rows]


def read_table_schema(conn: sqlite3.Connection, table: str) -> TableSchema:
    """Build a :class:`TableSchema` for ``table``."""
    columns = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
    if not columns:
        raise ValueError(f"table {table!r} does not exist in this GeoPackage")

    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone()
    create_sql = (row[0] if row else "") or ""

    geometry_column = None
    srs_id = None
    if _table_exists(conn, "gpkg_geometry_columns"):
        geom = conn.execute(
            "SELECT column_name, srs_id FROM gpkg_geometry_columns WHERE table_name = ?",
            (table,),
        ).fetchone()
        if geom:
            geometry_column, srs_id = geom[0], geom[1]

    domains = _domains(conn, table)
    specs: list[FieldSpec] = []
    pk_column = None
    for column in columns:
        name = column[1]
        spec = _spec_from_column(name, column[2], column[3], column[5])
        if name == geometry_column:
            spec = replace(spec, storage="geometry")
        spec = _refine_with_checks(spec, create_sql)
        if name in domains:
            spec = replace(spec, **domains[name])
        spec = _normalise_enum(spec)
        if spec.primary_key and pk_column is None:
            pk_column = name
        specs.append(spec)

    if pk_column is None:
        pk_column = "rowid"
    return TableSchema(
        name=table,
        fields=tuple(specs),
        pk_column=pk_column,
        geometry_column=geometry_column,
        srs_id=srs_id,
    )
