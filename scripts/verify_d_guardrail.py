#!/usr/env python3
"""D 项北极星回归护栏 — 可独立运行的 CI 闸门。

背景（第 178 轮 self-improve）：
  系统已进入稳定期，D 项（回测胜率/盈亏比/最大亏损）是唯一核心攻击目标，当前已强于
  北极星阈值（全量净胜率 75%+、净盈亏比 2.9、最大亏损 ~-5.9%）。但多个 self-improve
  实例并行编辑 strategy.yaml、不断加脆弱的「毒区」过滤器——这是真实的过拟合风险：
  单看样本内胜率刷高，却可能砍掉前瞻样本里的有效信号，损害整体 D 项。

  本脚本把北极星 D 项目标固化为硬闸门：任何改动若使 D 项退到阈值以下，脚本非零退出，
  CI / 编排可据此阻断合并。与 self_check.py 共用同一套纯函数（_fetch_dedup_live_formal
  + _compute_d_cost），口径与生产一致。

用法（在 worktree / 主仓均可）：
    cd backend && source .venv/bin/activate && python3 ../scripts/verify_d_guardrail.py
    python3 ../scripts/verify_d_guardrail.py --json output/d_guardrail_report.json   # 机器可读报告
退出码：0=达标，1=任意阈值被突破（回归），2=样本不足无法判定。

--json 说明：默认仅打印 stdout（行为向后兼容）；加 --json 才落盘 JSON 报告。报告写入
output/（已被 .gitignore 忽略），不进版本库，供跨 run 漂移追踪（慢退化单点阈值查不出）。
"""
import argparse
import datetime
import json
import os
import sys

BACKEND_DIR = os.path.join(os.path.dirname(__file__), '..', 'backend')
sys.path.insert(0, os.path.abspath(BACKEND_DIR))
sys.path.insert(0, os.path.abspath(os.path.join(BACKEND_DIR, '..', 'scripts')))

from app.db.session import SessionLocal
import self_check  # type: ignore[import-not-found]

# —— 北极星 D 项目标阈值（硬闸门）——
NET_WINRATE_FLOOR = 65.0
NET_PL_RATIO_FLOOR = 2.0
MAX_LOSS_FLOOR = -6.5          # 即 max_loss 不得低于 -6.5%
FORWARD_NET_WINRATE_FLOOR = 60.0

# 默认报告落盘路径（output/ 已被 .gitignore 忽略，不会进版本库）
REPORT_DEFAULT_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), '..', 'output', 'd_guardrail_report.json')
)


def _check(name: str, ok: bool, actual, floor) -> bool:
    status = "✅ PASS" if ok else "❌ FAIL"
    print(f"  [{status}] {name}: 实测 {actual}  (阈值 {'>=' if floor is not None else ''}{floor})")
    return ok


def build_report(full: dict | None, fwd: dict | None, ok: bool, exit_code: int) -> dict:
    """把本轮 D 项护栏结果结构化为机器可读报告，供跨 run 漂移追踪。

    full / fwd 为 self_check._compute_d_cost 的返回 dict（含 n/net_wr/net_pl/max_loss）。
    只萃取 D 项护栏关心的四个字段，剔除噪声字段，避免报告随实现细节漂移。
    """
    def _block(m):
        return None if m is None else {
            "n": m["n"],
            "net_wr": m["net_wr"],
            "net_pl": m["net_pl"],
            "max_loss": m["max_loss"],
        }
    return {
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "thresholds": {
            "net_winrate_floor": NET_WINRATE_FLOOR,
            "net_pl_ratio_floor": NET_PL_RATIO_FLOOR,
            "max_loss_floor": MAX_LOSS_FLOOR,
            "forward_net_winrate_floor": FORWARD_NET_WINRATE_FLOOR,
        },
        "full": _block(full),
        "forward": _block(fwd),
        "passed": bool(ok),
        "exit_code": int(exit_code),
    }


def write_report(report: dict, path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(description="D 项北极星回归护栏")
    parser.add_argument(
        "--json", nargs="?", const=REPORT_DEFAULT_PATH, default=None,
        help="写出机器可读 JSON 报告；不指定路径则用默认 output/d_guardrail_report.json",
    )
    args = parser.parse_args(argv)

    db = SessionLocal()
    rows = self_check._fetch_dedup_live_formal(db)
    db.close()

    if not rows:
        print("⚠️ D 项护栏：无 LIVE formal 样本，无法判定（退出码 2）。")
        report = build_report(None, None, False, 2)
        if args.json:
            write_report(report, args.json)
            print(f"📄 JSON 报告已写出: {args.json}")
        return 2

    full = self_check._compute_d_cost(rows)
    fwd = self_check._compute_d_cost(rows, since_date=self_check.FORWARD_EPOCH)

    print(f"D 项回归护栏（样本 全量 n={full['n']}"
          + (f" / 前瞻 n={fwd['n']}" if fwd else " / 前瞻 空")
          + "）")
    print("-" * 60)

    ok = True
    ok &= _check("全量净胜率", full["net_wr"] >= NET_WINRATE_FLOOR,
                 f"{full['net_wr']}%", NET_WINRATE_FLOOR)
    ok &= _check("全量净盈亏比", full["net_pl"] >= NET_PL_RATIO_FLOOR,
                 full["net_pl"], NET_PL_RATIO_FLOOR)
    ok &= _check("全量最大亏损(越接近0越好)", full["max_loss"] >= MAX_LOSS_FLOOR,
                 f"{full['max_loss']}%", MAX_LOSS_FLOOR)
    if fwd is not None:
        ok &= _check("前瞻净胜率(样本外)", fwd["net_wr"] >= FORWARD_NET_WINRATE_FLOOR,
                     f"{fwd['net_wr']}%", FORWARD_NET_WINRATE_FLOOR)
        ok &= _check("前瞻净盈亏比", fwd["net_pl"] >= NET_PL_RATIO_FLOOR,
                     fwd["net_pl"], NET_PL_RATIO_FLOOR)
    else:
        print("  [⚠️ SKIP] 前瞻窗口样本为空——无法验证样本外，请等修复后新 run 结算后复核。")

    print("-" * 60)
    if ok:
        print("✅ D 项护栏全部达标：未检测到对北极星 D 项的回归。")
    else:
        print("❌ D 项护栏触发：检测到对北极星 D 项的回归，阻断合并并排查最近 strategy.yaml 改动。")

    exit_code = 0 if ok else 1
    report = build_report(full, fwd, ok, exit_code)
    if args.json:
        write_report(report, args.json)
        print(f"📄 JSON 报告已写出: {args.json}")
    return exit_code


if __name__ == '__main__':
    sys.exit(main())
