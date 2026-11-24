"""Cross-process circuit-breaker state (backend, Prefect worker, scripts) via the runtime SQLite store.

Each process keeps its own local CircuitBreaker (failure counting, half-open probe). When a breaker
OPENS, the open-until wall-clock time is published to SQLite under a stable key; every other process
then refuses requests for that key until it expires. A success anywhere clears the shared row.
Reads are cached for SHARED_READ_TTL seconds, writes happen only on state changes, so the store is
touched rarely. If the store is disabled/unavailable (pytest, ASHARE_METRICS_DB=off, locked file)
this degrades to the plain local breaker.
"""
from __future__ import annotations

import logging
import sqlite3
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

SHARED_READ_TTL = 2.0
# Namespace isolation (owner, 2026-10-19): only the live backend and Prefect worker share the
# "live" namespace. Every other process (scripts/*, research runners, ad-hoc python) defaults to
# "research", so a research script tripping a breaker can never block live traffic.
BREAKER_NAMESPACE_ENV = "ASHARE_BREAKER_NAMESPACE"
LIVE_NAMESPACE = "live"
RESEARCH_NAMESPACE = "research"


def breaker_namespace() -> str:
    import os

    raw = os.environ.get(BREAKER_NAMESPACE_ENV, "").strip().lower()
    return LIVE_NAMESPACE if raw == LIVE_NAMESPACE else RESEARCH_NAMESPACE


def mark_live_process() -> None:
    """Called by the backend startup and the Prefect worker entrypoint (inherited by flow subprocesses)."""
    import os

    os.environ[BREAKER_NAMESPACE_ENV] = LIVE_NAMESPACE
_cache: dict[str, tuple[float, float]] = {}  # key -> (read_at_monotonic, open_until_epoch)
_cache_lock = threading.Lock()


def _conn() -> sqlite3.Connection | None:
    from app.core import metrics

    if metrics._process_role() == "pytest":
        return None
    path = metrics._store_path()
    if path is None:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=1.0)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS breaker_state (key TEXT PRIMARY KEY, open_until REAL NOT NULL, "
        "updated_at REAL NOT NULL, role TEXT, pid INTEGER, reason TEXT)"
    )
    # 半开探测连续失败次数（跨进程）：探测失败后的冷却按次数指数拉长，见 probe_backoff_delay。
    conn.execute("CREATE TABLE IF NOT EXISTS breaker_probe_streak (key TEXT PRIMARY KEY, streak INTEGER NOT NULL, updated_at REAL NOT NULL)")
    return conn


def _publish_open(key: str, open_until: float, reason: str = "") -> None:
    try:
        conn = _conn()
        if conn is None:
            return
        import os

        from app.core import metrics

        with conn:
            conn.execute(
                "INSERT INTO breaker_state (key, open_until, updated_at, role, pid, reason) VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET open_until=MAX(open_until, excluded.open_until), "
                "updated_at=excluded.updated_at, role=excluded.role, pid=excluded.pid, reason=excluded.reason",
                (key, open_until, time.time(), metrics._process_role(), os.getpid(), reason[:200]),
            )
        conn.close()
        with _cache_lock:
            _cache[key] = (time.monotonic(), open_until)
    except Exception as exc:  # noqa: BLE001
        logger.debug("shared breaker publish failed %s: %s", key, exc)


_pause_cache: dict[str, tuple[float, float]] = {}
_pause_lock = threading.Lock()


def _pause_key(key: str) -> str:
    return f"{key}{PAUSE_KEY_SUFFIX}"


def publish_pause(key: str, seconds: float) -> float:
    """给某个 provider×dataset 打一个「限频暂停」标记，返回恢复时间戳。

    暂停**不是熔断**：只让 allow_request 在窗口内拒绝该源，网关据此立刻降级到下一个源；
    不写探针记录、不累加 probe streak、不改健康权重、不出熔断告警。到期自动失效。
    """
    now = time.time()
    until = now + max(1.0, float(seconds))
    pkey = _pause_key(key)
    try:
        conn = _conn()
        if conn is None:
            return until
        import os

        from app.core import metrics

        with conn:
            conn.execute(
                "INSERT INTO breaker_state (key, open_until, updated_at, role, pid, reason) VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET open_until=MAX(open_until, excluded.open_until), "
                "updated_at=excluded.updated_at, role=excluded.role, pid=excluded.pid, reason=excluded.reason",
                (pkey, until, now, metrics._process_role(), os.getpid(), "rate_limit_pause"),
            )
        conn.close()
        with _pause_lock:
            _pause_cache[pkey] = (time.monotonic(), until)
    except Exception as exc:  # noqa: BLE001
        logger.debug("shared breaker pause publish failed %s: %s", pkey, exc)
    return until


