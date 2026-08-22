"""Thread-safe inference for SHOWDOWN's stable opaque table-rule codenames.

Phase 3 exposes many more showdowns than heads-up play.  Every revealed winner
is compared with every revealed loser, including hands where our bot folded, so
the six-seat table teaches the model without requiring us to pay for evidence.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import math
import threading
from typing import Any, Dict, Iterable, Optional


NUMBER_MIN = 1
NUMBER_MAX = 13


def _cmp(left: tuple[int, ...], right: tuple[int, ...]) -> int:
    return (left > right) - (left < right)


def _base_key(kind: str, number: int, community: int) -> tuple[int, ...]:
    distance = abs(number - community)
    keys = {
        "high": (number,),
        "low": (-number,),
        "seven_high": (int(number == 7), number),
        "near_tie": (-distance,),
        "near_high": (-distance, number),
        "near_low": (-distance, -number),
        "far_tie": (distance,),
        "far_high": (distance, number),
        "far_low": (distance, -number),
        "clockwise_near": (-((number - community) % 13),),
        "clockwise_far": ((number - community) % 13,),
        "mirror_near": (-abs(number - (14 - community)), number),
        "mirror_far": (abs(number - (14 - community)), number),
        "centre_near": (-abs(number - 7), number),
        "centre_far": (abs(number - 7), number),
        "low_then_high": (number if community <= 7 else -number,),
        "high_then_low": (-number if community <= 7 else number,),
        "above_near": (number >= community, -distance, number),
        "below_near": (number <= community, -distance, -number),
    }
    return keys[kind]


@dataclass(frozen=True)
class Hypothesis:
    name: str
    pair_mode: str
    base_kind: str

    def key(self, number: int, community: int) -> tuple[int, ...]:
        base = _base_key(self.base_kind, number, community)
        if self.pair_mode == "top":
            return (int(number == community),) + base
        if self.pair_mode == "bottom":
            return (-int(number == community),) + base
        return base

    def compare(self, left: int, right: int, community: int) -> int:
        return _cmp(self.key(left, community), self.key(right, community))


def _hypotheses() -> tuple[Hypothesis, ...]:
    bases = (
        "high", "low", "seven_high", "near_tie", "near_high", "near_low", "far_tie",
        "far_high", "far_low", "clockwise_near", "clockwise_far",
        "mirror_near", "mirror_far", "centre_near", "centre_far",
        "low_then_high", "high_then_low", "above_near", "below_near",
    )
    unique: Dict[tuple[int, ...], Hypothesis] = {}
    for pair_mode in ("top", "bottom", "none"):
        for base in bases:
            hypothesis = Hypothesis(f"{pair_mode}:{base}", pair_mode, base)
            signature = tuple(
                hypothesis.compare(left, right, community)
                for community in range(1, 14)
                for left in range(1, 14)
                for right in range(1, 14)
            )
            unique.setdefault(signature, hypothesis)
    return tuple(unique.values())


HYPOTHESES = _hypotheses()


# Public showdown evidence gathered during Phase 2.  The event guarantees the
# codename mapping remains unchanged in Phase 3.
EVENT_OBSERVATIONS: Dict[str, tuple[tuple[int, int, int, int], ...]] = {
    "verdigris": (
        (9, 6, 6, -1), (4, 9, 12, -1), (10, 11, 7, -1),
        (7, 3, 9, 1), (6, 13, 9, -1), (4, 13, 7, -1),
        (6, 13, 10, -1), (2, 9, 6, -1), (10, 9, 9, -1),
        (10, 10, 9, 0), (12, 12, 8, 0), (9, 13, 11, -1),
        (8, 1, 2, 1), (11, 12, 1, -1), (5, 8, 4, -1),
        (5, 6, 12, -1), (11, 13, 6, -1), (10, 9, 5, 1),
        (9, 13, 5, -1), (9, 8, 3, 1), (10, 10, 3, 0),
        (4, 7, 6, -1), (6, 10, 9, -1), (9, 11, 10, -1),
        (7, 6, 4, 1), (4, 4, 6, 0), (10, 11, 13, -1),
    ),
    "cinnabar": (
        (10, 3, 9, 1), (4, 8, 12, -1), (7, 9, 12, -1),
        (4, 5, 12, -1), (5, 10, 3, -1), (5, 5, 6, 0),
        (13, 10, 13, 1), (6, 13, 9, -1), (9, 10, 4, -1),
        (7, 2, 2, -1), (5, 1, 9, 1), (13, 13, 6, 0),
        (6, 6, 4, 0), (6, 11, 12, -1), (12, 9, 10, 1),
        (4, 3, 9, 1), (6, 6, 1, 0), (11, 13, 13, -1),
    ),
    "amaranth": (
        (4, 12, 4, 1), (11, 9, 12, 1), (1, 7, 2, -1),
        (12, 7, 8, -1), (8, 11, 3, -1), (8, 1, 4, 1),
        (6, 8, 13, -1), (11, 5, 5, -1), (7, 6, 2, 1),
        (10, 8, 5, 1), (9, 9, 5, 0), (11, 8, 3, 1),
        (7, 10, 4, 1), (12, 10, 9, 1), (13, 9, 11, 1),
        (6, 9, 2, -1), (13, 7, 10, -1), (7, 12, 3, 1),
        (12, 11, 4, 1),
    ),
    "obsidian": (
        (2, 4, 3, 1), (3, 8, 9, 1), (8, 6, 6, 1),
        (12, 4, 5, -1), (9, 5, 5, 1), (4, 3, 9, -1),
        (3, 7, 12, 1), (4, 5, 6, 1), (7, 2, 12, -1),
        (8, 5, 10, -1), (9, 11, 8, 1), (10, 13, 13, 1),
        (8, 6, 6, 1), (6, 12, 5, 1), (10, 8, 3, -1),
        (1, 7, 5, 1),
    ),
}


class RuleModel:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._log_weights = [0.0] * len(HYPOTHESES)
        self._seen: set[tuple[Any, ...]] = set()
        self._seen_order: deque[tuple[Any, ...]] = deque()
        self._exact: Dict[tuple[int, int, int], list[int]] = {}
        self._weights_cache: Optional[list[float]] = None
        self._outcome_cache: Dict[tuple[int, int, int], tuple[float, float, float]] = {}
        self._equity_cache: Dict[tuple[int, Optional[int]], tuple[float, float]] = {}
        self.observation_count = 0

    @staticmethod
    def _exact_key(left: int, right: int, community: int) -> tuple[tuple[int, int, int], int]:
        if left <= right:
            return (community, left, right), 1
        return (community, right, left), -1

    def observe(self, identity: tuple[Any, ...], left: int, right: int,
                community: int, outcome: int) -> bool:
        with self._lock:
            if identity in self._seen or outcome not in (-1, 0, 1):
                return False
            if not all(1 <= value <= 13 for value in (left, right, community)):
                return False
            self._seen.add(identity)
            self._seen_order.append(identity)
            while len(self._seen_order) > 8192:
                self._seen.discard(self._seen_order.popleft())
            self.observation_count += 1
            for index, hypothesis in enumerate(HYPOTHESES):
                prediction = hypothesis.compare(left, right, community)
                if prediction != outcome:
                    self._log_weights[index] -= 6.0 if prediction * outcome == -1 else 2.5
            key, orientation = self._exact_key(left, right, community)
            oriented = outcome * orientation
            counts = self._exact.setdefault(key, [0, 0, 0])  # win, tie, loss
            counts[0 if oriented > 0 else 1 if oriented == 0 else 2] += 1
            self._weights_cache = None
            self._outcome_cache.clear()
            self._equity_cache.clear()
            return True

    def _weights(self) -> list[float]:
        if self._weights_cache is not None:
            return self._weights_cache
        peak = max(self._log_weights)
        raw = [math.exp(max(-700.0, value - peak)) for value in self._log_weights]
        total = sum(raw)
        self._weights_cache = [value / total for value in raw]
        return self._weights_cache

    def outcome_probabilities(self, left: int, right: int,
                              community: int) -> tuple[float, float, float]:
        """Return probabilities of left winning, tying, and losing."""

        with self._lock:
            cache_key = (left, right, community)
            cached = self._outcome_cache.get(cache_key)
            if cached is not None:
                return cached
            key, orientation = self._exact_key(left, right, community)
            counts = self._exact.get(key)
            if counts and sum(counts):
                wins, ties, losses = counts
                if orientation < 0:
                    wins, losses = losses, wins
                total = wins + ties + losses
                result = wins / total, ties / total, losses / total
                self._outcome_cache[cache_key] = result
                return result
            result = [0.0, 0.0, 0.0]
            for weight, hypothesis in zip(self._weights(), HYPOTHESES):
                outcome = hypothesis.compare(left, right, community)
                result[0 if outcome > 0 else 1 if outcome == 0 else 2] += weight
            probabilities = result[0], result[1], result[2]
            self._outcome_cache[cache_key] = probabilities
            return probabilities

    def compare_probability(self, left: int, right: int, community: int) -> float:
        win, tie, _ = self.outcome_probabilities(left, right, community)
        return win + tie * 0.5

    def equity(self, number: int, community: Optional[int]) -> tuple[float, float]:
        cache_key = (number, community)
        with self._lock:
            cached = self._equity_cache.get(cache_key)
            if cached is not None:
                return cached
        communities: Iterable[int] = range(1, 14) if community is None else (community,)
        values = [
            self.compare_probability(number, opponent, shared)
            for shared in communities
            for opponent in range(1, 14)
        ]
        equity = sum(values) / max(1, len(values))
        with self._lock:
            weights = self._weights()
            peak = max(weights)
            depth = 1.0 - math.exp(-self.observation_count / 5.0)
            confidence = depth * (0.35 + 0.65 * peak)
        result = equity, min(1.0, confidence)
        with self._lock:
            self._equity_cache[cache_key] = result
        return result

    def diagnostics(self) -> Dict[str, Any]:
        with self._lock:
            weights = self._weights()
            best = max(range(len(weights)), key=weights.__getitem__)
            return {
                "observations": self.observation_count,
                "best_hypothesis": HYPOTHESES[best].name,
                "best_probability": weights[best],
            }


class RuleRegistry:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._models: Dict[str, RuleModel] = {}

    @staticmethod
    def _name(data: Dict[str, Any]) -> str:
        value = data.get("table_rule")
        return str(value) if value is not None else "standard"

    def model_for(self, data: Dict[str, Any]) -> RuleModel:
        name = self._name(data)
        with self._lock:
            if name not in self._models:
                model = RuleModel()
                for index, observation in enumerate(EVENT_OBSERVATIONS.get(name, ())):
                    model.observe(("event-seed", name, index), *observation)
                self._models[name] = model
            return self._models[name]

    def ingest(self, data: Dict[str, Any]) -> RuleModel:
        model = self.model_for(data)
        match_id = str(data.get("match_id", ""))
        for hand in data.get("recent_hands") or []:
            community = hand.get("community_number")
            shown_raw = hand.get("shown_numbers") or {}
            if community is None or len(shown_raw) < 2:
                continue
            shown = {int(seat): int(number) for seat, number in shown_raw.items()}
            winners = {int(seat) for seat in hand.get("winners") or []}
            shown_winners = sorted(winners.intersection(shown))
            losers = sorted(set(shown).difference(winners))
            # A single winner is unambiguously stronger than every shown loser.
            # Multiple payout recipients can be a true tie *or* different main
            # and side-pot winners, so they provide no safe pairwise ordering.
            if len(shown_winners) == 1:
                winner = shown_winners[0]
                for loser in losers:
                    identity = (
                        match_id, data.get("leg_number"), hand.get("hand_number"),
                        winner, loser, 1,
                    )
                    model.observe(
                        identity, shown[winner], shown[loser], int(community), 1
                    )
        return model

    def reset(self) -> None:
        with self._lock:
            self._models.clear()


RULES = RuleRegistry()
