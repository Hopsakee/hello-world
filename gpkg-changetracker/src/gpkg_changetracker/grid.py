"""Work out what a user changed in the editable table.

``mo.ui.data_editor`` hands back the whole table, not the individual edits, so
the applied values are found by comparing the rows that were handed to it with
the rows that come back. Comparison is deliberately forgiving: a grid reports
an emptied cell as ``NaN`` or ``""`` where the GeoPackage has ``NULL``, and a
number can come back as text, so those must not read as changes.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Iterable, Mapping, Sequence

if TYPE_CHECKING:  # pragma: no cover - import cycle only matters for typing
    from gpkg_changetracker.session import ChangeTrackingSession


def _key(value: Any) -> str | None:
    """Feature ids as text, so a grid handing back "12" still matches ``12``."""
    value = normalise_cell(value)
    if value is None:
        return None
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def normalise_cell(value: Any) -> Any:
    """Map a grid cell to the value it really means.

    Empty strings and ``NaN``/``NaT`` become ``None``, so "cleared" is one
    concept rather than three.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value if value.strip() else None
    if isinstance(value, float) and math.isnan(value):
        return None
    # pandas.NaT and other null sentinels compare unequal to themselves.
    if value != value:  # noqa: PLR0124 - NaN check without importing pandas
        return None
    return value


def as_cell_text(value: Any) -> str:
    """Render a stored value for a text cell in the editable table.

    Values go to the grid as text so that what the user types arrives here
    unchanged: a typed numeric cell silently turns anything it cannot parse
    into 0, which is a real bed level or width, not a rejected edit.
    """
    value = normalise_cell(value)
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def cells_differ(before: Any, after: Any) -> bool:
    """True when a grid cell holds a different value than the stored one."""
    before, after = normalise_cell(before), normalise_cell(after)
    if before is None or after is None:
        return not (before is None and after is None)
    if isinstance(before, bool) or isinstance(after, bool):
        return bool(before) != bool(after)
    if isinstance(before, (int, float)) and isinstance(after, (int, float)):
        return float(before) != float(after)
    if isinstance(before, (int, float)) or isinstance(after, (int, float)):
        # One side arrived as text: compare numerically when that is possible.
        try:
            return float(before) != float(after)
        except (TypeError, ValueError):
            return str(before) != str(after)
    return str(before) != str(after)


def diff_rows(
    before: Iterable[Mapping[str, Any]],
    after: Iterable[Mapping[str, Any]],
    *,
    key: str,
    columns: Sequence[str],
) -> dict[int, dict[str, Any]]:
    """Edits per row, as ``{feature_id: {column: new_value}}``.

    Rows are matched on ``key``, never on position, so rows the grid added or
    removed are ignored instead of being written to the wrong feature.
    """
    originals = {_key(row.get(key)): row for row in before if row.get(key) is not None}
    edits: dict[int, dict[str, Any]] = {}
    for row in after:
        identifier = _key(row.get(key))
        if identifier is None:
            continue  # a row added in the grid: no feature, no geometry
        original = originals.get(identifier)
        if original is None:
            continue
        for column in columns:
            if column not in row or column not in original:
                continue
            if cells_differ(original[column], row[column]):
                edits.setdefault(int(original[key]), {})[column] = normalise_cell(
                    row[column]
                )
    return edits


def added_or_removed(
    before: Iterable[Mapping[str, Any]], after: Iterable[Mapping[str, Any]], *, key: str
) -> tuple[int, int]:
    """``(added, removed)`` row counts, so the app can say they are ignored."""
    before_ids = {_key(row.get(key)) for row in before}
    after_rows = [_key(row.get(key)) for row in after]
    after_ids = set(after_rows)
    added = sum(
        1
        for identifier in after_rows
        if identifier is None or identifier not in before_ids
    )
    removed = sum(1 for identifier in before_ids if identifier not in after_ids)
    return added, removed


@dataclass
class GridOutcome:
    """What came of the edits a user made in the table."""

    applied: dict[int, list[str]] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    stamp: str | None = None
    added: int = 0
    removed: int = 0
    last_fid: int | None = None

    @property
    def value_count(self) -> int:
        return sum(len(columns) for columns in self.applied.values())

    @property
    def touched(self) -> bool:
        """True when the grid reported anything at all worth answering."""
        return bool(self.applied or self.errors or self.added or self.removed)

    @property
    def kind(self) -> str:
        return "danger" if self.errors else "success"

    def summary(self) -> str:
        """Markdown telling the user exactly what was stored and what was not."""
        if self.value_count:
            headline = (
                f"**{self.value_count} value(s) stored in {len(self.applied)} row(s)**"
            )
            if self.stamp:
                headline += f" - `changedate` set to {self.stamp}"
        else:
            headline = "**Nothing stored**"
        return "\n".join([headline, *(f"- {message}" for message in self.errors)])


def apply_grid_edits(
    session: "ChangeTrackingSession",
    before: Iterable[Mapping[str, Any]],
    after: Iterable[Mapping[str, Any]],
    columns: Sequence[str],
) -> GridOutcome:
    """Store what the user changed in the table, and report the rest.

    Each row is one call into the session, so a row edited in several columns
    gets a single ``changedate``. Values the column refuses are reported per
    cell and leave the stored value alone.
    """
    before = list(before)
    after = list(after)
    key = session.pk_column
    outcome = GridOutcome()
    outcome.added, outcome.removed = added_or_removed(before, after, key=key)

    for fid, values in diff_rows(before, after, key=key, columns=columns).items():
        result = session.apply_edits(fid, values)
        if result.applied:
            outcome.applied[fid] = sorted(result.applied)
            outcome.stamp = result.changedate or outcome.stamp
            outcome.last_fid = fid
        for column, message in result.errors.items():
            outcome.errors.append(
                f"row {fid}, `{column}`: {message} - the stored value is kept"
            )

    if outcome.added or outcome.removed:
        outcome.errors.append(
            f"{outcome.added} added and {outcome.removed} removed row(s) ignored: "
            "this app edits the attributes of existing features, it does not "
            "create or delete features"
        )
    return outcome
