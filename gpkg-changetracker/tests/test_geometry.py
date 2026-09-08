from __future__ import annotations

import pytest

from gpkg_changetracker import geometry as geom
from gpkg_changetracker.gpkg_io import open_gpkg
from gpkg_changetracker.sample import encode_multilinestring


def _first_blob(path, table):
    conn = open_gpkg(path, read_only=True)
    try:
        return conn.execute(f'SELECT geom FROM "{table}" ORDER BY fid').fetchone()[0]
    finally:
        conn.close()


def test_decodes_a_multilinestring(sample_gpkg, table):
    decoded = geom.decode(_first_blob(sample_gpkg, table))
    assert decoded is not None
    assert decoded.srs_id == 28992
    assert decoded.parts == (
        [(202000.0, 502000.0), (202150.0, 502080.0), (202320.0, 502090.0)],
    )
    assert decoded.bounds == (202000.0, 502000.0, 202320.0, 502090.0)
    assert decoded.representative_point == (202150.0, 502080.0)


def test_round_trips_through_the_encoder():
    parts = [[(1.0, 2.0), (3.0, 4.0)], [(5.0, 6.0)]]
    decoded = geom.decode(encode_multilinestring(parts, 28992))
    assert [list(part) for part in decoded.parts] == parts


def test_envelope_falls_back_to_the_geometry(sample_gpkg, table):
    blob = _first_blob(sample_gpkg, table)
    assert geom.envelope_of(blob) == (202000.0, 502000.0, 202320.0, 502090.0)


def test_merge_bounds_ignores_missing_boxes():
    assert geom.merge_bounds([None]) is None
    assert geom.merge_bounds([(0, 0, 1, 1), None, (-1, 2, 0, 3)]) == (-1, 0, 1, 3)


def test_thin_keeps_the_ends():
    coords = [(float(index), 0.0) for index in range(20)]
    thinned = geom.thin(coords, 5)
    assert len(thinned) == 5
    assert thinned[0] == coords[0] and thinned[-1] == coords[-1]
    assert geom.thin(coords, 50) == coords


def test_reprojects_rijksdriehoek_to_lon_lat():
    (part,) = geom.to_wgs84([[(202000.0, 502000.0)]], 28992)
    lon, lat = part[0]
    assert 6.0 < lon < 6.2
    assert 52.4 < lat < 52.6


def test_wgs84_input_is_left_alone():
    assert geom.to_wgs84([[(6.1, 52.5)]], 4326) == ([(6.1, 52.5)],)


def test_rejects_a_blob_that_is_not_geopackage_geometry():
    assert geom.decode(None) is None
    with pytest.raises(geom.GeometryError):
        geom.decode(b"NOTGP...")
