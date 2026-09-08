from __future__ import annotations

import math

from gpkg_changetracker import grid


def test_normalise_cell_reads_every_kind_of_empty():
    assert grid.normalise_cell(None) is None
    assert grid.normalise_cell("") is None
    assert grid.normalise_cell("   ") is None
    assert grid.normalise_cell(float("nan")) is None
    assert grid.normalise_cell("text") == "text"
    assert grid.normalise_cell(0) == 0
    assert grid.normalise_cell(False) is False


def test_cells_differ_ignores_representation():
    assert not grid.cells_differ(1.5, "1.5")
    assert not grid.cells_differ(2, 2.0)
    assert not grid.cells_differ(None, float("nan"))
    assert not grid.cells_differ(None, "")
    assert not grid.cells_differ("same", "same")
    assert grid.cells_differ(1.5, 1.6)
    assert grid.cells_differ(None, 0)
    assert grid.cells_differ("a", "b")
    assert grid.cells_differ("a", None)


def test_diff_rows_reports_only_edited_cells():
    before = [
        {"fid": 1, "code": "A", "depth": 1.0},
        {"fid": 2, "code": "B", "depth": 2.0},
    ]
    after = [
        {"fid": 1, "code": "A", "depth": "1.0"},
        {"fid": 2, "code": "B2", "depth": ""},
    ]

    edits = grid.diff_rows(before, after, key="fid", columns=["code", "depth"])

    assert edits == {2: {"code": "B2", "depth": None}}


def test_diff_rows_matches_on_the_key_not_the_position():
    before = [{"fid": 1, "code": "A"}, {"fid": 2, "code": "B"}]
    after = [{"fid": 2, "code": "B!"}, {"fid": 1, "code": "A"}]

    assert grid.diff_rows(before, after, key="fid", columns=["code"]) == {
        2: {"code": "B!"}
    }


def test_diff_rows_ignores_rows_the_grid_invented_or_dropped():
    before = [{"fid": 1, "code": "A"}, {"fid": 2, "code": "B"}]
    after = [
        {"fid": 1, "code": "A"},
        {"fid": None, "code": "new row"},
        {"fid": 99, "code": "unknown"},
    ]

    assert grid.diff_rows(before, after, key="fid", columns=["code"]) == {}
    assert grid.added_or_removed(before, after, key="fid") == (2, 1)


def test_diff_rows_skips_columns_that_are_not_editable():
    before = [{"fid": 1, "code": "A", "changedate": None}]
    after = [{"fid": 1, "code": "A", "changedate": "2026-01-01T00:00:00.000Z"}]

    assert grid.diff_rows(before, after, key="fid", columns=["code"]) == {}


def test_nan_never_reaches_the_geopackage():
    from gpkg_changetracker.schema import FieldSpec
    from gpkg_changetracker.validation import coerce_value

    spec = FieldSpec(name="depth", declared_type="FLOAT", storage="real")
    assert coerce_value(spec, math.nan) is None


def test_as_cell_text_round_trips_through_the_validator():
    from gpkg_changetracker.schema import FieldSpec
    from gpkg_changetracker.validation import coerce_value

    assert grid.as_cell_text(None) == ""
    assert grid.as_cell_text(float("nan")) == ""
    assert grid.as_cell_text(1.0) == "1"
    assert grid.as_cell_text(0.699999988079071) == "0.699999988079071"
    assert grid.as_cell_text(True) == "true"

    # What the grid shows must come back as the same stored value.
    spec = FieldSpec(name="depth", declared_type="FLOAT", storage="real")
    assert coerce_value(spec, grid.as_cell_text(0.699999988079071)) == 0.699999988079071
    assert not grid.cells_differ(
        0.699999988079071, grid.as_cell_text(0.699999988079071)
    )
    assert not grid.cells_differ(3, grid.as_cell_text(3))


def test_text_cells_keep_a_typo_out_of_the_geopackage():
    from gpkg_changetracker.schema import FieldSpec
    from gpkg_changetracker.validation import ValidationError, coerce_value

    spec = FieldSpec(name="depth", declared_type="FLOAT", storage="real")
    # A typed numeric grid cell would hand over 0.0 here; text hands over "diep".
    assert grid.cells_differ(1.2, "diep")
    try:
        coerce_value(spec, "diep")
    except ValidationError as exc:
        assert "must be a number" in str(exc)
    else:  # pragma: no cover - the point of the test
        raise AssertionError("a typo must not be storable")


