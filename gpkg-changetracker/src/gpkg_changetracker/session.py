"""The editing session: one working copy, tracked edits, changed-rows-only export.

A session owns a *working copy* of the user's GeoPackage. The original file is
opened read-only and never modified. The working copy gets

* a ``changedate`` column on the edited table, and
* a ``changetracker_log`` table with the per-field history.

``changedate`` is the moment of the row's most recent effective change, in UTC
ISO 8601 as the GeoPackage spec wants. Editing a second field on the same row
overwrites it with the new moment. Putting every field of a row back to its
original value clears it again, so a row that ends up identical to the source
is not exported as a change.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

from gpkg_changetracker import geometry as geom
from gpkg_changetracker import gpkg_io
from gpkg_changetracker.gpkg_io import CHANGEDATE_COLUMN, LOG_TABLE
from gpkg_changetracker.schema import (
    FieldSpec,
    TableSchema,
    list_feature_tables,
    read_table_schema,
)
from gpkg_changetracker.validation import ValidationError, coerce_value, utc_now_iso

DEFAULT_WORKSPACE = Path(".gpkg_changetracker")

#: Feature ids are queried in chunks of this size, well under SQLite's limit.
_PARAMETER_CHUNK = 900


@dataclass
class EditResult:
    """Outcome of one submitted edit for a single row."""

    fid: int
    applied: dict[str, Any] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    unchanged: tuple[str, ...] = ()
    changedate: str | None = None
    row_is_changed: bool = False

    @property
    def ok(self) -> bool:
        return not self.errors

    def summary(self) -> str:
        parts = []
        if self.applied:
            parts.append(
                f"{len(self.applied)} field(s) updated: "
                + ", ".join(sorted(self.applied))
            )
        if self.unchanged:
            parts.append(f"{len(self.unchanged)} field(s) already had that value")
        if self.errors:
            parts.append(f"{len(self.errors)} field(s) rejected")
        if self.changedate:
            parts.append(f"changedate = {self.changedate}")
        elif self.applied:
            parts.append("row matches the original again, changedate cleared")
        return "; ".join(parts) or "nothing to do"


@dataclass
class LogEntry:
    """One recorded field change."""

    fid: int
    column: str
    original_value: Any
    old_value: Any
    new_value: Any
    changed_at: str


@dataclass
class ExportResult:
    """Outcome of writing the changed-rows-only GeoPackage."""

    path: Path
    row_count: int
    fids: tuple[int, ...]
    columns_changed: tuple[str, ...] = ()

    def summary(self) -> str:
        return f"{self.row_count} changed row(s) written to {self.path.name}" + (
            f" - fields touched: {', '.join(self.columns_changed)}"
            if self.columns_changed
            else ""
        )


class ChangeTrackingSession:
    """Open a GeoPackage, edit attributes, export only what changed."""

    def __init__(
        self,
        source_path: Path,
        working_path: Path,
        table: str,
        conn: sqlite3.Connection,
        schema: TableSchema,
    ) -> None:
        self.source_path = source_path
        self.working_path = working_path
        self.table = table
        self._conn = conn
        self.schema = schema

    # ------------------------------------------------------------------ open

    @classmethod
    def open(
        cls,
        source: str | Path,
        *,
        table: str | None = None,
        workspace: str | Path | None = None,
        resume: bool = True,
    ) -> "ChangeTrackingSession":
        """Open ``source`` for editing through a working copy.

        ``resume=True`` reuses an existing working copy, so edits survive an app
        restart. ``resume=False`` starts again from the source file.
        """
        source = Path(source).expanduser().resolve()
        if not source.is_file():
            raise FileNotFoundError(f"no such GeoPackage: {source}")
        if not gpkg_io.is_geopackage(source):
            raise ValueError(f"{source.name} is not a GeoPackage")

        workspace = Path(workspace) if workspace is not None else DEFAULT_WORKSPACE
        working_path = (
            Path(workspace).expanduser().resolve() / f"{source.stem}.working.gpkg"
        )
        if working_path == source:
            raise ValueError("the working copy would overwrite the source file")
        if not working_path.exists() or not resume:
            gpkg_io.create_working_copy(source, working_path)

        conn = gpkg_io.open_gpkg(working_path)
        try:
            tables = list_feature_tables(conn)
            if table is None:
                if not tables:
                    raise ValueError(f"{source.name} contains no feature table")
                table = tables[0]
            elif table not in tables:
                raise ValueError(
                    f"{source.name} has no feature table {table!r}; found {tables}"
                )

            gpkg_io.ensure_changedate_column(conn, table)
            gpkg_io.ensure_log_table(conn)
            schema = read_table_schema(conn, table)
        except Exception:
            conn.close()
            raise
        return cls(source, working_path, table, conn, schema)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "ChangeTrackingSession":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # --------------------------------------------------------------- reading

    @property
    def pk_column(self) -> str:
        return self.schema.pk_column

    @property
    def geometry_column(self) -> str | None:
        return self.schema.geometry_column

    @property
    def editable_fields(self) -> tuple[FieldSpec, ...]:
        """Editable columns, without the app's own ``changedate``."""
        return tuple(
            spec
            for spec in self.schema.editable_fields
            if spec.name != CHANGEDATE_COLUMN
        )

    @property
    def attribute_columns(self) -> tuple[str, ...]:
        """All columns except the geometry, in table order."""
        return tuple(
            spec.name
            for spec in self.schema.fields
            if spec.name != self.geometry_column
        )

    def field_spec(self, column: str) -> FieldSpec:
        return self.schema.by_name(column)

    def row_count(self) -> int:
        return self._conn.execute(f'SELECT COUNT(*) FROM "{self.table}"').fetchone()[0]

    def row(self, fid: int, *, with_geometry: bool = False) -> dict[str, Any] | None:
        columns = self.schema.column_names if with_geometry else self.attribute_columns
        return gpkg_io.read_row(self._conn, self.table, self.pk_column, fid, columns)

    def rows(
        self,
        *,
        limit: int | None = 500,
        offset: int = 0,
        columns: Sequence[str] | None = None,
        changed_only: bool = False,
        search: str | None = None,
        search_columns: Sequence[str] | None = None,
        fids: Iterable[int] | None = None,
        order_by: str | None = None,
    ) -> list[dict[str, Any]]:
        """Read attribute rows with the filters the app exposes."""
        columns = tuple(columns) if columns else self.attribute_columns
        if self.pk_column not in columns:
            columns = (self.pk_column, *columns)
        selected = ", ".join(f'"{name}"' for name in columns)

        if fids is not None:
            ids = [int(fid) for fid in fids]
            if not ids:
                return []
            if len(ids) > _PARAMETER_CHUNK:
                # Stay well under SQLite's bound-parameter limit.
                out: list[dict[str, Any]] = []
                for start in range(0, len(ids), _PARAMETER_CHUNK):
                    out.extend(
                        self.rows(
                            limit=None,
                            columns=columns,
                            changed_only=changed_only,
                            search=search,
                            search_columns=search_columns,
                            fids=ids[start : start + _PARAMETER_CHUNK],
                            order_by=order_by,
                        )
                    )
                if limit is not None:
                    return out[offset : offset + int(limit)]
                return out[offset:] if offset else out
        else:
            ids = None

        clauses: list[str] = []
        params: list[Any] = []
        if changed_only:
            clauses.append(f'"{CHANGEDATE_COLUMN}" IS NOT NULL')
        if ids is not None:
            clauses.append(f'"{self.pk_column}" IN ({", ".join("?" for _ in ids)})')
            params.extend(ids)
        if search:
            targets = tuple(search_columns) if search_columns else self._text_columns()
            if targets:
                clauses.append(
                    "("
                    + " OR ".join(f'CAST("{name}" AS TEXT) LIKE ?' for name in targets)
                    + ")"
                )
                params.extend([f"%{search}%"] * len(targets))

        sql = f'SELECT {selected} FROM "{self.table}"'
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += f' ORDER BY "{order_by or self.pk_column}"'
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            params.extend([int(limit), int(offset)])
        return [dict(row) for row in self._conn.execute(sql, params)]

    def _text_columns(self) -> tuple[str, ...]:
        return tuple(
            spec.name
            for spec in self.schema.fields
            if spec.storage in ("text", "integer") and spec.name != self.geometry_column
        )

    def geometries(self, fids: Iterable[int]) -> dict[int, geom.Geometry]:
        """Decoded geometry per feature id."""
        if not self.geometry_column:
            return {}
        ids = [int(fid) for fid in fids]
        out: dict[int, geom.Geometry] = {}
        for start in range(0, len(ids), _PARAMETER_CHUNK):
            chunk = ids[start : start + _PARAMETER_CHUNK]
            placeholders = ", ".join("?" for _ in chunk)
            sql = (
                f'SELECT "{self.pk_column}" AS fid, "{self.geometry_column}" AS shape '
                f'FROM "{self.table}" WHERE "{self.pk_column}" IN ({placeholders})'
            )
            for row in self._conn.execute(sql, chunk):
                decoded = geom.decode(row["shape"])
                if decoded is not None:
                    out[int(row["fid"])] = decoded
        return out

    # --------------------------------------------------------------- editing

    def apply_edits(self, fid: int, values: dict[str, Any]) -> EditResult:
        """Validate and store ``values`` for row ``fid``, updating ``changedate``.

        Valid fields are written even when other fields in the same submission
        are rejected; the rejected ones are reported back per column.
        """
        fid = int(fid)
        current = self.row(fid)
        if current is None:
            raise KeyError(f"{self.table} has no row with {self.pk_column} = {fid}")

        result = EditResult(fid=fid)
        to_write: dict[str, Any] = {}
        unchanged: list[str] = []
        for column, raw in values.items():
            if column == CHANGEDATE_COLUMN:
                continue  # maintained by the app
            try:
                spec = self.schema.by_name(column)
            except KeyError as exc:
                result.errors[column] = str(exc)
                continue
            try:
                clean = coerce_value(spec, raw)
            except ValidationError as exc:
                result.errors[column] = str(exc)
                continue
            if _same(clean, current.get(column)):
                unchanged.append(column)
                continue
            to_write[column] = clean

        result.unchanged = tuple(unchanged)
        if not to_write:
            result.changedate = current.get(CHANGEDATE_COLUMN)
            result.row_is_changed = result.changedate is not None
            return result

        now = utc_now_iso()
        self._conn.execute("BEGIN")
        try:
            for column, clean in to_write.items():
                original = self._original_value(
                    fid, column, fallback=current.get(column)
                )
                self._conn.execute(
                    f'INSERT INTO "{LOG_TABLE}" '
                    "(table_name, fid, column_name, original_value, old_value, new_value, changed_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        self.table,
                        fid,
                        column,
                        _encode(original),
                        _encode(current.get(column)),
                        _encode(clean),
                        now,
                    ),
                )
            gpkg_io.update_values(self._conn, self.table, self.pk_column, fid, to_write)
            changed = self._recompute_changedate(fid, now)
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise

        result.applied = to_write
        result.row_is_changed = changed is not None
        result.changedate = changed
        return result

    def revert(self, fid: int, columns: Sequence[str] | None = None) -> EditResult:
        """Put fields back to the values the source file had."""
        fid = int(fid)
        originals = self._originals(fid)
        wanted = tuple(columns) if columns else tuple(originals)
        values = {column: originals[column] for column in wanted if column in originals}
        if not values:
            return EditResult(fid=fid)
        result = self.apply_edits(fid, values)
        return result

    def _original_value(self, fid: int, column: str, fallback: Any) -> Any:
        row = self._conn.execute(
            f'SELECT original_value FROM "{LOG_TABLE}" '
            "WHERE table_name = ? AND fid = ? AND column_name = ? ORDER BY id LIMIT 1",
            (self.table, fid, column),
        ).fetchone()
        return _decode(row[0]) if row is not None else fallback

    def _originals(self, fid: int) -> dict[str, Any]:
        """Original value of every field ever edited on this row."""
        rows = self._conn.execute(
            f'SELECT column_name, original_value FROM "{LOG_TABLE}" '
            "WHERE table_name = ? AND fid = ? ORDER BY id",
            (self.table, int(fid)),
        ).fetchall()
        originals: dict[str, Any] = {}
        for row in rows:
            originals.setdefault(row["column_name"], _decode(row["original_value"]))
        return originals

    def _recompute_changedate(self, fid: int, now: str) -> str | None:
        """Set ``changedate`` to ``now``, or clear it when the row matches the source."""
        originals = self._originals(fid)
        current = self.row(fid) or {}
        differs = any(
            not _same(current.get(column), original)
            for column, original in originals.items()
        )
        value = now if differs else None
        self._conn.execute(
            f'UPDATE "{self.table}" SET "{CHANGEDATE_COLUMN}" = ? WHERE "{self.pk_column}" = ?',
            (value, fid),
        )
        return value

    # -------------------------------------------------------------- tracking

    def changed_fids(self) -> tuple[int, ...]:
        rows = self._conn.execute(
            f'SELECT "{self.pk_column}" FROM "{self.table}" '
            f'WHERE "{CHANGEDATE_COLUMN}" IS NOT NULL ORDER BY "{CHANGEDATE_COLUMN}" DESC'
        ).fetchall()
        return tuple(int(row[0]) for row in rows)

    def changed_count(self) -> int:
        return self._conn.execute(
            f'SELECT COUNT(*) FROM "{self.table}" WHERE "{CHANGEDATE_COLUMN}" IS NOT NULL'
        ).fetchone()[0]

    def changed_columns(self, fid: int | None = None) -> tuple[str, ...]:
        """Columns that currently differ from the source, for one row or all rows."""
        if fid is not None:
            current = self.row(fid) or {}
            return tuple(
                column
                for column, original in self._originals(fid).items()
                if not _same(current.get(column), original)
            )
        columns: list[str] = []
        for changed in self.changed_fids():
            for column in self.changed_columns(changed):
                if column not in columns:
                    columns.append(column)
        return tuple(columns)

    def history(self, fid: int | None = None, limit: int = 200) -> list[LogEntry]:
        """The change log, newest first."""
        sql = (
            f"SELECT fid, column_name, original_value, old_value, new_value, changed_at "
            f'FROM "{LOG_TABLE}" WHERE table_name = ?'
        )
        params: list[Any] = [self.table]
        if fid is not None:
            sql += " AND fid = ?"
            params.append(int(fid))
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(int(limit))
        return [
            LogEntry(
                fid=int(row["fid"]),
                column=row["column_name"],
                original_value=_decode(row["original_value"]),
                old_value=_decode(row["old_value"]),
                new_value=_decode(row["new_value"]),
                changed_at=row["changed_at"],
            )
            for row in self._conn.execute(sql, params)
        ]

    # ---------------------------------------------------------------- export

    def export_changed(self, out_path: str | Path) -> ExportResult:
        """Write a new GeoPackage holding only the rows that were changed."""
        fids = self.changed_fids()
        if not fids:
            raise ValueError("nothing to export: no row has been changed yet")
        out_path = Path(out_path).expanduser()
        if out_path.suffix.lower() != ".gpkg":
            out_path = out_path.with_suffix(".gpkg")
        if out_path.resolve() == self.source_path:
            raise ValueError("refusing to overwrite the source GeoPackage")

        count = gpkg_io.export_subset(
            self.working_path,
            out_path,
            self.table,
            self.pk_column,
            fids,
            geometry_column=self.geometry_column,
        )
        return ExportResult(
            path=out_path,
            row_count=count,
            fids=fids,
            columns_changed=self.changed_columns(),
        )


def _same(left: Any, right: Any) -> bool:
    """Compare two stored values, treating None and equal numbers as identical."""
    if left is None or right is None:
        return left is None and right is None
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return float(left) == float(right)
    return left == right


def _encode(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (bytes, bytearray, memoryview)):
        return json.dumps("<binary>")
    if isinstance(value, datetime):
        return json.dumps(value.astimezone(timezone.utc).isoformat())
    return json.dumps(value)


def _decode(payload: str | None) -> Any:
    if payload is None:
        return None
    try:
        return json.loads(payload)
    except (TypeError, ValueError):
        return payload
