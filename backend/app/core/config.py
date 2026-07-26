from functools import lru_cache
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


BACKEND_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    app_name: str = "AShare Shortline System"
    database_url: str = "mysql+pymysql://aowang:1279990918@127.0.0.1:3306/ashare_workbench?charset=utf8mb4"
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
