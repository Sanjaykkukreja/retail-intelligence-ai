"""Tiny SQLite helpers. The SQL used everywhere is plain, so moving to Postgres later is a small job."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd


def connect(path: str | Path) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30)   # waits if another step is writing
    conn.row_factory = sqlite3.Row
    return conn


def read(conn: sqlite3.Connection, sql: str, params: tuple | dict = ()) -> pd.DataFrame:
    return pd.read_sql_query(sql, conn, params=params)


def write(conn: sqlite3.Connection, df: pd.DataFrame, table: str, if_exists: str = "replace") -> None:
    df.to_sql(table, conn, if_exists=if_exists, index=False)


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute("select 1 from sqlite_master where type='table' and name=?", (name,)).fetchone()
    return row is not None
