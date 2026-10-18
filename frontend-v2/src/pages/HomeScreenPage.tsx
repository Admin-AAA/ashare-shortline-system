import { ArrowUpRight } from "lucide-react";
import { useUrlRoute } from "../app/useUrlRoute";

/**
 * 空白驾驶舱（着陆页）—— 大留白起始页。
 *
 * 还原自 git 历史 32d20df「T10 add blank home page with dual entry points」：
 *   居中标题 + 一句副标题 + 两个入口（矩阵推荐 / 策略）。
 * 不堆叠数据面板，把信息密度留给 /dashboard 驾驶舱本体，避免两页内容重复。
 *
 * 设计遵循 T7 审美：暗色、大量留白、数字等宽、bold 只落在关键数字。
 */
export function HomePage() {
  const { navigateSection } = useUrlRoute();

  return (
    <div className="blank-cockpit">
      <div className="blank-cockpit__inner">
        <h1 className="blank-cockpit__title">短线交易驾驶舱</h1>
        <p className="blank-cockpit__subtitle">于数据海洋之上，凝视纵深，捕捉信号。</p>

        <div className="blank-cockpit__entries">
          <button
            type="button"
            className="blank-entry"
            onClick={() => navigateSection("pool")}
            aria-label="进入今日记录"
          >
            <span className="blank-entry__label">今日记录</span>
            <span className="blank-entry__hint">当日运行 · 推荐股票</span>
            <ArrowUpRight size={16} className="blank-entry__arrow" />
          </button>

          <button
            type="button"
            className="blank-entry"
            onClick={() => navigateSection("strategies")}
            aria-label="进入策略"
          >
            <span className="blank-entry__label">策略</span>
            <span className="blank-entry__hint">回测 · 复盘 · 健康 · 解释</span>
            <ArrowUpRight size={16} className="blank-entry__arrow" />
          </button>
        </div>
      </div>
    </div>
  );
}
