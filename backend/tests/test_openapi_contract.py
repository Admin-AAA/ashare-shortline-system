from __future__ import annotations

from app.main import app


HTTP_METHODS = {"get", "post", "put", "patch", "delete"}


def test_every_operation_has_unique_id_and_typed_success_response() -> None:
    document = app.openapi()
    operation_ids: list[str] = []
    missing: list[str] = []
    for path, path_item in document["paths"].items():
        for method, operation in path_item.items():
            if method.lower() not in HTTP_METHODS:
                continue
            operation_ids.append(operation["operationId"])
            successes = [
                response
                for status, response in operation.get("responses", {}).items()
                if str(status).startswith("2")
            ]
            if not successes or any(
                not response.get("content", {}).get("application/json", {}).get("schema")
                for response in successes
            ):
                missing.append(f"{method.upper()} {path}")

    # 137 之后补上的入口：POST /api/admin/data-gateway/item-switch，
    # GET /api/strategies/rolling-r12 与 standalone-metrics，
    # POST /api/stock-matrix/analysis 与 /backtest。
    assert len(operation_ids) == 142
    assert len(operation_ids) == len(set(operation_ids))
    assert missing == []
