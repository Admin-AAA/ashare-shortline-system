from app.schemas.common import JsonResponse
from app.services.single_stock_analysis import _json_safe


def test_pipeline_audit_tuples_become_json_value():
    raw = {"pipeline": {"pipeline_audit": {"aux": {"errors": (), "audit": ({"stage": "inspect"},)}}}}
    safe = _json_safe(raw)
    assert safe["pipeline"]["pipeline_audit"]["aux"]["errors"] == []
    JsonResponse.model_validate(safe)  # 原始 tuple 会让 ContractRouter 500
