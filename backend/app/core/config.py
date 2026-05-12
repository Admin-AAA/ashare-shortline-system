from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "AShare Shortline System"
    database_url: str = "sqlite:///./ashare_workbench.db"
    data_provider: str = "multi"
    tushare_token: str = ""
    quote_sources: str = "thsdk,eastmoney,akshare,tencent,sina"
    kline_sources: str = "tencent,thsdk,eastmoney,akshare"
    market_scan_size: int = 5200
    market_candidate_count: int = 0  # 0 = 使用所有合格股票
    market_candidate_lookahead_multiplier: int = 4
    market_kline_days: int = 90
    market_kline_seed_days: int = 250
    market_kline_incremental_buffer: int = 10
    market_quote_cache_ttl_seconds: int = 28800
    market_kline_cache_ttl_hours: int = 20
    market_pool_min_source_count: int = 2
    market_request_timeout_seconds: float = 10.0
    thsdk_batch_size: int = 600
    thsdk_username: str = ""
    thsdk_password: str = ""
    thsdk_mac: str = ""
    agent_skill_runtime: str = "llm"
    agent_skill_api_key: str = ""
    agent_skill_base_url: str = "https://api.deepseek.com"
    agent_skill_model: str = "deepseek-v4-pro"
    agent_skill_timeout_seconds: float = 20.0
    agent_skill_prompt_char_limit: int = 22000
    agent_retry_count: int = 3
    agent_retry_backoff: float = 1.5
    agent_model: str = "deepseek-v4-pro"
    agent_temperature: float = 0.3
    agent_max_tokens: int = 4096
    agent_skill_weight: float = 0.50
    # 辅助数据源
    limit_up_sources: str = "eastmoney"
    concept_sector_sources: str = "eastmoney"
    capital_flow_sources: str = "eastmoney"
    dragon_tiger_sources: str = "eastmoney"
    news_sources: str = "eastmoney"
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

    model_config = SettingsConfigDict(env_prefix="ASHARE_", env_file=".env", extra="ignore")

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
    settings = Settings()
    if settings.database_url.startswith("sqlite:///./"):
        filename = settings.database_url.removeprefix("sqlite:///./")
        settings.database_url = f"sqlite:///{settings.backend_root / filename}"
    return settings
