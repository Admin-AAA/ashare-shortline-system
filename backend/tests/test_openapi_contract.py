from __future__ import annotations

from collections import Counter

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

    # 端点数会随功能增长：硬编码精确值每新增一个路由就假失败一次
    # （2026-11-14 实测 146 个 operationId 全部唯一，却被 `== 145` 判失败）。
    # 真正的不变量是「operationId 唯一」+「2xx 有类型化 schema」；数量只守下限防误删路由。
    # 2026-11-15 预设层退役：有意删除预设 CRUD 与预设矩阵端点，基线降至 142。
    assert len(operation_ids) >= 142
    duplicates = [oid for oid, n in Counter(operation_ids).items() if n > 1]
    assert not duplicates, f"重复 operationId: {duplicates}"
    assert missing == []
