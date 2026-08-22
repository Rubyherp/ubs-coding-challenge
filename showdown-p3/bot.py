"""Objective-aware six-seat decision engine for SHOWDOWN Phase 3."""

from __future__ import annotations

from collections import OrderedDict, deque
import hashlib
import math
import threading
from typing import Any, Dict, Iterable, List, Optional

from rule_model import RULES, RuleModel


Action = Dict[str, Any]


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _hero(data: Dict[str, Any]) -> Dict[str, Any]:
    seat = int(data.get("your_seat", 0))
    return next(
        (player for player in data.get("players") or [] if int(player.get("seat", -1)) == seat),
        {},
    )


def _opponents(data: Dict[str, Any], *, live_only: bool = False) -> List[Dict[str, Any]]:
    seat = int(data.get("your_seat", 0))
    players = [
        player
        for player in data.get("players") or []
        if int(player.get("seat", -1)) != seat
    ]
    if live_only:
        players = [
            player
            for player in players
            if not player.get("folded", False) and not player.get("busted", False)
        ]
    return players


def _roll(data: Dict[str, Any], salt: str) -> float:
    key = "|".join(
        str(value)
        for value in (
            data.get("match_id", ""), data.get("leg_number", ""),
            data.get("hand_number", 0), data.get("round", ""),
            data.get("your_number", 0), len(data.get("current_hand_actions") or []), salt,
        )
    )
    return int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], "big") / 2**64


class OpponentProfile:
    """Bounded action tendencies for one of the five fixed Phase 3 names."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.actions = 0
        self.aggressive = 0
        self.fold_responses = 0
        self.continue_responses = 0
        self._seen: set[tuple[str, str, int]] = set()
        self._seen_order: deque[tuple[str, str, int]] = deque()

    def ingest(self, data: Dict[str, Any], seat: int) -> None:
        match_id = str(data.get("match_id", ""))
        leg = str(data.get("leg_number", ""))
        with self._lock:
            for hand in data.get("recent_hands") or []:
                identity = (match_id, leg, int(hand.get("hand_number", -1)))
                if identity in self._seen:
                    continue
                self._seen.add(identity)
                self._seen_order.append(identity)
                while len(self._seen_order) > 1024:
                    self._seen.discard(self._seen_order.popleft())
                for action in hand.get("actions") or []:
                    if int(action.get("seat", -1)) != seat:
                        continue
                    kind = action.get("action")
                    self.actions += 1
                    self.aggressive += int(kind in ("bet", "raise"))
                    if kind == "fold":
                        self.fold_responses += 1
                    elif kind in ("call", "raise"):
                        self.continue_responses += 1

    def stats(self) -> Dict[str, float]:
        with self._lock:
            return {
                "aggression": (self.aggressive + 2.0) / (self.actions + 8.0),
                "fold_rate": (self.fold_responses + 2.0)
                / (self.fold_responses + self.continue_responses + 5.0),
            }


class OpponentRegistry:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._profiles: OrderedDict[str, OpponentProfile] = OrderedDict()

    def ingest(self, data: Dict[str, Any]) -> Dict[int, OpponentProfile]:
        result: Dict[int, OpponentProfile] = {}
        with self._lock:
            for player in _opponents(data):
                seat = int(player.get("seat", -1))
                name = str(player.get("name", "")) or f"seat:{seat}"
                profile = self._profiles.pop(name, None) or OpponentProfile()
                profile.ingest(data, seat)
                self._profiles[name] = profile
                result[seat] = profile
            while len(self._profiles) > 64:
                self._profiles.popitem(last=False)
        return result

    def reset(self) -> None:
        with self._lock:
            self._profiles.clear()


OPPONENTS = OpponentRegistry()
_DECISION_CONTEXT = threading.local()


def _actions_in_round(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    round_name = data.get("round")
    return [
        action for action in data.get("current_hand_actions") or []
        if action.get("round") == round_name
    ]


def _last_action_for(data: Dict[str, Any], seat: int) -> Optional[Dict[str, Any]]:
    for action in reversed(_actions_in_round(data)):
        if int(action.get("seat", -1)) == seat:
            return action
    return None


def _last_aggressor(data: Dict[str, Any]) -> Optional[int]:
    hero_seat = int(data.get("your_seat", 0))
    for action in reversed(_actions_in_round(data)):
        if (
            int(action.get("seat", -1)) != hero_seat
            and action.get("action") in ("bet", "raise")
        ):
            return int(action.get("seat", -1))
    return None


def _aggression_count(data: Dict[str, Any], seat: Optional[int]) -> int:
    if seat is None:
        return 0
    return sum(
        int(action.get("seat", -1)) == seat
        and action.get("action") in ("bet", "raise")
        for action in _actions_in_round(data)
    )


def _single_strength(model: RuleModel, number: int, community: Optional[int]) -> float:
    return model.equity(number, community)[0]


def _range_weights(
    data: Dict[str, Any], player: Dict[str, Any], profile: OpponentProfile,
    strengths: List[float],
) -> List[float]:
    """Smoothed rank distribution conditioned on this opponent's current action."""

    seat = int(player.get("seat", -1))
    last = _last_action_for(data, seat)
    kind = last.get("action") if last else None
    aggression = profile.stats()["aggression"]
    pressure = 0.0
    if kind in ("bet", "raise"):
        pot = max(1.0, float(data.get("pot", 0)) - float(data.get("to_call", 0)))
        pressure = float(data.get("to_call", 0)) / pot

    weights: List[float] = []
    for number in range(1, 14):
        strength = strengths[number - 1]
        if kind == "raise":
            exponent = (1.7 + min(1.3, pressure)) * _clamp(0.42 / aggression, 0.7, 1.5)
            weight = (0.12 + strength) ** exponent
        elif kind == "bet":
            exponent = (1.2 + min(0.8, pressure * 0.5)) * _clamp(0.38 / aggression, 0.75, 1.35)
            weight = (0.20 + strength) ** exponent
        elif kind == "call":
            weight = 0.35 + strength
        elif kind == "check":
            weight = 1.20 - 0.35 * strength
        else:
            weight = 1.0
        weights.append(max(0.01, weight))

    # Directly observed action-conditioned showdowns outweigh the generic prior
    # gradually, without letting one exposed number collapse the range.
    signal_aggressive = kind in ("bet", "raise")
    for hand in data.get("recent_hands") or []:
        shown = hand.get("shown_numbers") or {}
        number = shown.get(str(seat), shown.get(seat))
        if number is None:
            continue
        actions = [
            action for action in hand.get("actions") or []
            if int(action.get("seat", -1)) == seat
            and action.get("round") == data.get("round")
        ]
        if not actions:
            continue
        historical_aggressive = any(
            action.get("action") in ("bet", "raise") for action in actions
        )
        if historical_aggressive == signal_aggressive:
            weights[int(number) - 1] += 1.5

    total = sum(weights)
    return [weight / total for weight in weights]


