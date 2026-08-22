import assert from "node:assert/strict";
import test from "node:test";
import { RiskEngine } from "../src/risk-engine.js";
import { normalizeTransaction } from "../../phase-1/src/transaction.js";

function tx(index, from, to, identity = {}) {
  return normalizeTransaction({
    txId: `identity-${index}`,
    fromUserId: from,
    toUserId: to,
    amount: 100,
    createdAt: `2026-06-08T12:${String(index).padStart(2, "0")}:00Z`,
    ...identity
  });
}

function finalScore(edges) {
  const engine = new RiskEngine();
  let score;
  edges.forEach(([from, to, identity], index) => {
    score = engine.processBatch([tx(index, from, to, identity)])[0].riskScore;
  });
  return score;
}

test("identity-free Phase 1 scoring remains unchanged", () => {
  const engine = new RiskEngine();
  assert.equal(engine.processBatch([tx(0, "a", "b")])[0].riskScore, 0.02);
});

test("agreement is weaker evidence than a device shift on the same path", () => {
  const prefix = [
    ["a", "b", { deviceId: "device-a" }],
    ["b", "c", { deviceId: "device-a" }]
  ];
  const agreement = finalScore([...prefix, ["c", "d", { deviceId: "device-a" }]]);
  const shift = finalScore([...prefix, ["c", "d", { deviceId: "device-b" }]]);
  assert.ok(shift > agreement, `${shift} should exceed ${agreement}`);
});

test("dropping identity on a connected flow is suspicious but isolated absence is not", () => {
  const isolatedMissing = finalScore([["x", "y", {}]]);
  const connectedMissing = finalScore([
    ["a", "b", { ipAddress: "192.0.2.10" }],
    ["b", "c", {}]
  ]);
  assert.equal(isolatedMissing, 0.02);
  assert.ok(connectedMissing > isolatedMissing * 3);
});

test("IP and device contribute independently", () => {
  const prefix = [["a", "b", { ipAddress: "ip-a", deviceId: "device-a" }]];
  const oneShift = finalScore([...prefix, ["b", "c", { ipAddress: "ip-b", deviceId: "device-a" }]]);
  const twoShifts = finalScore([...prefix, ["b", "c", { ipAddress: "ip-b", deviceId: "device-b" }]]);
  assert.ok(twoShifts > oneShift);
});

test("reuse across additional disconnected components accumulates cautiously", () => {
  const shared = { ipAddress: "203.0.113.7" };
  const first = finalScore([["a", "b", shared]]);
  const second = finalScore([["a", "b", shared], ["c", "d", shared]]);
  const third = finalScore([["a", "b", shared], ["c", "d", shared], ["e", "f", shared]]);
  assert.ok(second > first);
  assert.ok(third > second);
  assert.ok(second < 0.15, "one cross-component match should remain weak evidence");
});

test("identity evidence expires at the exact 24-hour boundary", () => {
  const engine = new RiskEngine();
  engine.processBatch([normalizeTransaction({
    txId: "old", fromUserId: "a", toUserId: "b", amount: 1,
    createdAt: "2026-06-08T00:00:00Z", deviceId: "shared"
  })]);
  const result = engine.processBatch([normalizeTransaction({
    txId: "boundary", fromUserId: "c", toUserId: "d", amount: 1,
    createdAt: "2026-06-09T00:00:00Z", deviceId: "shared"
  })])[0];
  assert.equal(result.riskScore, 0.02);
});

test("reset and idempotent replay do not retain or duplicate identity evidence", () => {
  const engine = new RiskEngine();
  const original = tx(0, "a", "b", { deviceId: "shared" });
  const first = engine.processBatch([original])[0];
  assert.deepEqual(engine.processBatch([original])[0], first);
  engine.reset();
  assert.deepEqual(engine.processBatch([original])[0], first);
});
