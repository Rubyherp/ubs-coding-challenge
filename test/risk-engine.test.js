import assert from "node:assert/strict";
import test from "node:test";
import { DirectedGraph } from "../src/directed-graph.js";
import { calibrateRisk, DuplicateConflictError, RiskEngine } from "../src/risk-engine.js";
import { normalizeTransaction } from "../src/transaction.js";

const BASE = "2026-06-08T12:00:00Z";

function transaction(txId, fromUserId, toUserId, createdAt = BASE, extra = {}) {
  return normalizeTransaction({ txId, fromUserId, toUserId, amount: 100, createdAt, ...extra });
}

function scoreSequence(edges) {
  const engine = new RiskEngine();
  let finalScore = 0;
  edges.forEach(([from, to], index) => {
    const minute = String(index).padStart(2, "0");
    const [result] = engine.processBatch([
      transaction(`tx-${index}`, from, to, `2026-06-08T12:${minute}:00Z`)
    ]);
    finalScore = result.riskScore;
  });
  return finalScore;
}

test("the five Phase 1 examples have coherent structural ordering", () => {
  const isolated = scoreSequence([["meridian", "apex"]]);
  const extension = scoreSequence([["meridian", "apex"], ["apex", "cascade"]]);
  const convergence = scoreSequence([
    ["meridian", "apex"],
    ["meridian", "horizon"],
    ["apex", "sterling"],
    ["horizon", "sterling"]
  ]);
  const returning = scoreSequence([
    ["meridian", "apex"],
    ["apex", "cascade"],
    ["cascade", "oakridge"],
    ["oakridge", "apex"]
  ]);
  const multiLoop = scoreSequence([
    ["meridian", "apex"],
    ["apex", "cascade"],
    ["cascade", "meridian"],
    ["apex", "nimbus"],
    ["nimbus", "meridian"]
  ]);

  assert.ok(isolated < extension, `${isolated} should be below ${extension}`);
  assert.ok(extension < convergence, `${extension} should be below ${convergence}`);
  assert.ok(convergence < returning, `${convergence} should be below ${returning}`);
  assert.ok(returning < multiLoop, `${returning} should be below ${multiLoop}`);
  assert.ok(multiLoop < 1, "complex patterns should retain headroom for stronger structures");
  assert.deepEqual(
    [isolated, extension, convergence, returning, multiLoop],
    [0.02, 0.20, 0.40, 0.70, 0.90]
  );
  for (const score of [isolated, extension, convergence, returning, multiLoop]) {
    assert.ok(score >= 0 && score <= 1);
  }
});

test("batch transactions are processed sequentially and returned in input order", () => {
  const engine = new RiskEngine();
  const results = engine.processBatch([
    transaction("one", "a", "b"),
    transaction("two", "b", "c", "2026-06-08T12:01:00Z"),
    transaction("three", "c", "a", "2026-06-08T12:02:00Z")
  ]);

  assert.deepEqual(results.map(({ txId }) => txId), ["one", "two", "three"]);
  assert.ok(results[0].riskScore < results[1].riskScore);
  assert.ok(results[1].riskScore < results[2].riskScore);
});

test("an identical duplicate returns its original score and does not add an edge", () => {
  const engine = new RiskEngine();
  const original = transaction("same", "a", "b", "2026-06-08T12:00:00.000000000Z", {
    ipAddress: "192.0.2.1"
  });
  const equivalent = transaction("same", "a", "b", "2026-06-08T20:00:00+08:00", {
    ipAddress: "192.0.2.1",
    ignoredFutureField: "accepted"
  });

  const first = engine.processBatch([original])[0];
  const second = engine.processBatch([equivalent])[0];
  assert.deepEqual(second, first);
  assert.equal(engine.diagnostics().activeTransactions, 1);

  const reverse = engine.processBatch([
    transaction("reverse", "b", "a", "2026-06-08T12:01:00Z")
  ])[0];
  assert.ok(reverse.riskScore > first.riskScore * 5);
});

test("conflicting IDs reject the whole batch before state changes", () => {
  const engine = new RiskEngine();
  const before = engine.diagnostics();

  assert.throws(() => engine.processBatch([
    transaction("collision", "a", "b"),
    transaction("collision", "a", "c")
  ]), DuplicateConflictError);

  assert.deepEqual(engine.diagnostics(), before);
});

test("a conflict with a previously accepted ID also leaves state unchanged", () => {
  const engine = new RiskEngine();
  engine.processBatch([transaction("existing", "a", "b")]);
  const before = engine.diagnostics();

  assert.throws(() => engine.processBatch([
    transaction("would-have-been-added", "b", "c", "2026-06-08T12:01:00Z"),
    transaction("existing", "x", "y")
  ]), DuplicateConflictError);

  assert.deepEqual(engine.diagnostics(), before);
});