def _multiway_equity(
    data: Dict[str, Any], model: RuleModel, profiles: Dict[int, OpponentProfile]
) -> tuple[float, float, int, float]:
    """Expected pot share and chance nobody beats us versus the live field."""

    opponents = _opponents(data, live_only=True)
    if not opponents:
        return 1.0, 1.0, 0, 1.0
    number = int(data.get("your_number", 1))
    community_value = data.get("community_number")
    communities: Iterable[int] = (
        range(1, 14) if community_value is None else (int(community_value),)
    )
    strength_community = int(community_value) if community_value is not None else None
    strengths = [
        _single_strength(model, candidate, strength_community)
        for candidate in range(1, 14)
    ]
    ranges = [
        _range_weights(data, player, profiles[int(player.get("seat", -1))], strengths)
        for player in opponents
    ]
    community_equities: List[float] = []
    community_safety: List[float] = []
    for community in communities:
        # dp[t] is the probability nobody beats us and exactly t opponents tie.
        dp = [1.0]
        for weights in ranges:
            win = tie = 0.0
            for opponent_number, probability in enumerate(weights, 1):
                p_win, p_tie, _ = model.outcome_probabilities(
                    number, opponent_number, community
                )
                win += probability * p_win
                tie += probability * p_tie
            updated = [0.0] * (len(dp) + 1)
            for ties, probability in enumerate(dp):
                updated[ties] += probability * win
                updated[ties + 1] += probability * tie
            dp = updated
        community_equities.append(
            sum(probability / (ties + 1) for ties, probability in enumerate(dp))
        )
        community_safety.append(sum(dp))
    equity = sum(community_equities) / len(community_equities)
    showdown_safety = sum(community_safety) / len(community_safety)
    _, confidence = model.equity(number, None if community_value is None else int(community_value))
    return (
        _clamp(equity, 0.001, 0.999), confidence, len(opponents),
        _clamp(showdown_safety, 0.001, 0.999),
    )


def _objective_target(data: Dict[str, Any]) -> int:
    opponents = _opponents(data)
    leader = max((int(player.get("chip_delta", -200)) for player in opponents), default=-200)
    return max(10, leader + 1)


