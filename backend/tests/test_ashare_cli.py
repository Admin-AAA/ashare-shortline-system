from __future__ import annotations

import json

from scripts import ashare_cli


def test_main_accepts_pretty_after_subcommand(monkeypatch):
    calls = {}

    def fake_with_db(handler, args):
        calls["command"] = args.command
        calls["pretty"] = args.pretty
        return 0

    monkeypatch.setattr(ashare_cli, "_with_db", fake_with_db)

    exit_code = ashare_cli.main(["presets", "--active-only", "--pretty"])

    assert exit_code == 0
    assert calls == {"command": "presets", "pretty": True}


def test_emit_outputs_valid_json(capsys):
    ashare_cli._emit({"ok": True, "名称": "预设"}, pretty=True)

    captured = capsys.readouterr().out
    assert json.loads(captured) == {"ok": True, "名称": "预设"}
    assert "\n  " in captured


def test_compact_recommendation_keeps_fields_needed_by_agent():
    result = ashare_cli._compact_recommendation(
        {
            "recommendation_id": "rec_1",
            "run_id": "run_1",
            "code": "000001",
            "name": "平安银行",
            "final_score": 88.5,
            "watch_status": "watching",
            "feature_snapshot": {"ideal_buy": 10.1, "stop_loss": 9.6},
            "rationale": "放量突破",
            "risk_tags": ["高位"],
        }
    )

    assert result["recommendation_id"] == "rec_1"
    assert result["code"] == "000001"
    assert result["ideal_buy"] == 10.1
    assert result["stop_loss"] == 9.6
    assert result["rationale"] == "放量突破"
    assert result["risk_tags"] == ["高位"]


def test_new_data_sync_and_integrity_commands_exist():
    parser = ashare_cli.build_parser()
    cases = [
        ["data-sources"],
        ["pool-status"],
        ["refresh-pool"],
        ["sync-pool-gaps"],
        ["sync-run-aux", "run_1"],
        ["data-health"],
        ["alerts"],
        ["readiness"],
        ["matrix-run"],
        ["matrix-result"],
        ["matrix-status", "matrix_1"],
        ["matrix-wait", "matrix_1"],
    ]
    for argv in cases:
        ns = parser.parse_args(argv)
        assert ns.command == argv[0]


def test_matrix_overlap_counts_cross_preset_recommendations():
    rows = [
        {
            "preset_name": "陈小群",
            "top_recommendations": [
                {"code": "000001", "name": "平安银行", "final_score": 80},
                {"code": "600519", "name": "贵州茅台", "final_score": 75},
            ],
        },
        {
            "preset_name": "炒股养家",
            "top_recommendations": [
                {"code": "000001", "name": "平安银行", "final_score": 82},
            ],
        },
    ]

    overlap = ashare_cli._matrix_overlap(rows)

    assert overlap == [
        {
            "code": "000001",
            "name": "平安银行",
            "count": 2,
            "presets": ["陈小群", "炒股养家"],
            "max_score": 82.0,
        }
    ]


def test_escape_md_keeps_table_cells_valid():
    assert ashare_cli._escape_md("a|b\nc") == "a\\|b c"
