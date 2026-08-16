"""Canonical Tushare initialization for maintenance scripts.

All upstream authentication, routing, limiting, and failover are owned by
``app.services.tushare_client``.  Scripts must not carry their own credentials.
"""

import tushare as ts

from app.services.tushare_client import get_pro as _get_pro


def get_pro():
    """Return the shared multi-provider Tushare client."""
    return _get_pro()


def get_pro_bar(ts_code: str, adj: str = "qfq", **kwargs):
    """Run ``tushare.pro_bar`` through the canonical routed query facade."""
    return ts.pro_bar(api=get_pro(), ts_code=ts_code, adj=adj, **kwargs)
