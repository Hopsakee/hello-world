"""Write a small demo GeoPackage: ``uv run python scripts/make_sample_gpkg.py [path]``."""

from __future__ import annotations

import sys
from pathlib import Path

from gpkg_changetracker.sample import write_sample

if __name__ == "__main__":
    target = Path(sys.argv[1] if len(sys.argv) > 1 else "sample_waterways.gpkg")
    print(f"wrote {write_sample(target).resolve()}")
