"""收盘后把常驻进程的代码版本对齐到当前 HEAD（L-228）。

Prefect worker 与后端 API 都在启动时钉住 ASHARE_APPLICATION_REVISION；白天合进 main 的提交不会改变它们
声明的身份，当晚的回放重建、补跑都会因 manifest revision 不匹配失败，直到有人手动重启。
本脚本由 launchd 每个交易日 15:10 调一次：worker 声明的 revision 与 HEAD 不同、且没有在跑的任务时，
重启 worker 与后端；相同则什么都不做。只读 git，不改工作树。
回放重建（replay_all_days）在跑时不重启：批次里两条 run 之间有几秒空档，active_work_check 会报空闲，
worker 中途换版本会让批次后面的 run 全部因 manifest revision 不一致失败。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.deployment_identity import _git_head_revision, read_worker_identity  # noqa: E402

BACKEND = Path(__file__).resolve().parents[1]
WORKER_LABEL = "com.wangao.ashare-prefect-worker"
BACKEND_RESTART = BACKEND / "scripts" / "safe_backend_restart.sh"
REPLAY_PATTERN = "replay_all_days"


def _replay_campaign_pids() -> list[int] | None:
    """正在跑的回放重建进程；查不了返回 None（按「有」处理，宁可不重启）。

    2026-11-27 16:35 的定时安全重启在回放批次进行中等到 16:42:10，正好落在两条 run 之间的空档，
    active_work_check 报 idle 就重启了后端。后端重启不影响回放，worker 重启会。
    """
    try:
        out = subprocess.run(["pgrep", "-f", REPLAY_PATTERN], capture_output=True, text=True, check=False).stdout
    except OSError:
        return None
    return [int(x) for x in out.split() if x.isdigit() and int(x) != os.getpid()]


def main() -> int:
    head = _git_head_revision()
    ident = read_worker_identity()
    worker_rev = str((ident or {}).get("revision") or "")
    if not head:
        print("HEAD 不可读，跳过")
        return 0
    if worker_rev == head:
        print(f"worker 已是 HEAD {head[:12]}，无需重启")
        return 0
    replay = _replay_campaign_pids()
    if replay is None or replay:
        print(f"回放重建在跑（replay_all_days pid={replay if replay else '查不到'}），本次不重启；"
              "批次两条 run 之间的空档 active_work_check 也会报空闲，等批次跑完再跑本脚本")
        return 0
    rc = subprocess.call([sys.executable, "-m", "scripts.active_work_check"], cwd=str(BACKEND))
    if rc != 0:
        print(f"有任务在跑（active_work_check rc={rc}），本次不重启；下个交易日再试")
        return 0
    uid = subprocess.run(["id", "-u"], capture_output=True, text=True, check=True).stdout.strip()
    print(f"worker {worker_rev[:12] or '未知'} -> HEAD {head[:12]}，重启 worker 与后端")
    subprocess.run(["launchctl", "kickstart", "-k", f"gui/{uid}/{WORKER_LABEL}"], check=True)
    subprocess.run(["bash", str(BACKEND_RESTART)], check=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
