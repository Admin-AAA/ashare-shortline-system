import assert from "node:assert/strict";
import test from "node:test";

import {
  DEFAULT_SORT,
  horizonMetrics,
  isSunk,
  sortStrategies,
  standaloneMetrics,
  type SortState,
} from "../src/utils/strategyRank.ts";

type Item = { id: string; rank_score: number | null; rank_flags: string[]; ret?: number | null; win?: number | null; dd?: number | null; rec?: number | null };
const rankOf = (t: Item) => t;
const metricsOf = (t: Item) => ({ return: t.ret, winrate: t.win, drawdown: t.dd, recent: t.rec });
const ids = (xs: Item[]) => xs.map((x) => x.id);

const A: Item = { id: "A", rank_score: 80, rank_flags: [], ret: 10, win: 50, dd: -10, rec: 1 };
const B: Item = { id: "B", rank_score: 60, rank_flags: [], ret: 30, win: 70, dd: -20, rec: 3 };
const C: Item = { id: "C", rank_score: 90, rank_flags: [], ret: 20, win: 60, dd: -5, rec: 2 };
const S1: Item = { id: "S1", rank_score: null, rank_flags: ["insufficient_sample"], ret: 99, win: 99, dd: 0, rec: 9 };
const S2: Item = { id: "S2", rank_score: null, rank_flags: ["missing_metrics"] };
const items = [A, S1, B, S2, C];
const run = (sort: SortState) => ids(sortStrategies(items, rankOf, metricsOf, sort));

test("default is composite desc with sunk at the bottom in original order", () => {
  assert.deepEqual(DEFAULT_SORT, { key: "composite", dir: "desc" });
  assert.deepEqual(ids(sortStrategies(items, rankOf, metricsOf)), ["C", "A", "B", "S1", "S2"]);
});

test("ascending composite still keeps sunk last", () => {
  assert.deepEqual(run({ key: "composite", dir: "asc" }), ["B", "A", "C", "S1", "S2"]);
});

test("metric sorts: return / winrate / recent desc, drawdown desc = smallest drawdown first", () => {
  assert.deepEqual(run({ key: "return", dir: "desc" }), ["B", "C", "A", "S1", "S2"]);
  assert.deepEqual(run({ key: "winrate", dir: "desc" }), ["B", "C", "A", "S1", "S2"]);
  assert.deepEqual(run({ key: "recent", dir: "desc" }), ["B", "C", "A", "S1", "S2"]);
  assert.deepEqual(run({ key: "drawdown", dir: "desc" }), ["C", "A", "B", "S1", "S2"]);
  assert.deepEqual(run({ key: "drawdown", dir: "asc" }), ["B", "A", "C", "S1", "S2"]);
});

test("ties and missing values are stable; missing sorts after live values", () => {
  const T1: Item = { id: "T1", rank_score: 50, rank_flags: [], ret: 5 };
  const T2: Item = { id: "T2", rank_score: 50, rank_flags: [], ret: 5 };
  const N: Item = { id: "N", rank_score: 70, rank_flags: [], ret: null };
  assert.deepEqual(ids(sortStrategies([T1, N, T2], rankOf, metricsOf, { key: "composite", dir: "desc" })), ["N", "T1", "T2"]);
  assert.deepEqual(ids(sortStrategies([T2, T1, N], rankOf, metricsOf, { key: "return", dir: "desc" })), ["T2", "T1", "N"]);
  assert.deepEqual(ids(sortStrategies([N, T1, T2], rankOf, metricsOf, { key: "return", dir: "asc" })), ["T1", "T2", "N"]);
});

test("does not mutate input", () => {
  const copy = [...items];
  run({ key: "return", dir: "asc" });
  assert.deepEqual(items, copy);
});

test("isSunk", () => {
  assert.equal(isSunk(undefined), true);
  assert.equal(isSunk({ rank_score: 10, rank_flags: [] }), false);
  assert.equal(isSunk({ rank_score: 10, rank_flags: ["stale_data"] }), true);
  assert.equal(isSunk({ rank_score: null, rank_flags: [] }), true);
});

test("metric extractors", () => {
  assert.deepEqual(
    horizonMetrics({ metrics: { recent_12m: { win: 50, avg: 2, tot: 3, mdd: -4 } }, rank_components: { tot_20: 4.5, mdd_20: -6 } }),
    { return: 4.5, winrate: 50, drawdown: -6, recent: 2 },
  );
  assert.deepEqual(horizonMetrics({ metrics: { recent_12m: { win: 50, avg: 2, tot: 3, mdd: -4 } } }),
    { return: 3, winrate: 50, drawdown: -4, recent: 2 });
  assert.deepEqual(
    standaloneMetrics({ last_12m: { win_rate: 60, avg_return_pct: 1.5, max_drawdown_pct: -9 }, recent: { avg_return_pct: 2 } }),
    { return: 1.5, winrate: 60, drawdown: -9, recent: 2 },
  );
  assert.deepEqual(standaloneMetrics({}), { return: null, winrate: null, drawdown: null, recent: null });
});
