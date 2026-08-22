"""Online inference for opaque SHOWDOWN table-rule codenames.

The coordinator never describes a rule.  It does, however, reveal both private
numbers and the winner whenever a hand reaches showdown.  This module keeps a
small Bayesian ensemble of plausible deterministic comparison rules and updates
the ensemble from those observations.  State is keyed by the opaque codename so
knowledge survives leg boundaries and later attempts in the same process.
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
    if kind == "high":
        return (number,)
    if kind == "low":
        return (-number,)
    if kind == "near_tie":
        return (-distance,)
    if kind == "near_high":
        return (-distance, number)
    if kind == "near_low":
        return (-distance, -number)
    if kind == "far_tie":
        return (distance,)
    if kind == "far_high":
        return (distance, number)
    if kind == "far_low":
        return (distance, -number)
    if kind == "clockwise_near":
        return (-((number - community) % 13),)
    if kind == "clockwise_far":
        return ((number - community) % 13,)
    if kind == "mirror_near":
        return (-abs(number - (14 - community)), number)
    if kind == "mirror_far":
        return (abs(number - (14 - community)), number)
    if kind == "centre_near":
        return (-abs(number - 7), number)
    if kind == "centre_far":
        return (abs(number - 7), number)
    if kind == "low_then_high":
        return (number if community <= 7 else -number,)
    if kind == "high_then_low":
        return (-number if community <= 7 else number,)
    if kind == "above_near":
        return (number >= community, -distance, number)
    if kind == "below_near":
        return (number <= community, -distance, -number)
    raise ValueError(f"unknown rule family: {kind}")


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
        "high",
        "low",
        "near_tie",
        "near_high",
        "near_low",
        "far_tie",
        "far_high",
        "far_low",
        "clockwise_near",
        "clockwise_far",
        "mirror_near",
        "mirror_far",
        "centre_near",
        "centre_far",
        "low_then_high",
        "high_then_low",
        "above_near",
        "below_near",
    )
    unique: Dict[tuple[int, ...], Hypothesis] = {}
    for pair_mode in ("top", "bottom", "none"):
        for base in bases:
            hypothesis = Hypothesis(f"{pair_mode}:{base}", pair_mode, base)
            signature = tuple(
                hypothesis.compare(left, right, community)
                for community in range(NUMBER_MIN, NUMBER_MAX + 1)
                for left in range(NUMBER_MIN, NUMBER_MAX + 1)
                for right in range(NUMBER_MIN, NUMBER_MAX + 1)
            )
            unique.setdefault(signature, hypothesis)
    return tuple(unique.values())


HYPOTHESES = _hypotheses()


# Revealed showdowns from our own completed Phase 2 attempt.  The challenge
# explicitly guarantees that a codename keeps the same ruleset across retries,
# so retaining this public match evidence avoids relearning from zero after a
# deploy restart.  Each tuple is (our number, opponent number, community,
# outcome from our perspective).  Folded hands are intentionally absent.
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


def _result_value(outcome: int) -> float:
    if outcome > 0:
        return 1.0
    if outcome < 0:
        return 0.0
    return 0.5


class RuleModel:
    """Bounded posterior and exact observations for one table-rule codename."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._log_weights = [0.0] * len(HYPOTHESES)
        self._seen: set[tuple[Any, ...]] = set()
        self._seen_order: deque[tuple[Any, ...]] = deque()
        self._exact: Dict[tuple[int, int, int], tuple[int, int]] = {}
        self.observation_count = 0

    def observe(
        self,
        observation_id: tuple[Any, ...],
        left: int,
        right: int,
        community: int,
        outcome: int,
    ) -> bool:
        with self._lock:
            if observation_id in self._seen:
                return False
            if not all(NUMBER_MIN <= value <= NUMBER_MAX for value in (left, right, community)):
                return False
            if outcome not in (-1, 0, 1):
                return False

            self._seen.add(observation_id)
            self._seen_order.append(observation_id)
            while len(self._seen_order) > 4096:
                self._seen.discard(self._seen_order.popleft())

            self.observation_count += 1
            for index, hypothesis in enumerate(HYPOTHESES):
                prediction = hypothesis.compare(left, right, community)
                if prediction == outcome:
                    continue
                # A decisive contradiction is stronger evidence than disagreeing
                # about whether two strengths tie.
                self._log_weights[index] -= 6.0 if prediction * outcome == -1 else 2.5

            key, oriented = self._exact_key(left, right, community, outcome)
            wins, losses = self._exact.get(key, (0, 0))
            if oriented > 0:
                wins += 1
            elif oriented < 0:
                losses += 1
            else:
                wins += 1
                losses += 1
            self._exact[key] = (wins, losses)
            return True

    @staticmethod
    def _exact_key(
        left: int, right: int, community: int, outcome: int
    ) -> tuple[tuple[int, int, int], int]:
        if left <= right:
            return (community, left, right), outcome
        return (community, right, left), -outcome

    def _weights(self) -> list[float]:
        peak = max(self._log_weights)
        raw = [math.exp(max(-700.0, weight - peak)) for weight in self._log_weights]
        total = sum(raw)
        return [weight / total for weight in raw]

    def _exact_outcome(self, left: int, right: int, community: int) -> Optional[float]:
        key, orientation = self._exact_key(left, right, community, 1)
        wins, losses = self._exact.get(key, (0, 0))
        if not wins and not losses:
            return None
        probability_for_sorted_left = wins / (wins + losses)
        if left > right:
            probability_for_sorted_left = 1.0 - probability_for_sorted_left
        return probability_for_sorted_left

    def equity(self, number: int, community: Optional[int]) -> tuple[float, float]:
        """Return equity against uniform ranks and hypothesis confidence."""
        with self._lock:
            communities: Iterable[int]
            if community is None:
                communities = range(NUMBER_MIN, NUMBER_MAX + 1)
            else:
                communities = (community,)
            weights = self._weights()
            total = 0.0
            count = 0
            hypothesis_totals = [0.0] * len(HYPOTHESES)
            for shared in communities:
                for opponent in range(NUMBER_MIN, NUMBER_MAX + 1):
                    exact = self._exact_outcome(number, opponent, shared)
                    if exact is not None:
                        total += exact
                        for index in range(len(hypothesis_totals)):
                            hypothesis_totals[index] += exact
                    else:
                        predictions = [
                            _result_value(hypothesis.compare(number, opponent, shared))
                            for hypothesis in HYPOTHESES
                        ]
                        total += sum(weight * value for weight, value in zip(weights, predictions))
                        for index, value in enumerate(predictions):
                            hypothesis_totals[index] += value
                    count += 1

            equity = total / max(1, count)
            hypothesis_equities = [value / max(1, count) for value in hypothesis_totals]
            variance = sum(
                weight * (value - equity) ** 2
                for weight, value in zip(weights, hypothesis_equities)
            )
            # Confidence is local to this hand strength: many hypotheses can remain
            # globally possible while agreeing strongly about this particular play.
            agreement = 1.0 - min(1.0, math.sqrt(variance) * 3.0)
            depth = 1.0 - math.exp(-self.observation_count / 5.0)
            confidence = depth * (0.25 + 0.75 * agreement)
            return equity, confidence

    def compare_probability(self, left: int, right: int, community: int) -> float:
        with self._lock:
            exact = self._exact_outcome(left, right, community)
            if exact is not None:
                return exact
            return sum(
                weight * _result_value(hypothesis.compare(left, right, community))
                for weight, hypothesis in zip(self._weights(), HYPOTHESES)
            )

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
    """Thread-safe collection of models keyed only by opaque rule codename."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._models: Dict[str, RuleModel] = {}

    @staticmethod
    def _rule_name(data: Dict[str, Any]) -> str:
        value = data.get("table_rule")
        return str(value) if value is not None else "standard"

    def model_for(self, data: Dict[str, Any]) -> RuleModel:
        name = self._rule_name(data)
        with self._lock:
            model = self._models.get(name)
            if model is None:
                model = RuleModel()
                for index, observation in enumerate(EVENT_OBSERVATIONS.get(name, ())):
                    model.observe(("event-seed", name, index), *observation)
                self._models[name] = model
            return model

    def ingest(self, data: Dict[str, Any]) -> RuleModel:
        model = self.model_for(data)
        hero_seat = int(data.get("your_seat", 0))
        opponent_seat = _opponent_seat(data, hero_seat)
        match_id = str(data.get("match_id", ""))
        with self._lock:
            for hand in data.get("recent_hands") or []:
                community = hand.get("community_number")
                shown = hand.get("shown_numbers") or {}
                left = shown.get(str(hero_seat), shown.get(hero_seat))
                right = shown.get(str(opponent_seat), shown.get(opponent_seat))
                if community is None or left is None or right is None:
                    continue
                winners = {int(seat) for seat in hand.get("winners") or []}
                if hero_seat in winners and opponent_seat in winners:
                    outcome = 0
                elif hero_seat in winners:
                    outcome = 1
                elif opponent_seat in winners:
                    outcome = -1
                else:
                    continue
                identity = (
                    match_id,
                    int(hand.get("hand_number", -1)),
                    int(community),
                    int(left),
                    int(right),
                    outcome,
                )
                model.observe(identity, int(left), int(right), int(community), outcome)
        return model

    def reset(self) -> None:
        with self._lock:
            self._models.clear()


def _opponent_seat(data: Dict[str, Any], hero_seat: int) -> int:
    for player in data.get("players") or []:
        seat = int(player.get("seat", -1))
        if seat != hero_seat:
            return seat
    return 1 - hero_seat


RULES = RuleRegistry()