def _is_clearing(data: Dict[str, Any]) -> bool:
    delta = int(_hero(data).get("chip_delta", 0))
    return delta >= _objective_target(data)


def _objective_pressure(data: Dict[str, Any]) -> float:
    """How strongly tournament utility should favour variance over chip safety."""

    delta = int(_hero(data).get("chip_delta", 0))
    target = _objective_target(data)
    if delta >= target:
        return 0.0
    gap = target - delta
    stack = max(1, int(data.get("your_stack", 1)))
    hand = max(1, int(data.get("hand_number", 1)))
    total = max(hand, int(data.get("total_hands", 60)))
    active_opponents = sum(
        not player.get("busted", False) for player in _opponents(data)
    )
    pressure = 0.12 + 0.55 * (gap / stack) + 0.33 * (hand / total)
    if active_opponents <= 2:
        pressure += 0.12
    return _clamp(pressure, 0.0, 1.0)


def _value_fraction(
    base: float, pressure: float, equity: float, showdown_safety: float
) -> float:
    """Turn a chip-EV sizing baseline into a first-place-aware value size."""

    edge = _clamp((equity - 0.50) / 0.50, 0.0, 1.0)
    fraction = base + pressure * (0.30 + 0.55 * edge)
    if showdown_safety >= 0.96:
        fraction += 0.25 * pressure
    return fraction


def _pair_draw_call(
    data: Dict[str, Any], pair_top_probability: float, objective_pressure: float
) -> bool:
    """Buy a cheap reveal when the community can turn any single into the nuts."""

    if data.get("round") != "pre_reveal" or pair_top_probability < 0.85:
        return False
    if _is_clearing(data) or "call" not in set(data.get("legal_actions") or []):
        return False
    to_call = max(0, int(data.get("to_call", 0)))
    stack = max(1, int(data.get("your_stack", 1)))
    cap = max(2, round(stack * (0.05 + 0.03 * objective_pressure)))
    return 0 < to_call <= cap and _aggression_count(data, _last_aggressor(data)) <= 1


def _future_forced_cost(data: Dict[str, Any]) -> int:
    hero_seat = int(data.get("your_seat", 0))
    active = sorted(
        int(player.get("seat", -1))
        for player in data.get("players") or []
        if not player.get("busted", False)
    )
    if hero_seat not in active or len(active) < 2:
        return 0
    button = int(data.get("button_seat", active[0]))
    if button not in active:
        button = active[0]
    hands_left = max(0, int(data.get("total_hands", 60)) - int(data.get("hand_number", 1)))
    cost = 0
    for _ in range(hands_left):
        button = active[(active.index(button) + 1) % len(active)]
        small = active[(active.index(button) + 1) % len(active)]
        big = active[(active.index(button) + 2) % len(active)]
        cost += int(hero_seat == small) + 2 * int(hero_seat == big)
    return cost


def _can_lock(data: Dict[str, Any]) -> bool:
    if not _is_clearing(data):
        return False
    active_opponents = [player for player in _opponents(data) if not player.get("busted", False)]
    if not active_opponents:
        return True
    hands_left = max(0, int(data.get("total_hands", 60)) - int(data.get("hand_number", 1)))
    if hands_left == 0:
        return True
    if hands_left > 3:
        return False
    delta = int(_hero(data).get("chip_delta", 0))
    leader = max(int(player.get("chip_delta", -200)) for player in active_opponents)
    safety = _future_forced_cost(data) + 4 * hands_left
    return delta - leader > safety and delta - 10 >= _future_forced_cost(data)


def _current_commitment(data: Dict[str, Any], delta: int) -> int:
    start = int(data.get("starting_stack", 200))
    current = int(data.get("your_stack", start + delta))
    return max(0, start + delta - current)


def _amount(data: Dict[str, Any], fraction: float) -> int:
    hero = _hero(data)
    already = int(hero.get("bet_this_round", 0))
    stack = max(0, int(data.get("your_stack", 0)))
    minimum_value = data.get("min_raise_to")
    maximum_value = data.get("max_raise_to")
    minimum = int(minimum_value) if minimum_value is not None else already
    maximum = int(maximum_value) if maximum_value is not None else already + stack
    if maximum < minimum:
        minimum, maximum = maximum, minimum
    to_call = max(0, int(data.get("to_call", 0)))
    pot = max(0, int(data.get("pot", 0)))
    target = already + to_call + max(1, round((pot + to_call) * fraction))
    return max(minimum, min(maximum, target))