def shared_paused_until(key: str) -> float:
    pkey = _pause_key(key)
    now_m = time.monotonic()
    with _pause_lock:
        cached = _pause_cache.get(pkey)
        if cached is not None and now_m - cached[0] < SHARED_READ_TTL:
            return cached[1]
    value = 0.0
    try:
        conn = _conn()
        if conn is not None:
            row = conn.execute("SELECT open_until FROM breaker_state WHERE key=?", (pkey,)).fetchone()
            conn.close()
            value = float(row[0]) if row else 0.0
    except Exception as exc:  # noqa: BLE001
        logger.debug("shared breaker pause read failed %s: %s", pkey, exc)
    with _pause_lock:
        _pause_cache[pkey] = (now_m, value)
    return value


def clear_pause(key: str) -> None:
    pkey = _pause_key(key)
    with _pause_lock:
        cached = _pause_cache.get(pkey)
        _pause_cache[pkey] = (time.monotonic(), 0.0)
    if cached is not None and cached[1] <= 0:
        return  # 已知未暂停，成功请求不必每次都写盘
    try:
        conn = _conn()
        if conn is None:
            return
        with conn:
            conn.execute("DELETE FROM breaker_state WHERE key=?", (pkey,))
        conn.close()
    except Exception as exc:  # noqa: BLE001
        logger.debug("shared breaker pause clear failed %s: %s", pkey, exc)


def probe_backoff_delay(streak: int, *, base: float | None = None, cap: float | None = None, rng=None) -> float:
    """半开探测失败后的下一次冷却：base × 2^streak，封顶 cap，再加 0–10% 抖动（避免多进程同一秒一起探）。

    2026-10-26：原先每次探测失败都固定再冷却 300 秒，一个真挂了的源（如小石日K）每 5 分钟被探一次、
    一直重开熔断。改为 5→10→20→（封顶）分钟；成功后清零。streak=0 即 base（兼容旧行为）。"""
    import random

    base = float(base if base is not None else PROBE_INTERVAL_SECONDS)
    cap = float(cap if cap is not None else PROBE_MAX_SECONDS)
    delay = min(cap, base * (2 ** max(0, int(streak))))
    return delay + (rng or random).uniform(0.0, 0.1) * delay


def _get_streak(key: str) -> int:
    try:
        conn = _conn()
        if conn is None:
            return 0
        row = conn.execute("SELECT streak FROM breaker_probe_streak WHERE key=?", (key,)).fetchone()
        conn.close()
        return int(row[0]) if row else 0
    except Exception:  # noqa: BLE001
        return 0


def _bump_streak(key: str) -> int:
    try:
        conn = _conn()
        if conn is None:
            return 0
        with conn:
            conn.execute(
                "INSERT INTO breaker_probe_streak(key, streak, updated_at) VALUES (?,1,?) "
                "ON CONFLICT(key) DO UPDATE SET streak=streak+1, updated_at=excluded.updated_at", (key, time.time()))
            row = conn.execute("SELECT streak FROM breaker_probe_streak WHERE key=?", (key,)).fetchone()
        conn.close()
        return int(row[0]) if row else 1
    except Exception:  # noqa: BLE001
        return 0


def _publish_closed(key: str) -> None:
    with _cache_lock:
        cached = _cache.get(key)
        _cache[key] = (time.monotonic(), 0.0)
    if cached is not None and cached[1] <= 0:
        return  # already known closed: avoid a write on every success
    try:
        conn = _conn()
        if conn is None:
            return
        with conn:
            conn.execute("DELETE FROM breaker_state WHERE key=?", (key,))
            conn.execute("DELETE FROM breaker_probe_streak WHERE key=?", (key,))
        conn.close()
    except Exception as exc:  # noqa: BLE001
        logger.debug("shared breaker clear failed %s: %s", key, exc)


