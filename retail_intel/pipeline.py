"""Run the whole chain: ingest -> reconcile -> quality gate -> core -> metrics findings.

The quality gate sits BEFORE the core build. If it finds a blocking problem the run stops and says why,
because wrong numbers delivered confidently are worse than no numbers.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from . import db
from .config import DB_PATH, OVERRIDES_PATH, SOURCE_DIR
from .core.build_core import build_core, check_core_reconciles
from .ingest.load_raw import load_raw
from .reconcile.order_matching import build_order_xref
from .reconcile.quality import blockers, run_checks
from .reconcile.sku_mapping import build_sku_map


def run_pipeline(source_dir: str | Path = SOURCE_DIR, db_path: str | Path = DB_PATH,
                 overrides_path: str | Path | None = OVERRIDES_PATH, with_findings: bool = True) -> dict:
    conn = db.connect(db_path)
    result: dict = {"status": "ok", "steps": {}}
    result["steps"]["ingest"] = load_raw(conn, source_dir)
    sku_map = build_sku_map(conn, overrides_path)
    result["steps"]["sku_map"] = sku_map["status"].value_counts().to_dict()
    build_order_xref(conn)
    dq = run_checks(conn)
    result["dq"] = dq
    blocked = blockers(dq)
    if len(blocked):
        result["status"] = "blocked"
        result["blockers"] = blocked
        conn.close()
        return result
    result["steps"]["core"] = build_core(conn)
    rec = check_core_reconciles(conn)
    result["reconciliation"] = rec
    if not bool(rec["ok"].iloc[0]):
        result["status"] = "blocked"
        result["blockers"] = rec
        conn.close()
        return result
    if with_findings:
        from .actions import sync_recommendations
        from .findings import run_all_findings, save_findings
        findings = run_all_findings(conn)
        save_findings(conn, findings)
        sync_recommendations(conn, findings)
        result["findings"] = findings
    # data version: the agent's tool cache is keyed on this, so a new run invalidates cached answers
    from datetime import datetime
    conn.execute("create table if not exists pipeline_runs (run_at text, status text)")
    conn.execute("insert into pipeline_runs values (?, 'ok')", (datetime.now().isoformat(timespec="microseconds"),))
    conn.commit()
    conn.close()
    return result
