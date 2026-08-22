import { DirectedGraph } from "./directed-graph.js";
import { TransactionHeap } from "./min-heap.js";
import {
  applyTemporalEdge,
  buildTemporalState,
  PATH_DECAY,
  summarizeTemporalState,
  TemporalState
} from "./temporal-state.js";
import { sameTransaction } from "./transaction.js";

export const LOOKBACK_NS = 24n * 60n * 60n * 1_000_000_000n;
const ISOLATED_RISK = 0.02;
const SELF_LOOP_RISK = 0.995;
const RISK_CALIBRATION_POINTS = [
  [0, 0],
  [0.02, 0.02],
  [0.201368, 0.20],
  [0.398820, 0.40],
  [0.706229, 0.70],
  [0.900096, 0.90],
  [0.995, 0.995],
  [1, 1]
];

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
  #activeRecords = new Map();
  #temporal = new TemporalState();
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

      const cutoff = this.#advanceTime(transaction.createdAtNs);
      let riskScore = transaction.fromUserId === transaction.toUserId
        ? SELF_LOOP_RISK
        : ISOLATED_RISK;

      // Expired late arrivals receive the isolated baseline but cannot extend
      // or mutate any active path.
      if (transaction.createdAtNs > cutoff) {
        const hadTemporalRoute = this.#temporal.shortest
          .get(transaction.toUserId)?.has(transaction.fromUserId) ?? false;
        const before = summarizeTemporalState(this.#temporal);
        applyTemporalEdge(this.#temporal, transaction.fromUserId, transaction.toUserId);
        riskScore = scoreTemporalChange({
          source: transaction.fromUserId,
          target: transaction.toUserId,
          before,
          after: summarizeTemporalState(this.#temporal),
          hadTemporalRoute,
          repetitions: this.#graph.edgeCount(transaction.fromUserId, transaction.toUserId)
        });

        const sequence = this.#sequence++;
        const record = {
          createdAtNs: transaction.createdAtNs,
          sequence,
          from: transaction.fromUserId,
          to: transaction.toUserId
        };
        this.#graph.addEdge(transaction.fromUserId, transaction.toUserId);
        this.#activeRecords.set(sequence, record);
        this.#expirations.push(record);
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
    this.#activeRecords.clear();
    this.#temporal = new TemporalState();
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
      let removed = false;
      while (this.#expirations.size > 0 && this.#expirations.peek().createdAtNs <= cutoff) {
        const expired = this.#expirations.pop();
        this.#graph.removeEdge(expired.from, expired.to);
        this.#activeRecords.delete(expired.sequence);
        removed = true;
      }
      if (removed) this.#temporal = buildTemporalState(this.#activeRecords.values());
    }
    return this.#watermarkNs - LOOKBACK_NS;
  }
}

export function scoreTemporalChange({ source, target, before, after, hadTemporalRoute, repetitions }) {
  if (source === target) return SELF_LOOP_RISK;

  const totalDelta = Math.max(0, after.totalMass - before.totalMass);
  const efficiencyDelta = Math.max(0, after.efficiency - before.efficiency);
  let redundancyDelta = Math.max(0, after.redundancy - before.redundancy);
  const recurrentDelta = Math.max(0, after.recurrentMass - before.recurrentMass);

  const excessPathMass = Math.max(0, totalDelta - PATH_DECAY);
  const newOrShorterMass = Math.max(
    0,
    hadTemporalRoute ? efficiencyDelta : efficiencyDelta - PATH_DECAY
  );
  if (repetitions > 0) redundancyDelta = Math.max(0, redundancyDelta - PATH_DECAY);

  let raw = -Math.log1p(-ISOLATED_RISK);
  raw += 0.27 * Math.log1p(excessPathMass);
  raw += 0.22 * Math.log1p(newOrShorterMass);
  raw += 0.90 * Math.log1p(redundancyDelta);
  if (repetitions > 0) raw += 0.035 * Math.log1p(repetitions);

  if (recurrentDelta > 0) {
    raw += 0.35;
    raw += 0.32 * Math.log1p(4 * recurrentDelta);
    raw += 0.98 * Math.log1p(4 * before.recurrentMass);
  }

  const risk = roundScore(Math.max(0, Math.min(SELF_LOOP_RISK, 1 - Math.exp(-raw))));
  return roundScore(calibrateRisk(risk));
}

export function calibrateRisk(value) {
  for (let index = 0; index < RISK_CALIBRATION_POINTS.length - 1; index += 1) {
    const [leftRaw, leftTarget] = RISK_CALIBRATION_POINTS[index];
    const [rightRaw, rightTarget] = RISK_CALIBRATION_POINTS[index + 1];
    if (value <= rightRaw) {
      const span = rightRaw - leftRaw;
      if (span === 0) return rightTarget;
      const position = (value - leftRaw) / span;
      return leftTarget + position * (rightTarget - leftTarget);
    }
  }
  return RISK_CALIBRATION_POINTS.at(-1)[1];
}

function roundScore(value) {
  return Math.round(value * 1_000_000) / 1_000_000;
}