def _fallback(data: Dict[str, Any]) -> Action:
    legal = set(data.get("legal_actions") or [])
    for kind in ("check", "call", "fold"):
        if kind in legal:
            return {"action": kind}
    for kind in ("bet", "raise"):
        if kind in legal:
            return {"action": kind, "amount": _amount(data, 0.0)}
    return {"action": "check"}


def _safe_exit(data: Dict[str, Any]) -> Action:
    legal = set(data.get("legal_actions") or [])
    if "check" in legal:
        return {"action": "check"}
    if "fold" in legal:
        return {"action": "fold"}
    return _fallback(data)


def _aggress(data: Dict[str, Any], fraction: float) -> Action:
    legal = set(data.get("legal_actions") or [])
    preferred = "raise" if int(data.get("to_call", 0)) > 0 else "bet"
    for kind in (preferred, "raise", "bet"):
        if kind in legal:
            return {
                "action": kind,
                "amount": _amount(data, fraction),
            }
    return {"action": "call"} if "call" in legal else _fallback(data)


def decide(data: Dict[str, Any]) -> Action:
    _DECISION_CONTEXT.value = None
    legal = set(data.get("legal_actions") or [])
    if not legal:
        return {"action": "check"}
    model = RULES.ingest(data)
    profiles = OPPONENTS.ingest(data)
    if _can_lock(data):
        return _safe_exit(data)

    equity, confidence, opponent_count, showdown_safety = _multiway_equity(
        data, model, profiles
    )
    hero = _hero(data)
    delta = int(hero.get("chip_delta", 0))
    target = _objective_target(data)
    protecting = delta >= target
    objective_pressure = _objective_pressure(data)
    pair_top_probability = model.pair_mode_probability("top")
    nut_pair = (
        data.get("round") == "post_reveal"
        and data.get("community_number") is not None
        and int(data.get("your_number", 0)) == int(data["community_number"])
        and pair_top_probability >= 0.85
    )
    pair_draw_call = _pair_draw_call(
        data, pair_top_probability, objective_pressure
    )
    hand = int(data.get("hand_number", 1))
    total = int(data.get("total_hands", 60))
    hands_left = max(0, total - hand)
    to_call = max(0, int(data.get("to_call", 0)))
    n = max(1, opponent_count)
    _DECISION_CONTEXT.value = {
        "request": data,
        "model": model,
        "equity": equity,
        "confidence": confidence,
        "opponents": opponent_count,
        "showdown_safety": showdown_safety,
        "objective_pressure": objective_pressure,
        "pair_top_probability": pair_top_probability,
        "pair_draw_call": pair_draw_call,
        "nut_pair": nut_pair,
    }

    if to_call > 0:
        pot = max(0, int(data.get("pot", 0)))
        stack = max(1, int(data.get("your_stack", 1)))
        pot_odds = to_call / max(1.0, pot + to_call)
        pressure = to_call / max(1.0, pot - to_call)
        risk = 0.035 + 0.10 * (to_call / stack) + 0.012 * (n - 1)
        risk += 0.06 * (1.0 - confidence) + max(0.0, pressure - 0.5) * 0.045
        aggressor = _last_aggressor(data)
        aggression_count = _aggression_count(data, aggressor)
        last = _last_action_for(data, aggressor) if aggressor is not None else None
        if last and last.get("action") == "raise":
            risk += 0.04
        if protecting:
            risk += 0.055
            if hands_left <= 15:
                risk += 0.035
            surplus = delta - target
            if _current_commitment(data, delta) + to_call > surplus:
                return _safe_exit(data)
        else:
            risk -= 0.075 * objective_pressure
            leader_player = max(
                _opponents(data),
                key=lambda player: int(player.get("chip_delta", -200)),
                default=None,
            )
            if (
                leader_player is not None
                and aggressor == int(leader_player.get("seat", -1))
            ):
                risk -= 0.025 * objective_pressure

        severe = aggression_count >= 2 or to_call >= max(25, round(stack * 0.30))
        if nut_pair:
            if "raise" in legal or "bet" in legal:
                return _aggress(data, 1.50)
            if "call" in legal:
                return {"action": "call"}
        if pair_draw_call:
            return {"action": "call"}
        if severe:
            profitable = equity + 1e-9 >= pot_odds + max(0.0, risk)
            safe_split = (
                showdown_safety >= 0.96
                and equity + 0.015 >= pot_odds
            )
            if "call" in legal and (profitable or safe_split):
                return {"action": "call"}
            return _safe_exit(data)

        raise_threshold = 0.62 + 0.18 / n - 0.10 * objective_pressure
        if aggressor is not None:
            raise_threshold += 0.08
        if protecting:
            raise_threshold = max(raise_threshold, 0.90)
        if equity >= raise_threshold and not protecting:
            base = 0.46 if data.get("round") == "post_reveal" else 0.34
            return _aggress(
                data,
                _value_fraction(base, objective_pressure, equity, showdown_safety),
            )
        if equity + 1e-9 >= pot_odds + risk and "call" in legal:
            return {"action": "call"}
        return _safe_exit(data)

    if data.get("round") == "pre_reveal":
        strong = 0.58 + 0.14 / n + (0.08 if protecting else 0.0)
        medium = 0.48 + 0.13 / n + (0.08 if protecting else 0.0)
    else:
        strong = 0.61 + 0.18 / n + (0.08 if protecting else 0.0)
        medium = 0.49 + 0.14 / n + (0.08 if protecting else 0.0)
    uncertainty = 0.05 * (1.0 - confidence)
    strong -= 0.12 * objective_pressure
    medium -= 0.10 * objective_pressure
    if nut_pair:
        return _aggress(data, 0.70 + 0.25 * objective_pressure)
    if equity >= strong + uncertainty:
        base = 0.55 if data.get("round") == "post_reveal" else 0.42
        return _aggress(
            data,
            _value_fraction(base, objective_pressure, equity, showdown_safety),
        )
    if equity >= medium + uncertainty:
        base = 0.36 if data.get("round") == "post_reveal" else 0.28
        return _aggress(
            data,
            _value_fraction(base, objective_pressure, equity, showdown_safety),
        )
    if protecting:
        return _safe_exit(data)

    active_profiles = [
        profiles[int(player.get("seat", -1))] for player in _opponents(data, live_only=True)
    ]
    fold_through = math.prod(profile.stats()["fold_rate"] for profile in active_profiles)
    bluff_rate = min(0.06, 0.10 * fold_through)
    if hands_left <= 6 and delta < target:
        bluff_rate += 0.015
    if equity <= max(0.18, 1.0 / (n + 1)) and _roll(data, "multiway-bluff") < bluff_rate:
        return _aggress(data, 0.30)
    return _safe_exit(data)


