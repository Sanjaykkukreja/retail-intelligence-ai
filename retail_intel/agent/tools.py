"""Component 2 - Tools. Every number the agent shows comes from one of these tested functions.

The tools themselves are plug-ins in retail_intel/tools/ (one file per area). They register with @tool and are
picked up automatically, so adding a tool never means editing this file. This module is the runtime around them:

  tier         cost hierarchy, cheapest first:  0 = local tested metric, 1 = free-form SQL fallback
  cacheable    read-only results are cached per data version (a new pipeline run clears the cache)
  side_effect  writes something (owner answers, approvals, business rules); never cached, only offered
               when the owner is clearly instructing

Every call (cached or not) is logged in tool_calls_log.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from .. import db
from ..platform.registry import TOOLS, load_plugins

MAX_RESULT_ROWS = 40
_CACHE: dict[tuple, object] = {}


def _json_safe(o):
    if isinstance(o, pd.DataFrame):      # tables come back with an explicit truncation flag, never silently cut
        return dict(rows=json.loads(o.head(MAX_RESULT_ROWS).round(2).to_json(orient="records")),
                    total_rows=len(o), truncated=len(o) > MAX_RESULT_ROWS)
    if isinstance(o, pd.Series):
        return json.loads(o.round(2).to_json())
    if isinstance(o, (np.integer,)): return int(o)
    if isinstance(o, (np.floating,)): return None if np.isnan(o) else round(float(o), 2)
    if isinstance(o, float): return round(o, 2)
    if isinstance(o, dict): return {k: _json_safe(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)): return [_json_safe(x) for x in o]
    return o


def is_empty(result) -> bool:
    return result in (None, [], {}) or (isinstance(result, dict) and result.get("rows") == [])


@dataclass
class ToolResult:
    name: str
    args: dict
    ok: bool
    result: object
    cached: bool
    ms: int


class Toolbox:
    def __init__(self, db_path: str | Path):
        load_plugins()
        self.db_path = str(db_path)
        self.specs = [spec for _, spec in TOOLS.items()]
        self.by_name = {s["name"]: s for s in self.specs}

    # ---- plumbing -----------------------------------------------------------------------------------
    def schemas(self, names: list[str] | None = None) -> list[dict]:
        """Tool definitions (name, description, JSON schema). Adapters convert to each provider's format."""
        wanted = self.specs if names is None else [self.by_name[n] for n in dict.fromkeys(names) if n in self.by_name]
        return [dict(name=s["name"], description=s["description"], input_schema=s["input_schema"]) for s in wanted]

    def data_version(self) -> str:
        conn = db.connect(self.db_path)
        try:
            if db.table_exists(conn, "pipeline_runs"):
                return conn.execute("select max(run_at) from pipeline_runs").fetchone()[0] or "0"
            return "0"
        finally:
            conn.close()

    def call(self, name: str, args: dict | None = None) -> object:
        return self.call_ex(name, args).result

    def call_ex(self, name: str, args: dict | None = None) -> ToolResult:
        args = args or {}
        t0 = time.time()
        spec = self.by_name.get(name)
        if spec is None:
            tr = ToolResult(name, args, False, {"error": f"unknown tool {name}"}, False, 0)
            self._log(tr)
            return tr
        key = (self.db_path, self.data_version(), name, json.dumps(args, sort_keys=True, default=str))
        if spec["cacheable"] and key in _CACHE:
            tr = ToolResult(name, args, True, _CACHE[key], True, int(1000 * (time.time() - t0)))
            self._log(tr)
            return tr
        conn = db.connect(self.db_path)
        try:
            result, ok = _json_safe(spec["fn"](conn, **args)), True
        except Exception as e:  # errors go back to the model so it can correct itself (conditional fallback)
            result, ok = {"error": f"{type(e).__name__}: {e}"}, False
        finally:
            conn.close()
        if ok and spec["cacheable"]:
            _CACHE[key] = result
        tr = ToolResult(name, args, ok, result, False, int(1000 * (time.time() - t0)))
        self._log(tr)
        return tr

    def _log(self, tr: ToolResult) -> None:
        conn = db.connect(self.db_path)
        try:
            conn.execute("create table if not exists tool_calls_log (ts text, tool text, args text, ok integer, cached integer, ms integer)")
            conn.execute("insert into tool_calls_log values (?,?,?,?,?,?)",
                         (datetime.now().isoformat(timespec="seconds"), tr.name, json.dumps(tr.args, default=str),
                          int(tr.ok), int(tr.cached), tr.ms))
            conn.commit()
        finally:
            conn.close()
