"""The connector plug. One class per client system (CSV files, Zoho Books, Shopify, GA4 ...).

Every connector says which data streams it can provide (see catalog/streams.toml) and implements:

    test(settings)                   -> (ok, message)      can we reach the system with these settings?
    sync(conn, settings, since=None) -> dict               scheduled pull of contract fields into the store
    fetch(settings, entity, key)     -> dict               live lookup for one record (no copy kept)
    act(settings, action, payload)   -> dict               change something in the client system
                                                           (only ever called by the actions module, after approval)

A connector that is planned but not built yet sets status = "planned" and raises NotImplementedError.
"""
from __future__ import annotations


class Connector:
    name: str = ""
    label: str = ""
    status: str = "ready"            # ready | planned
    streams: dict[str, str] = {}     # stream -> where it comes from in this system
    read_only: bool = True           # True until the connector is allowed to act
    settings_help: str = ""

    def test(self, settings: dict) -> tuple[bool, str]:
        raise NotImplementedError(f"{self.label}: not built yet")

    def sync(self, conn, settings: dict, since: str | None = None) -> dict:
        raise NotImplementedError(f"{self.label}: not built yet")

    def fetch(self, settings: dict, entity: str, key: str) -> dict:
        raise NotImplementedError(f"{self.label}: live lookups not built yet")

    def act(self, settings: dict, action: str, payload: dict) -> dict:
        raise NotImplementedError(f"{self.label}: actions not allowed (read-only connector)")
