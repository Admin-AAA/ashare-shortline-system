type LogLevel = "debug" | "info" | "warn" | "error" | "perf";

interface LogEntry {
  timestamp: number;
  iso: string;
  level: LogLevel;
  category: string;
  message: string;
  duration?: number;
  extra?: Record<string, unknown>;
}

const MAX_BUFFER = 500;
const buffer: LogEntry[] = [];

function nowISO() {
  return new Date().toISOString();
}

/** 关键日志回传后端 */
let backendLogFlushTimer: ReturnType<typeof setTimeout> | null = null;
const backendLogQueue: LogEntry[] = [];
const BACKEND_LOG_FLUSH_INTERVAL = 5000;
const BACKEND_LOG_MIN_LEVELS = new Set<LogLevel>(["warn", "error", "perf"]);

function enqueueBackendLog(entry: LogEntry) {
  if (!BACKEND_LOG_MIN_LEVELS.has(entry.level)) return;
  backendLogQueue.push(entry);
  if (!backendLogFlushTimer) {
    backendLogFlushTimer = setTimeout(flushBackendLogs, BACKEND_LOG_FLUSH_INTERVAL);
  }
}

async function flushBackendLogs() {
  backendLogFlushTimer = null;
  if (backendLogQueue.length === 0) return;
  const batch = backendLogQueue.splice(0);
  try {
    await fetch("/api/frontend-logs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(
        batch.map((e) => ({
          level: e.level,
          category: e.category,
          message: e.message,
          extra: { ...e.extra, duration: e.duration },
        })),
      ),
    });
  } catch {
    // 静默失败，不影响前端
  }
}

function emit(entry: LogEntry) {
  buffer.push(entry);
  if (buffer.length > MAX_BUFFER) buffer.shift();

  const prefix = `[${entry.iso}] [${entry.level.toUpperCase()}] ${entry.category}:`;
  const extra = entry.duration !== undefined ? ` (${entry.duration}ms)` : "";
  const meta = entry.extra ? ` ${JSON.stringify(entry.extra)}` : "";

  switch (entry.level) {
    case "error":
      console.error(`${prefix} ${entry.message}${extra}${meta}`);
      break;
    case "warn":
    case "perf":
      console.warn(`${prefix} ${entry.message}${extra}${meta}`);
      break;
    case "info":
      console.info(`${prefix} ${entry.message}${extra}${meta}`);
      break;
    default:
      console.debug(`${prefix} ${entry.message}${extra}${meta}`);
  }

  enqueueBackendLog(entry);
}

function log(level: LogLevel, category: string, message: string, extra?: Record<string, unknown>, duration?: number) {
  emit({ timestamp: Date.now(), iso: nowISO(), level, category, message, duration, extra });
}

export const logger = {
  debug: (category: string, message: string, extra?: Record<string, unknown>) =>
    log("debug", category, message, extra),
  info: (category: string, message: string, extra?: Record<string, unknown>) =>
    log("info", category, message, extra),
  warn: (category: string, message: string, extra?: Record<string, unknown>) =>
    log("warn", category, message, extra),
  error: (category: string, message: string, extra?: Record<string, unknown>) =>
    log("error", category, message, extra),
  perf: (category: string, message: string, extra?: Record<string, unknown>) =>
    log("perf", category, message, extra),
  api: (method: string, path: string, duration: number, status: number) =>
    log(status >= 400 ? "warn" : "info", "api", `${method} ${path} → ${status}`, undefined, duration),
  stateChange: (key: string, reason: string) =>
    log("debug", "state", `${key} changed (${reason})`),
  perfMark: (label: string) => {
    const start = performance.now();
    return {
      end: (extra?: Record<string, unknown>) => {
        const duration = Math.round(performance.now() - start);
        log(duration > 1000 ? "warn" : "debug", "perf", label, extra, duration);
        return duration;
      },
    };
  },
  getBuffer: () => [...buffer],
  clearBuffer: () => {
    buffer.length = 0;
  },
  exportRecent: (n = 100) => {
    const slice = buffer.slice(-n);
    return slice
      .map(
        (e) =>
          `${e.iso} [${e.level.toUpperCase()}] ${e.category}: ${e.message}${
            e.duration !== undefined ? ` (${e.duration}ms)` : ""
          }${e.extra ? ` ${JSON.stringify(e.extra)}` : ""}`,
      )
      .join("\n");
  },
};

/** 包装 fetch，自动记录耗时和状态 */
export async function loggedFetch(
  input: RequestInfo,
  init?: RequestInit,
): Promise<Response> {
  const method = init?.method ?? "GET";
  const path = typeof input === "string" ? input : input.url;
  const mark = logger.perfMark(`fetch ${method} ${path}`);
  try {
    const res = await fetch(input, init);
    mark.end();
    logger.api(method, path, mark.end(), res.status);
    return res;
  } catch (err) {
    mark.end();
    logger.error("api", `${method} ${path} failed`, { error: String(err) });
    throw err;
  }
}

/** 长任务检测：主线程被阻塞超过阈值时记录 */
let longTaskObserver: PerformanceObserver | null = null;
let longTaskCount = 0;

export function startLongTaskMonitor(thresholdMs = 100) {
  if (longTaskObserver) return;
  if (typeof PerformanceObserver === "undefined") {
    logger.warn("perf", "浏览器不支持 PerformanceObserver，无法监控长任务");
    return;
  }
  try {
    longTaskObserver = new PerformanceObserver((list) => {
      for (const entry of list.getEntries()) {
        longTaskCount += 1;
        const duration = Math.round(entry.duration);
        logger.perf("longtask", `检测到主线程卡顿! #${longTaskCount}`, {
          duration,
          startTime: Math.round(entry.startTime),
          attribution: (entry as any).attribution?.[0]?.name ?? "unknown",
        });
        if (duration > 500) {
          logger.warn("longtask", `严重卡顿! 主线程阻塞 ${duration}ms`, {
            recentLogs: logger.exportRecent(20),
          });
        }
      }
    });
    longTaskObserver.observe({ type: "longtask", buffered: true });
    logger.info("perf", `长任务监控已启动 (阈值=${thresholdMs}ms)`);
  } catch (err) {
    logger.warn("perf", "启动长任务监控失败", { error: String(err) });
  }
}

export function stopLongTaskMonitor() {
  longTaskObserver?.disconnect();
  longTaskObserver = null;
  longTaskCount = 0;
}

export function getLongTaskStats() {
  return { count: longTaskCount, active: longTaskObserver !== null };
}
