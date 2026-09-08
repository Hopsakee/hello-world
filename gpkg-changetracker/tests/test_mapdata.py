from __future__ import annotations

from gpkg_changetracker import mapdata
from gpkg_changetracker.gpkg_io import CHANGEDATE_COLUMN


def test_builds_one_handle_per_feature(session):
    fids = [int(row["fid"]) for row in session.rows(limit=None)]
    vertices, points = mapdata.feature_frames(
        session, fids, label_columns=["code", "name"]
    )

    assert list(points["fid"]) == fids
    assert set(points["status"]) == {"unchanged"}
    assert len(vertices) > len(points)
    assert vertices["lon"].between(5.5, 6.5).all()
    assert vertices["lat"].between(52.0, 53.0).all()
    assert "code: WG-0001" in points.iloc[0]["label"]


def test_marks_changed_features(session):
    fids = [int(row["fid"]) for row in session.rows(limit=None)]
    session.apply_edits(fids[1], {"depth": 2.5})
    _, points = mapdata.feature_frames(session, fids)

    changed = points.loc[points["fid"] == fids[1]].iloc[0]
    assert changed["status"] == "changed"
    assert changed[CHANGEDATE_COLUMN] is not None
    assert set(points.loc[points["fid"] != fids[1], "status"]) == {"unchanged"}


def test_thins_long_geometries(session):
    fids = [int(row["fid"]) for row in session.rows(limit=None)]
    vertices, _ = mapdata.feature_frames(session, fids, max_points=2)
    assert (vertices.groupby("part").size() <= 2).all()


def test_empty_input_gives_empty_frames(session):
    vertices, points = mapdata.feature_frames(session, [])
    assert vertices.empty and points.empty
    assert list(points.columns) == list(mapdata.POINT_COLUMNS)
    assert mapdata.bounds_of(points) is None


def test_points_per_feature_spends_a_fixed_budget():
    # Few features: full detail. Many: less per feature, never below a segment.
    assert mapdata.points_per_feature(10) == mapdata.DEFAULT_MAX_POINTS
    assert mapdata.points_per_feature(0) == mapdata.DEFAULT_MAX_POINTS
    assert mapdata.points_per_feature(2000) == 6
    assert mapdata.points_per_feature(100_000) == 2
    for count in (1, 7, 750, 3000, 25_000):
        drawn = count * mapdata.points_per_feature(count)
        assert drawn <= max(mapdata.VERTEX_BUDGET, count * 2)


def test_the_map_stays_inside_the_vertex_budget(session):
    fids = [int(row["fid"]) for row in session.rows(limit=None)]
    vertices, _ = mapdata.feature_frames(
        session, fids, max_points=mapdata.points_per_feature(len(fids))
    )
    assert len(vertices) <= mapdata.VERTEX_BUDGET
