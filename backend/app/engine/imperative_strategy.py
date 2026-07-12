"""命令式策略模板方法基类（``ImperativeStrategy``，任务 3.3，需求 4.3）。

落点说明——为什么放在 ``engine/`` 而不是新建 ``services/strategies/`` 包：
    与 ``strategy.py``（3.2）同一个理由：``engine/`` 已经是「策略评估原语」的深模块
    聚集地——``evaluator.py`` / ``skill_loader.py`` / ``strategy_runner.py`` /
    ``signal.py`` / ``strategy.py`` 都在这里。``ImperativeStrategy`` 是这套原语之上
    再加一层「命令式策略的统一模板」，继续放在 ``engine/`` 保持 locality。

设计取舍：
    - **模板方法模式**：``evaluate(...)``（``final``，子类不应覆写）编排「不变的步骤」
      ——universe/prefilter 校验 → 子类 ``score(...)`` 钩子 → evidence 覆盖度诚实性
      上限 → ``Signal`` 组装。子类**只写** ``score(stock, ctx) -> ImperativeScoreResult``
      这一处「新逻辑」（打分/阶段/setup/风控标记的原始判断），与声明式 YAML 路径共享
      同一套风控/上限/组装规则（design §4.2：「加命令式策略只写打分，风控/上限与
      声明式一致，locality 不破」）。
    - **不改 ``strategy_runner.py``**：universe 校验与 evidence 上限直接**复用**
      ``strategy_runner._passes_universe`` / ``strategy_runner._evidence_score_cap``
      两个既有函数（模块内已有先例直接引用该模块的下划线前缀函数，见
      ``scripts/diagnose_skill_prefilter.py`` 对 ``_rule_expr`` 的引用）。这样声明式
      路径（``run_strategy`` / ``DeclarativeStrategy``）一行代码都不变，3.1/3.2 的
      等价对拍测试不受影响，不需要重新验证。
    - **``contract`` 概念收窄为 ``universe``/``prefilter`` 两个类属性**：命令式策略
      没有完整的 ``strategy.yaml``（阶段/打分/setup/风控规则都在 ``score()`` 里用
      Python 写），但 universe/prefilter 门槛校验仍是「声明式」的（复用同一套
      AST 表达式求值），所以命令式子类可选声明 ``universe``/``prefilter`` 两个类
      属性（结构与 yaml 里的同名键完全一致：``{"include": [...], "exclude": [...]}``
      / ``{"market": {"exclude": [...]}}``），基类内部拼成 ``_passes_universe`` 需要
      的 ``{"universe": ..., "prefilter": ...}`` 形状即可复用，无需子类关心内部
      拼装细节。不声明则默认空——即「不设门槛，全量放行」。
    - **身份信息作为类属性而非从 ``StrategySkill``/``manifest`` 读取**：3.4 才会把
      ``load_skills`` 扩展为识别 ``impl_class`` 并做实例化装配，本任务先把
      ``ImperativeStrategy`` 需要哪些身份信息设计清楚——``strategy_id`` /
      ``strategy_name`` / ``version`` / ``weight`` / ``strategy_independence``，与
      声明式 ``_signal(...)`` 组装用到的字段一一对应（``skill.skill_id`` /
      ``skill.name`` / ``skill.version`` / 合同 ``weight`` / ``meta.strategy_independence``）。
      3.4 接入 manifest 装配后，这些类属性可由装配层从 manifest 覆盖/校验，本类
      本身不依赖 ``StrategySkill``/``contract_hash``（命令式策略没有 yaml contract
      hash 可用），``version_id`` 用固定后缀 ``"imperative"`` 替代声明式的
      ``contract_hash[:8]`` 位——语义上标注「本 Signal 出自命令式实现」，格式仍是
      ``f"{strategy_id}:{version}:<后缀>"``，与声明式格式呼应但不强行复刻字面量。
    - **不要求命令式与声明式产出完全相同的 Signal 值**——只要求**结构相同**：同一组
      ``SIGNAL_FIELDS`` 键、类型契合 ``Signal`` 注解（design §4.2 / 需求 4.4）。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar, final

from app.engine.signal import Signal
from app.engine.strategy import StrategyContext
from app.engine.strategy_runner import _evidence_score_cap, _passes_universe


@dataclass(frozen=True)
class ImperativeScoreResult:
    """``ImperativeStrategy.score(...)`` 钩子的返回契约。

    这是命令式子类**唯一要产出**的「新逻辑」——打分与阶段/setup/风控相关的原始
    判断结果。基类 ``evaluate(...)`` 拿到这个结果后，统一套用 evidence 覆盖度
    诚实性上限并组装成最终 ``Signal``，子类不必（也不应）关心这两步。

    字段与声明式 ``run_strategy`` 内部对应的中间变量一一对应，便于迁移直觉：
    ``score``→打分累加结果，``phase``→阶段命中名，``score_cap``→阶段/子类自定的
    分数上限（会再与 evidence 覆盖度上限取 min），``hard_veto``/``veto_reason``→
    子类自身判定的否决（如「观察类」信号），``risk_flags``/``evidence``→子类产出
    的风险提示与证据说明（基类会在此基础上追加 evidence 覆盖度相关的追加项）。
    """

    score: float
    phase: str = "未分类"
    score_cap: float = 100.0
    hard_veto: bool = False
    veto_reason: str = ""
    risk_flags: list[str] = field(default_factory=list)
    setup_match: str = ""
    entry_rule: str = ""
    invalid_rule: str = ""
    setup_quality: float = 50.0
    evidence: list[str] = field(default_factory=list)


class ImperativeStrategy(ABC):
    """命令式（Python）策略的模板方法基类。

    子类**只需**：
      1. 声明最小身份属性：``strategy_id`` / ``strategy_name``（必填，无默认值，
         不声明会在实例化时因缺少类属性而在 ``evaluate`` 内取值处报错，等价于
         「显式要求填写」）；``version`` / ``weight`` / ``strategy_independence``
         有合理默认值，可选覆写。
      2. 可选声明 ``universe`` / ``prefilter`` 两个类属性（结构同 yaml 同名键），
         供基类做门槛校验；不声明则不设门槛。
      3. 实现 ``score(self, stock, ctx) -> ImperativeScoreResult`` 这一个抽象方法。

    子类**不应**覆写 ``evaluate``——它是模板方法，标记为 ``@final``，把「不变的
    步骤」（universe/prefilter 校验 → evidence 上限 → Signal 组装）钉死在基类，
    确保命令式策略与声明式策略共享同一套风控/诚实性口径（design §4.2）。
    """

    # ── 身份信息（子类必须声明 strategy_id / strategy_name；其余有默认值） ──
    strategy_id: ClassVar[str]
    strategy_name: ClassVar[str]
    version: ClassVar[str] = "0.1.0"
    weight: ClassVar[float] = 1.0
    strategy_independence: ClassVar[float] = 0.85

    # ── 可选门槛声明：结构与 strategy.yaml 同名键一致，供 _passes_universe 复用 ──
    universe: ClassVar[dict[str, Any]] = {}
    prefilter: ClassVar[dict[str, Any]] = {}

    @abstractmethod
    def score(self, stock: dict[str, Any], ctx: StrategyContext) -> ImperativeScoreResult:
        """子类唯一要写的「新逻辑」：对一只股票打分并给出阶段/setup/风控原始判断。

        ``stock`` 是基类已注入 ``is_main_theme`` 派生字段后的上下文视图（与声明式
        ``run_strategy`` 内部传给 ``evaluate(...)`` 表达式求值的 ``context`` 语义
        一致），``ctx`` 是本次调用的 ``StrategyContext``（携带 ``main_industries`` /
        ``skip_universe_check``）。
        """
        raise NotImplementedError

    @final
    def evaluate(self, stock: dict[str, Any], ctx: StrategyContext) -> Signal:
        """模板方法：不应被子类覆写。

        步骤（与声明式 ``run_strategy`` 的不变步骤对齐）：
          1. universe/prefilter 校验（复用 ``strategy_runner._passes_universe``，
             逻辑与声明式路径完全一致）——不过则提前返回否决 ``Signal``，子类
             ``score(...)`` 不会被调用。
          2. 调用子类 ``score(stock, ctx)`` 钩子，取得打分原始结果。
          3. 应用 evidence 覆盖度诚实性上限（复用
             ``strategy_runner._evidence_score_cap``，阈值/追加风险提示与证据说明
             逐字对齐声明式路径）。
          4. 组装最终 ``Signal``（身份字段取自子类声明的类属性）。
        """
        context = dict(stock)
        industry = stock.get("industry")
        context["is_main_theme"] = bool(
            stock.get("is_main_theme")
            or (industry in set(ctx.main_industries) and industry != "未分类")
        )

        mini_contract = {"universe": self.universe or {}, "prefilter": self.prefilter or {}}
        if not ctx.skip_universe_check and not _passes_universe(mini_contract, context):
            return self._build_signal(
                score=0.0,
                phase="排除",
                score_cap=100.0,
                hard_veto=True,
                veto_reason="不在策略股票池",
                risk_flags=["不符合股票池"],
                setup_match="",
                entry_rule="",
                invalid_rule="",
                setup_quality=50.0,
                evidence=[],
            )

        result = self.score(context, ctx)

        data_quality = dict(stock.get("data_quality") or {})
        missing_fields = set(data_quality.get("missing_fields") or [])
        data_completeness = float(
            stock.get("data_completeness_score") or data_quality.get("data_completeness_score") or 100.0
        )
        evidence_quality = float(
            stock.get("evidence_quality_score") or data_quality.get("evidence_quality_score") or 100.0
        )
        coverage_cap = _evidence_score_cap(
            sample_count=int(stock.get("market_sample_count") or 0),
            data_completeness=data_completeness,
            evidence_quality=evidence_quality,
            missing_fields=missing_fields,
        )

        score_cap = min(result.score_cap, coverage_cap)
        risk_flags = list(result.risk_flags)
        evidence = list(result.evidence)
        if coverage_cap < 82:
            risk_flags.append("样本或证据覆盖不足，评分已降权")
            evidence.append(f"诚实性上限: {round(coverage_cap, 1)}")

        score = max(0.0, min(result.score, score_cap))

        return self._build_signal(
            score=score,
            phase=result.phase,
            score_cap=score_cap,
            hard_veto=result.hard_veto,
            veto_reason=result.veto_reason,
            risk_flags=risk_flags,
            setup_match=result.setup_match,
            entry_rule=result.entry_rule,
            invalid_rule=result.invalid_rule,
            setup_quality=result.setup_quality,
            evidence=evidence,
        )

    def _build_signal(
        self,
        *,
        score: float,
        phase: str,
        score_cap: float,
        hard_veto: bool,
        veto_reason: str,
        risk_flags: list[str],
        setup_match: str,
        entry_rule: str,
        invalid_rule: str,
        setup_quality: float,
        evidence: list[str],
    ) -> Signal:
        score = min(score, score_cap)
        return {
            "strategy_id": self.strategy_id,
            "strategy_name": self.strategy_name,
            "version_id": f"{self.strategy_id}:{self.version}:imperative",
            "strategy_independence": max(0.0, min(1.0, float(self.strategy_independence))),
            "weight": round(float(self.weight), 3),
            "phase": phase,
            "score": round(score, 2),
            "score_cap": round(score_cap, 2),
            "confidence": round(min(0.95, max(0.2, score / 100)), 2),
            "setup_match": setup_match,
            "entry_rule": entry_rule,
            "invalid_rule": invalid_rule,
            "setup_quality": setup_quality,
            "hard_veto": hard_veto,
            "veto_reason": veto_reason,
            "risk_flags": risk_flags,
            "evidence": evidence,
        }
