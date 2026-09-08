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

    from gpkg_changetracker import mapdata
    from gpkg_changetracker.gpkg_io import CHANGEDATE_COLUMN
    from gpkg_changetracker.session import ChangeTrackingSession

    return (
        CHANGEDATE_COLUMN,
        ChangeTrackingSession,
        Path,
        alt,
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
        options={"drag a box": "interval", "click a feature": "point"},
        value="drag a box",
        label="Map selection",
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
    vertices_df, points_df = mapdata.feature_frames(
        live, candidate_fids, label_columns=display_columns.value[:3]
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
    _lines = (
        alt.Chart(vertices_df)
        .mark_line(strokeWidth=2)
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
        .mark_point(size=70, filled=True, opacity=0.85)
        .encode(
            x=_x,
            y=_y,
            color=alt.Color("status:N", scale=STATUS_COLOURS, title="status"),
            tooltip=["fid:Q", "label:N", f"{CHANGEDATE_COLUMN}:N"],
        )
    )
    map_chart = mo.ui.altair_chart(
        alt.layer(_lines, _handles)
        .properties(width=MAP_WIDTH, height=MAP_HEIGHT)
        .configure_view(strokeWidth=0),
        chart_selection=map_selection_mode.value,
        legend_selection=False,
    )
    map_chart
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
    live,
    mo,
    pd,
    working_fids,
):
    get_version()  # refresh after every edit
    _columns = [live.pk_column, CHANGEDATE_COLUMN, *display_columns.value]
    table_df = pd.DataFrame(
        live.rows(fids=working_fids, columns=_columns, limit=None),
        columns=_columns,
    )
    row_table = mo.ui.table(
        table_df,
        selection="single",
        page_size=12,
        label=(
            f"**3. Pick a row to edit** - {len(working_fids)} of "
            f"{len(candidate_fids)} loaded features "
            f"{'(map selection active)' if len(working_fids) != len(candidate_fids) else ''}"
        ),
    )
    row_table
    return (row_table,)


@app.cell
def _(mo):
    get_selected_fid, set_selected_fid = mo.state(None)
    return get_selected_fid, set_selected_fid


@app.cell
def _(live, row_table, set_selected_fid):
    _value = row_table.value
    _rows = (
        _value.to_dict("records") if hasattr(_value, "to_dict") else list(_value or [])
    )
    if _rows:
        # Keep the last picked row when the table is rebuilt after an edit.
        set_selected_fid(int(_rows[0][live.pk_column]))
    return


@app.cell(hide_code=True)
def _(CHANGEDATE_COLUMN, get_selected_fid, get_version, live, mo):
    get_version()
    selected_fid = get_selected_fid()
    mo.stop(
        selected_fid is None,
        mo.callout(
            mo.md("Select a row in the table above to edit its fields."), kind="info"
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
            .mark_line(strokeWidth=1, color="#c9d3dc")
            .encode(x=_x, y=_y, detail="part:N", order="seq:Q")
        )
        _feature = (
            alt.Chart(_vertices)
            .mark_line(strokeWidth=3.5)
            .encode(
                x=_x,
                y=_y,
                detail="part:N",
                order="seq:Q",
                color=alt.Color("status:N", scale=STATUS_COLOURS, legend=None),
            )
        )
        detail_map = mo.ui.altair_chart(
            alt.layer(_context, _feature)
            .properties(width=MAP_WIDTH // 2, height=MAP_HEIGHT // 2)
            .configure_view(strokeWidth=0),
            chart_selection=False,
            legend_selection=False,
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
            return mo.ui.number(
                start=spec.minimum,
                stop=spec.maximum,
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
def _(CHANGEDATE_COLUMN, get_selected_fid, get_version, live, mo, pd):
    get_version()
    # Independent of the row picker, so the overview shows even with nothing selected.
    focus_fid = get_selected_fid()
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
