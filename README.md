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

For a prospective edge `u → v`, the engine examines paths that reached `u`
before the transaction arrived. A path can only be extended by later arrivals:

1. **Path growth:** every arrival extends the decayed mass of paths already ending at its source.
2. **Efficiency:** newly reachable pairs and genuine shortcuts increase shortest-path efficiency.
3. **Convergence:** additional routes increase redundancy without treating unrelated fan-in as a shared flow.
4. **Return closure:** paths returning to their origin create recurrent mass, the dominant Phase 1 signal.
5. **Established recurrence:** later returns inside the same connected transaction component receive a larger increment than the first loop; unrelated components cannot leak risk into one another.
6. **Repeated edges:** parallel transfers remain neutral unless they extend or reinforce a converging or returning temporal path.

Self-transfers use the same incremental return calculation: the first is a
strong immediate loop, while subsequent self-transfers reinforce established
recurrence instead of every occurrence being hard-coded to the maximum score.

Path contributions decay by `0.72` per hop. A monotonic calibration maps the five
reference tiers to `0.02`, `0.20`, `0.40`, `0.70`, and `0.90` while retaining
headroom for stronger structures.

## Streaming and time semantics

- Arrival order defines state evolution, including for out-of-order timestamps.
- The maximum `createdAt` seen is the event-time watermark.
- An edge is active when `createdAt > watermark - 24 hours`; the rolling window is `(watermark - 24h, watermark]`.
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
