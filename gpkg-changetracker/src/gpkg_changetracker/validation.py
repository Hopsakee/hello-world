"""Turn user input into a value the GeoPackage column actually accepts.

SQLite stores whatever it is given, but GDAL writes ``CHECK`` constraints that
test ``typeof(col)``, so an integer written into a ``FLOAT`` column makes the
whole file invalid. Every value therefore goes through :func:`coerce_value`,
which returns a value of the exact storage class the column wants, or raises
:class:`ValidationError` with a message meant for the user.
"""

from __future__ import annotations

import fnmatch
import math
from datetime import date, datetime, timezone
from typing import Any

from gpkg_changetracker.schema import FieldSpec

_TRUE = {"1", "true", "t", "yes", "y", "ja", "waar"}
_FALSE = {"0", "false", "f", "no", "n", "nee", "onwaar"}

#: Formats accepted for DATE / DATETIME columns, on top of ISO 8601.
_DATE_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
    "%d-%m-%Y %H:%M:%S",
    "%d-%m-%Y %H:%M",
    "%d-%m-%Y",
)


class ValidationError(ValueError):
    """Raised when a value cannot be stored in a field."""


def utc_now_iso() -> str:
    """Current time as a GeoPackage DATETIME: ISO 8601 in UTC, milliseconds, ``Z``."""
    moment = datetime.now(timezone.utc)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"


def _is_empty(raw: Any) -> bool:
    """An empty input: ``None``, blank text, or a null sentinel such as ``NaN``.

    Table widgets report a cleared cell as ``NaN`` or ``NaT`` rather than
    ``None``, and neither can be stored in a GeoPackage, so both mean "no
    value" here.
    """
    if raw is None:
        return True
    if isinstance(raw, str):
        return not raw.strip()
    if isinstance(raw, float) and math.isnan(raw):
        return True
    return raw != raw  # NaT and friends are not equal to themselves


def coerce_value(spec: FieldSpec, raw: Any) -> Any:
    """Validate ``raw`` against ``spec`` and return the value to store.

    Empty strings are read as "no value" so clearing an input means NULL.
    """
    if not spec.editable:
        raise ValidationError(f"{spec.label} cannot be edited")

    if _is_empty(raw):
        if not spec.nullable:
            raise ValidationError(f"{spec.label} is required and cannot be empty")
        return None

    if spec.storage == "integer":
        value: Any = _to_int(spec, raw)
    elif spec.storage == "real":
        value = _to_float(spec, raw)
    elif spec.storage == "boolean":
        value = _to_bool(spec, raw)
    elif spec.storage == "datetime":
        value = _to_datetime(spec, raw)
    else:
        value = _to_text(spec, raw)

    _check_domain(spec, value)
    return value


def coerce_row(
    specs: dict[str, FieldSpec], values: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, str]]:
    """Coerce a whole row. Returns ``(clean_values, errors_by_column)``."""
    clean: dict[str, Any] = {}
    errors: dict[str, str] = {}
    for column, raw in values.items():
        spec = specs.get(column)
        if spec is None:
            errors[column] = f"unknown column {column!r}"
            continue
        try:
            clean[column] = coerce_value(spec, raw)
        except ValidationError as exc:
            errors[column] = str(exc)
    return clean, errors


def _to_int(spec: FieldSpec, raw: Any) -> int:
    if isinstance(raw, bool):
        value = int(raw)
    elif isinstance(raw, int):
        value = raw
    elif isinstance(raw, float):
        if not math.isfinite(raw) or not float(raw).is_integer():
            raise ValidationError(f"{spec.label} must be a whole number, got {raw!r}")
        value = int(raw)
    else:
        text = str(raw).strip().replace(" ", "")
        try:
            value = int(text, 10)
        except ValueError:
            try:
                as_float = float(text.replace(",", "."))
            except ValueError:
                raise ValidationError(
                    f"{spec.label} must be a whole number, got {raw!r}"
                ) from None
            if not as_float.is_integer():
                raise ValidationError(
                    f"{spec.label} must be a whole number, got {raw!r}"
                )
            value = int(as_float)
    _check_range(spec, value)
    return value


def _to_float(spec: FieldSpec, raw: Any) -> float:
    if isinstance(raw, bool):
        value = float(raw)
    elif isinstance(raw, (int, float)):
        value = float(raw)
    else:
        text = str(raw).strip().replace(" ", "")
        # Accept the Dutch decimal comma when there is no thousands ambiguity.
        if "," in text and "." not in text:
            text = text.replace(",", ".")
        try:
            value = float(text)
        except ValueError:
            raise ValidationError(
                f"{spec.label} must be a number, got {raw!r}"
            ) from None
    if not math.isfinite(value):
        raise ValidationError(f"{spec.label} must be a finite number, got {raw!r}")
    _check_range(spec, value)
    return value


def _to_bool(spec: FieldSpec, raw: Any) -> int:
    if isinstance(raw, bool):
        return int(raw)
    if isinstance(raw, (int, float)) and float(raw) in (0.0, 1.0):
        return int(raw)
    text = str(raw).strip().lower()
    if text in _TRUE:
        return 1
    if text in _FALSE:
        return 0
    raise ValidationError(f"{spec.label} must be true or false, got {raw!r}")


def _to_datetime(spec: FieldSpec, raw: Any) -> str:
    if isinstance(raw, datetime):
        moment = raw
    elif isinstance(raw, date):
        moment = datetime(raw.year, raw.month, raw.day)
    else:
        text = str(raw).strip()
        moment = None
        try:
            moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            for fmt in _DATE_FORMATS:
                try:
                    moment = datetime.strptime(text, fmt)
                    break
                except ValueError:
                    continue
        if moment is None:
            raise ValidationError(
                f"{spec.label} must be a date or date-time, got {raw!r}"
            )

    if (
        spec.declared_type.upper().startswith("DATE")
        and "TIME" not in spec.declared_type.upper()
    ):
        return moment.date().isoformat()
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    moment = moment.astimezone(timezone.utc)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"


def _to_text(spec: FieldSpec, raw: Any) -> str:
    value = raw if isinstance(raw, str) else str(raw)
    if spec.max_length is not None and len(value) > spec.max_length:
        raise ValidationError(
            f"{spec.label} accepts at most {spec.max_length} characters, got {len(value)}"
        )
    if spec.glob and not fnmatch.fnmatchcase(value, spec.glob):
        raise ValidationError(f"{spec.label} must match the pattern {spec.glob}")
    return value


def _check_range(spec: FieldSpec, value: float) -> None:
    if spec.minimum is not None and value < spec.minimum:
        raise ValidationError(
            f"{spec.label} must be {spec.minimum:g} or more, got {value:g}"
        )
    if spec.maximum is not None and value > spec.maximum:
        raise ValidationError(
            f"{spec.label} must be {spec.maximum:g} or less, got {value:g}"
        )


def _check_domain(spec: FieldSpec, value: Any) -> None:
    if not spec.enum:
        return
    allowed = [item[0] for item in spec.enum]
    if value in allowed:
        return
    # Enum values from gpkg_data_column_constraints are stored as text.
    if any(str(value) == str(option) for option in allowed):
        return
    shown = ", ".join(str(option) for option in allowed[:12])
    raise ValidationError(f"{spec.label} must be one of: {shown}")
