# Ghost Chains

The challenge implementations are split by cumulative phase:

- [`phase-1/`](./phase-1/) contains the structural and temporal transaction-graph scorer.
- [`phase-2/`](./phase-2/) extends Phase 1 with IP-address and device-identity evidence.

Phase 1 owns the shared graph, transaction validation, rolling-window, and
temporal-path modules. Phase 2 imports those modules directly and adds only its
identity state and integration layer, preventing the Phase 1 foundation from
drifting between phases.

## Commands

From this directory:

```bash
npm test
npm run test:phase-1
npm run test:phase-2
npm start
npm run start:phase-1
```

`npm start`, the repository-level Dockerfile, and the Procfile start Phase 2 by
default because each challenge phase is cumulative.
