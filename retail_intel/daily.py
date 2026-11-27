"""The daily job: refresh data, update findings and tasks, check outcomes, write the briefing to a file.

Run it by hand (python -m retail_intel daily) or schedule it (README, "Run it every morning").
No AI call is needed, so the daily run costs nothing in API fees.
"""
from __future__ import annotations

from pathlib import Path

from . import db
from .actions import check_outcomes
from .briefing import build_briefing
from .config import DATA_DIR, DB_PATH, OVERRIDES_PATH, SOURCE_DIR
from .pipeline import run_pipeline


def run_daily(source_dir=SOURCE_DIR, db_path=DB_PATH, out_dir=DATA_DIR / "briefings", overrides_path=OVERRIDES_PATH) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    r = run_pipeline(source_dir, db_path, overrides_path)
    if r["status"] != "ok":
        path = out_dir / "ALERT_pipeline_blocked.md"
        path.write_text("# Data problem - today's numbers were NOT produced\n\n```\n" + r["blockers"].to_string(index=False) + "\n```\n")
        return path
    conn = db.connect(db_path)
    try:
        check_outcomes(conn)
        b = build_briefing(conn)
        path = out_dir / f"briefing_{b['day']}.md"
        path.write_text(b["markdown"])
        return path
    finally:
        conn.close()
