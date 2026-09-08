from __future__ import annotations

import pytest

from gpkg_changetracker.schema import FieldSpec
from gpkg_changetracker.validation import ValidationError, coerce_value, utc_now_iso

TEXT = FieldSpec(name="name", declared_type="TEXT(6)", storage="text", max_length=6)
REQUIRED_TEXT = FieldSpec(
    name="code", declared_type="TEXT(8)", storage="text", max_length=8, nullable=False
)
INTEGER = FieldSpec(
    name="category",
    declared_type="MEDIUMINT",
    storage="integer",
    minimum=-10,
    maximum=10,
)
REAL = FieldSpec(
    name="depth", declared_type="FLOAT", storage="real", minimum=-100.0, maximum=100.0
)
BOOLEAN = FieldSpec(name="accessible", declared_type="BOOLEAN", storage="boolean")
DATE = FieldSpec(name="inspected", declared_type="DATE", storage="datetime")
STAMP = FieldSpec(name="changedate", declared_type="DATETIME", storage="datetime")
ENUM = FieldSpec(
    name="category",
    declared_type="MEDIUMINT",
    storage="integer",
    enum=(("1", "one"), ("2", "two")),
)
GEOMETRY = FieldSpec(name="geom", declared_type="MULTILINESTRING", storage="geometry")


def test_empty_input_means_null():
    assert coerce_value(TEXT, "") is None
    assert coerce_value(TEXT, "   ") is None
    assert coerce_value(TEXT, None) is None


def test_required_field_rejects_empty():
    with pytest.raises(ValidationError, match="required"):
        coerce_value(REQUIRED_TEXT, "")


def test_text_length_is_enforced():
    assert coerce_value(TEXT, "abcdef") == "abcdef"
    with pytest.raises(ValidationError, match="at most 6 characters"):
        coerce_value(TEXT, "abcdefg")


def test_integers_keep_integer_storage():
    assert coerce_value(INTEGER, "4") == 4
    assert isinstance(coerce_value(INTEGER, 4.0), int)
    with pytest.raises(ValidationError, match="whole number"):
        coerce_value(INTEGER, 4.5)
    with pytest.raises(ValidationError, match="whole number"):
        coerce_value(INTEGER, "twelve")


def test_numeric_ranges_are_enforced():
    with pytest.raises(ValidationError, match="10 or less"):
        coerce_value(INTEGER, 11)
    with pytest.raises(ValidationError, match="-100 or more"):
        coerce_value(REAL, -101)


def test_reals_keep_real_storage_and_accept_a_decimal_comma():
    value = coerce_value(REAL, "1,25")
    assert isinstance(value, float) and value == 1.25
    assert isinstance(coerce_value(REAL, 2), float)
    with pytest.raises(ValidationError, match="finite"):
        coerce_value(REAL, float("nan"))


def test_booleans_become_zero_or_one():
    assert coerce_value(BOOLEAN, "ja") == 1
    assert coerce_value(BOOLEAN, "false") == 0
    assert coerce_value(BOOLEAN, True) == 1
    with pytest.raises(ValidationError, match="true or false"):
        coerce_value(BOOLEAN, "maybe")


def test_dates_and_timestamps_are_normalised():
    assert coerce_value(DATE, "18-04-2025") == "2025-04-18"
    assert coerce_value(DATE, "2025-04-18") == "2025-04-18"
    assert coerce_value(STAMP, "2025-04-18 09:30") == "2025-04-18T09:30:00.000Z"
    with pytest.raises(ValidationError, match="date"):
        coerce_value(DATE, "not a date")


def test_enum_values_are_checked():
    assert coerce_value(ENUM, 2) == 2
    with pytest.raises(ValidationError, match="one of"):
        coerce_value(ENUM, 9)


def test_geometry_cannot_be_edited():
    with pytest.raises(ValidationError, match="cannot be edited"):
        coerce_value(GEOMETRY, "LINESTRING(0 0, 1 1)")


def test_utc_now_is_a_geopackage_datetime():
    stamp = utc_now_iso()
    assert stamp.endswith("Z") and stamp[10] == "T" and len(stamp) == 24
