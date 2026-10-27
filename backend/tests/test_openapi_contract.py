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

    assert len(operation_ids) == 137  # 128 on main (+3 /api/horizons); +1 GET /api/metrics/runtime; +1 GET /api/horizons/long/picks; +2 POST /api/horizons/{horizon}/run, GET /api/horizons/{horizon}/picks/{sid}/{date}/{code}; +2 GET /api/admin/data-gateway, POST /api/admin/data-gateway/switch; +3 GET /api/horizons/{horizon}/trades|metrics|runs
    assert len(operation_ids) == len(set(operation_ids))
    assert missing == []
