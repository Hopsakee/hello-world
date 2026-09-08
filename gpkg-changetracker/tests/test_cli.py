from __future__ import annotations

from gpkg_changetracker.cli import main
from gpkg_changetracker.gpkg_io import CHANGEDATE_COLUMN, open_gpkg


def test_tables_lists_fields(sample_gpkg, capsys):
    assert main(["tables", str(sample_gpkg)]) == 0
    printed = capsys.readouterr().out
    assert "waterways: 5 rows" in printed
    assert "max 8 characters" in printed


def test_set_then_status_then_export(sample_gpkg, tmp_path, capsys):
    workspace = ["--workspace", str(tmp_path / "workspace")]

    assert (
        main(["set", str(sample_gpkg), "2", "depth=1,75", "name=Aangepast", *workspace])
        == 0
    )
    assert "changedate =" in capsys.readouterr().out

    assert main(["status", str(sample_gpkg), *workspace]) == 0
    status = capsys.readouterr().out
    assert "changed rows  : 1" in status
    assert "depth" in status

    out = tmp_path / "changed.gpkg"
    assert main(["export", str(sample_gpkg), str(out), *workspace]) == 0
    assert "1 changed row" in capsys.readouterr().out

    conn = open_gpkg(out, read_only=True)
    try:
        rows = conn.execute(
            f'SELECT fid, depth, name, "{CHANGEDATE_COLUMN}" AS stamp FROM waterways'
        ).fetchall()
    finally:
        conn.close()
    assert len(rows) == 1
    assert (rows[0]["fid"], rows[0]["depth"], rows[0]["name"]) == (2, 1.75, "Aangepast")
    assert rows[0]["stamp"].endswith("Z")


def test_set_reports_invalid_values(sample_gpkg, tmp_path, capsys):
    exit_code = main(
        [
            "set",
            str(sample_gpkg),
            "1",
            "depth=diep",
            "--workspace",
            str(tmp_path / "workspace"),
        ]
    )
    assert exit_code == 1
    assert "rejected depth" in capsys.readouterr().err
