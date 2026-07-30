from pathlib import Path
import re


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_prompt_names_single_production_decision_function():
    prompt = (PROJECT_ROOT / ".claude/commands/self-improve.md").read_text(
        encoding="utf-8"
    )

    assert "必须调用 `evaluate_challenger()`" in prompt
    assert "SELF_IMPROVE_ISOLATED_RUN=1" in prompt
    assert "self_improve_state.py commit" in prompt


def test_legacy_experiments_do_not_assign_shadow_or_observe_decisions():
    forbidden = re.compile(
        r"(?:decision|verdict)\s*=\s*[\"'](?:SHADOW|OBSERVE)[\"']"
    )
    scripts = PROJECT_ROOT / "backend/scripts"

    offenders = [
        path.name
        for path in scripts.glob("si_*.py")
        if forbidden.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []
