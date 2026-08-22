from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import random
import unittest

from bot import (
    OPPONENTS,
    _amount,
    _can_lock_clear,
    _reset_learning_for_tests,
    decide,
    decision_diagnostics,
)
from rule_model import RULES


def request(**overrides):
    data = {
        "protocol_version": 2,
        "match_id": "test-match",
        "phase": 2,
        "table_rule": "test-opaque",
        "leg_number": 1,
        "total_legs": 4,
        "small_blind": 1,
        "big_blind": 2,
        "starting_stack": 200,
        "your_stack": 198,
        "hand_number": 1,
        "total_hands": 40,
        "round": "pre_reveal",
        "your_number": 7,
        "community_number": None,
        "your_seat": 0,
        "button_seat": 1,
        "pot": 3,
        "to_call": 0,
        "min_raise_to": 4,
        "max_raise_to": 198,
        "legal_actions": ["check", "raise"],
        "players": [
            {
                "seat": 0,
                "name": "you",
                "chip_delta": 0,
                "bet_this_round": 2,
                "stack": 198,
            },
            {
                "seat": 1,
                "name": "Marlowe",
                "chip_delta": 0,
                "bet_this_round": 2,
                "stack": 198,
            },
        ],
        "current_hand_actions": [],
        "recent_hands": [],
    }
    data.update(overrides)
    return data


def shown_hand(number, hero, villain, community, winner, actions=None):
    return {
        "hand_number": number,
        "community_number": community,
        "shown_numbers": {"0": hero, "1": villain},
        "winners": winner if isinstance(winner, list) else [winner],
        "pot": 8,
        "actions": actions or [],
    }


def low_rule_history():
    comparisons = [
        (1, 13, 7),
        (2, 12, 4),
        (3, 11, 8),
        (4, 10, 2),
        (5, 9, 6),
        (6, 8, 12),
        (7, 13, 3),
        (8, 12, 11),
        (9, 11, 1),
        (10, 10, 9),
    ]
    return [
        shown_hand(index, low, high, community, 0)
        for index, (low, high, community) in enumerate(comparisons, 1)
    ]