def test_rows_match_even_when_the_grid_returns_the_key_as_text():
    # A grid that hands every cell back as text must not look like 2 added rows
    # and 2 removed ones.
    before = [{"fid": 1, "code": "A"}, {"fid": 2, "code": "B"}]
    after = [{"fid": "1", "code": "A"}, {"fid": "2", "code": "B!"}]

    assert grid.diff_rows(before, after, key="fid", columns=["code"]) == {
        2: {"code": "B!"}
    }
    assert grid.added_or_removed(before, after, key="fid") == (0, 0)


def test_apply_grid_edits_stores_valid_cells_and_stamps_the_row(session):
    before = session.rows(fids=[1], columns=["code", "name", "depth"], limit=None)
    after = [dict(before[0], name="Grote Wetering noord", depth="1.85")]

    outcome = grid.apply_grid_edits(session, before, after, ["code", "name", "depth"])

    assert outcome.applied == {1: ["depth", "name"]}
    assert outcome.value_count == 2
    assert outcome.errors == []
    assert outcome.kind == "success"
    assert outcome.stamp and outcome.stamp.endswith("Z")
    assert outcome.last_fid == 1
    assert "2 value(s) stored in 1 row(s)" in outcome.summary()
    assert session.row(1)["depth"] == 1.85
    assert session.row(1)["changedate"] == outcome.stamp


def test_apply_grid_edits_explains_a_refused_cell_and_keeps_the_value(session):
    before = session.rows(fids=[2], columns=["code", "depth"], limit=None)
    after = [dict(before[0], depth="diep", code="WAY-TOO-LONG-CODE")]

    outcome = grid.apply_grid_edits(session, before, after, ["code", "depth"])

    assert outcome.applied == {}
    assert outcome.value_count == 0
    assert outcome.kind == "danger"
    assert len(outcome.errors) == 2
    assert any(
        "must be a number" in message and "depth" in message
        for message in outcome.errors
    )
    assert any("at most 8 characters" in message for message in outcome.errors)
    assert all("the stored value is kept" in message for message in outcome.errors)
    assert "Nothing stored" in outcome.summary()
    # The refused values did not touch the GeoPackage.
    assert session.row(2)["depth"] == 0.8
    assert session.row(2)["code"] == "WG-0002"
    assert session.row(2)["changedate"] is None


def test_apply_grid_edits_stores_the_good_cells_of_a_partly_wrong_row(session):
    before = session.rows(fids=[3], columns=["code", "name", "depth"], limit=None)
    after = [dict(before[0], name="Molensloot west", depth="ondiep")]

    outcome = grid.apply_grid_edits(session, before, after, ["code", "name", "depth"])

    assert outcome.applied == {3: ["name"]}
    assert len(outcome.errors) == 1
    assert session.row(3)["name"] == "Molensloot west"
    assert session.row(3)["depth"] == 0.55
    assert session.row(3)["changedate"] == outcome.stamp


def test_apply_grid_edits_reports_rows_the_grid_added_or_deleted(session):
    before = session.rows(fids=[4, 5], columns=["code"], limit=None)
    after = [dict(before[0]), {"fid": None, "code": "invented"}]

    outcome = grid.apply_grid_edits(session, before, after, ["code"])

    assert (outcome.added, outcome.removed) == (1, 1)
    assert outcome.applied == {}
    assert "does not\ncreate or delete features" in outcome.summary().replace(
        " \n", "\n"
    ) or ("create or delete features" in outcome.summary())
    assert session.changed_count() == 0


def test_apply_grid_edits_does_nothing_when_the_grid_reports_no_change(session):
    before = session.rows(fids=[1, 2], columns=["code", "depth"], limit=None)
    after = [
        {key: grid.as_cell_text(value) for key, value in row.items()} for row in before
    ]

    outcome = grid.apply_grid_edits(session, before, after, ["code", "depth"])

    assert not outcome.touched
    assert outcome.summary() == "**Nothing stored**"
    assert session.changed_count() == 0
