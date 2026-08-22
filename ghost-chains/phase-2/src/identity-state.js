const DIMENSIONS = Object.freeze(["ipAddress", "deviceId"]);

/**
 * Active-window indexes used to compare identity evidence with graph context.
 * Records are owned by RiskEngine; this class only indexes their identities and
 * the prior transaction legs that end at each entity.
 */
export class IdentityState {
  #records = new Map();
  #incoming = new Map();
  #byIdentity = new Map(DIMENSIONS.map((key) => [key, new Map()]));

  add(record) {
    this.#records.set(record.sequence, record);
    addToSetMap(this.#incoming, record.to, record.sequence);
    for (const key of DIMENSIONS) {
      const identity = record[key];
      if (identity.present) addToSetMap(this.#byIdentity.get(key), identity.value, record.sequence);
    }
  }

  remove(record) {
    this.#records.delete(record.sequence);
    removeFromSetMap(this.#incoming, record.to, record.sequence);
    for (const key of DIMENSIONS) {
      const identity = record[key];
      if (identity.present) removeFromSetMap(this.#byIdentity.get(key), identity.value, record.sequence);
    }
  }

  clear() {
    this.#records.clear();
    this.#incoming.clear();
    for (const index of this.#byIdentity.values()) index.clear();
  }

  /**
   * Measure identity evidence visible immediately before transaction is added.
   * IP and device are evaluated independently, then combined additively.
   */
  analyze(transaction, graph) {
    const localNodes = graph.weaklyConnected(transaction.fromUserId);
    for (const node of graph.weaklyConnected(transaction.toUserId)) localNodes.add(node);

    const dimensions = {};
    let hazard = 0;
    for (const key of DIMENSIONS) {
      const evidence = this.#analyzeDimension(key, transaction[key], transaction.fromUserId, localNodes, graph);
      dimensions[key] = evidence;
      hazard += evidence.hazard;
    }

    return Object.freeze({
      hazard: Math.min(0.72, hazard),
      dimensions: Object.freeze(dimensions)
    });
  }

  #analyzeDimension(key, identity, source, localNodes, graph) {
    let agreement = false;
    let shift = false;
    let dropped = false;

    // The prior legs ending at the sender are the identity boundary that the
    // new transaction extends. This avoids flagging missing data on unrelated
    // transactions elsewhere in the same component.
    const incoming = [...(this.#incoming.get(source) ?? [])]
      .map((sequence) => this.#records.get(sequence));
    const carriedValues = new Set(
      incoming.filter((record) => record[key].present).map((record) => record[key].value)
    );

    if (identity.present && carriedValues.size > 0) {
      agreement = carriedValues.has(identity.value);
      shift = [...carriedValues].some((value) => value !== identity.value);
    } else if (!identity.present && carriedValues.size > 0) {
      dropped = true;
    }

    let disconnectedComponents = 0;
    if (identity.present) {
      const candidates = new Set();
      for (const sequence of this.#byIdentity.get(key).get(identity.value) ?? []) {
        const record = this.#records.get(sequence);
        if (!localNodes.has(record.from) && !localNodes.has(record.to)) {
          candidates.add(record.from);
          candidates.add(record.to);
        }
      }

      // Count distinct foreign weak components without counting every matching
      // transaction in a component as separate coordination evidence.
      // Four foreign components already saturate this deliberately cautious
      // signal; stopping there also bounds work for heavily shared NAT values.
      while (candidates.size > 0 && disconnectedComponents < 4) {
        const [node] = candidates;
        disconnectedComponents += 1;
        for (const connected of graph.weaklyConnected(node)) candidates.delete(connected);
      }
    }

    let hazard = 0;
    if (agreement) hazard += 0.045;
    if (shift) hazard += 0.19;
    if (dropped) hazard += 0.17;
    if (disconnectedComponents > 0) {
      hazard += 0.055 + 0.035 * Math.min(3, disconnectedComponents - 1);
    }

    return Object.freeze({ agreement, shift, dropped, disconnectedComponents, hazard });
  }
}

/** Preserve structural ordering while adding bounded independent evidence. */
export function combineIdentityRisk(structuralRisk, identityHazard) {
  if (identityHazard <= 0) return structuralRisk;
  const combined = 1 - (1 - structuralRisk) * Math.exp(-identityHazard);
  return Math.round(Math.min(0.995, combined) * 1_000_000) / 1_000_000;
}

function addToSetMap(map, key, value) {
  let values = map.get(key);
  if (!values) {
    values = new Set();
    map.set(key, values);
  }
  values.add(value);
}

function removeFromSetMap(map, key, value) {
  const values = map.get(key);
  if (!values) return;
  values.delete(value);
  if (values.size === 0) map.delete(key);
}
