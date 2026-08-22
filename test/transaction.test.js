import assert from "node:assert/strict";
import test from "node:test";
import { normalizeTransaction, parseTimestamp, sameTransaction, ValidationError } from "../src/transaction.js";

test("timestamp parser preserves nanoseconds and normalizes offsets", () => {
  assert.equal(
    parseTimestamp("2026-06-08T12:00:00.123456789Z"),
    parseTimestamp("2026-06-08T20:00:00.123456789+08:00")
  );
  assert.equal(
    parseTimestamp("2026-06-08T12:00:00.000000001Z") - parseTimestamp("2026-06-08T12:00:00Z"),
    1n
  );
});

test("timestamp parser validates calendar and offset boundaries", () => {
  assert.doesNotThrow(() => parseTimestamp("2024-02-29T23:59:59Z"));
  for (const value of [
    "2026-02-29T00:00:00Z",
    "2026-13-01T00:00:00Z",
    "2026-01-01 00:00:00Z",
    "2026-01-01T24:00:00Z",
    "2026-01-01T00:00:00+24:00",
    "not-a-date"
  ]) {
    assert.throws(() => parseTimestamp(value), ValidationError, value);
  }
});

test("unknown fields are ignored and missing optional identities are observable", () => {
  const base = {
    txId: "id",
    fromUserId: "from",
    toUserId: "to",
    amount: 1,
    createdAt: "2026-06-08T12:00:00Z"
  };
  const missing = normalizeTransaction({ ...base, unknown: { nested: true } });
  const present = normalizeTransaction({ ...base, deviceId: "" });
  const nullIdentity = normalizeTransaction({ ...base, deviceId: null });

  assert.deepEqual(missing.deviceId, { present: false, value: null });
  assert.deepEqual(present.deviceId, { present: true, value: "" });
  assert.ok(sameTransaction(missing, nullIdentity));
  assert.ok(!sameTransaction(missing, present));
});

test("normalization rejects malformed required values", () => {
  const valid = {
    txId: "id",
    fromUserId: "from",
    toUserId: "to",
    amount: 1,
    createdAt: "2026-06-08T12:00:00Z"
  };
  for (const mutation of [
    { txId: "" },
    { fromUserId: null },
    { toUserId: 42 },
    { amount: "1" },
    { amount: -1 },
    { createdAt: null },
    { ipAddress: 123 }
  ]) {
    assert.throws(() => normalizeTransaction({ ...valid, ...mutation }), ValidationError);
  }
});
