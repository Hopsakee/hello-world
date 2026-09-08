# GeoPackage change tracker

A local [Marimo](https://marimo.io) app to edit the attributes of a GeoPackage
and hand back **only the rows you changed**, each stamped with the moment it was
changed.

- Opens any GeoPackage feature table (tested against a 26 000-feature
  `MULTILINESTRING Z` layer in EPSG:28992).
- Adds a **`changedate`** column. Editing a field stamps the row with the
  current UTC date and time; editing another field of the same row moves the
  stamp forward.
- Only lets you enter values the column actually accepts: type, text length,
  numeric range, `NOT NULL` and coded-value domains all come from the file
  itself.
- Saving writes a **new GeoPackage containing just the changed rows**, with the
  original schema, CRS, metadata and spatial index intact.
- The file you open is **never written to**: all edits go to a working copy.
- **Values are changed in the table itself**: click a cell, type, done.
- Map and table are linked - zoom and click a feature on the map to work on it,
  or brush an area to narrow the table.

No GDAL, GEOS or geopandas needed: a GeoPackage is a SQLite database, and the
handful of spatial operations this app performs are done directly on it.

## Requirements

[uv](https://docs.astral.sh/uv/) and Python 3.11+. Everything else is installed
by `uv` on first run.

## Run it

```bash
cd gpkg-changetracker

# 1. Try it on a small demo file (5 waterways, all field types)
uv run python scripts/make_sample_gpkg.py sample_waterways.gpkg
uv run gpkg-changetracker sample_waterways.gpkg

# 2. Or open your own file
uv run gpkg-changetracker /path/to/Primaire_watergang.gpkg
```

That starts Marimo at <http://localhost:2718> in app mode. Useful flags:

```bash
uv run gpkg-changetracker run mydata.gpkg --port 8080   # different port
uv run gpkg-changetracker run mydata.gpkg --edit        # open as a notebook
uv run marimo run app.py -- --gpkg mydata.gpkg          # the same, without the wrapper
```

You can also start the app with no file at all and pick one from the field at
the top (or browse for it).

## Using the app

1. **Open a file.** Point the app at a `.gpkg`. It copies the file to
   `.gpkg_changetracker/<name>.working.gpkg`, adds the `changedate` column
   there, and reports the table, row count and CRS. Reopening the same file
   resumes where you left off; tick *Start over* to throw the working copy away.
2. **Choose the features to work on.** Search, limit how many features are
   loaded, and pick the columns you want in the table. The map draws the
   geometry of the loaded features:
   - *zoom and click* (default): scroll to zoom, drag to pan, click a feature's
     dot to work on that one feature, double-click to clear. Zooming matters -
     a watercourse a few hundred metres long is thinner than a pixel when a
     whole area is in view, which is why it looks like a dot until you zoom in.
   - *drag a box*: brush a region to narrow the table to those features.
3. **Change values in the table.** Click a cell in the table and type. Each
   edit is checked against its column and stored immediately, and the row's
   `changedate` is set to that moment. A value the column will not take (a word
   in a number field, text over the column's length, a value outside its range)
   is reported per cell with the reason, and the stored value is kept. Every
   cell is a text cell on purpose: a typed numeric cell turns anything it
   cannot parse into `0`, which for a bed level is a silent data error rather
   than a rejected edit. `New row` and `delete row` do nothing - this app edits
   attributes, not features.
4. **Edit any field of one row.** The table shows the columns you selected; to
   reach the other fields, pick a row and use the field-by-field editor. Each
   field gets an input that matches the column - a number input with the real
   minimum and maximum, a length-capped text box, a dropdown for a coded-value
   domain. *Apply changes to this row* validates everything, writes the fields
   that are valid, reports per field what was rejected and why, and stamps
   `changedate`. The detail map next to it shows the selected feature against
   the rest of the working set.
5. **Tracked changes.** A table of every changed row, plus the full change log
   (when, which field, from what, to what, and the original value).
6. **Save.** *Write GeoPackage with N changed row(s)* produces a new `.gpkg`
   next to your source file (default `<name>_changed.gpkg`) containing only the
   changed rows, and offers it as a download.

Reverting a field to its original value is supported, and if every edited field
of a row is back to its original value the row's `changedate` is cleared and the
row drops out of the export - a row that is identical to the source is not a
change. The change log keeps the full history either way.

## Command line

The same engine without the UI, handy for scripting or a quick check:

```bash
uv run gpkg-changetracker tables mydata.gpkg          # fields and their constraints
uv run gpkg-changetracker set mydata.gpkg 42 depth=1,75 name=Aangepast
uv run gpkg-changetracker status mydata.gpkg          # what is changed, and when
uv run gpkg-changetracker export mydata.gpkg out.gpkg # changed rows only
```

## How it works

| Module | Responsibility |
| --- | --- |
| `schema.py` | Field definitions from `PRAGMA table_info`, the `CHECK` constraints GDAL writes, and the GeoPackage *Schema* extension (`gpkg_data_columns`). |
| `validation.py` | Coerces user input to the exact storage class the column demands and refuses the rest. |
| `geometry.py` | Reads GeoPackageBinary/WKB blobs, thins them and reprojects to lon/lat with `pyproj`. |
| `gpkg_io.py` | Working copies, the `changedate` column, attribute updates, and writing the changed-rows-only file. |
| `session.py` | The editing session: change log, `changedate` bookkeeping, export. |
| `mapdata.py` | Builds the two small frames the map is drawn from, inside a vertex budget. |
| `grid.py` | Works out what changed in the editable table and applies it. |
| `app.py` | The Marimo notebook: the UI and nothing else. |

Details worth knowing:

- **Storage classes matter.** GDAL writes `CHECK(typeof(depth) = 'real' ...)`
  constraints, so writing an integer into a `FLOAT` column would corrupt the
  file. Every value is coerced to the column's storage class first.
- **`ST_*` functions.** GDAL's spatial-index triggers call `ST_MinX` and
  `ST_IsEmpty`, which plain SQLite does not have - without them *any* update to
  a feature table fails. They are implemented on top of the blob reader, so the
  spatial index stays correct instead of being disabled.
- **`changedate` format.** `DATETIME` in a GeoPackage is ISO 8601 in UTC, so
  stamps look like `2026-09-08T12:50:48.633Z`. They are UTC, not local time.
- **Export by subtraction.** The export duplicates the working copy and deletes
  the rows that did not change. That is why CRS definitions, metadata tables and
  the RTree index come out exactly as the source had them.
- **The map has no basemap.** Vega-Lite draws the geometry itself, so the app
  works offline; there are no background tiles. Features are thinned to at most
  ~14 vertices for drawing, and the whole map shares a fixed vertex budget, so a
  large working set stays responsive - the exported geometry is always the
  untouched original.
- **Marks are clipped.** Vega grows its drawing surface to fit marks that fall
  outside the scale domain, so one zoom step on an unclipped map can produce a
  canvas thousands of pixels wide and push the rest of the page off screen.
  Every mark sets `clip=True`.

## Tests

```bash
uv run pytest
```

The suite builds a small GeoPackage from scratch (typed columns, `CHECK`
constraints, RTree with triggers, a coded-value domain), then covers schema
reading, validation, change tracking, revert behaviour, table edits (including
the values a column refuses and rows a grid invents), export validity, the CLI,
and a headless run of the Marimo notebook.
