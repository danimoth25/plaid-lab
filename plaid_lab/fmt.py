"""Console formatting.

ASCII only. The Windows console here is cp1252, so box-drawing characters and
em-dashes come back as `?` and make output harder to read, not easier.
"""

from __future__ import annotations

from typing import Any, Sequence


def heading(text: str) -> str:
    return f"\n{text}\n{'-' * len(text)}"


def money(value: Any, currency: str | None = None) -> str:
    if value is None:
        return "-"
    try:
        amount = f"{float(value):,.2f}"
    except (TypeError, ValueError):
        return str(value)
    return f"{amount} {currency}" if currency else amount


def table(rows: Sequence[Sequence[Any]], headers: Sequence[str]) -> str:
    """Left-aligned columns, numeric-looking columns right-aligned."""
    if not rows:
        return "(none)"

    cells = [[("" if c is None else str(c)) for c in row] for row in rows]
    widths = [len(h) for h in headers]
    for row in cells:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))

    right = [
        all(_numeric(row[i]) for row in cells if row[i])
        for i in range(len(headers))
    ]

    def line(values: Sequence[str]) -> str:
        return "  ".join(
            v.rjust(widths[i]) if right[i] else v.ljust(widths[i])
            for i, v in enumerate(values)
        ).rstrip()

    out = [line(list(headers)), "  ".join("-" * w for w in widths)]
    out.extend(line(row) for row in cells)
    return "\n".join(out)


def _numeric(text: str) -> bool:
    try:
        float(text.replace(",", "").replace("%", ""))
        return True
    except ValueError:
        return False
