"""
数据库模型 — 重构版 v2。

变更：
- 去掉所有 raw_* 原始数据表（不再双写）
- daily_bars + daily_indicators → daily_kbars（合并）
- unified_quotes JSON → stock_quotes 类型化列
- 新增 margin_data / hk_holdings / holder_numbers / stk_limits
- 辅助表字段对齐 Tushare 数据结构
"""

from datetime import datetime

from sqlalchemy import (
    CHAR,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    Index,
    Integer,
    LargeBinary,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.mysql import DATETIME, LONGBLOB, LONGTEXT, MEDIUMTEXT
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import utcnow_naive
from app.db.session import Base

# Split by domain; this module re-exports everything so existing imports keep working.
from app.models.market import (  # noqa: F401,E402
    Stock,
    StockMetadataHistory,
    DailyBasic,
    DailyKbar,
    StockQuote,
    LimitUpBoard,
    ConceptSector,
    ConceptSectorStock,
    CapitalFlow,
    DragonTiger,
    NewsAnnouncement,
    MinuteKbar,
    IndexDaily,
    MarginData,
    MarginDetail,
    BlockTrade,
    HsgtFlow,
    HsgtTop10,
    HkHolding,
    HolderNumber,
    FinaIndicator,
    StkLimit,
    AuctionData,
    BrokerRecommend,
    HolderTrade,
    InstitutionalSurvey,
    ShareFloat,
    PledgeStat,
    Repurchase,
    MarketSnapshot,
    StockMatrixHistory,
)
from app.models.datasource import (  # noqa: F401,E402
    DataSourceHealth,
    DataSourceConfig,
    ApiCallLog,
    DataSourceRequestTrace,
    DataSourceAttempt,
    ApiRequestMetric,
)
from app.models.strategy import (  # noqa: F401,E402
    SkillRegistry,
    SkillVersion,
    StrategyVersion,
    StrategyVersionSnapshot,
    Preset,
    PresetSkill,
)
from app.models.recommendation import (  # noqa: F401,E402
    RecommendationReconstructionBatch,
    RecommendationRun,
    RecommendationEvidenceClassificationAudit,
    RecommendationCollectionSeal,
    Recommendation,
    RecommendationOutcome,
    RecommendationOutcomeVersion,
    RecommendationExecutionState,
    ReviewNote,
    WatchItem,
    AgentDecisionLog,
    IntradayLockMonitor,
    IntradayLockSignal,
)
from app.models.ops import (  # noqa: F401,E402
    BacktestRun,
    CalibrationConfig,
    ModelRouteConfig,
    LLMReviewCache,
    ScheduledTask,
    ScheduledTaskRun,
)
from app.models.backtest import (  # noqa: F401,E402
    _BACKTEST_DATETIME,
    _BACKTEST_LONGTEXT,
    _BACKTEST_LONGBLOB,
    BacktestDataSnapshot,
    BacktestHoldoutClaim,
    BacktestInputArtifact,
    BacktestRequestClaim,
    BacktestRunV2,
    BacktestCandidate,
    BacktestTrade,
    BacktestFailureAttribution,
    BacktestExperiment,
)
