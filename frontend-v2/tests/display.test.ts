import assert from "node:assert/strict";
import test from "node:test";

import { exitReasonLabel, isOutcomeSettled } from "../src/utils/display.ts";

test("not-triggered exits use the settled outcome wording", () => {
  assert.equal(exitReasonLabel("not_triggered"), "未触发");
  assert.equal(exitReasonLabel("entry_not_triggered"), "未触发");
});

test("provisional outcome remains unsettled for realtime pnl", () => {
  assert.equal(isOutcomeSettled("success"), true);
  assert.equal(isOutcomeSettled("failed"), true);
  assert.equal(isOutcomeSettled("missed"), true);
  assert.equal(isOutcomeSettled("provisional"), false);
  assert.equal(isOutcomeSettled("pending"), false);
  assert.equal(isOutcomeSettled(undefined), false);
});
