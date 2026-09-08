from __future__ import annotations

import hashlib
import re
import sqlite3
import time

import pytest

from gpkg_changetracker.gpkg_io import (
    CHANGEDATE_COLUMN,
    LOG_TABLE,
    column_names,
    open_gpkg,
)
from gpkg_changetracker.session import ChangeTrackingSession

STAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")


def test_open_adds_the_changedate_column_to_the_working_copy(
    session, sample_gpkg, table
):
    assert session.working_path != sample_gpkg
    assert session.working_path.exists()

    working = open_gpkg(session.working_path, read_only=True)
    source = open_gpkg(sample_gpkg, read_only=True)
    try:
        assert CHANGEDATE_COLUMN in column_names(working, table)
        assert CHANGEDATE_COLUMN not in column_names(source, table)
    finally:
        working.close()
        source.close()


def test_the_source_file_is_never_written_to(session, sample_gpkg):
    before = hashlib.sha256(sample_gpkg.read_bytes()).hexdigest()
    session.apply_edits(1, {"name": "Nieuwe naam", "depth": 1.9})
    session.export_changed(sample_gpkg.parent / "changed.gpkg")
    assert hashlib.sha256(sample_gpkg.read_bytes()).hexdigest() == before


def test_an_edit_stamps_changedate(session):
    assert session.row(1)[CHANGEDATE_COLUMN] is None

    result = session.apply_edits(1, {"name": "Grote Wetering (gecontroleerd)"})

    assert result.ok
    assert result.applied == {"name": "Grote Wetering (gecontroleerd)"}
    assert STAMP.match(result.changedate)
    assert session.row(1)[CHANGEDATE_COLUMN] == result.changedate
    assert session.changed_fids() == (1,)


def test_editing_another_field_moves_the_stamp_forward(session):
    first = session.apply_edits(1, {"name": "Eerste wijziging"}).changedate
    time.sleep(0.01)
    second = session.apply_edits(1, {"depth": 1.45}).changedate

    assert second > first
    assert session.row(1)[CHANGEDATE_COLUMN] == second
    assert set(session.changed_columns(1)) == {"name", "depth"}
    assert session.changed_count() == 1  # still one changed row


def test_rewriting_the_same_value_leaves_the_stamp_alone(session):
    stamp = session.apply_edits(1, {"depth": 1.45}).changedate
    time.sleep(0.01)
    again = session.apply_edits(1, {"depth": 1.45})

    assert again.applied == {}
    assert again.unchanged == ("depth",)
    assert session.row(1)[CHANGEDATE_COLUMN] == stamp


def test_invalid_values_are_rejected_per_field(session):
    result = session.apply_edits(1, {"code": "WAY-TOO-LONG", "depth": 2.0})

    assert not result.ok
    assert "code" in result.errors and "at most 8 characters" in result.errors["code"]
    assert result.applied == {"depth": 2.0}
    assert session.row(1)["code"] == "WG-0001"
    assert session.row(1)["depth"] == 2.0


def test_values_keep_the_storage_class_the_column_demands(session):
    session.apply_edits(2, {"depth": "3", "category": "2", "accessible": "nee"})

    conn = open_gpkg(session.working_path, read_only=True)
    try:
        row = conn.execute(
            "SELECT typeof(depth) AS depth, typeof(category) AS category, "
            "typeof(accessible) AS accessible FROM waterways WHERE fid = 2"
        ).fetchone()
    finally:
        conn.close()
    assert (row["depth"], row["category"], row["accessible"]) == (
        "real",
        "integer",
        "integer",
    )


def test_changedate_cannot_be_edited_by_hand(session):
    stamp = session.apply_edits(1, {"name": "Wijziging"}).changedate
    result = session.apply_edits(1, {CHANGEDATE_COLUMN: "1999-01-01T00:00:00.000Z"})

    assert result.applied == {}
    assert session.row(1)[CHANGEDATE_COLUMN] == stamp


def test_reverting_every_field_clears_the_stamp(session):
    session.apply_edits(3, {"name": "Tijdelijk", "width": 5.0})
    assert session.changed_count() == 1

    session.revert(3)

    assert session.row(3)[CHANGEDATE_COLUMN] is None
    assert session.changed_fids() == ()
    assert session.row(3)["name"] == "Molensloot"
    # The history is kept even though the row is no longer counted as changed.
    assert len(session.history(3)) == 4


def test_reverting_one_field_keeps_the_row_changed(session):
    session.apply_edits(3, {"name": "Tijdelijk", "width": 5.0})
    session.revert(3, ["name"])

    assert session.changed_fids() == (3,)
    assert session.changed_columns(3) == ("width",)


def test_history_records_original_old_and_new(session):
    session.apply_edits(1, {"depth": 2.0})
    session.apply_edits(1, {"depth": 2.5})

    newest, older = session.history(1)
    assert (older.old_value, older.new_value) == (pytest.approx(1.2, abs=1e-6), 2.0)
    assert (newest.old_value, newest.new_value) == (2.0, 2.5)
    assert newest.original_value == pytest.approx(1.2, abs=1e-6)


def test_rows_can_be_filtered_and_searched(session):
    session.apply_edits(4, {"notes": "Gecontroleerd"})

    assert [row["fid"] for row in session.rows(changed_only=True)] == [4]
    assert [row["code"] for row in session.rows(search="WG-0002")] == ["WG-0002"]
    assert len(session.rows(limit=2)) == 2
    assert [row["fid"] for row in session.rows(fids=[2, 5])] == [2, 5]