test("transactions exactly 24 hours old are outside the window", () => {
  const engine = new RiskEngine();
  const isolated = engine.processBatch([transaction("old", "a", "b", "2026-06-08T00:00:00Z")])[0];
  const boundary = engine.processBatch([transaction("boundary", "b", "c", "2026-06-09T00:00:00Z")])[0];

  assert.equal(boundary.riskScore, isolated.riskScore);
  assert.equal(engine.diagnostics().activeTransactions, 1);
});

test("transactions older than 24 hours by one nanosecond expire", () => {
  const engine = new RiskEngine();
  const first = engine.processBatch([transaction("old", "a", "b", "2026-06-08T00:00:00.000000000Z")])[0];
  const after = engine.processBatch([transaction("after", "b", "c", "2026-06-09T00:00:00.000000001Z")])[0];

  assert.equal(after.riskScore, first.riskScore);
  assert.equal(engine.diagnostics().activeTransactions, 1);
});

test("transactions one nanosecond inside the 24-hour boundary remain active", () => {
  const engine = new RiskEngine();
  const isolated = engine.processBatch([transaction("old", "a", "b", "2026-06-08T00:00:00.000000001Z")])[0];
  const extension = engine.processBatch([transaction("inside", "b", "c", "2026-06-09T00:00:00.000000000Z")])[0];

  assert.ok(extension.riskScore > isolated.riskScore);
  assert.equal(engine.diagnostics().activeTransactions, 2);
});

test("late arrivals inside the watermark window participate in arrival order", () => {
  const engine = new RiskEngine();
  const isolated = engine.processBatch([transaction("newer", "a", "b", "2026-06-08T12:10:00Z")])[0];
  const lateReturn = engine.processBatch([transaction("late", "b", "a", "2026-06-08T12:05:00Z")])[0];

  assert.ok(lateReturn.riskScore > isolated.riskScore * 5);
  assert.equal(engine.diagnostics().activeTransactions, 2);
});

test("too-old late arrivals are neutral and never mutate the graph", () => {
  const engine = new RiskEngine();
  engine.processBatch([transaction("watermark", "a", "b", "2026-06-10T00:00:00Z")]);
  const stale = engine.processBatch([transaction("stale", "b", "a", "2026-06-08T23:59:59Z")])[0];

  assert.equal(stale.riskScore, 0.02);
  assert.equal(engine.diagnostics().activeTransactions, 1);
});

test("parallel edge instances expire independently", () => {
  const engine = new RiskEngine();
  engine.processBatch([transaction("first", "a", "b", "2026-06-08T00:00:00Z")]);
  engine.processBatch([transaction("second", "a", "b", "2026-06-08T00:01:00Z")]);
  engine.processBatch([transaction("advance", "x", "y", "2026-06-09T00:00:30Z")]);

  // Only the first a->b edge is older than the window.
  assert.equal(engine.diagnostics().activeTransactions, 2);
  const extension = engine.processBatch([transaction("extension", "b", "c", "2026-06-09T00:00:31Z")])[0];
  assert.ok(extension.riskScore > scoreSequence([["b", "c"]]));
});

test("a self-transfer is treated as an immediate return loop", () => {
  const engine = new RiskEngine();
  const self = engine.processBatch([transaction("self", "a", "a")])[0];
  const isolated = scoreSequence([["a", "b"]]);
  assert.ok(self.riskScore > isolated * 5);
});

test("unrelated fan-in and fan-out stay at the isolated baseline", () => {
  const isolatedEngine = new RiskEngine();
  const isolated = isolatedEngine.processBatch([transaction("isolated", "x", "sink")])[0].riskScore;

  const fanInEngine = new RiskEngine();
  fanInEngine.processBatch([transaction("in-1", "a", "sink")]);
  const fanIn = fanInEngine.processBatch([
    transaction("in-2", "b", "sink", "2026-06-08T12:01:00Z")
  ])[0].riskScore;

  const fanOutEngine = new RiskEngine();
  fanOutEngine.processBatch([transaction("out-1", "source", "a")]);
  const fanOut = fanOutEngine.processBatch([
    transaction("out-2", "source", "b", "2026-06-08T12:01:00Z")
  ])[0].riskScore;

  assert.equal(fanIn, isolated);
  assert.equal(fanOut, isolated);
});

test("reset restores startup-equivalent scoring and clears idempotency", () => {
  const engine = new RiskEngine();
  const first = engine.processBatch([transaction("id", "a", "b")])[0];
  engine.processBatch([transaction("cycle", "b", "a", "2026-06-08T12:01:00Z")]);
  engine.reset();

  assert.deepEqual(engine.diagnostics(), {
    activeTransactions: 0,
    activeNodes: 0,
    rememberedTransactionIds: 0,
    watermarkNs: null
  });
  assert.deepEqual(engine.processBatch([transaction("id", "a", "b")])[0], first);
});

test("graph path counts recognize multiple independent shortest return paths", () => {
  const graph = new DirectedGraph();
  graph.addEdge("target", "left");
  graph.addEdge("target", "right");
  graph.addEdge("left", "source");
  graph.addEdge("right", "source");

  const path = graph.shortestPath("target", "source");
  assert.deepEqual(path, { exists: true, distance: 2, pathCount: 2 });
  const features = graph.analyzeEdge("source", "target");
  assert.equal(features.returnPath.pathCount, 2);
});

