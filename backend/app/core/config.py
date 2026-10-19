from dataclasses import dataclass, field
from functools import lru_cache
import os
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.production_hardening_flags import RolloutMode
from app.governance.scope_guard import assert_no_brokerage_access


BACKEND_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


@dataclass(frozen=True)
class ProviderSettings:
    """Nested, read-only view of one upstream provider's flat ASHARE_TUSHARE_<NAME>_* settings."""

    name: str
    url: str
    credential: str = field(repr=False)
    rate_limit_per_minute: int
    max_concurrency: int = 0
    api_rate_limits: dict = field(default_factory=dict)


_PROVIDER_PREFIXES = ("promax", "quantdata")


class Settings(BaseSettings):
    # Production hardening paths are deliberately independent. All six default
    # to ENFORCED (authoritative); set ASHARE_HARDENING_<X>_MODE=shadow/audit/legacy
    # to roll a single path back without touching the others.
    hardening_evidence_projection_mode: RolloutMode = RolloutMode.ENFORCED
    hardening_canonical_execution_mode: RolloutMode = RolloutMode.ENFORCED
    hardening_release_gate_mode: RolloutMode = RolloutMode.ENFORCED
    hardening_portfolio_risk_mode: RolloutMode = RolloutMode.ENFORCED
    hardening_scheduler_claim_mode: RolloutMode = RolloutMode.ENFORCED
    hardening_dashboard_refresh_mode: RolloutMode = RolloutMode.ENFORCED

    app_name: str = "AShare Shortline System"
    database_url: str = "mysql+pymysql://aowang@127.0.0.1:3306/ashare_workbench?charset=utf8mb4"
    data_provider: str = "tushare"
    # Deprecated compatibility fields. New upstream credentials use provider-specific settings.
    tushare_token: str = Field(default="", repr=False)
    tushare_rate_limit_per_minute: int = 0
    # Promax：X-API-Key + GET /tushare/pro/{api_name} 聚合网关。
    # 服务端公开健康信息显示单 IP 200 RPM；客户端保留余量按 180 RPM 限速。
    tushare_promax_url: str = "https://pcd.mobcvb.cn/tushare/pro"
    tushare_promax_api_key: str = Field(default="", repr=False)
    tushare_promax_rate_limit_per_minute: int = 180
    tushare_promax_max_concurrency: int = 4
    # research: 命名空间在盘中（工作日 9:15–15:05）对 promax 的限速，给 live: 实时行情留额度。
    tushare_promax_research_intraday_rpm: int = 30
    # quantdata：第三方 tushare pro 协议数据源（JSON token，POST 根路径不带 api 路径）。
    # 探测权限：日线/基本面/筹码/资金流/涨跌停/龙虎榜/同花顺概念全量可用（数据到当天）；
    # anns_d/rt_k/stk_mins 未购买（走 per-API 路由链自动 fallback）。平滑限速，稳态 6rps 无限制。
    tushare_quantdata_url: str = "https://quantdata888.duckdns.org"
    tushare_quantdata_token: str = Field(default="", repr=False)
    tushare_quantdata_rate_limit_per_minute: int = 120
    # Xiaoshi v3：独立于 Tushare Pro 协议，提供全市场快照、复权日线、财务与量化数据。
    xiaoshi_api_base_url: str = "https://api.shizixi.com"
    xiaoshi_api_key: str = Field(default="", repr=False)
    xiaoshi_max_qps: int = 30
    xiaoshi_max_attempts: int = 2
    xiaoshi_timeout_seconds: float = 30.0
    xiaoshi_skill_url: str = "https://api.shizixi.com/api/v3/skills/xiaoshi-quant-expert/SKILL.md?v=2026-08-14.1"
    xiaoshi_skill_version: str = "2026-08-14.1"
    xiaoshi_skill_sha256: str = "a6683e89da671b78ec04456f12b18a463cf9874707f81ee26f65c64ca043b1f7"
    xiaoshi_minute_cache_dir: str = ""
    # 同质量档内按剩余限速额度×实时健康度加权分流（档位见 datasource/routing_table.json，
    # 单个 api 可用 "balance": false 关闭）；False = 全局关闭，退回纯链序。
    tushare_load_balancing: bool = True
    # 数据网关按 dataset 切换（逗号分隔，如 "quote.realtime,bar.minute"）；默认空 = 全部走旧路径。
    datasource_cutover: str = ""
    # 影子比对：旧路径照常返回，同时抽样跑网关并比对（逗号分隔 dataset）。
    datasource_shadow: str = ""
    datasource_shadow_sample_rate: float = 0.1
    tushare_provider_failure_threshold: int = 3
    tushare_provider_recovery_seconds: float = 300.0
    # 熔断阈值按失败类型（网关审计 1）：超时 N 次、错误数据（空/缺字段）N 次熔断；限流按上游等待时间。
    breaker_threshold_timeout: int = 2
    breaker_threshold_bad_data: int = 3
    tushare_provider_max_attempts: int = 2
    tushare_connect_timeout_seconds: float = 5.0
    tushare_read_timeout_seconds: float = 120.0
    quote_sources: str = "xiaoshi,tushare"
    kline_sources: str = "xiaoshi,tushare"
    market_scan_size: int = 6000
    market_candidate_count: int = 0  # 0 = 不在首段召回前硬截断，交给宽召回窗口与后续精排控制
    market_broad_recall_count: int = 6000  # 两段召回第一段：让全部可交易股票进入策略层，由各策略 universe 规则自行筛选
    market_candidate_lookahead_multiplier: int = 4
    market_skill_review_limit: int = 0  # 0 = Skill Agent 接收本次实际扫描的全部股票
    market_skill_review_concurrency: int = 10  # 默认 10 并发；运行时可通过 agent_review_concurrency 参数覆盖
    market_skill_rule_gate_min_score: float = 58.0
    market_skill_rule_gate_min_per_skill: int = 20
    market_skill_rule_gate_min_total: int = 20
    market_skill_rule_gate_max_per_skill: int = 0  # 0 = 动态计算 max(20, min(eligible * 10%, 30))
    market_skill_rule_gate_max_total: int = 120
    market_kline_days: int = 90
    market_kline_seed_days: int = 250
    market_kline_incremental_buffer: int = 10
    market_quote_cache_ttl_seconds: int = 300
    market_kline_cache_ttl_hours: int = 20
    market_pool_min_source_count: int = 2
    market_request_timeout_seconds: float = 10.0
    script_timeout_seconds: int = 7200  # subprocess 调用回填脚本的超时；0 表示不限时
    agent_skill_runtime: str = "llm"
    # ── 以下 agent_skill_* / agent_model 已弃用 ──
    # 模型路由改由数据库 model_route_config 表驱动：每条路由自带独立
    # api_key / base_url / model。「全局默认 key/url」不再参与任何路由解析，
    # 也不作为回退。保留字段仅为向后兼容 .env 解析，路由代码不再读取。
    agent_skill_api_key: str = Field(default="", repr=False)          # 弃用：不再作为任何路由的回退 key
    agent_skill_base_url: str = ""         # 弃用：不再作为任何路由的回退 base_url
    agent_skill_model: str = ""            # 弃用
    agent_skill_timeout_seconds: float = 300.0  # 单次 Skill 调用超时，避免单 Skill 异常拖长整次推荐
    # LLM 复核是可选标记，不阻塞推荐：整批复核超过该秒数即放弃未完成部分，
    # 未完成候选按「未复核」处理（不影响分数与出票）。
    agent_review_stage_timeout_seconds: float = 600.0
    agent_skill_prompt_char_limit: int = 22000
    agent_retry_count: int = 3
    agent_retry_backoff: float = 1.5
    agent_model: str = ""                  # 弃用：Agno 路径已改为读取路由表
    agent_temperature: float = 0.3
    agent_max_tokens: int = 40000
    agent_skill_weight: float = 0.50
    # 正式推荐覆盖全 A 股；"20cm" 仅保留为显式研究实验选项。
    formal_board_universe: str = "all"
    # 辅助数据源（tushare 优先，eastmoney/akshare 备用）
    limit_up_sources: str = "tushare"
    concept_sector_sources: str = "tushare"
    capital_flow_sources: str = "tushare"
    dragon_tiger_sources: str = "tushare"
    news_sources: str = "tushare,eastmoney"
    limit_up_cache_ttl_seconds: int = 3600
    concept_sector_cache_ttl_seconds: int = 3600
    capital_flow_cache_ttl_seconds: int = 7200
    dragon_tiger_cache_ttl_seconds: int = 3600
    news_cache_ttl_seconds: int = 7200
    auxiliary_prefetch_max_codes: int = 120
    concept_member_fetch_timeout_seconds: float = 30.0
    enable_limit_up_fetch: bool = True
    enable_concept_sector_fetch: bool = True
    enable_capital_flow_fetch: bool = True
    enable_dragon_tiger_fetch: bool = True
    enable_news_fetch: bool = True
    log_level: str = "INFO"
    log_dir_override: str = Field(default="", alias="log_dir")
    log_max_bytes: int = 10 * 1024 * 1024
    log_backup_count: int = 5
    access_password: str = Field(default="", repr=False)
    access_cookie_name: str = "ashare_access"
    access_session_days: int = 7

    model_config = SettingsConfigDict(env_prefix="ASHARE_", env_file=str(BACKEND_ENV_FILE), extra="ignore")

    @property
    def providers(self) -> dict[str, ProviderSettings]:
        """Grouped provider settings (env var names unchanged; flat fields remain authoritative)."""
        out: dict[str, ProviderSettings] = {}
        for name in _PROVIDER_PREFIXES:
            pre = f"tushare_{name}_"
            cred = getattr(self, pre + "token", None)
            if cred is None:
                cred = getattr(self, pre + "api_key", "")
            api_limits: dict = {}
            out[name] = ProviderSettings(
                name=name,
                url=getattr(self, pre + "url"),
                credential=cred,
                rate_limit_per_minute=getattr(self, pre + "rate_limit_per_minute"),
                max_concurrency=getattr(self, pre + "max_concurrency", 0),
                api_rate_limits=api_limits,
            )
        return out

    @model_validator(mode="before")
    @classmethod
    def _reject_brokerage_config_section(cls, values):
        # Scope_Guard（S0 任务 0.10）：D3 券商实盘为延期范围。brokerage_* 配置段
        # 在 Settings 解析阶段即 fail-closed 拒绝（仅扫描 key 名，绝不记录值），
        # 先于任何 secret 使用。extra="ignore" 会静默丢弃的 kwargs / ASHARE_ env
        # key 也在这里显式拦截。
        keys: list[str] = []
        if isinstance(values, dict):
            keys.extend(str(key) for key in values)
        keys.extend(key for key in os.environ if key.startswith("ASHARE_"))
        assert_no_brokerage_access(keys, context="app.core.config.Settings")
        return values

    @model_validator(mode="after")
    def _validate_critical_config(self):
        # 注：ASHARE_AGENT_SKILL_API_KEY 已弃用。模型路由由数据库
        # model_route_config 表驱动，每条路由自带独立 key，不再依赖全局 env key。
        if not any(
            (
                self.tushare_promax_api_key,
                self.tushare_quantdata_token,
            )
        ):
            import logging
            logging.getLogger(__name__).warning(
                "Tushare Provider 凭据均未配置 — 上游同步和盘中实时分钟将不可用。"
            )
        return self

    @property
    def project_root(self) -> Path:
        return Path(__file__).resolve().parents[3]

    @property
    def backend_root(self) -> Path:
        return Path(__file__).resolve().parents[2]

    @property
    def skills_dir(self) -> Path:
        return self.backend_root / "skills"

    @property
    def log_dir(self) -> str:
        return self.log_dir_override or str(self.backend_root / "logs")


@lru_cache
def get_settings() -> Settings:
    return Settings()
