"""Command line entry point: start the Marimo app, or edit/export headlessly."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from gpkg_changetracker.session import DEFAULT_WORKSPACE, ChangeTrackingSession

APP_FILE = "app.py"


def _app_path() -> Path:
    """Locate ``app.py``, whether running from the repo or an installed package."""
    candidates = [
        Path.cwd() / APP_FILE,
        Path(__file__).resolve().parents[2] / APP_FILE,
        Path(__file__).resolve().parent / APP_FILE,
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(
        f"could not find {APP_FILE}; run this command from the project directory"
    )


def _run(args: argparse.Namespace) -> int:
    """Launch the Marimo app, optionally with a GeoPackage preloaded."""
    app = _app_path()
    command = [
        sys.executable,
        "-m",
        "marimo",
        "edit" if args.edit else "run",
        str(app),
        "--port",
        str(args.port),
    ]
    if args.no_browser:
        command.append("--headless")
    extra: list[str] = []
    if args.gpkg:
        extra += ["--gpkg", str(Path(args.gpkg).expanduser().resolve())]
    if args.workspace:
        extra += ["--workspace", str(args.workspace)]
    if extra:
        command += ["--", *extra]
    print("starting:", " ".join(command))
    return subprocess.call(command, env=os.environ.copy())


def _tables(args: argparse.Namespace) -> int:
    from gpkg_changetracker.gpkg_io import open_gpkg
    from gpkg_changetracker.schema import list_feature_tables, read_table_schema

    conn = open_gpkg(args.gpkg, read_only=True)
    try:
        for table in list_feature_tables(conn):
            schema = read_table_schema(conn, table)
            count = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            print(
                f"{table}: {count} rows, {len(schema.fields)} columns, srs {schema.srs_id}"
            )
            for spec in schema.fields:
                print(f"    {spec.name:<28} {spec.describe()}")
    finally:
        conn.close()
    return 0


def _status(args: argparse.Namespace) -> int:
    with ChangeTrackingSession.open(
        args.gpkg, table=args.table, workspace=args.workspace
    ) as session:
        print(f"source        : {session.source_path}")
        print(f"working copy  : {session.working_path}")
        print(f"table         : {session.table} ({session.row_count()} rows)")
        print(f"changed rows  : {session.changed_count()}")
        changed = session.changed_columns()
        if changed:
            print(f"changed fields: {', '.join(changed)}")
        for entry in session.history(limit=args.limit):
            print(
                f"  {entry.changed_at}  {session.pk_column}={entry.fid:<8} "
                f"{entry.column}: {entry.old_value!r} -> {entry.new_value!r}"
            )
    return 0


def _set(args: argparse.Namespace) -> int:
    values = {}
    for assignment in args.assignments:
        if "=" not in assignment:
            print(f"error: expected COLUMN=VALUE, got {assignment!r}", file=sys.stderr)
            return 2
        column, value = assignment.split("=", 1)
        values[column.strip()] = value
    with ChangeTrackingSession.open(
        args.gpkg, table=args.table, workspace=args.workspace
    ) as session:
        result = session.apply_edits(args.fid, values)
        for column, message in result.errors.items():
            print(f"rejected {column}: {message}", file=sys.stderr)
        print(result.summary())
        return 0 if result.ok else 1


def _export(args: argparse.Namespace) -> int:
    with ChangeTrackingSession.open(
        args.gpkg, table=args.table, workspace=args.workspace
    ) as session:
        result = session.export_changed(args.out)
        print(result.summary())
        print(f"written to {result.path.resolve()}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="gpkg-changetracker",
        description="Edit GeoPackage attributes with automatic change-date tracking.",
    )
    # Every subcommand accepts --workspace, so it can follow the file argument.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--workspace",
        default=str(DEFAULT_WORKSPACE),
        help="directory holding the working copy (default: %(default)s)",
    )
    subparsers = parser.add_subparsers(dest="command")

    run = subparsers.add_parser(
        "run", parents=[common], help="start the Marimo app (default)"
    )
    run.add_argument("gpkg", nargs="?", help="GeoPackage to open on start")
    run.add_argument("--port", type=int, default=2718)
    run.add_argument("--edit", action="store_true", help="open as an editable notebook")
    run.add_argument("--no-browser", action="store_true", help="do not open a browser")
    run.set_defaults(func=_run)

    tables = subparsers.add_parser(
        "tables", parents=[common], help="list feature tables and their fields"
    )
    tables.add_argument("gpkg")
    tables.set_defaults(func=_tables)

    status = subparsers.add_parser(
        "status", parents=[common], help="show tracked changes of a session"
    )
    status.add_argument("gpkg")
    status.add_argument("--table")
    status.add_argument("--limit", type=int, default=20)
    status.set_defaults(func=_status)

    set_command = subparsers.add_parser(
        "set", parents=[common], help="edit one row without the UI"
    )
    set_command.add_argument("gpkg")
    set_command.add_argument("fid", type=int)
    set_command.add_argument("assignments", nargs="+", metavar="COLUMN=VALUE")
    set_command.add_argument("--table")
    set_command.set_defaults(func=_set)

    export = subparsers.add_parser(
        "export", parents=[common], help="write a GeoPackage with the changed rows"
    )
    export.add_argument("gpkg")
    export.add_argument("out")
    export.add_argument("--table")
    export.set_defaults(func=_export)

    raw = list(sys.argv[1:]) if argv is None else list(argv)
    known = {"run", "tables", "status", "set", "export"}
    # Allow the shorthand "gpkg-changetracker some.gpkg" for "run some.gpkg".
    if not raw or (raw[0] not in known and not raw[0].startswith("-")):
        raw = ["run", *raw]
    args = parser.parse_args(raw)
    if args.command is None:
        args = parser.parse_args(["run"])
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