def test_export_writes_only_the_changed_rows(session, table, tmp_path):
    session.apply_edits(2, {"depth": 1.1})
    session.apply_edits(5, {"name": "Zandwetering noord", "accessible": 1})
    out = tmp_path / "only_changed.gpkg"

    result = session.export_changed(out)

    assert result.row_count == 2
    assert set(result.fids) == {2, 5}
    conn = open_gpkg(out, read_only=True)
    try:
        assert [
            row["fid"]
            for row in conn.execute(f'SELECT fid FROM "{table}" ORDER BY fid')
        ] == [
            2,
            5,
        ]
        stamps = [
            row[0]
            for row in conn.execute(f'SELECT "{CHANGEDATE_COLUMN}" FROM "{table}"')
        ]
        assert all(STAMP.match(stamp) for stamp in stamps)
        # A valid GeoPackage: application id, registered contents, pruned index.
        assert conn.execute("PRAGMA application_id").fetchone()[0] == 0x47504B47
        contents = conn.execute(
            "SELECT * FROM gpkg_contents WHERE table_name = ?", (table,)
        ).fetchone()
        assert contents["data_type"] == "features" and contents["srs_id"] == 28992
        assert (
            conn.execute("SELECT COUNT(*) FROM rtree_waterways_geom").fetchone()[0] == 2
        )
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        # The working copy's bookkeeping does not travel with the deliverable.
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE name = ?", (LOG_TABLE,)
            ).fetchone()[0]
            == 0
        )
    finally:
        conn.close()


def test_export_updates_the_bounding_box(session, table, tmp_path):
    session.apply_edits(1, {"depth": 1.3})
    out = session.export_changed(tmp_path / "bbox.gpkg").path

    conn = open_gpkg(out, read_only=True)
    try:
        contents = conn.execute(
            "SELECT min_x, min_y, max_x, max_y FROM gpkg_contents WHERE table_name = ?",
            (table,),
        ).fetchone()
    finally:
        conn.close()
    assert (contents["min_x"], contents["min_y"]) == (202000.0, 502000.0)
    assert (contents["max_x"], contents["max_y"]) == (202320.0, 502090.0)


def test_export_needs_at_least_one_change(session, tmp_path):
    with pytest.raises(ValueError, match="nothing to export"):
        session.export_changed(tmp_path / "empty.gpkg")


def test_export_refuses_to_overwrite_the_source(session, sample_gpkg):
    session.apply_edits(1, {"depth": 1.3})
    with pytest.raises(ValueError, match="source"):
        session.export_changed(sample_gpkg)


def test_export_adds_the_gpkg_suffix(session, tmp_path):
    session.apply_edits(1, {"depth": 1.3})
    result = session.export_changed(tmp_path / "no_suffix")
    assert result.path.name == "no_suffix.gpkg"


def test_edits_survive_reopening_and_can_be_reset(sample_gpkg, tmp_path):
    workspace = tmp_path / "workspace"
    with ChangeTrackingSession.open(sample_gpkg, workspace=workspace) as first:
        stamp = first.apply_edits(1, {"name": "Blijft staan"}).changedate

    with ChangeTrackingSession.open(sample_gpkg, workspace=workspace) as resumed:
        assert resumed.row(1)["name"] == "Blijft staan"
        assert resumed.row(1)[CHANGEDATE_COLUMN] == stamp

    with ChangeTrackingSession.open(
        sample_gpkg, workspace=workspace, resume=False
    ) as restarted:
        assert restarted.row(1)["name"] == "Grote Wetering"
        assert restarted.changed_count() == 0


def test_open_rejects_files_that_are_not_geopackages(tmp_path):
    plain = tmp_path / "plain.db"
    sqlite3.connect(plain).close()
    with pytest.raises(ValueError, match="not a GeoPackage"):
        ChangeTrackingSession.open(plain, workspace=tmp_path / "workspace")

    with pytest.raises(FileNotFoundError):
        ChangeTrackingSession.open(
            tmp_path / "missing.gpkg", workspace=tmp_path / "workspace"
        )


def test_open_rejects_an_unknown_table(sample_gpkg, tmp_path):
    with pytest.raises(ValueError, match="no feature table"):
        ChangeTrackingSession.open(
            sample_gpkg, table="does_not_exist", workspace=tmp_path / "workspace"
        )


def test_editing_a_missing_row_raises(session):
    with pytest.raises(KeyError):
        session.apply_edits(9999, {"name": "nope"})


def test_a_coded_value_domain_is_enforced(session):
    result = session.apply_edits(1, {"category": 9})
    assert "category" in result.errors and "one of" in result.errors["category"]

    assert session.apply_edits(1, {"category": 3}).applied == {"category": 3}


def test_rows_handles_more_ids_than_sqlite_takes_at_once(session, monkeypatch):
    import gpkg_changetracker.session as session_module

    monkeypatch.setattr(session_module, "_PARAMETER_CHUNK", 2)
    fids = [1, 2, 3, 4, 5]

    rows = session.rows(fids=fids, columns=["code"], limit=None)

    assert [row["fid"] for row in rows] == fids
    assert session.rows(fids=fids, limit=2)[0]["fid"] == 1
    assert len(session.rows(fids=fids, limit=2)) == 2
    assert len(session.geometries(fids)) == 5
