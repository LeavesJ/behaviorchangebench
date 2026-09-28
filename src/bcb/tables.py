"""CSV writing and the small statistics the tables use. Deterministic formatting."""

from __future__ import annotations

import csv
import io
import math
from pathlib import Path
from typing import Any, Iterable, Sequence

from .util import write_text_atomic


def fmt(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def write_csv(path: str | Path, header: Sequence[str], rows: Iterable[Sequence[Any]]) -> None:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    for row in rows:
        if len(row) != len(header):
            raise ValueError(f"{path}: row has {len(row)} fields, header has {len(header)}")
        w.writerow([fmt(v) for v in row])
    write_text_atomic(path, buf.getvalue())


def wilson(successes: int, n: int, z: float = 1.96) -> tuple[float | None, float | None]:
    """Wilson score interval for a proportion. With n = 3 it is wide, and that is the point."""
    if n == 0:
        return None, None
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def ratio(num: int, den: int) -> float | None:
    return num / den if den else None


def median(values: Sequence[float]) -> float | None:
    if not values:
        return None
    s = sorted(values)
    mid = len(s) // 2
    return float(s[mid]) if len(s) % 2 else (s[mid - 1] + s[mid]) / 2
