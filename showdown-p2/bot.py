"""Adaptive decision engine for SHOWDOWN protocol v2, phase 2.

Phase 2 assigns each leg an opaque table-rule codename.  Revealed showdowns are
fed to a rule-specific inference model, while opponent behaviour is accumulated
across the four legs.  All updates are deduplicated, bounded, and thread-safe so
the HTTP server can safely retry or use concurrent request handlers.
"""

from __future__ import annotations

from collections import OrderedDict, deque
import hashlib
import threading
from typing import Any, Dict, Iterable, List, Optional

from rule_model import RULES, RuleModel


Action = Dict[str, Any]


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _players(data: Dict[str, Any]) -> tuple[Dict[str, Any], Dict[str, Any]]:
    seat = int(data.get("your_seat", 0))
    players = data.get("players") or []
    hero = next((p for p in players if int(p.get("seat", -1)) == seat), {})
    villain = next((p for p in players if int(p.get("seat", -1)) != seat), {})
    return hero, villain


def _opponent_seat(data: Dict[str, Any]) -> int:
    seat = int(data.get("your_seat", 0))
    for player in data.get("players") or []:
        other = int(player.get("seat", -1))
        if other != seat:
            return other
    return 1 - seat


def _roll(data: Dict[str, Any], salt: str) -> float:
    """Stable pseudo-randomness prevents an identical request changing its mind."""

    key = "|".join(
        [
            str(data.get("match_id", "")),
            str(data.get("leg_number", "")),
            str(data.get("hand_number", 0)),
            str(data.get("round", "")),
            str(data.get("your_number", 0)),
            str(len(data.get("current_hand_actions") or [])),
            salt,
        ]
    )
    value = int.from_bytes(hashlib.sha256(key.encode("utf-8")).digest()[:8], "big")
    return value / float(2**64)