class BotTests(unittest.TestCase):
    def setUp(self):
        _reset_learning_for_tests()

    def assert_legal(self, data):
        move = decide(data)
        self.assertIn(move["action"], data["legal_actions"])
        if move["action"] in ("bet", "raise"):
            self.assertIsInstance(move.get("amount"), int)
            self.assertGreaterEqual(move["amount"], data["min_raise_to"])
            self.assertLessEqual(move["amount"], data["max_raise_to"])
        else:
            self.assertNotIn("amount", move)

    def test_low_rule_learning_reverses_raw_rank_strength(self):
        data = request(
            match_id="low-leg",
            table_rule="basalt",
            hand_number=20,
            round="post_reveal",
            community_number=7,
            your_number=1,
            pot=10,
            min_raise_to=2,
            legal_actions=["check", "bet"],
            recent_hands=low_rule_history(),
        )
        self.assertEqual(decide(data)["action"], "bet")
        data["your_number"] = 13
        self.assertEqual(decide(data), {"action": "check"})

    def test_event_rule_evidence_survives_a_fresh_process_model(self):
        verdigris = request(table_rule="verdigris")
        obsidian = request(table_rule="obsidian")
        high_model = RULES.model_for(verdigris)
        low_model = RULES.model_for(obsidian)
        self.assertEqual(high_model.diagnostics()["best_hypothesis"], "top:high")
        self.assertEqual(low_model.diagnostics()["best_hypothesis"], "bottom:low")
        self.assertGreater(high_model.observation_count, 20)
        self.assertGreater(low_model.equity(1, 5)[0], low_model.equity(13, 5)[0])

    def test_verdigris_weak_hand_folds_to_post_reveal_value_bet(self):
        data = request(
            table_rule="verdigris",
            round="post_reveal",
            your_number=4,
            community_number=12,
            pot=17,
            to_call=7,
            min_raise_to=14,
            legal_actions=["fold", "call", "raise"],
            current_hand_actions=[
                {"round": "post_reveal", "seat": 0, "action": "check"},
                {"round": "post_reveal", "seat": 1, "action": "bet", "amount": 7},
            ],
        )
        self.assertEqual(decide(data), {"action": "fold"})

    def test_verdigris_high_hand_does_not_reraise_nadia(self):
        data = request(
            match_id="phase2-replay-verdigris",
            table_rule="verdigris",
            hand_number=29,
            round="post_reveal",
            your_number=11,
            community_number=12,
            your_stack=170,
            pot=33,
            to_call=13,
            min_raise_to=40,
            max_raise_to=170,
            legal_actions=["fold", "call", "raise"],
            current_hand_actions=[
                {"round": "post_reveal", "seat": 1, "action": "bet", "amount": 13},
            ],
        )
        self.assertEqual(decide(data), {"action": "call"})

    def test_verdigris_marginal_equity_folds_to_post_reveal_raise(self):
        data = request(
            match_id="phase2-replay-verdigris-marginal",
            table_rule="verdigris",
            hand_number=8,
            round="post_reveal",
            your_number=8,
            community_number=1,
            your_stack=180,
            pot=32,
            to_call=14,
            min_raise_to=46,
            max_raise_to=180,
            legal_actions=["fold", "call", "raise"],
            current_hand_actions=[
                {"round": "post_reveal", "seat": 0, "action": "bet", "amount": 4},
                {"round": "post_reveal", "seat": 1, "action": "raise", "amount": 14},
            ],
            recent_hands=[
                shown_hand(
                    6,
                    8,
                    13,
                    1,
                    1,
                    [{"round": "post_reveal", "seat": 1, "action": "raise", "amount": 14}],
                )
            ],
        )
        self.assertEqual(decide(data), {"action": "fold"})

    def test_cinnabar_second_reraise_preserves_stack(self):
        data = request(
            match_id="phase2-replay-cinnabar",
            table_rule="cinnabar",
            hand_number=10,
            round="post_reveal",
            your_number=12,
            community_number=7,
            your_stack=169,
            pot=180,
            to_call=80,
            min_raise_to=211,
            max_raise_to=211,
            legal_actions=["fold", "call", "raise"],
            current_hand_actions=[
                {"round": "post_reveal", "seat": 0, "action": "bet", "amount": 5},
                {"round": "post_reveal", "seat": 1, "action": "raise", "amount": 14},
                {"round": "post_reveal", "seat": 0, "action": "raise", "amount": 27},
                {"round": "post_reveal", "seat": 1, "action": "raise", "amount": 80},
            ],
        )
        self.assertEqual(decide(data), {"action": "fold"})

    def test_obsidian_low_non_pair_value_bets_immediately(self):
        data = request(
            table_rule="obsidian",
            round="post_reveal",
            your_number=1,
            community_number=5,
            pot=10,
            min_raise_to=2,
            legal_actions=["check", "bet"],
        )
        self.assertEqual(decide(data)["action"], "bet")

    def test_learning_survives_leg_reset_and_isolated_by_codename(self):
        first_leg = request(
            match_id="leg-one",
            table_rule="basalt",
            recent_hands=low_rule_history(),
        )
        decide(first_leg)
        learned = RULES.model_for(first_leg).observation_count
        self.assertEqual(learned, len(low_rule_history()))

        next_leg = request(
            match_id="leg-two",
            leg_number=2,
            table_rule="basalt",
            recent_hands=[],
        )
        decide(next_leg)
        self.assertEqual(RULES.model_for(next_leg).observation_count, learned)

        other_rule = request(table_rule="quartz", recent_hands=[])
        decide(other_rule)
        self.assertEqual(RULES.model_for(other_rule).observation_count, 0)

    def test_repeated_request_does_not_double_count_showdown(self):
        data = request(
            recent_hands=[shown_hand(1, 9, 4, 6, 0)],
        )
        first = decide(data)
        second = decide(data)
        self.assertEqual(first, second)
        self.assertEqual(RULES.model_for(data).observation_count, 1)

    def test_decision_diagnostics_are_protocol_safe_and_serializable(self):
        data = request(table_rule="verdigris")
        action = decide(data)
        diagnostics = decision_diagnostics(data, action)
        self.assertEqual(diagnostics["action"], action)
        self.assertEqual(diagnostics["rule"], "verdigris")
        self.assertIsInstance(diagnostics["equity"], float)

    def test_tied_showdown_is_recorded_as_half_equity(self):
        data = request(
            recent_hands=[shown_hand(1, 6, 6, 9, [0, 1])],
        )
        decide(data)
        model = RULES.model_for(data)
        self.assertEqual(model.compare_probability(6, 6, 9), 0.5)

    def test_pair_is_not_assumed_unbeatable(self):
        history = low_rule_history() + [
            shown_hand(11, 8, 2, 8, 1),
            shown_hand(12, 11, 3, 11, 1),
            shown_hand(13, 6, 1, 6, 1),
        ]
        data = request(
            match_id="pair-loses",
            table_rule="inverse",
            hand_number=20,
            round="post_reveal",
            your_number=8,
            community_number=8,
            to_call=40,
            pot=50,
            legal_actions=["fold", "call", "raise"],
            min_raise_to=80,
            max_raise_to=198,
            recent_hands=history,
        )
        self.assertNotEqual(decide(data)["action"], "raise")

    def test_phase_two_clear_requires_plus_25(self):
        below = request(
            hand_number=40,
            your_stack=224,
            players=[
                {"seat": 0, "name": "you", "chip_delta": 24, "bet_this_round": 0},
                {"seat": 1, "name": "Marlowe", "chip_delta": -24, "bet_this_round": 0},
            ],
        )
        clear = dict(below, your_stack=225)
        self.assertFalse(_can_lock_clear(below))
        self.assertTrue(_can_lock_clear(clear))

    def test_lock_accounts_for_every_remaining_blind(self):
        data = request(hand_number=38, total_hands=40, your_stack=228, button_seat=1)
        # Seat 0 pays 1 then 2 in the two remaining hands: 225 + 3 is safe.
        self.assertTrue(_can_lock_clear(data))
        data["your_stack"] = 227
        self.assertFalse(_can_lock_clear(data))

    def test_opponent_tendencies_carry_across_legs_by_name(self):
        actions = [
            {"round": "pre_reveal", "seat": 1, "action": "raise", "amount": 6},
            {"round": "pre_reveal", "seat": 0, "action": "fold"},
        ]
        first = request(
            match_id="leg-a",
            recent_hands=[shown_hand(1, 0, 0, None, 1, actions)],
        )
        profile = OPPONENTS.ingest(first)
        aggression = profile.stats()["aggression"]
        second = request(match_id="leg-b", leg_number=2, recent_hands=[])
        self.assertEqual(OPPONENTS.ingest(second).stats()["aggression"], aggression)

        stranger = request(
            match_id="other-attempt",
            players=[
                {"seat": 0, "name": "you", "chip_delta": 0, "bet_this_round": 2},
                {"seat": 1, "name": "Ada", "chip_delta": 0, "bet_this_round": 2},
            ],
        )
        self.assertNotEqual(OPPONENTS.ingest(stranger).stats()["aggression"], aggression)

    def test_amount_uses_authoritative_total_bounds(self):
        data = request(
            your_stack=5,
            pot=100,
            to_call=3,
            min_raise_to=7,
            max_raise_to=7,
        )
        self.assertEqual(_amount(data, 10.0), 7)
        self.assertEqual(_amount(data, 0.0, shove=True), 7)

    def test_concurrent_duplicate_requests_are_safe(self):
        data = request(recent_hands=[shown_hand(1, 9, 4, 6, 0)])
        with ThreadPoolExecutor(max_workers=8) as pool:
            moves = list(pool.map(lambda _: decide(data), range(32)))
        self.assertTrue(all(move == moves[0] for move in moves))
        self.assertEqual(RULES.model_for(data).observation_count, 1)

    def test_random_legal_action_invariants(self):
        rng = random.Random(2026)
        for index in range(2000):
            facing = rng.random() < 0.55
            stack = rng.randint(4, 400)
            minimum = rng.randint(2, max(2, stack))
            maximum = rng.randint(minimum, max(minimum, stack))
            if facing:
                legal = ["fold", "call"]
                if stack > 2 and rng.random() < 0.75:
                    legal.append("raise")
            else:
                legal = ["check"]
                if stack > 2 and rng.random() < 0.85:
                    legal.append(rng.choice(["bet", "raise"]))
            data = request(
                match_id=f"fuzz-{index}",
                table_rule=f"rule-{index % 7}",
                hand_number=rng.randint(1, 40),
                round=rng.choice(["pre_reveal", "post_reveal"]),
                your_number=rng.randint(1, 13),
                community_number=rng.choice([None] + list(range(1, 14))),
                your_stack=stack,
                pot=rng.randint(1, 500),
                to_call=rng.randint(1, stack) if facing else 0,
                min_raise_to=minimum,
                max_raise_to=maximum,
                legal_actions=legal,
            )
            if data["round"] == "pre_reveal":
                data["community_number"] = None
            elif data["community_number"] is None:
                data["community_number"] = rng.randint(1, 13)
            self.assert_legal(data)


if __name__ == "__main__":
    unittest.main()
