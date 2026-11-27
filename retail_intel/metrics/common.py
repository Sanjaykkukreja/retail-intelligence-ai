"""Shared helpers for the metric functions. Metrics are plain, tested Python: the AI never does the arithmetic."""
from __future__ import annotations

from datetime import date, timedelta

from .. import db
from ..config import DEFAULT_CONTEXT


def context(conn) -> dict[str, float]:
    """Owner-editable business rules (margin target, thresholds). Falls back to defaults."""
    out = dict(DEFAULT_CONTEXT)
    if db.table_exists(conn, "business_context"):
        for k, v in conn.execute("select key, value from business_context"):
            try:
                out[k] = float(v)
            except ValueError:
                pass
    return out


def as_of(conn) -> str:
    return conn.execute("select max(date) from fact_inventory_daily").fetchone()[0]


def shift(day: str, days: int) -> str:
    return (date.fromisoformat(day) + timedelta(days=days)).isoformat()
