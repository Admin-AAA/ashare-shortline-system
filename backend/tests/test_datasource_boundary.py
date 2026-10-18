"""Data-fetch boundary: provider SDKs / HTTP clients only inside app/services/datasource.

AST based (includes function-local imports).  LEGACY lists modules that still fetch
directly and are wrapped via ``gw_call``/adapters; the list may only shrink.
Mirrors the ``datasource boundary`` forbidden contract in .importlinter.
"""
import ast
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
BANNED = ("tushare", "baostock", "akshare", "httpx", "requests", "efinance", "mootdx", "pytdx")
ALLOWED_DIRS = ("app/services/datasource/",)
NON_MARKET = {"app/services/model_router_service.py"}  # LLM HTTP, not a market data provider
LEGACY = {
    "app/services/cninfo_news.py",
    "app/services/eastmoney_news.py",
    "app/services/market_auxiliary.py",
    "app/services/market_data.py",
    "app/services/tushare_client.py",
    "app/services/tushare_transport.py",
    "app/services/xiaoshi_client.py",
    "app/services/xiaoshi_minute_source.py",
}


def _mods(path):
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            yield node.module.split(".")[0]
        elif isinstance(node, ast.Import):
            for a in node.names:
                yield a.name.split(".")[0]


def test_no_direct_provider_calls_outside_datasource():
    offenders = set()
    for f in APP.rglob("*.py"):
        rel = f.relative_to(APP.parent).as_posix()
        if rel.startswith(ALLOWED_DIRS) or rel in NON_MARKET:
            continue
        if any(m in BANNED for m in _mods(f)):
            offenders.add(rel)
    assert offenders - LEGACY == set(), "new direct provider call: add an adapter under app/services/datasource"
    assert LEGACY - offenders == set(), "legacy module migrated: shrink LEGACY"
