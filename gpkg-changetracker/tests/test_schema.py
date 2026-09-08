from __future__ import annotations

from gpkg_changetracker.gpkg_io import open_gpkg
from gpkg_changetracker.schema import list_feature_tables, read_table_schema


def test_lists_the_feature_table(sample_gpkg, table):
    conn = open_gpkg(sample_gpkg, read_only=True)
    try:
        assert list_feature_tables(conn) == [table]
    finally:
        conn.close()


def test_reads_types_lengths_and_ranges(sample_gpkg, table):
    conn = open_gpkg(sample_gpkg, read_only=True)
    try:
        schema = read_table_schema(conn, table)
    finally:
        conn.close()

    assert schema.pk_column == "fid"
    assert schema.geometry_column == "geom"
    assert schema.srs_id == 28992

    code = schema.by_name("code")
    assert (code.storage, code.max_length, code.nullable) == ("text", 8, False)

    depth = schema.by_name("depth")
    assert depth.storage == "real"
    assert (depth.minimum, depth.maximum) == (-100.0, 100.0)

    category = schema.by_name("category")
    assert category.storage == "integer"
    assert category.title == "Watergang categorie"
    # Domain values arrive as text in the file and are cast to the column's own type.
    assert category.enum == ((1, "hoofdwatergang"), (2, "schouwsloot"), (3, "kanaal"))

    assert schema.by_name("accessible").storage == "boolean"
    assert schema.by_name("inspected").storage == "datetime"
    assert schema.by_name("width").storage == "real"
    assert schema.by_name("notes").max_length == 500


def test_geometry_and_primary_key_are_not_editable(sample_gpkg, table):
    conn = open_gpkg(sample_gpkg, read_only=True)
    try:
        schema = read_table_schema(conn, table)
    finally:
        conn.close()

    assert schema.by_name("geom").editable is False
    assert schema.by_name("fid").editable is False
    assert {spec.name for spec in schema.editable_fields} == {
        "code",
        "name",
        "depth",
        "category",
        "width",
        "inspected",
        "accessible",
        "notes",
    }


def test_describe_mentions_the_constraints(sample_gpkg, table):
    conn = open_gpkg(sample_gpkg, read_only=True)
    try:
        schema = read_table_schema(conn, table)
    finally:
        conn.close()

    assert "max 8 characters" in schema.by_name("code").describe()
    assert "required" in schema.by_name("code").describe()
    assert "-100 .. 100" in schema.by_name("depth").describe()
    assert "one of: 1, 2, 3" in schema.by_name("category").describe()
