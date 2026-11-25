"""过滤审计快照独立表 recommendation_run_filter_audits 的读写往返。"""
from __future__ import annotations

import json
import zlib

from app.models.tables import RecommendationRun, RecommendationRunFilterAudit
from app.services.runs.audit_payload import decode_audit_payload, encode_audit_payload
from app.services.workbench_service import get_run_filter_audit, store_filter_audit


def _run(run_id: str) -> RecommendationRun:
    return RecommendationRun(
        run_id=run_id,
        run_name="audit storage",
        trade_date="2099-03-02",
        status="completed",
        params_json="{}",
        market_snapshot_json="{}",
        result_summary_json="{}",
    )


def test_store_and_read_filter_audit_roundtrip(db):
    run_id = "__test_audit_roundtrip__"
    db.add(_run(run_id))
    db.flush()

    store_filter_audit(db, run_id, {"empty_reason": "first", "rows": []})
    db.flush()
    assert get_run_filter_audit(db, run_id)["empty_reason"] == "first"

    store_filter_audit(db, run_id, {"empty_reason": "second", "rows": []})
    db.flush()
    assert get_run_filter_audit(db, run_id)["empty_reason"] == "second"
    assert db.query(RecommendationRunFilterAudit).filter_by(run_id=run_id).count() == 1


def test_missing_audit_row_returns_fallback_dict(db):
    run_id = "__test_audit_missing__"
    db.add(_run(run_id))
    db.flush()

    audit = get_run_filter_audit(db, run_id)

    assert isinstance(audit, dict)


def test_unknown_run_returns_none(db):
    assert get_run_filter_audit(db, "__test_audit_no_such_run__") is None


def test_audit_payload_roundtrip_through_zlib():
    audit = {
        "stage_summary": [{"stage": "score_line", "input_count": 3, "pass_count": 0}],
        "rows": [{"code": "000001", "final_reason": "最终分 72.5 低于有效线 78.0"}],
        "empty_reason": "production_policy_blocked: 3/3",
    }
    blob = encode_audit_payload(audit)
    assert isinstance(blob, bytes)
    assert zlib.decompress(blob).decode("utf-8") == json.dumps(audit, ensure_ascii=False)
    assert decode_audit_payload(blob) == audit


def test_audit_payload_compresses_repeated_rows():
    """同构逐票对象是审计体积的来源，压缩后必须显著小于原文。"""
    row = {
        "code": "000001", "name": "平安银行", "industry": "银行",
        "final_stage": "skill_gate", "final_status": "filtered",
        "final_reason": "规则门分 2.0 低于 58.0",
        "trace": [{"stage": "prefilter_common", "passed": True, "reason": "公共可交易过滤通过"}],
    }
    audit = {"rows": [dict(row, code=f"{i:06d}") for i in range(500)]}
    raw = json.dumps(audit, ensure_ascii=False).encode("utf-8")
    blob = encode_audit_payload(audit)
    assert len(blob) < len(raw) * 0.2
    assert decode_audit_payload(blob) == audit


def test_audit_payload_empty_values():
    assert encode_audit_payload({}) == b""
    assert decode_audit_payload(b"") == {}
    assert decode_audit_payload(None) == {}


def test_stored_row_holds_compressed_bytes(db):
    run_id = "__test_audit_compressed__"
    db.add(_run(run_id))
    db.flush()
    audit = {"empty_reason": "x", "rows": [{"code": "600000"}]}

    store_filter_audit(db, run_id, audit)
    db.flush()

    stored = db.get(RecommendationRunFilterAudit, run_id)
    assert isinstance(stored.payload_z, bytes)
    assert zlib.decompress(stored.payload_z) == json.dumps(audit, ensure_ascii=False).encode("utf-8")
    # 读接口会补齐缺失的 trace（upgrade_filter_audit 的既有行为），只校验代码与压缩存储
    assert [r["code"] for r in get_run_filter_audit(db, run_id)["rows"]] == ["600000"]
