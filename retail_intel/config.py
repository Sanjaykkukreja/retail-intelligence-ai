"""Central settings. Change numbers here, not inside the code."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
SOURCE_DIR = DATA_DIR / "source"      # the three "client systems" (CSV exports)
TRUTH_DIR = DATA_DIR / "truth"        # what the generator planted (used by tests only)
DB_PATH = DATA_DIR / "glowbasket.db"
OVERRIDES_PATH = DATA_DIR / "sku_map_overrides.csv"   # human-approved SKU mappings

# Default business rules. They live in the business_context table so the
# owner can change them without touching code.
DEFAULT_CONTEXT = {
    "gross_margin_target_pct": 30.0,   # gross margin (after product cost, discounts, refunds) the owner wants to protect
    "slow_mover_days_of_supply": 120, # above this a SKU is "slow"
    "low_stock_days_of_supply": 7,    # below this a SKU needs a reorder now
    "expiry_horizon_days": 90,        # batches expiring within this window are watched
    "dispatch_sla_hours": 48,         # order-to-dispatch promise
    "markdown_elasticity": 1.5,       # ASSUMPTION: +1.5% units per +1% discount (untested)
    "lookback_days": 28,              # window used for run-rates
}


@dataclass(frozen=True)
class SynthConfig:
    """Size and dates of the pretend business, GlowBasket."""
    seed: int = 42
    n_skus: int = 200
    target_orders: int = 30000
    start: date = date(2025, 11, 24)
    end: date = date(2026, 11, 23)
