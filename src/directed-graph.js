const PATH_COUNT_CAP = 64;

/** Directed multigraph. Traversal treats parallel edges as one structural route. */
export class DirectedGraph {
  #out = new Map();
  #in = new Map();
  #edgeTotal = 0;

  get edgeTotal() {
    return this.#edgeTotal;
  }

  get nodeCount() {
    return new Set([...this.#out.keys(), ...this.#in.keys()]).size;
  }

  edgeCount(from, to) {
    return this.#out.get(from)?.get(to) ?? 0;
  }

  distinctOutDegree(node) {
    return this.#out.get(node)?.size ?? 0;
  }

  distinctInDegree(node) {
    return this.#in.get(node)?.size ?? 0;
  }

  addEdge(from, to) {
    increment(this.#out, from, to);
    increment(this.#in, to, from);
    this.#edgeTotal += 1;
  }

  removeEdge(from, to) {
    const removed = decrement(this.#out, from, to);
    if (!removed) return false;
    decrement(this.#in, to, from);
    this.#edgeTotal -= 1;
    return true;
  }

  clear() {
    this.#out.clear();
    this.#in.clear();
    this.#edgeTotal = 0;
  }

  reachable(start, { reverse = false, positiveOnly = false } = {}) {
    const adjacency = reverse ? this.#in : this.#out;
    const seen = new Set();
    const queue = [];

    if (!positiveOnly) seen.add(start);
    for (const neighbor of adjacency.get(start)?.keys() ?? []) {
      if (!seen.has(neighbor)) {
        seen.add(neighbor);
        queue.push(neighbor);
      }
    }

    for (let index = 0; index < queue.length; index += 1) {
      const node = queue[index];
      for (const neighbor of adjacency.get(node)?.keys() ?? []) {
        if (!seen.has(neighbor)) {
          seen.add(neighbor);
          queue.push(neighbor);
        }
      }
    }
    return seen;
  }

  /** Unweighted shortest distances, with the start node at distance zero. */
  distances(start, { reverse = false } = {}) {
    const adjacency = reverse ? this.#in : this.#out;
    const distances = new Map([[start, 0]]);
    const queue = [start];

    for (let index = 0; index < queue.length; index += 1) {
      const node = queue[index];
      const candidate = distances.get(node) + 1;
      for (const neighbor of adjacency.get(node)?.keys() ?? []) {
        if (!distances.has(neighbor)) {
          distances.set(neighbor, candidate);
          queue.push(neighbor);
        }
      }
    }
    return distances;
  }

  /** Shortest positive-length route and number of shortest structural routes. */
  shortestPath(start, target) {
    if (start === target) return this.#shortestCycle(start);

    const distances = new Map([[start, 0]]);
    const counts = new Map([[start, 1]]);
    const queue = [start];

    for (let index = 0; index < queue.length; index += 1) {
      const node = queue[index];
      const distance = distances.get(node);
      if (distances.has(target) && distance + 1 > distances.get(target)) break;

      for (const neighbor of this.#out.get(node)?.keys() ?? []) {
        const candidate = distance + 1;
        if (!distances.has(neighbor)) {
          distances.set(neighbor, candidate);
          counts.set(neighbor, counts.get(node));
          queue.push(neighbor);
        } else if (distances.get(neighbor) === candidate) {
          counts.set(neighbor, Math.min(PATH_COUNT_CAP, counts.get(neighbor) + counts.get(node)));
        }
      }
    }

    return distances.has(target)
      ? { exists: true, distance: distances.get(target), pathCount: counts.get(target) }
      : { exists: false, distance: null, pathCount: 0 };
  }

  #shortestCycle(start) {
    let bestDistance = Number.POSITIVE_INFINITY;
    let bestCount = 0;

    for (const neighbor of this.#out.get(start)?.keys() ?? []) {
      if (neighbor === start) {
        bestDistance = 1;
        bestCount = 1;
        continue;
      }
      const path = this.shortestPath(neighbor, start);
      if (!path.exists) continue;
      const distance = path.distance + 1;
      if (distance < bestDistance) {
        bestDistance = distance;
        bestCount = path.pathCount;
      } else if (distance === bestDistance) {
        bestCount = Math.min(PATH_COUNT_CAP, bestCount + path.pathCount);
      }
    }

    return Number.isFinite(bestDistance)
      ? { exists: true, distance: bestDistance, pathCount: bestCount }
      : { exists: false, distance: null, pathCount: 0 };
  }

  /**
   * Structural features created by a prospective edge. Ancestor × descendant
   * pairs describe all routes that the edge can create or shorten.
   */
  analyzeEdge(from, to) {
    const ancestorDistances = this.distances(from, { reverse: true });
    const descendantDistances = this.distances(to);
    const ancestors = ancestorDistances.keys();
    const descendants = [...descendantDistances.keys()];
    const ancestorCount = ancestorDistances.size;
    const descendantCount = descendantDistances.size;
    const affectedPairs = ancestorCount * descendantCount;
    let newPairs = 0;
    let shortenedPairs = 0;
    let equalAlternatePairs = 0;
    let longerAlternatePairs = 0;
    let relativeDistanceSavings = 0;

    // Evaluate the exact shortest-path delta for every ancestor × descendant
    // pair. This distinguishes reachability, shortening, and route redundancy.
    if (ancestorCount <= descendantCount) {
      for (const ancestor of ancestors) {
        const existingDistances = this.distances(ancestor);
        for (const descendant of descendants) {
          const candidateDistance = ancestorDistances.get(ancestor) + 1 + descendantDistances.get(descendant);
          const existingDistance = positiveDistance(this, existingDistances, ancestor, descendant);
          ({ newPairs, shortenedPairs, equalAlternatePairs, longerAlternatePairs, relativeDistanceSavings } = classifyPathDelta({
            existingDistance,
            candidateDistance,
            newPairs,
            shortenedPairs,
            equalAlternatePairs,
            longerAlternatePairs,
            relativeDistanceSavings
          }));
        }
      }
    } else {
      for (const descendant of descendants) {
        const reverseDistances = this.distances(descendant, { reverse: true });
        for (const [ancestor, distanceToFrom] of ancestorDistances) {
          const candidateDistance = distanceToFrom + 1 + descendantDistances.get(descendant);
          const existingDistance = positiveDistance(this, reverseDistances, descendant, ancestor);
          ({ newPairs, shortenedPairs, equalAlternatePairs, longerAlternatePairs, relativeDistanceSavings } = classifyPathDelta({
            existingDistance,
            candidateDistance,
            newPairs,
            shortenedPairs,
            equalAlternatePairs,
            longerAlternatePairs,
            relativeDistanceSavings
          }));
        }
      }
    }

    const returnPath = from === to
      ? { exists: true, distance: 0, pathCount: 1 }
      : this.shortestPath(to, from);

    // Nodes reachable both from and toward the target form its current SCC.
    const targetForward = this.reachable(to);
    const targetBackward = this.reachable(to, { reverse: true });
    let targetSccSize = 0;
    for (const node of targetForward) {
      if (targetBackward.has(node)) targetSccSize += 1;
    }
    const targetHasSelfCycle = this.reachable(to, { positiveOnly: true }).has(to);

    let cycleRegionSize = 0;
    if (returnPath.exists) {
      const forwardFromTarget = this.reachable(to);
      const backwardFromSource = this.reachable(from, { reverse: true });
      for (const node of forwardFromTarget) {
        if (backwardFromSource.has(node)) cycleRegionSize += 1;
      }
      if (from === to && cycleRegionSize === 0) cycleRegionSize = 1;
    }

    const redundantPairs = shortenedPairs + equalAlternatePairs + longerAlternatePairs;

    return Object.freeze({
      affectedPairs,
      newPairs,
      redundantPairs,
      shortenedPairs,
      equalAlternatePairs,
      longerAlternatePairs,
      relativeDistanceSavings,
      returnPath,
      cycleRegionSize,
      existingEdgeCount: this.edgeCount(from, to),
      sourceOutDegree: this.distinctOutDegree(from),
      targetInDegree: this.distinctInDegree(to),
      targetSccSize,
      targetHasSelfCycle
    });
  }
}

function positiveDistance(graph, distances, start, target) {
  if (start !== target) return distances.get(target);
  const cycle = graph.shortestPath(start, start);
  return cycle.exists ? cycle.distance : undefined;
}

function classifyPathDelta(state) {
  const { existingDistance, candidateDistance } = state;
  if (existingDistance === undefined) {
    state.newPairs += 1;
  } else if (candidateDistance < existingDistance) {
    state.shortenedPairs += 1;
    state.relativeDistanceSavings += (existingDistance - candidateDistance) / existingDistance;
  } else if (candidateDistance === existingDistance) {
    state.equalAlternatePairs += 1;
  } else {
    state.longerAlternatePairs += 1;
  }
  return state;
}

function increment(adjacency, from, to) {
  let neighbors = adjacency.get(from);
  if (!neighbors) {
    neighbors = new Map();
    adjacency.set(from, neighbors);
  }
  neighbors.set(to, (neighbors.get(to) ?? 0) + 1);
}

function decrement(adjacency, from, to) {
  const neighbors = adjacency.get(from);
  const count = neighbors?.get(to) ?? 0;
  if (count === 0) return false;
  if (count === 1) {
    neighbors.delete(to);
    if (neighbors.size === 0) adjacency.delete(from);
  } else {
    neighbors.set(to, count - 1);
  }
  return true;
}
