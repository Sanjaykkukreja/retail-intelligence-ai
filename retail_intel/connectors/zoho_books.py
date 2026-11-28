"""Zoho Books (India data centre: https://www.zohoapis.in/books/v3). PLANNED - build phase 2.

Plan: OAuth with read-only scopes first; incremental sync by last_modified_time; respect 100 calls/minute and the
plan's daily cap (1,000 Free to 10,000 Premium); live fetch for single records; act() only for drafts (L2) later.
"""
from __future__ import annotations

from ..platform.connector import Connector
from ..platform.registry import connector


@connector
class ZohoBooks(Connector):
    name = "zoho_books"
    label = "Zoho Books"
    status = "planned"
    settings_help = "organization_id, data centre (in), OAuth client - kept in Secrets, never in files"
    streams = {
        "invoices": "/invoices",
        "order_lines": "/invoices/{id} line items",
        "refunds": "/creditnotes",
        "products": "/items",
        "purchase_orders": "/purchaseorders",
        "bills": "/bills",
        "vendor_payments": "/vendorpayments",
    }
