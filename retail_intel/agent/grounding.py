"""Response synthesis check: every figure in the answer must appear in a tool result (or the question).

This is the guard against the most damaging agent failure for a business owner: a confident, invented number.
Numbers like 'Rs 1.58 L', '1,57,894', '48%' are normalised and compared with tolerance for rounding.
"""
from __future__ import annotations

import re

_MON = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*"
# Dates are not figures: 2026-10-27, 27/10/2026, "27 Oct", "14 to 20 Sep 2026", "Sep 14". (Live run 27 Nov flagged "27 Oct".)
_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b"
                   r"|\b\d{1,2}(?:\s*(?:-|\u2013|to|and)\s*\d{1,2})?\s+" + _MON + r"\b(?:\s+\d{4})?"
                   r"|\b" + _MON + r"\s+\d{1,2}\b(?:,?\s+\d{4})?", re.I)
_NUM = re.compile(r"(?<![\w.#])(\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*(%|crores?|cr\b|lakhs?|l\b|k\b)?", re.I)
_SCALE = {"cr": 1e7, "crore": 1e7, "crores": 1e7, "l": 1e5, "lakh": 1e5, "lakhs": 1e5, "k": 1e3}


def _numbers(text: str) -> list[tuple[str, float, float]]:
    """(raw, value, tolerance) for each figure."""
    out = []
    for m in _NUM.finditer(_DATE.sub(" ", text)):
        raw, unit = m.group(1), (m.group(2) or "").lower()
        v = float(raw.replace(",", ""))
        decimals = len(raw.split(".")[1]) if "." in raw else 0
        scale = _SCALE.get(unit, 1.0)
        out.append((m.group(0).strip(), v * scale, 0.5 * 10 ** -decimals * scale))
    return out


def _pool(texts: list[str]) -> list[float]:
    vals = []
    for t in texts:
        for m in re.finditer(r"-?\d+(?:\.\d+)?", _DATE.sub(" ", t.replace(",", ""))):
            vals.append(abs(float(m.group(0))))
    return vals


def unsupported_numbers(answer: str, evidence_texts: list[str]) -> list[str]:
    pool = _pool(evidence_texts)
    bad = []
    for raw, v, tol in _numbers(answer):
        if v <= 12 and float(v).is_integer():       # list numbering, "3 days", "top 5": too noisy to police
            continue
        if 1900 <= v <= 2100 and float(v).is_integer():
            continue
        ok = any(abs(v - p) <= max(tol, 0.006 * abs(p)) or abs(v - 100 * p) <= max(tol, 0.006 * abs(100 * p))
                 for p in pool)
        if not ok:
            bad.append(raw)
    return bad