def shared_open_until(key: str) -> float:
    now_m = time.monotonic()
    with _cache_lock:
        cached = _cache.get(key)
        if cached is not None and now_m - cached[0] < SHARED_READ_TTL:
            return cached[1]
    value = 0.0
    try:
        conn = _conn()
        if conn is not None:
            row = conn.execute("SELECT open_until FROM breaker_state WHERE key=?", (key,)).fetchone()
            conn.close()
            value = float(row[0]) if row else 0.0
    except Exception as exc:  # noqa: BLE001
        logger.debug("shared breaker read failed %s: %s", key, exc)
    with _cache_lock:
        _cache[key] = (now_m, value)
    return value


def shared_breaker_states() -> list[dict[str, Any]]:
    """For /api/metrics/runtime: currently open shared breakers.

    ``kind`` 区分 ``breaker``（真熔断，走冷却/半开探测）与 ``pause``（限频暂停，仅跳过本源、
    网关立即降级），供前端与管理页分开呈现，避免把「限额等 60 秒」误报成「源挂了」。
    """
    try:
        conn = _conn()
        if conn is None:
            return []
        rows = conn.execute(
            "SELECT key, open_until, updated_at, role, pid, reason FROM breaker_state WHERE open_until > ?",
            (time.time(),),
        ).fetchall()
        conn.close()
    except Exception:  # noqa: BLE001
        return []
    out: list[dict[str, Any]] = []
    for r in rows:
        key = str(r[0])
        paused = key.endswith(PAUSE_KEY_SUFFIX)
        out.append(
            {
                "key": key[: -len(PAUSE_KEY_SUFFIX)] if paused else key,
                "open_until": r[1],
                "updated_at": r[2],
                "role": r[3],
                "pid": r[4],
                "reason": r[5],
                "kind": "pause" if paused else "breaker",
            }
        )
    return out


# Unified cooldown / half-open policy (user, 2026-10-19): an open breaker stays open at least
# PROBE_INTERVAL_SECONDS; then exactly ONE process (per namespace) claims the half-open probe via an
# atomic UPDATE on the SQLite row. Probe success deletes the row (closed); failure re-opens it for
# another PROBE_INTERVAL_SECONDS. A crashed prober's claim expires after PROBE_LEASE_SECONDS.
PROBE_INTERVAL_SECONDS = 300.0
PROBE_LEASE_SECONDS = 60.0
def _env_float(name: str, default: float) -> float:
    """读数值型开关：进程环境变量 → backend/.env（pydantic 不会把 .env 导出到 os.environ）。"""
    from app.core.runtime_env import env_value

    try:
        return float(env_value(name, default=str(default)) or default)
    except (TypeError, ValueError):
        return default


# ── 限频暂停（2026-11-15 二次重设，用户指令）──────────────────────────────
# 命中上游次数上限时**不熔断数据源**，只做「暂停 + 降级到下一个源」：不动熔断状态、不进半开
# 探测、不累加 probe streak、不影响健康权重。真正熔断只由故障类失败（timeout / bad_data / 5xx /
# permission）与日额度耗尽触发。理由：限频是额度信号而非故障，而熔断的副作用（健康降权、半开
# 探针、告警、长冷却）会把「一分钟后就能恢复的额度」放大成整个源停摆——免费额度下尤其明显。
# 实测三个源的上游窗口都是分钟级：promax 直接回「请在 56 秒后重试」（IP 200/min 硬窗）、
# teajoin 在 550 次/分钟处回 `rate limit exceeded`、quantdata 高并发 2.6s 即 50302。
# 上游给了 retry-after / 文案里给了秒数，就用上游的；否则用下面的默认值。
PAUSE_KEY_SUFFIX = "|pause"
PAUSE_BASE_SECONDS = _env_float("ASHARE_RATE_LIMIT_PAUSE_SECONDS", 60.0)
PAUSE_MAX_SECONDS = 300.0
# 日额度耗尽不适用上面的短暂停：当天已无额度，按「故障级」长冷却处理。
QUOTA_EXHAUSTED_SECONDS = _env_float("ASHARE_QUOTA_EXHAUSTED_SECONDS", 3600.0)
# 探测连续失败时冷却指数拉长的上限（默认 10 分钟）。原 1200s（20min）在免费/小窗口额度下过长：
# 实测上游恢复都在 60~90s 量级，指数退避封顶压到 600s 即可防抖又不过度停摆。
PROBE_MAX_SECONDS = _env_float("ASHARE_BREAKER_PROBE_MAX_SECONDS", 600.0)


