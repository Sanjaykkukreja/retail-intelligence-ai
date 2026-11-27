"""Backward-compatible entry point. The real agent lives in orchestrator.py."""
from __future__ import annotations

from .models import make_model
from .orchestrator import Agent


def ask(question: str, db_path: str, session_id: str | None = None) -> str:
    return Agent.from_env(db_path).ask(question, session_id).answer
