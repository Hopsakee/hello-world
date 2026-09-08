import marimo

__generated_with = "0.24.0"
app = marimo.App(width="full", app_title="GeoPackage change tracker")


@app.cell
def _():
    import marimo as mo

    return (mo,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(
        """
        # GeoPackage change tracker

        Open a GeoPackage, edit attribute values, and export **only the rows you
        changed**. Every edit stamps the row's `changedate` column with the
        current UTC date and time; editing another field of the same row moves
        the stamp forward. The source file is never written to - all edits go to
        a working copy.
        """
    )
    return


@app.cell
def _():
    import math
    from pathlib import Path

    import altair as alt
    import pandas as pd

    # Altair refuses to render more than 5000 inline rows by default, which a
    # few hundred thinned watercourses already exceed; the app keeps the row
    # count in hand itself (see the vertex budget below).
    alt.data_transformers.disable_max_rows()

    from gpkg_changetracker import grid, mapdata
    from gpkg_changetracker.gpkg_io import CHANGEDATE_COLUMN
    from gpkg_changetracker.session import ChangeTrackingSession

    return (
        CHANGEDATE_COLUMN,
        ChangeTrackingSession,
        Path,
        alt,
        grid,
        mapdata,
        math,
        pd,
    )


@app.cell(hide_code=True)
def _(Path, mo):
    _cli = mo.cli_args()
    _cli_gpkg = str(_cli.get("gpkg") or "")
    _cli_workspace = str(_cli.get("workspace") or ".gpkg_changetracker")

    path_input = mo.ui.text(
        value=_cli_gpkg,
        placeholder="/path/to/file.gpkg",
        label="GeoPackage",
        full_width=True,
    )
    workspace_input = mo.ui.text(value=_cli_workspace, label="Working directory")
    fresh_switch = mo.ui.switch(
        False, label="Start over (throw away the working copy and its tracked edits)"
    )
    file_browser = mo.ui.file_browser(
        initial_path=Path(_cli_gpkg).parent if _cli_gpkg else Path.cwd(),
        filetypes=[".gpkg"],
        multiple=False,
        restrict_navigation=False,
        label="… or browse for a file",
    )

    mo.vstack(
        [
            mo.md("## 1. Open a file"),
            path_input,
            mo.accordion({"Browse the file system": file_browser}),
            mo.hstack([workspace_input, fresh_switch], justify="start", gap=2),
        ]
    )
    return file_browser, fresh_switch, path_input, workspace_input


@app.cell
def _(ChangeTrackingSession, file_browser, fresh_switch, path_input, workspace_input):
    _chosen = path_input.value.strip()
    if file_browser.value:
        _chosen = str(file_browser.value[0].path)

    session = None
    session_error = None
    if _chosen:
        try:
            session = ChangeTrackingSession.open(
                _chosen,
                workspace=workspace_input.value.strip() or None,
                resume=not fresh_switch.value,
            )
        except Exception as exc:  # surfaced in the UI instead of a stack trace
            session_error = f"{type(exc).__name__}: {exc}"
    return session, session_error


@app.cell(hide_code=True)
def _(mo, session, session_error):
    mo.stop(
        session is None,
        mo.callout(
            mo.md(
                session_error
                or "Point the field above at a GeoPackage (`.gpkg`) to start editing."
            ),
            kind="danger" if session_error else "info",
        ),
    )
    live = session
    mo.callout(
        mo.md(
            f"""
            **{live.source_path.name}** - table `{live.table}`,
            {live.row_count():,} rows, {len(live.editable_fields)} editable fields,
            CRS EPSG:{live.schema.srs_id}.

            Working copy: `{live.working_path}` - the source file stays untouched.
            """
        ),
        kind="success",
    )
    return (live,)


@app.cell
def _(mo):
    # Bumped after every edit so the tables and counters refresh.
    get_version, set_version = mo.state(0)
    # Bumped only on request, so an edit does not throw away the map selection.
    get_map_epoch, set_map_epoch = mo.state(0)
    return get_map_epoch, get_version, set_map_epoch, set_version


@app.cell(hide_code=True)
def _(CHANGEDATE_COLUMN, live, mo):
    _attribute_columns = [
        name for name in live.attribute_columns if name != live.pk_column
    ]
    _default_display = [
        name for name in _attribute_columns if name != CHANGEDATE_COLUMN
    ][:6]

    search_input = mo.ui.text(
        placeholder="e.g. OK2965", label="Search text/number fields", full_width=True
    )
    changed_only_switch = mo.ui.switch(False, label="Only rows I changed")
    feature_limit = mo.ui.number(
        start=10, stop=20000, step=10, value=750, label="Max features to load"
    )
    display_columns = mo.ui.multiselect(
        options=_attribute_columns,
        value=_default_display,
        label="Columns to show in the table",
        full_width=True,
    )
    map_selection_mode = mo.ui.dropdown(
        options={"zoom and click": "click", "drag a box": "box"},
        value="zoom and click",
        label="Map interaction",
    )
    refresh_map_button = mo.ui.run_button(label="Redraw map", kind="neutral")

    mo.vstack(
        [
            mo.md("## 2. Choose the features to work on"),
            mo.hstack(
                [search_input, changed_only_switch, feature_limit],
                justify="start",
                gap=2,
                widths=[3, 1, 1],
            ),
            display_columns,
            mo.hstack([map_selection_mode, refresh_map_button], justify="start", gap=2),
        ]
    )
    return (
        changed_only_switch,
        display_columns,
        feature_limit,
        map_selection_mode,
        refresh_map_button,
        search_input,
    )


@app.cell
def _(refresh_map_button, set_map_epoch):
    if refresh_map_button.value:
        set_map_epoch(lambda epoch: epoch + 1)
    return


@app.cell
def _(
    changed_only_switch,
    display_columns,
    feature_limit,
    get_map_epoch,
    live,
    mapdata,
    search_input,
):
    get_map_epoch()  # redraw when the user asks for it
    candidate_rows = live.rows(
        limit=int(feature_limit.value or 750),
        changed_only=changed_only_switch.value,
        search=search_input.value.strip() or None,
        columns=[live.pk_column, *display_columns.value],
    )
    candidate_fids = [int(row[live.pk_column]) for row in candidate_rows]
    # Vertices are drawn inline in the browser, so the whole map gets a budget
    # and the per-feature detail is whatever fits inside it.
    _points_per_feature = mapdata.points_per_feature(len(candidate_fids))
    vertices_df, points_df = mapdata.feature_frames(
        live,
        candidate_fids,
        label_columns=display_columns.value[:3],
        max_points=_points_per_feature,
    )
    return candidate_fids, points_df, vertices_df


@app.cell
def _(alt, mapdata, math, vertices_df):
    def geo_domains(frame, width=760, height=460, pad=0.12):
        """Scale domains that keep the shapes roughly undistorted."""
        bounds = mapdata.bounds_of(frame)
        if bounds is None:
            return None, None
        min_lon, min_lat, max_lon, max_lat = bounds
        centre_lat = (min_lat + max_lat) / 2
        squeeze = max(math.cos(math.radians(centre_lat)), 0.1)
        lon_span = max(max_lon - min_lon, 1e-4)
        lat_span = max(max_lat - min_lat, 1e-4)
        # Grow the shorter axis until the drawn aspect matches the canvas.
        target = (width / height) * (lat_span / (lon_span * squeeze))
        if target > 1:
            lon_span *= target
        else:
            lat_span /= target
        lon_centre, lat_centre = (min_lon + max_lon) / 2, (min_lat + max_lat) / 2
        return (
            [lon_centre - lon_span * (0.5 + pad), lon_centre + lon_span * (0.5 + pad)],
            [lat_centre - lat_span * (0.5 + pad), lat_centre + lat_span * (0.5 + pad)],
        )

    STATUS_COLOURS = alt.Scale(
        domain=["unchanged", "changed"], range=["#5b7c99", "#e8590c"]
    )
    MAP_WIDTH, MAP_HEIGHT = 760, 460
    # Domains come from the drawn vertices, so no line is clipped at the edge.
    lon_domain, lat_domain = geo_domains(vertices_df, MAP_WIDTH, MAP_HEIGHT)
    return (
        MAP_HEIGHT,
        MAP_WIDTH,
        STATUS_COLOURS,
        geo_domains,
        lat_domain,
        lon_domain,
    )


@app.cell(hide_code=True)
def _(
    CHANGEDATE_COLUMN,
    MAP_HEIGHT,
    MAP_WIDTH,
    STATUS_COLOURS,
    alt,
    lat_domain,
    lon_domain,
    map_selection_mode,
    mo,
    points_df,
    vertices_df,
):
    mo.stop(
        points_df.empty,
        mo.callout(mo.md("No features match the current filters."), kind="warn"),
    )

    _x = alt.X(
        "lon:Q", title="longitude", scale=alt.Scale(domain=lon_domain, nice=False)
    )
    _y = alt.Y(
        "lat:Q", title="latitude", scale=alt.Scale(domain=lat_domain, nice=False)
    )

    # Watercourses are a few hundred metres long: at the extent of a whole area
    # they are shorter than a pixel, which is why the map needs to zoom. The
    # param is named "pan_zoom" because marimo leaves such a param out of the
    # selection it reports back.
    if map_selection_mode.value == "box":
        _params = (alt.selection_interval(name="box", encodings=["x", "y"]),)
        _hint = (
            "Drag a box over the features to work on. Switch to zoom and click "
            "to zoom in on the lines."
        )
    else:
        _params = (
            alt.selection_point(
                name="pick", fields=["fid"], on="click", clear="dblclick"
            ),
            alt.selection_interval(bind="scales", name="pan_zoom"),
        )
        _hint = (
            "Scroll to zoom in until the lines show their shape, drag to pan, "
            "click a feature's dot to work on that feature alone, double-click "
            "to clear."
        )

    # clip=True matters: without it Vega grows the drawing surface to fit marks
    # that fall outside the scale domain, so one zoom step can blow the canvas
    # up to thousands of pixels and push the rest of the page off screen.
    _lines = (
        alt.Chart(vertices_df)
        .mark_line(strokeWidth=2.5, clip=True)
        .encode(
            x=_x,
            y=_y,
            detail="part:N",
            order="seq:Q",
            color=alt.Color("status:N", scale=STATUS_COLOURS, title="status"),
        )
    )
    _handles = (
        alt.Chart(points_df)
        .mark_point(size=45, filled=True, opacity=0.9, clip=True)
        .encode(
            x=_x,
            y=_y,
            color=alt.Color("status:N", scale=STATUS_COLOURS, title="status"),
            tooltip=["fid:Q", "label:N", f"{CHANGEDATE_COLUMN}:N"],
        )
        .add_params(*_params)
    )
    map_chart = mo.ui.altair_chart(
        alt.layer(_lines, _handles)
        .properties(width=MAP_WIDTH, height=MAP_HEIGHT)
        .configure_view(strokeWidth=0),
        # The chart brings its own selection params; marimo must not add more,
        # or Vega fails with "Unrecognized signal name".
        chart_selection=False,
        legend_selection=False,
    )
    mo.vstack([map_chart, mo.md(f"_{_hint}_")])
    return (map_chart,)


@app.cell
def _(candidate_fids, map_chart, points_df):
    _selected = map_chart.apply_selection(points_df)
    if _selected is None or getattr(_selected, "empty", True):
        working_fids = list(candidate_fids)
    else:
        working_fids = [int(fid) for fid in _selected["fid"].tolist()]
    return (working_fids,)


@app.cell(hide_code=True)
def _(
    CHANGEDATE_COLUMN,
    candidate_fids,
    display_columns,
    get_version,
    grid,
    live,
    mo,
    pd,
    working_fids,
):
    get_version()  # refresh after every edit
    grid_columns = [live.pk_column, CHANGEDATE_COLUMN, *display_columns.value]
    grid_rows = live.rows(fids=working_fids, columns=grid_columns, limit=None)
    # The feature id and the app's own stamp stay read-only.
    grid_editable = [
        name
        for name in display_columns.value
        if live.schema.has(name) and live.field_spec(name).editable
    ]
    # Every cell is handed over as text on purpose. A typed numeric cell in the
    # grid turns anything it cannot parse - a typo, a cleared cell - into 0,
    # which for a bed level or a width is a real value and a silent data error.
    # As text, whatever is typed reaches the validator, which either stores it
    # in the column's own type or refuses it with a reason.
    value_grid = mo.ui.data_editor(
        pd.DataFrame(
            [
                {column: grid.as_cell_text(row.get(column)) for column in grid_columns}
                for row in grid_rows
            ],
            columns=grid_columns,
        ),
        editable_columns=grid_editable,
    )
    mo.vstack(
        [
            mo.md(
                f"""
                ## 3. Change values in the table

                {len(working_fids)} of {len(candidate_fids)} loaded features{
                    " (narrowed by the map selection)"
                    if len(working_fids) != len(candidate_fids)
                    else ""
                }. Click a cell and type to change a value; every value is checked
                against its column before it is stored, and the row's
                `changedate` is set to the moment it changed. New row and delete
                row do nothing here - this app edits the attributes of existing
                features.
                """
            ),
            value_grid,
        ]
    )
    return grid_editable, grid_rows, value_grid


@app.cell
def _(mo):
    get_selected_fid, set_selected_fid = mo.state(None)
    return get_selected_fid, set_selected_fid


@app.cell(hide_code=True)
def _(
    grid,
    grid_editable,
    grid_rows,
    live,
    mo,
    pd,
    set_selected_fid,
    set_version,
    value_grid,
):
    _returned = value_grid.value
    _rows = (
        _returned.to_dict("records")
        if isinstance(_returned, pd.DataFrame)
        else list(_returned or [])
    )
    _outcome = grid.apply_grid_edits(live, grid_rows, _rows, grid_editable)

    grid_feedback = mo.md("")
    if _outcome.touched:
        if _outcome.applied:
            set_selected_fid(_outcome.last_fid)
            set_version(lambda version: version + 1)
        grid_feedback = mo.callout(mo.md(_outcome.summary()), kind=_outcome.kind)
    grid_feedback
    return (grid_feedback,)


@app.cell(hide_code=True)
def _(display_columns, get_selected_fid, live, mo, working_fids):
    _label_column = next(
        (name for name in display_columns.value if live.schema.has(name)), None
    )
    _rows = live.rows(
        fids=working_fids,
        columns=[live.pk_column] + ([_label_column] if _label_column else []),
        limit=None,
    )
    _options = {}
    for _row in _rows:
        _fid = int(_row[live.pk_column])
        _suffix = f" - {_row[_label_column]}" if _label_column else ""
        _options[f"{live.pk_column} {_fid}{_suffix}"] = _fid

    _remembered = get_selected_fid()
    _default = next((key for key, fid in _options.items() if fid == _remembered), None)
    if _default is None and _options:
        _default = next(iter(_options))  # never leave the field editor empty-handed
    row_picker = mo.ui.dropdown(
        options=_options,
        value=_default,
        searchable=True,
        label="Row to inspect and edit field by field",
        full_width=True,
    )
    row_picker
    return (row_picker,)


@app.cell(hide_code=True)
def _(CHANGEDATE_COLUMN, get_version, live, mo, row_picker):
    get_version()
    selected_fid = row_picker.value
    mo.stop(
        selected_fid is None,
        mo.callout(
            mo.md(
                "Pick a row above to edit every field of it, not just the columns in the table."
            ),
            kind="info",
        ),
    )
    selected_row = live.row(selected_fid)
    mo.stop(
        selected_row is None,
        mo.callout(mo.md(f"Row {selected_fid} is no longer present."), kind="warn"),
    )
    changed_here = live.changed_columns(selected_fid)
    mo.md(
        f"""
        ### Row `{live.pk_column} = {selected_fid}`

        - `{CHANGEDATE_COLUMN}`: **{selected_row.get(CHANGEDATE_COLUMN) or "never changed"}**
        - changed fields: {", ".join(f"`{name}`" for name in changed_here) or "none"}
        """
    )
    return changed_here, selected_fid, selected_row


@app.cell(hide_code=True)
def _(
    MAP_HEIGHT,
    MAP_WIDTH,
    STATUS_COLOURS,
    alt,
    geo_domains,
    live,
    mapdata,
    mo,
    selected_fid,
    vertices_df,
):
    _vertices, _points = mapdata.feature_frames(live, [selected_fid], max_points=60)
    if _points.empty:
        detail_map = mo.md("_This row has no geometry to draw._")
    else:
        _lon, _lat = geo_domains(_vertices, MAP_WIDTH // 2, MAP_HEIGHT // 2, pad=1.5)
        _x = alt.X("lon:Q", title=None, scale=alt.Scale(domain=_lon, nice=False))
        _y = alt.Y("lat:Q", title=None, scale=alt.Scale(domain=_lat, nice=False))
        _context = (
            alt.Chart(vertices_df)
            .mark_line(strokeWidth=1, color="#c9d3dc", clip=True)
            .encode(x=_x, y=_y, detail="part:N", order="seq:Q")
        )
        _feature = (
            alt.Chart(_vertices)
            .mark_line(strokeWidth=3.5, clip=True)
            .encode(
                x=_x,
                y=_y,
                detail="part:N",
                order="seq:Q",
                color=alt.Color("status:N", scale=STATUS_COLOURS, legend=None),
            )
        )
        # A plain chart, not a mo.ui element: this one is a picture, not an
        # input, and marimo's own altair formatter renders it reliably.
        detail_map = (
            alt.layer(_context, _feature)
            .properties(width=MAP_WIDTH // 2, height=MAP_HEIGHT // 2)
            .configure_view(strokeWidth=0)
        )
    detail_map
    return (detail_map,)


@app.cell(hide_code=True)
def _(live, mo, selected_row):
    _editable = [spec.name for spec in live.editable_fields]
    _prefilled = [name for name in _editable if selected_row.get(name) is not None][:6]
    field_picker = mo.ui.multiselect(
        options=_editable,
        value=_prefilled or _editable[:6],
        label="Fields to edit",
        full_width=True,
    )
    field_picker
    return (field_picker,)


@app.cell
def _(field_picker, live, mo, selected_fid, selected_row):
    def _in_range(bound, limit):
        return bound is not None and -limit < bound < limit

    def build_input(spec, current):
        """Give every field an input that can only produce valid values."""
        hint = spec.describe()
        label = f"`{spec.name}` — {hint}"
        if spec.enum:
            options = {f"{value} — {text}": value for value, text in spec.enum}
            match = next(
                (key for key, value in options.items() if str(value) == str(current)),
                None,
            )
            if match is None and current is not None:
                # Keep a value that is not in the domain, rather than nulling it.
                match = f"{current} — value already in the file"
                options[match] = current
            return mo.ui.dropdown(
                options=options,
                value=match,
                allow_select_none=spec.nullable,
                searchable=len(options) > 8,
                label=label,
            )
        if spec.storage == "boolean":
            options = {"true": 1, "false": 0}
            match = None if current is None else ("true" if current else "false")
            return mo.ui.dropdown(
                options=options,
                value=match,
                allow_select_none=spec.nullable,
                label=label,
            )
        if spec.storage in ("integer", "real"):
            # Bounds beyond JavaScript's exact integer range (a FLOAT column
            # reaches 3.4e38) only make the input warn; the validator still
            # enforces them.
            _safe = 2**53
            return mo.ui.number(
                start=spec.minimum if _in_range(spec.minimum, _safe) else None,
                stop=spec.maximum if _in_range(spec.maximum, _safe) else None,
                step=1 if spec.storage == "integer" else None,
                value=None if current is None else float(current),
                label=label,
                full_width=True,
            )
        if spec.storage == "text" and (spec.max_length or 0) > 120:
            return mo.ui.text_area(
                value="" if current is None else str(current),
                max_length=spec.max_length,
                rows=3,
                label=label,
                full_width=True,
            )
        return mo.ui.text(
            value="" if current is None else str(current),
            max_length=spec.max_length,
            label=label,
            full_width=True,
        )

    _elements = {
        name: build_input(live.field_spec(name), selected_row.get(name))
        for name in field_picker.value
    }
    edit_form = mo.ui.dictionary(_elements).form(
        submit_button_label="Apply changes to this row",
        bordered=True,
        clear_on_submit=False,
    )
    form_fid = selected_fid
    mo.vstack([mo.md("## 4. Edit the fields"), edit_form])
    return build_input, edit_form, form_fid


@app.cell(hide_code=True)
def _(edit_form, form_fid, live, mo, selected_fid, set_version):
    edit_feedback = mo.md("")
    _values = edit_form.value
    if _values and form_fid == selected_fid:
        _result = live.apply_edits(selected_fid, dict(_values))
        if _result.applied:
            set_version(lambda version: version + 1)
        _lines = [f"**{_result.summary()}**"]
        _lines += [
            f"- `{column}` rejected: {message}"
            for column, message in _result.errors.items()
        ]
        edit_feedback = mo.callout(
            mo.md("\n".join(_lines)),
            kind="danger" if _result.errors else "success",
        )
    edit_feedback
    return (edit_feedback,)


@app.cell(hide_code=True)
def _(changed_here, mo):
    revert_picker = mo.ui.multiselect(
        options=list(changed_here),
        value=list(changed_here),
        label="Fields to put back to their original value",
    )
    revert_button = mo.ui.run_button(label="Revert selected fields", kind="warn")
    mo.hstack([revert_picker, revert_button], justify="start", gap=2)
    return revert_button, revert_picker


@app.cell(hide_code=True)
def _(live, mo, revert_button, revert_picker, selected_fid, set_version):
    revert_feedback = mo.md("")
    if revert_button.value and revert_picker.value:
        _result = live.revert(selected_fid, revert_picker.value)
        if _result.applied:
            set_version(lambda version: version + 1)
        revert_feedback = mo.callout(mo.md(_result.summary()), kind="info")
    revert_feedback
    return (revert_feedback,)


@app.cell(hide_code=True)
def _(CHANGEDATE_COLUMN, get_version, live, mo, pd, row_picker):
    get_version()
    focus_fid = row_picker.value
    _changed_fids = live.changed_fids()
    changed_table = pd.DataFrame(
        live.rows(
            fids=_changed_fids,
            columns=[live.pk_column, CHANGEDATE_COLUMN, *live.changed_columns()],
            limit=None,
        )
    )

    def log_frame(fid=None):
        return pd.DataFrame(
            [
                {
                    "when (UTC)": entry.changed_at,
                    live.pk_column: entry.fid,
                    "field": entry.column,
                    "from": entry.old_value,
                    "to": entry.new_value,
                    "original": entry.original_value,
                }
                for entry in live.history(fid, limit=200)
            ]
        )

    _log_items = {}
    if focus_fid is not None:
        _log_items[f"Change log for row {focus_fid}"] = mo.ui.table(
            log_frame(focus_fid), selection=None, page_size=8
        )
    _log_items["Full change log"] = mo.ui.table(
        log_frame(), selection=None, page_size=10
    )

    mo.vstack(
        [
            mo.md(f"## 5. Tracked changes \u2014 {len(_changed_fids)} row(s)"),
            mo.ui.table(changed_table, selection=None, page_size=8)
            if not changed_table.empty
            else mo.md("_No changes yet._"),
            mo.accordion(_log_items),
        ]
    )
    return changed_table, focus_fid, log_frame


@app.cell(hide_code=True)
def _(get_version, live, mo):
    get_version()
    _default_out = str(
        live.source_path.with_name(f"{live.source_path.stem}_changed.gpkg")
    )
    export_input = mo.ui.text(value=_default_out, label="Save as", full_width=True)
    export_button = mo.ui.run_button(
        label=f"Write GeoPackage with {live.changed_count()} changed row(s)",
        kind="success",
        disabled=live.changed_count() == 0,
    )
    mo.vstack([mo.md("## 6. Save the changed rows"), export_input, export_button])
    return export_button, export_input


@app.cell(hide_code=True)
def _(export_button, export_input, live, mo):
    export_feedback = mo.md("")
    if export_button.value:
        try:
            _result = live.export_changed(export_input.value.strip())
            _size = _result.path.stat().st_size
            export_feedback = mo.vstack(
                [
                    mo.callout(
                        mo.md(
                            f"""
                            **{_result.summary()}**

                            `{_result.path.resolve()}` ({_size / 1024:.0f} kB) -
                            same schema and CRS as the source, plus the
                            `changedate` column.
                            """
                        ),
                        kind="success",
                    ),
                    mo.download(
                        data=_result.path.read_bytes(),
                        filename=_result.path.name,
                        mimetype="application/geopackage+sqlite3",
                        label=f"Download {_result.path.name}",
                    ),
                ]
            )
        except Exception as exc:
            export_feedback = mo.callout(
                mo.md(f"Export failed: {type(exc).__name__}: {exc}"), kind="danger"
            )
    export_feedback
    return (export_feedback,)


if __name__ == "__main__":
    app.run()