def _try_claim_probe(key: str) -> bool | None:
    """True = this process owns the probe; False = shared row still open / someone else probing;
    None = shared store unavailable (caller falls back to local breaker)."""
    try:
        conn = _conn()
        if conn is None:
            return None
        import os

        from app.core import metrics

        now = time.time()
        with conn:
            cur = conn.execute(
                "UPDATE breaker_state SET open_until=?, updated_at=?, role=?, pid=?, reason='half_open_probe' "
                "WHERE key=? AND open_until<=?",
                (now + PROBE_LEASE_SECONDS, now, metrics._process_role(), os.getpid(), key, now),
            )
            claimed = cur.rowcount == 1
            row = None if claimed else conn.execute("SELECT open_until FROM breaker_state WHERE key=?", (key,)).fetchone()
        conn.close()
        with _cache_lock:
            _cache[key] = (time.monotonic(), now + PROBE_LEASE_SECONDS if claimed else (float(row[0]) if row else 0.0))
        if claimed:
            return True
        return False if row else True  # row vanished = closed meanwhile
    except Exception as exc:  # noqa: BLE001
        logger.debug("shared breaker probe claim failed %s: %s", key, exc)
        return None


def is_open(key: str) -> bool:
    """Generic pre-request check for an un-namespaced breaker key (e.g. an upstream transport key):
    True while the current namespace's shared row says open (half-open probes not claimed here)."""
    return shared_open_until(f"{breaker_namespace()}:{key}") > time.time()


# Per-kind thresholds (configurable: ASHARE_BREAKER_THRESHOLD_TIMEOUT / _BAD_DATA).
# Per-provider overrides (2026-11-15): one global 2/3 fits nobody — teajoin (primary, 98.6% success)
# should not trip on two transient timeouts, while promax (pool-exhaustion failures, 79.9% empty on
# eastmoney) needs to open fast. ASHARE_BREAKER_THRESHOLD_OVERRIDES='{"promax":{"timeout":1}}'.
def _provider_overrides() -> dict[str, dict[str, int]]:
    from app.core.config import get_settings

    raw = getattr(get_settings(), "breaker_threshold_overrides", None) or {}
    if isinstance(raw, dict):
        return {str(k): v for k, v in raw.items() if isinstance(v, dict)}
    return {}


def _provider_of(key: str | None) -> str | None:
    """Breaker key 里的 provider 段：``gw:teajoin:bar.minute`` → ``teajoin``。

    兼容 ``{namespace}:{provider}:...``（SharedCircuitBreaker 会拼上命名空间前缀）
    和裸 key（``promax``），取第一个能命中覆盖表的分段。
    """
    if not key:
        return None
    overrides = _provider_overrides()
    if not overrides:
        return None
    for part in str(key).split(":"):
        if part in overrides:
            return part
    return None


def kind_threshold(kind: str | None, key: str | None = None) -> int | None:
    # rate_limit 也纳入可配阈值（2026-11-15 实测）：旧逻辑「1 次限频即熔断」，而上游限频在
    # 免费/小额度下是常态噪音（teajoin 550/min、quantdata 并发抖动都只影响单次请求），
    # 单发就整源停摆 5 分钟比限频本身代价更大。默认连续 2 次才熔断，需要旧行为可配 1。
    # 注意：rate_limit 已改为「暂停+降级」，不再有熔断阈值；此处保留其读取路径仅为向后兼容
    # 旧配置项（breaker_threshold_rate_limit / .env 里的 provider overrides），实际不再生效。
    if kind not in ("timeout", "bad_data", "rate_limit"):
        return None
    default = {"timeout": 2, "bad_data": 3, "rate_limit": 2}[kind]
    from app.core.config import get_settings

    base = max(1, int(getattr(get_settings(), f"breaker_threshold_{kind}", default)))
    provider = _provider_of(key)
    if provider:
        spec = _provider_overrides().get(provider) or {}
        if kind in spec:
            return max(1, int(spec[kind]))
    return base


