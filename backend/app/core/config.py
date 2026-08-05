from functools import lru_cache
import os
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.production_hardening_flags import RolloutMode
from app.governance.scope_guard import assert_no_brokerage_access


BACKEND_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    # Production hardening paths are deliberately independent. Safe defaults
    # compute/observe new behavior without making it authoritative.
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
    tushare_token: str = ""
    tushare_rate_limit_per_minute: int = 0
    tushare_fastapic_url: str = "https://fastapic.stockai888.top/"
    tushare_fastapic_token: str = ""
    tushare_fastapic_rate_limit_per_minute: int = 30
    tushare_fastapic_max_concurrency: int = 1
    tushare_ai_tool_url: str = "https://ai-tool.indevs.in/tushare/pro"
    tushare_ai_tool_api_key: str = ""
    tushare_ai_tool_rate_limit_per_minute: int = 90
    tushare_ai_tool_rt_min_rate_limit_per_minute: int = 72
    tushare_ai_tool_stk_mins_rate_limit_per_minute: int = 48
    # xiaodefa：第三方 tushare pro 协议数据源（pro_http），作为通用同步的补充 fallback。
    tushare_xiaodefa_url: str = "https://t.xiaodefa.top/"
    tushare_xiaodefa_token: str = ""
    tushare_xiaodefa_rate_limit_per_minute: int = 60
    # tushare_official：Tushare 官方 api.tushare.pro。核心批量接口（daily/daily_basic/
    # moneyflow/adj_factor/top_list）有权限且低延迟；stk_factor/cyq_perf/rt_k/stk_mins/
    # anns_d 无权限（40203 权限错误会自动 fallback 到下一源）。
    tushare_official_url: str = "https://api.tushare.pro"
    tushare_official_token: str = ""
    tushare_official_rate_limit_per_minute: int = 120
    # quantdata：第三方 tushare pro 协议数据源（JSON token，POST 根路径不带 api 路径）。
    # 探测权限：日线/基本面/筹码/资金流/涨跌停/龙虎榜/同花顺概念全量可用（数据到当天）；
    # anns_d/rt_k/stk_mins 未购买（走 per-API 路由链自动 fallback）。平滑限速，稳态 6rps 无限制。
    tushare_quantdata_url: str = "https://quantdata888.duckdns.org"
    tushare_quantdata_token: str = ""
    tushare_quantdata_rate_limit_per_minute: int = 120
    # 实时报价爬虫接口（tushare realtime_quote，走 realtime.stockai888.top）。
    # 独立于 ai-tool/fastapic 的 pro HTTP 协议：多代码批量、含现价/最高/最低/昨收，
    # 用于未结算推荐的盘中实时浮盈。token 复用 fastapic token。
    tushare_realtime_quote_url: str = "https://realtime.stockai888.top"
    tushare_realtime_quote_batch_size: int = 60
    tushare_realtime_quote_ttl_seconds: float = 20.0
    tushare_provider_failure_threshold: int = 3
    tushare_provider_recovery_seconds: float = 60.0
    tushare_provider_max_attempts: int = 2
    tushare_connect_timeout_seconds: float = 5.0
    tushare_read_timeout_seconds: float = 120.0
    quote_sources: str = "tushare"
    kline_sources: str = "tushare"
    market_scan_size: int = 5200
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
    agent_skill_api_key: str = ""          # 弃用：不再作为任何路由的回退 key
    agent_skill_base_url: str = ""         # 弃用：不再作为任何路由的回退 base_url
    agent_skill_model: str = ""            # 弃用
    agent_skill_timeout_seconds: float = 300.0  # 正式链路默认按分钟级超时控制长尾，避免单Skill异常把整次推荐拖到 10+ 分钟
    agent_skill_prompt_char_limit: int = 22000
    agent_retry_count: int = 3
    agent_retry_backoff: float = 1.5
    agent_model: str = ""                  # 弃用：Agno 路径已改为读取路由表
    agent_temperature: float = 0.3
    agent_max_tokens: int = 40000
    agent_skill_weight: float = 0.50
    # Production universe validated by the serial self-improve replay.
    # Empty string or "all" disables the entry-side board filter.
    formal_board_universe: str = "20cm"
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
    access_password: str = ""
    access_cookie_name: str = "ashare_access"
    access_session_days: int = 7

    model_config = SettingsConfigDict(env_prefix="ASHARE_", env_file=str(BACKEND_ENV_FILE), extra="ignore")

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
        if not self.tushare_fastapic_token and not self.tushare_ai_tool_api_key:
            import logging
            logging.getLogger(__name__).warning(
                "Fastapic Token 与 ai-tool API Key 均未配置 — 上游同步和盘中实时分钟将不可用。"
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
