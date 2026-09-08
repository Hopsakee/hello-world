"""Edit GeoPackage attributes with automatic change-date tracking."""

from gpkg_changetracker.schema import FieldSpec, TableSchema, read_table_schema
from gpkg_changetracker.session import ChangeTrackingSession, EditResult, ExportResult
from gpkg_changetracker.validation import ValidationError, coerce_value

__all__ = [
    "ChangeTrackingSession",
    "EditResult",
    "ExportResult",
    "FieldSpec",
    "TableSchema",
    "ValidationError",
    "coerce_value",
    "read_table_schema",
]
