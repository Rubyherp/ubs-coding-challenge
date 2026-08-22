# SHOWDOWN Phase 2 bot

A dependency-free Python implementation of the SHOWDOWN protocol v2 Phase 2
challenge. It exposes:

- `POST /move` for decisions
- `GET /health` for coordinator warm-up
- `OPTIONS /move` as a fallback warm-up probe

## Phase 2 strategy

Each 40-hand leg announces an opaque `table_rule` codename. The bot never
assumes that a high number or a pair is strong. Instead, it:

1. ingests completed showdowns idempotently;
2. maintains a bounded rule model for each codename;
3. compares observed outcomes with a broad ensemble of deterministic rule
   families, including high/low, near/far, cyclic and pair variants;
4. carries learned rule knowledge across leg resets and later attempts in the
   same server process;
5. uses cheap early calls selectively to collect evidence without paying large
   bets merely to learn; and
6. combines inferred equity with opponent aggression and fold tendencies.

Opponent tendencies carry across the four legs by the attempt's shared opponent
name, while shown-number ranges remain leg-local because hand strength changes
with the table rule. All mutable state is thread-safe and bounded. Duplicate
requests and overlapping `recent_hands` windows do not double-count evidence.

Observed showdowns from completed event attempts are retained as startup
evidence for the event's stable codenames, so a deployment restart does not
erase everything learned from earlier retries. The server also emits compact
decision diagnostics to Render logs without changing protocol responses.

The end-game lock targets the Phase 2 threshold of `+25` per leg and reserves
every forced bet still payable, rather than reusing Phase 1's `+10` target.

## Run locally

Requires Python 3.12 or newer.

```bash
python3 app.py
```

The server listens on `PORT` (default `5000`).

```bash
curl http://localhost:5000/health
```

## Test

From this directory:

```bash
python3 -m unittest discover -s tests -v
```

The tests cover rule inference, codename isolation, cross-leg retention,
showdown ties, pair-losing rules, request idempotency, concurrent calls, exact
bet bounds, the `+25` lock, and randomized legal-action invariants.

## Deploy

Build from this directory so `rule_model.py` is included:

```bash
docker build -t showdown-p2 .
docker run --rm -p 5000:5000 showdown-p2
```

The included `Procfile` supports Heroku-style runtimes.
