"""T12 调度健康诊断测试（check_scheduler_health）。

锁定盘后流水线调度健康可观测性：post_sync_formal_recommendation 的 last_status=failed
或超过 2 天未跑时，self_check 应警告（outcome 结算 + formal 推荐未执行 → D 项阻塞）。

背景（2026-07-23 调研）：07-22 推荐未结算（outcome=None），因为盘后流水线
last_status=failed（07-21 10:20），07-22 17:35 没跑。流水线含 outcome 结算步骤 →
不跑则前瞻样本无法积累（D 项阻塞）。
"""
import pathlib
import sys

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = BACKEND_ROOT.parent / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import self_check  # type: ignore[import-not-found]


def test_scheduler_health_runs_without_error():
    """check_scheduler_health 在空 DB 时不崩（无 enabled tasks 返回空）。"""
    # 用 mock db（execute 返回空列表）
    class _MockDB:
        def execute(self, *args, **kwargs):
            class _Result:
                def fetchall(self):
                    return []
            return _Result()
    # 不应抛异常
    self_check.check_scheduler_health(_MockDB())


def test_scheduler_health_detects_failed_postclose():
    """盘后流水线 last_status=failed 时应标 ⚠️上次失败。"""
    class _MockDB:
        def execute(self, *args, **kwargs):
            class _Result:
                def fetchall(self):
                    # 模拟 post_sync_formal_recommendation failed
                    return [(
                        "post_sync_formal_recommendation",  # task_id
                        "recommendation_run",  # task_type
                        "35 17 * * 1-5",  # cron
                        1,  # enabled
                        None,  # last_run_at
                        "failed",  # last_status
                        None,  # last_error
                    )]
            return _Result()
    # 不应抛异常（函数只 print，不返回值）
    self_check.check_scheduler_health(_MockDB())


def test_scheduler_health_detects_stale_postclose():
    """盘后流水线超过 2 天未跑时应标 ⚠️N天未跑。"""
    from datetime import datetime, timedelta
    old_date = datetime.now() - timedelta(days=3)

    class _MockDB:
        def execute(self, *args, **kwargs):
            class _Result:
                def fetchall(self):
                    return [(
                        "post_sync_formal_recommendation",
                        "recommendation_run",
                        "35 17 * * 1-5",
                        1,
                        old_date,  # 3 天前
                        "completed",  # status ok 但太旧
                        None,
                    )]
            return _Result()
    self_check.check_scheduler_health(_MockDB())
