"""Units through the dictionary (DECISIONS D51, D59).

The reader keeps numbers as they are in the source: Razorpay's ``amount`` stays whole paise. The dictionary says
which columns are paise (``divide_by: 100``), and everything that does arithmetic on money (metrics, the agent's
tools in M8) reads the table through ``to_units`` so a rupee is always a rupee.
"""

from __future__ import annotations

import pandas as pd

from app.dictionary.model import DatasetEntry


def to_units(frame: pd.DataFrame, entry: DatasetEntry) -> pd.DataFrame:
    """A copy of ``frame`` with every column the dictionary says is stored in a smaller unit divided out."""
    scaled = [
        (name, column.divide_by)
        for name, column in entry.columns.items()
        if column.divide_by and name in frame
    ]
    if not scaled:
        return frame
    out = frame.copy()
    for name, divisor in scaled:
        out[name] = pd.to_numeric(out[name], errors="coerce").astype("float64") / divisor
    return out
