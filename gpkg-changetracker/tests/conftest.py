from __future__ import annotations

from pathlib import Path

import pytest

from gpkg_changetracker.gpkg_io import open_gpkg
from gpkg_changetracker.sample import TABLE, write_sample
from gpkg_changetracker.session import ChangeTrackingSession


@pytest.fixture()
def sample_gpkg(tmp_path: Path) -> Path:
    """A small demo GeoPackage with typed columns, an RTree and a coded domain."""
    return write_sample(tmp_path / "source" / "waterways.gpkg")


@pytest.fixture()
def session(sample_gpkg: Path, tmp_path: Path):
    with ChangeTrackingSession.open(
        sample_gpkg, workspace=tmp_path / "workspace"
    ) as open_session:
        yield open_session


@pytest.fixture()
def table() -> str:
    return TABLE


@pytest.fixture()
def read_gpkg():
    """Helper that opens a GeoPackage read-only for assertions."""

    def _read(path: Path):
        return open_gpkg(path, read_only=True)

    return _read
