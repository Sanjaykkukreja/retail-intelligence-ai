"""Registries: the plug points of the platform.

Core code never imports a specific tool, KPI or connector. Plug-in modules register themselves here with a
decorator, and `load_plugins()` imports every module in the plug-in packages. To add a tool, KPI or
connector you add a file; you never edit core code.

    @tool("stock_position", "Units on hand and days of cover per SKU", {...})
    def stock_position(conn, ...): ...

    @kpi("ongoing_stockouts", "SKUs out of stock now", unit="count", better="down")
    def ongoing_stockouts(conn): ...

    @connector
    class CsvFiles(Connector): ...
"""
from __future__ import annotations

import importlib
import pkgutil
from dataclasses import dataclass
from typing import Callable

# Plug-in packages are listed as names (configuration), not imported here.
PLUGIN_PACKAGES = ["retail_intel.tools", "retail_intel.kpis", "retail_intel.connectors"]


class Registry:
    def __init__(self, kind: str):
        self.kind = kind
        self._items: dict[str, object] = {}

    def add(self, name: str, item):
        if name in self._items and self._items[name] is not item:
            raise ValueError(f"{self.kind} '{name}' is registered twice")
        self._items[name] = item
        return item

    def get(self, name: str):
        if name not in self._items:
            raise KeyError(f"unknown {self.kind} '{name}'. Available: {sorted(self._items)}")
        return self._items[name]

    def names(self) -> list[str]:
        return list(self._items)

    def items(self):
        return list(self._items.items())

    def __contains__(self, name) -> bool:
        return name in self._items

    def __len__(self) -> int:
        return len(self._items)


TOOLS = Registry("tool")
KPIS = Registry("kpi")
CONNECTORS = Registry("connector")


def tool(name: str, description: str, props: dict | None = None, required: tuple | list = (), *, tier: int = 0,
         cacheable: bool = True, side_effect: bool = False):
    """Register fn(conn, **args) as an agent tool.

    tier         0 = tested metric, 1 = free-form fallback (offered only when the specific tools fail)
    cacheable    read-only results are cached per data version
    side_effect  writes something; never cached
    """
    def deco(fn: Callable):
        TOOLS.add(name, dict(name=name, description=description, fn=fn, tier=tier,
                             cacheable=cacheable and not side_effect, side_effect=side_effect,
                             input_schema=dict(type="object", properties=props or {}, required=list(required))))
        return fn
    return deco


@dataclass
class KpiSpec:
    name: str
    label: str
    unit: str            # inr | count | pct | hours | units
    better: str          # up | down
    description: str
    fn: Callable

    def value(self, conn):
        v = self.fn(conn)
        return None if v is None else float(v)


def kpi(name: str, label: str, unit: str = "count", better: str = "down", description: str = ""):
    """Register fn(conn) -> number as a dashboard KPI."""
    def deco(fn: Callable):
        KPIS.add(name, KpiSpec(name, label, unit, better, description or (fn.__doc__ or "").strip(), fn))
        return fn
    return deco


def connector(cls):
    """Register a Connector subclass under its `name`."""
    CONNECTORS.add(cls.name, cls)
    return cls


_loaded = False


def load_plugins() -> None:
    """Import every module in the plug-in packages once. New files are picked up automatically."""
    global _loaded
    if _loaded:
        return
    for pkg_name in PLUGIN_PACKAGES:
        pkg = importlib.import_module(pkg_name)
        for m in sorted(pkgutil.iter_modules(pkg.__path__), key=lambda m: m.name):
            if not m.name.startswith("_"):
                importlib.import_module(f"{pkg_name}.{m.name}")
    _loaded = True
