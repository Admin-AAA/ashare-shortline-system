"""Static guardrails for the closed, typed MarketSource boundary."""
from __future__ import annotations

import ast
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
PORT_PATH = BACKEND_ROOT / "app/services/market_source/port.py"
ADAPTER_CLASSES = {
    BACKEND_ROOT / "app/adapters/market_source/tushare.py": "TushareMarketSourceAdapter",
    BACKEND_ROOT / "app/services/market_source/in_memory_adapter.py": "InMemoryMarketSourceAdapter",
    BACKEND_ROOT / "app/services/market_source/local_pool_adapter.py": "LocalPoolMarketSourceAdapter",
}
PRODUCTION_BOUNDARY_PATHS = (
    PORT_PATH,
    *ADAPTER_CLASSES,
    BACKEND_ROOT / "app/services/market_pool_adapters.py",
    BACKEND_ROOT / "app/services/market_pool/core.py",
    BACKEND_ROOT / "app/services/data_gap_remediation_service.py",
)
_DIRECT_DAILY_TUSHARE_HELPERS = frozenset(
    {"fetch_basic_daily_kbars", "fetch_daily_kbars_for_trade_date"}
)


def _class_node(path: Path, class_name: str) -> ast.ClassDef:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == class_name
    )


def test_market_source_port_declares_only_the_two_typed_operations() -> None:
    port = _class_node(PORT_PATH, "MarketSourcePort")
    methods = [
        node.name
        for node in port.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]

    assert methods == ["fetch_quotes", "fetch_daily_bars"]


def test_market_source_adapters_expose_no_legacy_collection_method() -> None:
    for path, class_name in ADAPTER_CLASSES.items():
        adapter = _class_node(path, class_name)
        public_methods = [
            node.name
            for node in adapter.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and not node.name.startswith("_")
        ]
        assert public_methods == ["fetch_quotes", "fetch_daily_bars"], path


def test_production_market_boundary_has_no_legacy_helper_or_compatibility_import() -> None:
    for path in PRODUCTION_BOUNDARY_PATHS:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))

        assert "fetch_bars_for_quote" not in source, path
        assert "QuoteScope" not in source, path
        assert "DateRange" not in source, path
        assert "getattr(" not in source or path.name in {"core.py", "data_gap_remediation_service.py"}, path
        assert not any(
            isinstance(node, ast.ImportFrom)
            and node.module == "app.services.market_source.multi_source_adapter"
            for node in ast.walk(tree)
        ), path
        if path.name == "data_gap_remediation_service.py":
            assert not any(
                isinstance(node, ast.ImportFrom)
                and node.module == "app.services.tushare_client"
                and any(alias.name in _DIRECT_DAILY_TUSHARE_HELPERS for alias in node.names)
                for node in ast.walk(tree)
            ), path