def decision_diagnostics(data: Dict[str, Any], action: Action) -> Dict[str, Any]:
    cached = getattr(_DECISION_CONTEXT, "value", None)
    if cached is not None and cached.get("request") is data:
        model = cached["model"]
        equity = cached["equity"]
        confidence = cached["confidence"]
        opponents = cached["opponents"]
        showdown_safety = cached["showdown_safety"]
        objective_pressure = cached["objective_pressure"]
        pair_top_probability = cached["pair_top_probability"]
        pair_draw_call = cached["pair_draw_call"]
        nut_pair = cached["nut_pair"]
    else:
        model = RULES.model_for(data)
        profiles = OPPONENTS.ingest(data)
        equity, confidence, opponents, showdown_safety = _multiway_equity(
            data, model, profiles
        )
        objective_pressure = _objective_pressure(data)
        pair_top_probability = model.pair_mode_probability("top")
        nut_pair = (
            data.get("round") == "post_reveal"
            and data.get("community_number") is not None
            and int(data.get("your_number", 0)) == int(data["community_number"])
            and pair_top_probability >= 0.85
        )
        pair_draw_call = _pair_draw_call(
            data, pair_top_probability, objective_pressure
        )
    hero = _hero(data)
    info = model.diagnostics()
    leader = max((int(player.get("chip_delta", -200)) for player in _opponents(data)), default=-200)
    return {
        "event": "showdown_phase3_decision",
        "match_id": str(data.get("match_id", "")),
        "leg": data.get("leg_number"),
        "hand": data.get("hand_number"),
        "round": data.get("round"),
        "rule": data.get("table_rule"),
        "number": data.get("your_number"),
        "community": data.get("community_number"),
        "delta": hero.get("chip_delta"),
        "leader_delta": leader,
        "target_delta": _objective_target(data),
        "live_opponents": opponents,
        "pot": data.get("pot"),
        "to_call": data.get("to_call"),
        "equity": round(equity, 4),
        "showdown_safety": round(showdown_safety, 4),
        "confidence": round(confidence, 4),
        "objective_pressure": round(objective_pressure, 4),
        "pair_top_probability": round(pair_top_probability, 4),
        "pair_draw_call": pair_draw_call,
        "nut_pair": nut_pair,
        "observations": info["observations"],
        "hypothesis": info["best_hypothesis"],
        "action": action,
    }


def _reset_learning_for_tests() -> None:
    RULES.reset()
    OPPONENTS.reset()
