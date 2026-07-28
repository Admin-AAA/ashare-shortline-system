import assert from "node:assert/strict";
import test from "node:test";

import { isOutcomeSettled } from "../src/utils/display.ts";

test("provisional outcome remains unsettled for realtime pnl", () => {
  assert.equal(isOutcomeSettled("success"), true);
  assert.equal(isOutcomeSettled("failed"), true);
  assert.equal(isOutcomeSettled("missed"), true);
  assert.equal(isOutcomeSettled("provisional"), false);
  assert.equal(isOutcomeSettled("pending"), false);
  assert.equal(isOutcomeSettled(undefined), false);
});
