"""策略收益质量与组合口径指标（纯计算，无 IO、无副作用）。

背景（2026-11-15 评审）：系统原有指标是「每笔均值 / 胜率 / PF / 单笔 MDD」，
这个口径无法区分「稳定小赚」和「靠少数几笔翻回来」，也看不到组合层风险
（同日多票收益离散度只有全样本的 0.44~0.63，等于同一个赌注下多注）。

本模块只做两件事，且**只增字段、不改既有字段**：
1. `quality_metrics`  收益质量：去两端均值、前 10 笔利润占比、尾部离散；
2. `portfolio_metrics` 组合口径：按信号日等权日序列，算组合回撤 / 年化 / Calmar / Sharpe / 平均在手仓位。

口径约定：
- 收益单位与调用方一致（通常为「百分比」，即 4.0 表示 +4%）。
- **组合模型 = 槽位滚动组合（slot rolling），不是每日满仓再平衡。**
  把资金等分成 N 份（N = 持有天数），每个信号日投入 1 份买入当天入选的票
  （批内等权），持有 N 个交易日后该份自动到期卖出。稳态下资金始终满仓，
  在手批次数 ≈ N，在手笔数 ≈ N × 每日票数。
  组合日收益 r_t = 当天「所有未到期批次」的日均收益的等权均值，
  因为每批恰好占 1/N 资金。这样仓位多少不会放大收益率：
  100 只在手只是 20 批 × 5 只，每只权重 1/100，与 20 只在手的口径一致。
- 每笔的总收益按持有天数做**几何**均摊成日均收益：
  daily = (1 + ret/100)^(1/days) - 1。持有期内均匀分布是近似，
  忽略路径形状（同一笔的日内先后顺序不影响组合日序列之外的统计）。
- 时间轴 = 信号日集合（中线/短线基本覆盖全部交易日；信号日稀疏时年化波动率会偏高，
  用 `date_density` 字段可判断轴是否稠密）。
- 样本不足时返回 None 而不是 0，避免把「没数据」误读成「零收益」。
"""
from __future__ import annotations

from datetime import date
from statistics import median, pstdev
from typing import Any, Iterable, Mapping, Sequence

# 兼容三套调用方：horizon_analytics 用 signal_date/ret，回放研究用 dt/net，
# 组合诊断脚本用 signal_date/net_pct。
_DATE_KEYS = ("signal_date", "dt", "entry_date")
_RET_KEYS = ("ret", "net", "net_pct", "net_return_pct")
_DAYS_KEYS = ("days", "hold_days", "holding_days")

# 去两端需要的样本量；低于此值不做 trim（否则丢两笔就改变分布形状）
_TRIM_MIN_N = 20


def _pick(row: Mapping[str, Any], keys: Sequence[str]) -> Any:
    for k in keys:
        v = row.get(k)
        if v is not None:
            return v
    return None


def _r(x: float | None, nd: int = 2) -> float | None:
    return None if x is None else round(float(x), nd)


def quality_metrics(rets: Sequence[float], *, trim_pct: float = 0.05) -> dict[str, Any]:
    """收益质量。输入是每笔收益（百分比），顺序无关。

    返回字段：
    - `trim_avg_ret`：去掉最大 / 最小各 trim_pct 后的均值。与 avg 差得越远，
      说明收益越依赖尾部；转负即「去掉极端交易后不赚钱」。
    - `top10_profit_share_pct`：最大的 10 笔之和 / **净利润** × 100。
      >100% 表示这 10 笔赚的比最终利润还多（其余净亏），即彩票型。
      净利润 ≤0 时无意义，返回 None（此时应直接看 trim_avg_ret < 0）。
    - `top10_share_of_gross_pct`：最大的 10 笔之和 / **全部盈利之和** × 100。
      这是纯集中度，恒在 0~100%，用于区分「利润厚」与「利润薄但同样集中」。
    - `median_ret`：中位数。明显高于 avg 时，均值被左尾大亏拖累。
    - `ret_std` / `worst_ret` / `best_ret`：尾部形状。
    """
    vals = [float(x) for x in rets if x is not None]
    n = len(vals)
    if n == 0:
        return {
            "trim_avg_ret": None,
            "top10_profit_share_pct": None,
            "top10_share_of_gross_pct": None,
            "median_ret": None,
            "ret_std": None,
            "worst_ret": None,
            "best_ret": None,
        }
    desc = sorted(vals, reverse=True)
    if n >= _TRIM_MIN_N:
        k = max(1, int(round(n * trim_pct)))
        trimmed = desc[k : n - k] if n - 2 * k > 0 else desc
    else:
        trimmed = desc
    gross_pos = sum(v for v in vals if v > 0)
    net = sum(vals)
    top10 = sum(desc[: min(10, n)])
    return {
        "trim_avg_ret": _r(sum(trimmed) / len(trimmed)),
        # 对净利润取占比：只有这样才能识别「前 10 笔赚的比总利润还多」
        "top10_profit_share_pct": _r(100 * top10 / net, 1) if net > 0 else None,
        # 对盈利总额取占比：纯集中度，恒 ≤100%
        "top10_share_of_gross_pct": _r(100 * top10 / gross_pos, 1) if gross_pos > 0 else None,
        "median_ret": _r(median(vals)),
        "ret_std": _r(pstdev(vals), 2) if n > 1 else None,
        "worst_ret": _r(min(vals)),
        "best_ret": _r(max(vals)),
    }