class SharedCircuitBreaker:
    """Drop-in for tushare_transport.CircuitBreaker with cross-process open state and a single
    cross-process half-open probe every PROBE_INTERVAL_SECONDS."""

    def __init__(self, key: str, failure_threshold: int, recovery_seconds: float) -> None:
        from app.services.tushare_transport import CircuitBreaker

        self.base_key = key
        self.key = f"{breaker_namespace()}:{key}"
        self._recovery = max(float(recovery_seconds), PROBE_INTERVAL_SECONDS)
        self._local = CircuitBreaker(failure_threshold, self._recovery)
        self._probing = False
        self._kind_counts: dict[str, int] = {}
        self._kind_lock = threading.Lock()

    def allow_request(self) -> bool:
        # 限频暂停优先：只跳过本源的这一段窗口，让网关换下一个源，不进入熔断/半开状态机。
        if shared_paused_until(self.key) > time.time():
            return False
        open_until = shared_open_until(self.key)
        if open_until > time.time():
            return False
        if open_until > 0:  # cooldown elapsed: race for the one shared probe
            claimed = _try_claim_probe(self.key)
            if claimed is False:
                return False
            if claimed is True:
                self._probing = True
                self._local.record_success()  # shared state governs; local half-open would double-gate
                return True
        return self._local.allow_request()

    def record_success(self) -> None:
        self._probing = False
        with self._kind_lock:
            self._kind_counts.clear()
        self._local.record_success()
        _publish_closed(self.key)
        clear_pause(self.key)

    def record_failure(
        self, *, force_open: bool = False, recovery_seconds: float | None = None, kind: str | None = None
    ) -> None:
        """按失败类型分流（2026-11-15 重设）：

        - ``rate_limit``：**不熔断**，只打限频暂停（默认 60s 或上游 retry-after），窗口内
          allow_request 拒绝本源的请求，网关立即降级到下一个源；到期自动恢复，无需半开探测。
        - ``quota_exhausted``：日额度耗尽属「当天不可再用」，按故障级长冷却（默认 1h）真熔断。
        - ``timeout`` / ``bad_data``：按各自可配阈值熔断；其他走 force_open / failure_threshold。
        """
        kind = (kind or "").lower() or None
        if self._probing:
            self._probing = False
            streak = _bump_streak(self.key)
            self._open(probe_backoff_delay(streak - 1 if streak > 0 else 0), f"probe_failed#{streak}")
            return
        if kind == "rate_limit":
            # 关键：不调用 _open。限频是额度信号而非故障，熔断会顺带降权健康分、触发半开探针
            # 与告警，把一个「等 60 秒就能用」的源变成「每分钟被降级一次」的坏源。
            delay = (
                float(recovery_seconds)
                if recovery_seconds and recovery_seconds > 0
                else PAUSE_BASE_SECONDS
            )
            publish_pause(self.key, delay)
            return
        if kind == "quota_exhausted":
            self._open(max(float(recovery_seconds or 0.0), QUOTA_EXHAUSTED_SECONDS), "quota_exhausted")
            return
        if force_open:
            self._open(max(self._recovery, float(recovery_seconds or 0.0)), "force_open")
            return
        threshold = kind_threshold(kind, self.base_key)
        if threshold is None:
            before = self._local.state
            self._local.record_failure(recovery_seconds=recovery_seconds)
            if self._local.state == "open" and before != "open":
                self._publish_local("threshold")
            return
        with self._kind_lock:
            n = self._kind_counts.get(kind, 0) + 1
            self._kind_counts[kind] = n
        if n >= threshold:
            self._open(self._recovery, f"{kind}x{n}")

    def _open(self, delay: float, reason: str) -> None:
        loc = self._local
        with loc._lock:
            loc._open_until = time.monotonic() + max(0.1, float(delay))
            loc._half_open_probe = False
            loc._failure_count = 0
        with self._kind_lock:
            self._kind_counts.clear()
        self._publish_local(reason)

    def _publish_local(self, reason: str) -> None:
        remaining = max(0.0, self._local._open_until - time.monotonic())
        _publish_open(self.key, time.time() + remaining, reason=reason)

    @property
    def state(self) -> str:
        local = self._local.state
        if local != "closed":
            return local
        ou = shared_open_until(self.key)
        if ou <= 0:
            return "closed"
        return "open" if ou > time.time() else "half_open"
