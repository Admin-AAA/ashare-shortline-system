from app.services.runs.filter_audit import build_filter_audit


def test_filter_audit_marks_unreachable_score_lines_as_data_quality_blocked() -> None:
    cards = [
        {
            "code": "000001",
            "name": "样本一",
            "final_score": 71.0,
            "score_breakdown": {"quality_cap": 72.0},
            "score_line_meta": {"effective_score_line": 89.0, "passed": False},
            "data_quality": {"missing_fields": ["capital_flow", "dragon_tiger"]},
        },
        {
            "code": "000002",
            "name": "样本二",
            "final_score": 70.0,
            "score_breakdown": {"quality_cap": 76.0},
            "score_line_meta": {"effective_score_line": 95.0, "passed": False},
            "data_quality": {"missing_fields": ["capital_flow"]},
        },
    ]

    audit = build_filter_audit(
        prefilter_audit={"rows": []},
        skill_gate_audit={"rows": []},
        scored_cards=cards,
        recommended_cards=[],
        pipeline={},
    )

    assert audit["empty_reason"].startswith("data_quality_blocked:")
    assert "2/2" in audit["empty_reason"]
    assert "capital_flow" in audit["empty_reason"]


def test_filter_audit_accepts_hard_veto_card_without_effective_score_line() -> None:
    audit = build_filter_audit(
        prefilter_audit={"rows": []},
        skill_gate_audit={"rows": []},
        scored_cards=[
            {
                "code": "000003",
                "name": "硬否决样本",
                "final_score": 0,
                "hard_veto": True,
                "veto_reason": "系统性恐慌禁入",
                "score_line_meta": {},
            }
        ],
        recommended_cards=[],
        pipeline={},
    )

    score_line = audit["rows"][0]["score_line"]
    assert score_line["effective_score_line"] is None
    assert score_line["reason"] == "hard_veto: 系统性恐慌禁入"
