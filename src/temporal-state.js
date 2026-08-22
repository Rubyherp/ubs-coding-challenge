export const PATH_DECAY = 0.72;
const MAX_PAIR_PATH_MASS = 1_000_000;

/** Summary of all paths that can be extended in transaction arrival order. */
export class TemporalState {
  constructor() {
    this.pathMass = new Map();
    this.shortest = new Map();
    this.totalMass = 0;
    this.efficiency = 0;
    this.recurrentMass = 0;
    this.recurrentByNode = new Map();
  }

  get redundancy() {
    return Math.max(0, this.totalMass - this.efficiency);
  }
}

/**
 * Apply one edge to the arrival-order path state. Only paths that reached the
 * source before this transaction can be extended by it.
 */
export function applyTemporalEdge(state, source, target) {
  const prefixMass = new Map([[source, 1]]);
  const prefixDistance = new Map([[source, 0]]);

  for (const [origin, mass] of state.pathMass.get(source) ?? []) {
    prefixMass.set(origin, (prefixMass.get(origin) ?? 0) + mass);
  }
  for (const [origin, distance] of state.shortest.get(source) ?? []) {
    const oldDistance = prefixDistance.get(origin);
    if (oldDistance === undefined || distance < oldDistance) {
      prefixDistance.set(origin, distance);
    }
  }

  let targetMass = state.pathMass.get(target);
  if (!targetMass) {
    targetMass = new Map();
    state.pathMass.set(target, targetMass);
  }
  let targetShortest = state.shortest.get(target);
  if (!targetShortest) {
    targetShortest = new Map();
    state.shortest.set(target, targetShortest);
  }

  for (const [origin, mass] of prefixMass) {
    const contribution = PATH_DECAY * mass;
    const oldMass = targetMass.get(origin) ?? 0;
    const newMass = Math.min(MAX_PAIR_PATH_MASS, oldMass + contribution);
    const addedMass = newMass - oldMass;
    if (addedMass <= 0) continue;

    targetMass.set(origin, newMass);
    state.totalMass += addedMass;
    if (origin === target) {
      state.recurrentMass += addedMass;
      state.recurrentByNode.set(target, (state.recurrentByNode.get(target) ?? 0) + addedMass);
    }

    const candidateDistance = prefixDistance.get(origin) + 1;
    const oldDistance = targetShortest.get(origin);
    if (oldDistance === undefined) {
      targetShortest.set(origin, candidateDistance);
      state.efficiency += PATH_DECAY ** candidateDistance;
    } else if (candidateDistance < oldDistance) {
      targetShortest.set(origin, candidateDistance);
      state.efficiency += PATH_DECAY ** candidateDistance - PATH_DECAY ** oldDistance;
    }
  }
}

export function buildTemporalState(records) {
  const state = new TemporalState();
  const ordered = [...records].sort((left, right) => left.sequence - right.sequence);
  for (const record of ordered) {
    applyTemporalEdge(state, record.from, record.to);
  }
  return state;
}

export function summarizeTemporalState(state) {
  return Object.freeze({
    totalMass: state.totalMass,
    efficiency: state.efficiency,
    recurrentMass: state.recurrentMass,
    redundancy: state.redundancy
  });
}
