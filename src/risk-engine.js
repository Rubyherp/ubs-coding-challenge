import { DirectedGraph } from "./directed-graph.js";
import { TransactionHeap } from "./min-heap.js";
import { sameTransaction } from "./transaction.js";

export const LOOKBACK_NS = 24n * 60n * 60n * 1_000_000_000n;

export class DuplicateConflictError extends Error {
  constructor(txId) {
    super(`txId ${JSON.stringify(txId)} was already used with a different payload`);
    this.name = "DuplicateConflictError";
    this.txId = txId;
  }
}

export class RiskEngine {
  #graph = new DirectedGraph();
  #expirations = new TransactionHeap();
  #ledger = new Map();
  #watermarkNs = null;
  #sequence = 0;

  processBatch(transactions) {
    this.#assertNoConflicts(transactions);
    const results = [];

    for (const transaction of transactions) {
      const prior = this.#ledger.get(transaction.txId);
      if (prior) {
        results.push({ txId: transaction.txId, riskScore: prior.riskScore });
        continue;
      }

      this.#advanceTime(transaction.createdAtNs);
      const cutoff = this.#watermarkNs - LOOKBACK_NS;
      let riskScore = 0;

      // The active interval is (watermark - 24h, watermark]. An event on the
      // lower boundary is no longer within the most recent 24 hours.
      if (transaction.createdAtNs > cutoff) {
        const features = this.#graph.analyzeEdge(transaction.fromUserId, transaction.toUserId);
        riskScore = scoreFeatures(features);
        this.#graph.addEdge(transaction.fromUserId, transaction.toUserId);
        this.#expirations.push({
          createdAtNs: transaction.createdAtNs,
          sequence: this.#sequence++,
          from: transaction.fromUserId,
          to: transaction.toUserId
        });
      }

      this.#ledger.set(transaction.txId, { transaction, riskScore });
      results.push({ txId: transaction.txId, riskScore });
    }

    return results;
  }

  reset() {
    this.#graph.clear();
    this.#expirations.clear();
    this.#ledger.clear();
    this.#watermarkNs = null;
    this.#sequence = 0;
  }

  diagnostics() {
    return Object.freeze({
      activeTransactions: this.#graph.edgeTotal,
      activeNodes: this.#graph.nodeCount,
      rememberedTransactionIds: this.#ledger.size,
      watermarkNs: this.#watermarkNs
    });
  }

  #assertNoConflicts(transactions) {
    const requestTransactions = new Map();
    for (const transaction of transactions) {
      const prior = this.#ledger.get(transaction.txId)?.transaction ?? requestTransactions.get(transaction.txId);
      if (prior && !sameTransaction(prior, transaction)) {
        throw new DuplicateConflictError(transaction.txId);
      }
      if (!prior) requestTransactions.set(transaction.txId, transaction);
    }
  }

  #advanceTime(createdAtNs) {
    if (this.#watermarkNs === null || createdAtNs > this.#watermarkNs) {
      this.#watermarkNs = createdAtNs;
      const cutoff = this.#watermarkNs - LOOKBACK_NS;
      while (this.#expirations.size > 0 && this.#expirations.peek().createdAtNs <= cutoff) {
        const expired = this.#expirations.pop();
        this.#graph.removeEdge(expired.from, expired.to);
      }
    }
  }
}

/** Map structural graph deltas to a stable relative score in [0, 1]. */
export function scoreFeatures(features) {
  const growth = normalizedLog(features.newPairs, 12);
  const affected = normalizedLog(features.affectedPairs, 32);
  const routeCapacity = normalizedLog(
    1.25 * features.shortenedPairs
      + 0.80 * features.equalAlternatePairs
      + 0.30 * features.longerAlternatePairs,
    12
  );
  const distanceImprovement = 1 - Math.exp(-features.relativeDistanceSavings / 2);
  const repeat = 1 - Math.exp(-features.existingEdgeCount);
  const fanIn = 1 - Math.exp(-features.targetInDegree / 2);
  const fanOut = 1 - Math.exp(-features.sourceOutDegree / 2);

  let returnStrength = 0;
  if (features.returnPath.exists) {
    if (features.returnPath.distance === 0) {
      returnStrength = 1; // A self-transfer creates the shortest possible loop.
    } else {
      const proximity = 1 / (1 + 0.12 * (features.returnPath.distance - 1));
      const diversity = Math.min(1, Math.log2(1 + features.returnPath.pathCount) / Math.log2(9));
      returnStrength = 0.82 * proximity + 0.18 * diversity;
    }
  }

  const establishedCycleNodes = Math.max(0, features.targetSccSize - 1)
    + (features.targetHasSelfCycle ? 1 : 0);
  const cyclicContext = 1 - Math.exp(-establishedCycleNodes / 2);
  const cycleBreadth = normalizedLog(features.cycleRegionSize, 8);
  const cycleMagnitude = returnStrength * (0.90 + 0.10 * cycleBreadth);

  // Percentages are deliberately separated by structural meaning. Return
  // closure dominates, alternate/shortened routes are intermediate, and
  // local degree or repeats can refine but never overwhelm path structure.
  const raw = 0.005
    + 0.12 * growth
    + 0.03 * affected
    + 0.20 * routeCapacity
    + 0.05 * distanceImprovement
    + 0.42 * cycleMagnitude
    + 0.10 * cyclicContext
    + 0.025 * repeat
    + 0.03 * fanIn
    + 0.015 * fanOut;

  return roundScore(Math.max(0, Math.min(1, raw)));
}

function normalizedLog(value, saturationPoint) {
  if (value <= 0) return 0;
  return Math.min(1, Math.log1p(value) / Math.log(saturationPoint + 1));
}

function roundScore(value) {
  return Math.round(value * 1_000_000) / 1_000_000;
}
