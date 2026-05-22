from functools import lru_cache
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "AShare Shortline System"
    database_url: str = "mysql+pymysql://aowang:1279990918@139.196.242.53:4407/ashare_workbench?charset=utf8mb4"
    data_provider: str = "multi"
    tushare_token: str = "f8b05f8ee54f468220f2569afe56b4c027f1d388d164899339d394ad"
    tushare_rate_limit_per_minute: int = 0  # 0 = 不限速（自建服务器）
    quote_sources: str = "thsdk,eastmoney,akshare,tencent,sina"
    kline_sources: str = "tushare,tencent,eastmoney,akshare"
    market_scan_size: int = 5200
    market_candidate_count: int = 0  # 0 = 不在首段召回前硬截断，交给宽召回窗口与后续精排控制
    market_broad_recall_count: int = 1800  # 两段召回第一段：从全可交易池宽召回，再进入后续规则门/深审
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
    thsdk_batch_size: int = 600
    thsdk_username: str = ""
    thsdk_password: str = ""
    thsdk_mac: str = ""
    agent_skill_runtime: str = "llm"
    agent_skill_api_key: str = ""
    agent_skill_base_url: str = "https://api.deepseek.com"
    agent_skill_model: str = "deepseek-v4-pro"
    agent_skill_timeout_seconds: float = 300.0  # 正式链路默认按分钟级超时控制长尾，避免单Skill异常把整次推荐拖到 10+ 分钟
    agent_skill_prompt_char_limit: int = 22000
    agent_retry_count: int = 3
    agent_retry_backoff: float = 1.5
    agent_model: str = "deepseek-v4-pro"
    agent_temperature: float = 0.3
    agent_max_tokens: int = 4096
    agent_skill_weight: float = 0.50
    # 辅助数据源（tushare 优先，eastmoney/akshare 备用）
    limit_up_sources: str = "tushare,akshare,eastmoney"
    concept_sector_sources: str = "tushare,akshare,eastmoney"
    capital_flow_sources: str = "tushare,eastmoney"
    dragon_tiger_sources: str = "tushare,eastmoney"
    news_sources: str = "eastmoney,akshare"
    limit_up_cache_ttl_seconds: int = 3600
    concept_sector_cache_ttl_seconds: int = 3600
    capital_flow_cache_ttl_seconds: int = 7200
    dragon_tiger_cache_ttl_seconds: int = 3600
    news_cache_ttl_seconds: int = 7200
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

    model_config = SettingsConfigDict(env_prefix="ASHARE_", env_file=".env", extra="ignore")

    @model_validator(mode="after")
    def _validate_critical_config(self):
        if self.agent_skill_runtime == "llm" and not self.agent_skill_api_key:
            import logging
            logging.getLogger(__name__).warning(
                "ASHARE_AGENT_SKILL_API_KEY 未配置 — LLM Agent 审查将全部失败，"
                "系统将仅依赖策略规则引擎。请设置环境变量或 .env 文件。"
            )
        if not self.tushare_token:
            import logging
            logging.getLogger(__name__).warning(
                "ASHARE_TUSHARE_TOKEN 未配置 — Tushare 数据源将不可用。"
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