class OpponentProfile:
    """Cross-leg tendencies for one attempt's randomly named opponent."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.opponent_actions = 0
        self.opponent_aggression = 0
        self.fold_chances = 0
        self.folds = 0
        self._seen: set[tuple[str, str, int]] = set()
        self._seen_order: deque[tuple[str, str, int]] = deque()

    def ingest(self, data: Dict[str, Any]) -> None:
        with self._lock:
            opponent_seat = _opponent_seat(data)
            hero_seat = int(data.get("your_seat", 0))
            match_id = str(data.get("match_id", ""))
            leg_number = str(data.get("leg_number", ""))
            for hand in data.get("recent_hands") or []:
                identity = (match_id, leg_number, int(hand.get("hand_number", -1)))
                if identity in self._seen:
                    continue
                self._seen.add(identity)
                self._seen_order.append(identity)
                while len(self._seen_order) > 512:
                    self._seen.discard(self._seen_order.popleft())

                actions = hand.get("actions") or []
                for action in actions:
                    if int(action.get("seat", -1)) == opponent_seat:
                        self.opponent_actions += 1
                        if action.get("action") in ("bet", "raise"):
                            self.opponent_aggression += 1
                for index, action in enumerate(actions[:-1]):
                    following = actions[index + 1]
                    same_round = action.get("round") == following.get("round")
                    hero_bet = (
                        int(action.get("seat", -1)) == hero_seat
                        and action.get("action") in ("bet", "raise")
                    )
                    opponent_reply = int(following.get("seat", -1)) == opponent_seat
                    if same_round and hero_bet and opponent_reply:
                        self.fold_chances += 1
                        self.folds += int(following.get("action") == "fold")

    def stats(self) -> Dict[str, float]:
        # Beta priors prevent a handful of actions from dominating decisions.
        with self._lock:
            return {
                "aggression": (self.opponent_aggression + 2.0)
                / (self.opponent_actions + 7.0),
                "fold_rate": (self.folds + 2.0) / (self.fold_chances + 5.0),
            }


class OpponentRegistry:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._profiles: OrderedDict[str, OpponentProfile] = OrderedDict()

    def _name(self, data: Dict[str, Any]) -> str:
        _, villain = _players(data)
        # Names are stable across the four legs but freshly drawn each attempt.
        # Include a fallback prefix from match_id for malformed/minimal requests.
        name = str(villain.get("name", ""))
        return name or f"match:{str(data.get('match_id', ''))[:32]}"

    def ingest(self, data: Dict[str, Any]) -> OpponentProfile:
        key = self._name(data)
        with self._lock:
            profile = self._profiles.pop(key, None) or OpponentProfile()
            profile.ingest(data)
            self._profiles[key] = profile
            while len(self._profiles) > 128:
                self._profiles.popitem(last=False)
            return profile

    def reset(self) -> None:
        with self._lock:
            self._profiles.clear()


OPPONENTS = OpponentRegistry()


def _actions_in_round(hand: Dict[str, Any], round_name: str) -> List[Dict[str, Any]]:
    return [action for action in hand.get("actions") or [] if action.get("round") == round_name]


def _equity_against_number(
    model: RuleModel,
    hero_number: int,
    opponent_number: int,
    community: Optional[int],
) -> float:
    if community is not None:
        return model.compare_probability(hero_number, opponent_number, community)
    return sum(
        model.compare_probability(hero_number, opponent_number, shared)
        for shared in range(1, 14)
    ) / 13.0


def _observed_range_equity(
    data: Dict[str, Any],
    model: RuleModel,
    hero_number: int,
    community: Optional[int],
    signal: str,
) -> tuple[Optional[float], int]:
    """Estimate equity versus ranks shown with the same action type this leg."""

    opponent_seat = _opponent_seat(data)
    round_name = str(data.get("round", "pre_reveal"))
    scores: List[float] = []
    for hand in data.get("recent_hands") or []:
        shown = hand.get("shown_numbers") or {}
        shown_number = shown.get(str(opponent_seat), shown.get(opponent_seat))
        if shown_number is None:
            continue
        actions = [
            action
            for action in _actions_in_round(hand, round_name)
            if int(action.get("seat", -1)) == opponent_seat
        ]
        if not actions:
            continue
        aggressive = any(action.get("action") in ("bet", "raise") for action in actions)
        if aggressive == (signal == "aggressive"):
            scores.append(
                _equity_against_number(
                    model, hero_number, int(shown_number), community
                )
            )
    if not scores:
        return None, 0
    return sum(scores) / len(scores), len(scores)


def _last_opponent_action(data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    opponent_seat = _opponent_seat(data)
    round_name = data.get("round")
    for action in reversed(data.get("current_hand_actions") or []):
        if action.get("round") == round_name and int(action.get("seat", -1)) == opponent_seat:
            return action
    return None


def _estimated_equity(
    data: Dict[str, Any], model: RuleModel, stats: Dict[str, float]
) -> tuple[float, float]:
    number = int(data.get("your_number", 1))
    community_value = data.get("community_number")
    community = int(community_value) if community_value is not None else None
    base, confidence = model.equity(number, community)
    last = _last_opponent_action(data)
    signal = "aggressive" if last and last.get("action") in ("bet", "raise") else "passive"
    observed, sample_count = _observed_range_equity(
        data, model, number, community, signal
    )
    if observed is not None:
        weight = min(0.48, sample_count / 18.0)
        base = base * (1.0 - weight) + observed * weight

    if signal == "aggressive":
        # A frequent aggressor has a wider betting range.  Very large bets still
        # get a small caution adjustment independent of the inferred rule.
        base += (stats["aggression"] - 0.29) * 0.18
        pot = max(1.0, float(data.get("pot", 0)))
        to_call = float(data.get("to_call", 0))
        size_ratio = to_call / max(1.0, pot - to_call)
        base -= max(0.0, size_ratio - 0.65) * 0.04
    else:
        base += (0.29 - stats["aggression"]) * 0.04
    return _clamp(base, 0.01, 0.99), confidence


def _future_forced_cost(data: Dict[str, Any]) -> int:
    """Exact blinds still to be paid after the current hand."""

    hero_seat = int(data.get("your_seat", 0))
    button = int(data.get("button_seat", 0))
    hand = int(data.get("hand_number", 1))
    total = int(data.get("total_hands", 40))
    cost = 0
    for offset in range(1, max(0, total - hand) + 1):
        future_button = button if offset % 2 == 0 else 1 - button
        cost += 1 if future_button == hero_seat else 2
    return cost


def _target_delta(data: Dict[str, Any]) -> int:
    return 25 if int(data.get("phase", 2)) >= 2 else 10


def _can_lock_clear(data: Dict[str, Any]) -> bool:
    target_stack = int(data.get("starting_stack", 200)) + _target_delta(data)
    return int(data.get("your_stack", 0)) >= target_stack + _future_forced_cost(data)


def _amount(data: Dict[str, Any], pot_fraction: float, *, shove: bool = False) -> int:
    minimum_value = data.get("min_raise_to")
    maximum_value = data.get("max_raise_to")
    hero, _ = _players(data)
    already_in = int(hero.get("bet_this_round", 0))
    stack = max(0, int(data.get("your_stack", 0)))
    minimum = int(minimum_value) if minimum_value is not None else already_in
    maximum = int(maximum_value) if maximum_value is not None else already_in + stack
    if maximum < minimum:
        minimum, maximum = maximum, minimum
    if shove:
        return maximum

    to_call = max(0, int(data.get("to_call", 0)))
    pot = max(0, int(data.get("pot", 0)))
    target = already_in + to_call + max(1, round((pot + to_call) * pot_fraction))
    return max(minimum, min(maximum, target))


def _fallback(data: Dict[str, Any]) -> Action:
    legal = set(data.get("legal_actions") or [])
    for action in ("check", "call", "fold"):
        if action in legal:
            return {"action": action}
    for action in ("bet", "raise"):
        if action in legal:
            return {"action": action, "amount": _amount(data, 0.0)}
    return {"action": "check"}


def _aggress(data: Dict[str, Any], fraction: float, *, shove: bool = False) -> Action:
    legal = set(data.get("legal_actions") or [])
    preferred = "raise" if int(data.get("to_call", 0)) > 0 else "bet"
    alternatives: Iterable[str] = (preferred, "raise", "bet")
    for action in alternatives:
        if action in legal:
            return {"action": action, "amount": _amount(data, fraction, shove=shove)}
    if "call" in legal:
        return {"action": "call"}
    return _fallback(data)


def _safe_exit(data: Dict[str, Any]) -> Action:
    legal = set(data.get("legal_actions") or [])
    if "check" in legal:
        return {"action": "check"}
    if "fold" in legal:
        return {"action": "fold"}
    return _fallback(data)


def decide(data: Dict[str, Any]) -> Action:
    """Return one protocol-v2 legal action for a SHOWDOWN Phase 2 request."""

    legal = set(data.get("legal_actions") or [])
    if not legal:
        return {"action": "check"}

    model = RULES.ingest(data)
    profile = OPPONENTS.ingest(data)
    stats = profile.stats()

    round_name = str(data.get("round", "pre_reveal"))
    to_call = max(0, int(data.get("to_call", 0)))
    hero, _ = _players(data)
    delta = int(hero.get("chip_delta", 0))
    hand = int(data.get("hand_number", 1))
    total_hands = int(data.get("total_hands", 40))
    hands_left = max(0, total_hands - hand)

    if _can_lock_clear(data):
        return _safe_exit(data)

    equity, confidence = _estimated_equity(data, model, stats)
    observations = model.observation_count
    target = _target_delta(data)
    catch_up = max(0.0, (target - delta) / 45.0) if hands_left <= 14 else 0.0

    if to_call > 0:
        pot = max(0, int(data.get("pot", 0)))
        stack = max(1, int(data.get("your_stack", 1)))
        pot_odds = to_call / max(1.0, pot + to_call)
        risk_premium = 0.035 + 0.11 * (to_call / stack)
        if delta >= target and hands_left <= 12:
            risk_premium += 0.07
        risk_premium -= min(0.06, catch_up * 0.08)

        # Cheap early calls buy showdown evidence.  The cap prevents learning
        # from becoming an excuse to pay large, dominated bets.
        cheap_probe = (
            observations < 7
            and hand <= 14
            and "call" in legal
            and to_call <= max(2, min(6, round(pot * 0.20)))
            and to_call <= stack * 0.04
        )
        if cheap_probe:
            risk_premium -= 0.075 * (1.0 - confidence)

        value_raise = 0.80 if round_name == "post_reveal" else 0.84
        value_raise += 0.04 * (1.0 - confidence)
        if equity >= value_raise and not (delta >= target and hands_left <= 10):
            shove = equity >= 0.965 and confidence >= 0.55
            return _aggress(
                data,
                0.58 if round_name == "post_reveal" else 0.40,
                shove=shove,
            )
        if equity + 1e-9 >= pot_odds + risk_premium and "call" in legal:
            return {"action": "call"}
        return _safe_exit(data)

    # With no price to continue, use learned equity rather than raw rank: under
    # opaque rules a 13 is not inherently strong and a pair is not guaranteed.
    if round_name == "pre_reveal":
        if equity >= 0.72 + 0.05 * (1.0 - confidence):
            return _aggress(data, 0.52)
        if equity >= 0.61 + 0.03 * (1.0 - confidence):
            return _aggress(data, 0.34)
        bluff_rate = 0.02 + max(0.0, stats["fold_rate"] - 0.38) * 0.20
        if equity <= 0.32 and _roll(data, "pre-bluff") < bluff_rate:
            return _aggress(data, 0.30)
        return _safe_exit(data)

    if equity >= 0.79 + 0.04 * (1.0 - confidence):
        return _aggress(data, 0.66, shove=equity >= 0.97 and confidence >= 0.60)
    if equity >= 0.63 + 0.03 * (1.0 - confidence):
        return _aggress(data, 0.40)

    bluff_rate = 0.03 + max(0.0, stats["fold_rate"] - 0.35) * 0.27
    if delta < target and hands_left <= 10:
        bluff_rate += 0.04
    if equity <= 0.30 and _roll(data, "post-bluff") < bluff_rate:
        return _aggress(data, 0.42)
    return _safe_exit(data)


def _reset_learning_for_tests() -> None:
    RULES.reset()
    OPPONENTS.reset()
