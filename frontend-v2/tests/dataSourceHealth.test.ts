import assert from "node:assert/strict";
import test from "node:test";

import { countDegradedDataSources, countHistoricalLowSuccessSources } from "../src/utils/dataSourceHealth.ts";

test("recovered sources with poor cumulative history are not counted as degraded", () => {
  const rows = [
    { status: "healthy", success_rate: 0.158 },
    { status: "healthy", success_rate: 1 },
    { status: "degraded", success_rate: 0.2 },
  ];

  assert.equal(countDegradedDataSources(rows), 1);
  assert.equal(countHistoricalLowSuccessSources(rows), 1);
});

test("disabled and untested sources are not reported as degraded", () => {
  assert.equal(
    countDegradedDataSources([
      { status: "disabled", success_rate: 0 },
      { status: "untested", success_rate: 0 },
    ]),
    0,
  );
});
