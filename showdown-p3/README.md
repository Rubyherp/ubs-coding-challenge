# SHOWDOWN Phase 3 bot

A standalone, dependency-free Python service for **Phase 3 — A Crowded Table**.
It exposes `POST /move`, `GET /health`, `HEAD /health`, and `OPTIONS /move`.

## What Phase 3 changes

- Six seats and multiway pots: folded and busted players are filtered explicitly.
- Four 60-hand legs using the stable Phase 2 rule codenames.
- Five fixed opponents with separately learned aggression and fold tendencies.
- Bust-outs persist and the button skips busted seats.
- A leg clears only at `chip_delta >= +10` and a strictly higher delta than every
  other seat.

## Strategy

The rule model learns from every revealed winner-versus-loser comparison at the
table, even if this bot folded. Multiway equity is expected pot share against
all live opponents, using action-conditioned number ranges and correct split-pot
weighting. Bet thresholds scale with the number of opponents; multiway bluffs
require the estimated probability that *everyone* folds.

The decision engine targets the current table leader rather than a fixed chip
number, protects a qualifying unique lead, models remaining forced bets while
skipping busted seats, and avoids escalating repeated raises or risking an
entire lead in one pot. When the bot is behind, objective pressure rises with
the leader gap, elapsed hands, and shrinking field; this lowers value thresholds
and increases bet sizes because a safe third-place finish scores the same as a
bust-out. Large reraises are evaluated with pot odds and the probability that no
opponent can beat the hand, so dominant hands and profitable split pots are not
discarded by a blanket stack-risk cutoff. If the remaining ordinary pots cannot
plausibly close the first-place gap, a high-confidence post-reveal edge switches
to forced-double sizing instead of accepting a zero-point finish with chips left.

## Run and test

```bash
python3 app.py
python3 -m unittest discover -s tests -v
```

The server listens on `PORT` (default `5000`). Build the Dockerfile from this
directory so all three Python modules are included.
