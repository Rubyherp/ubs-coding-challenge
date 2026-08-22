# Ghost Chains — Phase 1

A dependency-free Node.js service that assigns streaming AML risk scores from the evolving structure of a directed transaction graph.

## Run

Requires Node.js 22 or newer.

```bash
npm start
```

The service listens on `PORT` (default `8080`) and exposes:

- `GET /ghost-chains/health`
- `POST /ghost-chains/reset`
- `POST /ghost-chains/transactions`

Run the full verification suite with:

```bash
npm test
npm run test:coverage
```

## Scoring model

For a prospective edge `u → v`, the engine examines the active graph before inserting it:

1. **Reachability growth:** the Cartesian product of nodes that can reach `u` and nodes reachable from `v` describes the routes the edge can create.
2. **Convergence and shortening:** each affected pair is classified as newly reachable, shortened, an equal-length alternate route, or a longer alternate route. These contribute different weights.
3. **Return closure:** a pre-existing route from `v` back to `u` means the new edge closes a loop. Shorter return paths and multiple independent shortest routes increase the signal.
4. **Cyclic context:** returning into an already strongly connected region is stronger than closing the first loop.
5. **Fan-in/fan-out:** distinct counterparties converging on a destination or spreading from a source contribute a smaller signal even before a shared upstream route is visible.
6. **Repeated edges:** parallel transfers add a smaller structural signal without being mistaken for independent graph routes.

The weighted signal is clamped and rounded to a deterministic score in `[0, 1]`. The weights intentionally preserve the challenge's qualitative ordering: isolated edge, extension, convergence, return, then multiple return paths into an established loop.

## Streaming and time semantics

- Arrival order defines state evolution, including for out-of-order timestamps.
- The maximum `createdAt` seen is the event-time watermark.
- An edge is active when `createdAt >= watermark - 24 hours`; expiration starts once it is strictly older than 24 hours.
- A late transaction older than the current window receives a neutral score and does not mutate the active graph.
- Active edges are held in a timestamp min-heap and expired incrementally. Parallel edges expire independently.
- RFC 3339 timestamps are parsed at nanosecond precision, including numeric UTC offsets.

## Idempotency and validation

Known transaction fields are normalized before comparison. Equivalent timestamps with different offsets are identical, and unknown fields do not affect identity. Replaying an identical `txId` returns its original score without changing state, even after its graph edge expires. Reusing an ID with a different known payload returns HTTP `409` and leaves the entire batch unapplied.

The active graph is bounded by the 24-hour window. A compact idempotency ledger is retained until reset so the service can honor the unconditional duplicate-ID contract; its entries contain only normalized known fields and the original score.

Malformed batches are validated in full before state mutation. Missing optional identity fields and unknown future-phase fields are accepted. A reset clears the graph, expiration heap, watermark, and idempotency ledger.

## Container deployment

```bash
docker build -t ghost-chains .
docker run --rm -p 8080:8080 ghost-chains
```

The included `Procfile` also supports Heroku-style runtimes.
