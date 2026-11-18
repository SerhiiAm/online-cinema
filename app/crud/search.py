from datetime import UTC, date, datetime, time

from sqlalchemy import ColumnElement
from sqlalchemy.orm import InstrumentedAttribute


def contains(column: InstrumentedAttribute[str], text: str) -> ColumnElement[bool]:
    """Part of the text, any case. `%` and `_` are searched as themselves:
    without autoescape they would be SQL wildcards ("%" would match all)."""
    return column.icontains(text, autoescape=True)


def within_days(
    column: InstrumentedAttribute[datetime],
    date_from: date | None,
    date_to: date | None,
) -> list[ColumnElement[bool]]:
    """Whole days in UTC, both included: from 00:00:00 of the first day to
    the last microsecond of the last one (the DB stores microseconds too).
    A missing day is no limit on that side."""
    conditions = []
    if date_from is not None:
        conditions.append(column >= datetime.combine(date_from, time.min, UTC))
    if date_to is not None:
        conditions.append(column <= datetime.combine(date_to, time.max, UTC))
    return conditions