def portfolio_metrics(
    rows: Iterable[Mapping[str, Any]],
    *,
    holding_days: int | None = None,
    target_mdd_pct: float = 20.0,
    periods_per_year: int = 252,
) -> dict[str, Any]:
    """槽位滚动组合口径。rows 需含日期与收益；可用信号日 < 5 时返回 {}（不产生字段）。

    模型：资金等分 N 份（N = 持有天数），每个信号日投 1 份买入当天入选票（批内等权），
    持有 N 个交易日后到期卖出。组合日收益 = 当天未到期各批日均收益的等权均值。

    返回字段：
    - `portfolio_total_ret_pct` / `portfolio_mdd_pct` / `portfolio_ann_ret_pct`
    - `portfolio_ann_vol_pct` / `portfolio_sharpe` / `portfolio_calmar`
    - `max_concurrent_batches`：槽位数（覆盖计数峰值，正常应等于持有天数）。
      用它算利用率，不用均值——均值被首尾爬坡期拉低，会把正常运行误报成闲置。
    - `capital_utilization_pct`：资金利用率 = 槽位数 / 平均持有天数 × 100。
      <100% 说明有空槽（信号不足，钱在闲置）；>100% 说明存在额外重叠。
    - `avg_concurrent_positions`：平均在手笔数，只作容量诊断，**不参与**收益计算。
    - `holding_days_std`：持有期标准差。>0 说明汇总了不同持有期的策略，
      此时「每批 = 1/N 资金」的语义会在策略间漂移，解读组合收益需注意。
    - `date_density`：信号日数 / 自然日跨度 × 365，判断时间轴是否稠密（≈250 为日频）。
    - `scaled_ann_ret_pct`：按 target_mdd 缩放到目标回撤后的年化，用于跨策略比较。
    """
    # 1) 按信号日分批；批内每笔几何均摊成日均收益
    batches: dict[str, list[float]] = {}   # date -> 每笔的日均收益（百分比）
    batch_len: dict[str, list[float]] = {}  # date -> 每笔的持有天数
    for r in rows:
        d = _pick(r, _DATE_KEYS)
        ret = _pick(r, _RET_KEYS)
        if d is None or ret is None:
            continue
        key = str(d)[:10]
        hd = _pick(r, _DAYS_KEYS)
        if hd is None:
            hd = holding_days
        days = max(int(hd), 1) if hd is not None else 1
        f = 1 + float(ret) / 100.0
        # 几何均摊：跌破 -100% 时无意义，截到 0 附近避免复数
        daily = ((f ** (1.0 / days)) - 1) * 100 if f > 0 else -100.0
        batches.setdefault(key, []).append(daily)
        batch_len.setdefault(key, []).append(float(days))

    if len(batches) < 5:
        return {}

    dates = sorted(batches)
    idx = {d: i for i, d in enumerate(dates)}
    n_ticks = len(dates)

    # 2) 每批的日均收益（批内等权）与覆盖区间。
    # 同一信号日若混入不同持有期（多策略汇总），批 span 取**最长**：
    # 这批资金要等最晚的那笔回来，用最短会低估占用。
    batch_daily = {d: sum(v) / len(v) for d, v in batches.items()}
    avg_len = {d: (sum(v) / len(v)) for d, v in batch_len.items()}
    span: dict[str, int] = {d: max(int(round(x)) for x in batch_len[d]) for d in dates}
    span = {d: max(span[d], 1) for d in dates}

    # 3) 逐日组合收益：覆盖当日的各批等权均值（每批 = 1/N 资金）
    diff = [0.0] * (n_ticks + 1)   # 用于统计在手批次数（覆盖计数）
    for d in dates:
        i = idx[d]
        diff[i] += 1
        diff[min(i + span[d], n_ticks)] -= 1
    covering_count: list[int] = []
    run = 0
    for t in range(n_ticks):
        run += diff[t]
        covering_count.append(run)

    # 逐日收益用滑窗累加，避免 O(n × N)
    add = [0.0] * (n_ticks + 1)
    for d in dates:
        i = idx[d]
        add[i] += batch_daily[d]
        add[min(i + span[d], n_ticks)] -= batch_daily[d]
    series: list[float] = []
    run = 0.0
    for t in range(n_ticks):
        run += add[t]
        c = covering_count[t]
        series.append(run / c if c > 0 else 0.0)

    # 4) 净值 / 回撤 / 风险指标
    equity, peak, mdd = 1.0, 1.0, 0.0
    for x in series:
        equity *= 1 + x / 100.0
        peak = max(peak, equity)
        mdd = min(mdd, equity / peak - 1)

    try:
        d0, d1 = date.fromisoformat(dates[0]), date.fromisoformat(dates[-1])
        cal_days = max((d1 - d0).days, 1)
        years = max(cal_days / 365.25, 1e-6)
        density = n_ticks / cal_days * 365.0
    except ValueError:
        years = max(n_ticks / periods_per_year, 1e-6)
        density = float(periods_per_year)

    ann_ret = (equity ** (1 / years) - 1) * 100 if equity > 0 else -100.0
    # 波动率年化用**时间轴真实频率**（n_ticks / years），不硬套 252：
    # 信号日稀疏时（如中线部分策略隔日才有信号）套 252 会把波动率系统性放大。
    periods = (n_ticks / years) if years > 0 else periods_per_year
    ann_vol = pstdev(series) * (periods ** 0.5) if len(series) > 1 else 0.0
    mdd_pct = mdd * 100
    scale = target_mdd_pct / abs(mdd_pct) if mdd_pct else None

    mean_len = sum(avg_len.values()) / len(avg_len)
    avg_batches = sum(covering_count) / n_ticks
    # 稳态批次数：取覆盖计数峰值，即设计上的槽位数（= 持有天数）。
    # 不用均值：首尾爬坡期（逐步建仓 / 逐步到期）会把它显著拉低，
    # 用它算资金利用率会把「正常运行」误报成「钱在闲置」。
    steady_batches = float(max(covering_count)) if covering_count else 0.0

    # 平均在手笔数 = Σ_t（覆盖 t 的各批笔数之和）/ 交易日数，只作容量诊断
    diff_n = [0.0] * (n_ticks + 1)
    for d in dates:
        i = idx[d]
        w = float(len(batches[d]))
        diff_n[i] += w
        diff_n[min(i + span[d], n_ticks)] -= w
    avg_positions = 0.0
    run_n = 0.0
    for t in range(n_ticks):
        run_n += diff_n[t]
        avg_positions += run_n
    avg_positions /= n_ticks

    util = (steady_batches / mean_len * 100) if mean_len else None
    # 持有期离散度：>0 说明同一批里混了不同持有期的交易（多策略汇总），
    # 此时「每批 = 1/N 资金」的槽位语义会在策略间漂移，解读组合收益需注意。
    all_lens = [x for v in batch_len.values() for x in v]
    len_std = pstdev(all_lens) if len(all_lens) > 1 else 0.0
    return {
        "portfolio_total_ret_pct": _r((equity - 1) * 100, 1),
        "portfolio_mdd_pct": _r(mdd_pct, 1),
        "portfolio_ann_ret_pct": _r(ann_ret, 1),
        "portfolio_ann_vol_pct": _r(ann_vol, 1),
        "portfolio_sharpe": _r(ann_ret / ann_vol, 2) if ann_vol else None,
        "portfolio_calmar": _r(ann_ret / abs(mdd_pct), 2) if mdd_pct else None,
        "scaled_ann_ret_pct": _r(ann_ret * scale, 1) if scale else None,
        "avg_concurrent_batches": _r(avg_batches, 2),
        "max_concurrent_batches": _r(steady_batches, 1),
        "avg_concurrent_positions": _r(avg_positions, 2),
        "avg_holding_days": _r(mean_len, 1),
        "holding_days_std": _r(len_std, 2),
        "capital_utilization_pct": _r(util, 1) if util is not None else None,
        "date_density": _r(density, 1),
    }