test("path deltas distinguish new, shortened, equal, and longer routes", () => {
  const shortened = new DirectedGraph();
  shortened.addEdge("a", "x");
  shortened.addEdge("x", "y");
  shortened.addEdge("y", "b");
  assert.equal(shortened.analyzeEdge("a", "b").shortenedPairs, 1);

  const equal = new DirectedGraph();
  equal.addEdge("root", "left");
  equal.addEdge("left", "sink");
  equal.addEdge("root", "right");
  const equalFeatures = equal.analyzeEdge("right", "sink");
  assert.equal(equalFeatures.equalAlternatePairs, 1);

  const longer = new DirectedGraph();
  longer.addEdge("root", "sink");
  longer.addEdge("root", "middle");
  const longerFeatures = longer.analyzeEdge("middle", "sink");
  assert.equal(longerFeatures.longerAlternatePairs, 1);
});

test("multiple returns score above a single similarly sized loop", () => {
  const single = scoreSequence([
    ["a", "b"], ["b", "c"], ["c", "d"], ["d", "a"]
  ]);
  const multiple = scoreSequence([
    ["a", "b"], ["b", "c"], ["c", "a"], ["b", "d"], ["d", "a"]
  ]);
  assert.ok(multiple > single);
});

test("an earlier-arriving downstream edge cannot be extended backward in arrival order", () => {
  const engine = new RiskEngine();
  const first = engine.processBatch([
    transaction("downstream-first", "b", "c", "2026-06-10T12:00:00Z")
  ])[0];
  const second = engine.processBatch([
    transaction("upstream-later", "a", "b", "2026-06-10T11:00:00Z")
  ])[0];

  assert.equal(first.riskScore, 0.02);
  assert.equal(second.riskScore, 0.02);
});

test("a chronological onward edge extends an arrival-order path", () => {
  const engine = new RiskEngine();
  engine.processBatch([transaction("upstream", "a", "b", "2026-06-10T11:00:00Z")]);
  const onward = engine.processBatch([
    transaction("downstream", "b", "c", "2026-06-10T12:00:00Z")
  ])[0];
  assert.equal(onward.riskScore, 0.20);
});

test("a shortcut outranks an ordinary extension", () => {
  const extension = scoreSequence([["a", "b"], ["b", "c"]]);
  const shortcut = scoreSequence([
    ["a", "b"], ["b", "c"], ["c", "d"], ["a", "d"]
  ]);
  assert.ok(shortcut > extension);
});

test("repeating an edge inside a cycle outranks repeating an isolated edge", () => {
  const isolated = scoreSequence([["a", "b"]]);
  const isolatedRepeat = scoreSequence([["a", "b"], ["a", "b"]]);
  const recurrentRepeat = scoreSequence([["a", "b"], ["b", "a"], ["a", "b"]]);
  assert.equal(isolatedRepeat, isolated);
  assert.ok(recurrentRepeat > isolatedRepeat);
});

test("disconnected recurrence does not raise an unrelated return", () => {
  const standalone = scoreSequence([["a", "b"], ["b", "a"]]);

  const engine = new RiskEngine();
  engine.processBatch([transaction("prior-loop", "prior", "prior")]);
  engine.processBatch([transaction("outbound", "a", "b", "2026-06-08T12:01:00Z")]);
  const afterRecurrence = engine.processBatch([
    transaction("return", "b", "a", "2026-06-08T12:02:00Z")
  ])[0].riskScore;

  assert.equal(afterRecurrence, standalone);
});

test("recurrence in the same component raises a later return", () => {
  const firstReturn = scoreSequence([["a", "b"], ["b", "c"], ["c", "a"]]);
  const laterReturn = scoreSequence([
    ["a", "b"], ["b", "c"], ["c", "a"], ["b", "d"], ["d", "a"]
  ]);
  assert.ok(laterReturn > firstReturn);
});

test("expiration rebuilds temporal paths from only the remaining arrivals", () => {
  const engine = new RiskEngine();
  engine.processBatch([transaction("expired-prefix", "a", "b", "2026-06-08T00:00:00Z")]);
  engine.processBatch([transaction("remaining-edge", "b", "c", "2026-06-08T01:00:00Z")]);
  engine.processBatch([transaction("advance", "x", "y", "2026-06-09T00:00:00.000000001Z")]);

  const candidate = engine.processBatch([
    transaction("candidate", "c", "a", "2026-06-09T00:01:00Z")
  ])[0].riskScore;

  assert.equal(candidate, 0.20);
  assert.equal(engine.diagnostics().activeTransactions, 3);
});

test("risk calibration is monotonic", () => {
  let previous = 0;
  for (let index = 0; index <= 1_000; index += 1) {
    const calibrated = calibrateRisk(index / 1_000);
    assert.ok(calibrated >= previous);
    previous = calibrated;
  }
});
