import type { StockMatrixAnalysisResult, StockMatrixBacktestResult } from "../types";

const BASE = "";

async function request<T>(url: string, init?: RequestInit, timeoutMs = 30_000): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(`${BASE}${url}`, { ...init, signal: controller.signal });
    if (!res.ok) {
      const body = await res.text().catch(() => "");
      throw new Error(`${res.status} ${res.statusText}: ${body}`);
    }
    return (await res.json()) as T;
  } finally {
    clearTimeout(timer);
  }
}

function json(method: string, body?: unknown): RequestInit {
  return {
    method,
    headers: { "Content-Type": "application/json" },
    body: body != null ? JSON.stringify(body) : undefined,
  };
}

export const stockMatrixApi = {
  analysis: (payload: {
    code: string;
    trade_date?: string;
    include_llm?: boolean;
    skill_ids?: string[];
  }) =>
    request<StockMatrixAnalysisResult>(
      "/api/stock-matrix/analysis",
      json("POST", payload),
      payload.include_llm ? 600_000 : 30_000,
    ),

  backtest: (payload: {
    code: string;
    start_date: string;
    end_date: string;
    horizon_days?: number;
    skill_ids?: string[];
  }) =>
    request<StockMatrixBacktestResult>(
      "/api/stock-matrix/backtest",
      json("POST", payload),
      120_000,
    ),
};
