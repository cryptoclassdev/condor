import assert from "node:assert/strict";
import test from "node:test";

import { classifyChatReconnect } from "../src/lib/chatReconnect.ts";

test("an unauthorized auth probe logs out instead of retrying forever", () => {
  assert.equal(classifyChatReconnect(401), "logout");
  assert.equal(classifyChatReconnect(403), "logout");
});

test("transient failures remain retryable", () => {
  assert.equal(classifyChatReconnect(503), "retry");
  assert.equal(classifyChatReconnect(null), "retry");
});
