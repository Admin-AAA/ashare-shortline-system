from pathlib import Path
import re


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_prompt_names_single_production_decision_function():
    prompt = (PROJECT_ROOT / ".claude/commands/self-improve.md").read_text(
        encoding="utf-8"
    )

    assert "必须调用 `evaluate_challenger()`" in prompt
    # 唯一使用方式是 /loop 多窗口直接调用，自己生成 researcher_id。
    assert "多窗口 `/loop` 直接调用是本项目唯一使用方式" in prompt
    assert "si-<当前时间戳>" in prompt
    # 状态记录只走 publish-champion/publish-finding，禁止直接写状态文件；
    # 操作契约已抽到 OPERATIONS.md。
    assert "禁止直接写状态文件" in prompt
    assert "OPERATIONS.md" in prompt


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
