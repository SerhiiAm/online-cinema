from sqlalchemy import ColumnElement
from sqlalchemy.orm import InstrumentedAttribute


def contains(column: InstrumentedAttribute[str], text: str) -> ColumnElement[bool]:
    """Part of the text, any case. `%` and `_` are searched as themselves:
    without autoescape they would be SQL wildcards ("%" would match all)."""
    return column.icontains(text, autoescape=True)
