from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import random
import unittest
from unittest.mock import patch

from bot import (
    OPPONENTS,
    _amount,
    _future_forced_cost,
    _force_double,
    _is_clearing,
    _multiway_equity,
    _objective_pressure,
    _objective_target,
    _opponents,
    _reset_learning_for_tests,
    decide,
    decision_diagnostics,
)
from rule_model import EVENT_OBSERVATIONS, RULES, warm_event_models


NAMES = ("you", "Dana", "Miles", "Theo", "Rhea", "Bram")


def players(hero_delta=0, other_deltas=None):
    deltas = [hero_delta] + list(other_deltas or [0] * 5)
    return [
        {
            "seat": seat,
            "name": name,
            "folded": False,
            "chip_delta": deltas[seat],
            "bet_this_round": 0,
            "stack": 200 + deltas[seat],
            "all_in": False,
            "busted": False,
        }
        for seat, name in enumerate(NAMES)
    ]


def request(**overrides):
    data = {
        "protocol_version": 2,
        "match_id": "phase3-test",
        "phase": 3,
        "table_rule": "verdigris",
        "leg_number": 1,
        "total_legs": 4,
        "small_blind": 1,
        "big_blind": 2,
        "starting_stack": 200,
        "your_stack": 200,
        "hand_number": 1,
        "total_hands": 60,
        "round": "pre_reveal",
        "your_number": 7,
        "community_number": None,
        "your_seat": 0,
        "button_seat": 5,
        "pot": 3,
        "to_call": 0,
        "min_raise_to": 4,
        "max_raise_to": 200,
        "legal_actions": ["check", "raise"],
        "players": players(),
        "current_hand_actions": [],
        "recent_hands": [],
    }
    data.update(overrides)
    return data


def completed(hand_number, community, shown, winners, actions=None):
    return {
        "hand_number": hand_number,
        "community_number": community,
        "shown_numbers": {str(seat): number for seat, number in shown.items()},
        "winners": winners,
        "pot": 20,
        "actions": actions or [],
    }


class Phase3BotTests(unittest.TestCase):
    def setUp(self):
        _reset_learning_for_tests()

    def assert_legal(self, data):
        action = decide(data)
        self.assertIn(action["action"], data["legal_actions"])
        if action["action"] in ("bet", "raise"):
            self.assertIsInstance(action.get("amount"), int)
            self.assertGreaterEqual(action["amount"], data["min_raise_to"])
            self.assertLessEqual(action["amount"], data["max_raise_to"])
        else:
            self.assertNotIn("amount", action)

    def test_filters_folded_and_busted_but_keeps_all_in(self):
        seats = players()
        seats[1]["folded"] = True
        seats[2]["busted"] = True
        seats[3]["all_in"] = True
        live = _opponents(request(players=seats), live_only=True)
        self.assertEqual([player["seat"] for player in live], [3, 4, 5])

    def test_clear_requires_plus_ten_and_strictly_first(self):
        tied = request(players=players(20, [20, 0, 0, 0, 0]))
        ahead = request(players=players(20, [19, 0, 0, 0, 0]))
        below = request(players=players(9, [0, 0, 0, 0, 0]))
        self.assertFalse(_is_clearing(tied))
        self.assertTrue(_is_clearing(ahead))
        self.assertFalse(_is_clearing(below))

    def test_objective_tracks_table_leader(self):
        data = request(players=players(-4, [35, -10, 2, 4, 1]))
        self.assertEqual(_objective_target(data), 36)

    def test_multiway_equity_is_lower_than_heads_up_equity(self):
        six = request(round="post_reveal", your_number=11, community_number=4)
        six_profiles = OPPONENTS.ingest(six)
        six_equity = _multiway_equity(six, RULES.ingest(six), six_profiles)[0]
        heads_players = players()
        for seat in range(2, 6):
            heads_players[seat]["folded"] = True
        heads = request(round="post_reveal", your_number=11, community_number=4,
                        players=heads_players)
        heads_profiles = OPPONENTS.ingest(heads)
        heads_equity = _multiway_equity(heads, RULES.ingest(heads), heads_profiles)[0]
        self.assertLess(six_equity, heads_equity)

    def test_pair_value_bets_multiway_under_verdigris(self):
        data = request(
            round="post_reveal",
            your_number=7,
            community_number=7,
            pot=12,
            min_raise_to=3,
            legal_actions=["check", "bet"],
        )
        self.assertEqual(decide(data)["action"], "bet")

    def test_bad_pair_has_negligible_multiway_equity_under_obsidian(self):
        data = request(
            table_rule="obsidian",
            round="post_reveal",
            your_number=7,
            community_number=7,
            pot=12,
            min_raise_to=3,
            legal_actions=["check", "bet"],
        )
        profiles = OPPONENTS.ingest(data)
        equity = _multiway_equity(data, RULES.ingest(data), profiles)[0]
        self.assertLess(equity, 0.01)

    def test_rule_learning_uses_opponent_only_showdowns(self):
        hand = completed(1, 6, {1: 13, 2: 8, 4: 3}, [1])
        data = request(table_rule="new-rule", recent_hands=[hand])
        decide(data)
        self.assertEqual(RULES.model_for(data).observation_count, 2)

    def test_multiwinner_showdown_does_not_invent_sidepot_ordering(self):
        hand = completed(1, 6, {1: 10, 2: 10, 3: 4}, [1, 2])
        data = request(table_rule="new-tie-rule", recent_hands=[hand])
        decide(data)
        self.assertEqual(RULES.model_for(data).observation_count, 0)

    def test_phase_two_evidence_identifies_amaranth_seven_trump(self):
        data = request(table_rule="amaranth")
        model = RULES.model_for(data)
        self.assertEqual(model.diagnostics()["best_hypothesis"], "top:seven_high")
        self.assertGreater(model.equity(7, 4)[0], model.equity(13, 4)[0])

    def test_known_rule_models_can_be_prewarmed(self):
        warm_event_models()
        for name in EVENT_OBSERVATIONS:
            model = RULES.model_for({"table_rule": name})
            self.assertIn((13, None), model._equity_cache)

    def test_duplicate_recent_window_is_idempotent(self):
        hand = completed(1, 6, {1: 13, 2: 8, 4: 3}, [1])
        data = request(table_rule="new-rule", recent_hands=[hand])
        decide(data)
        decide(data)
        self.assertEqual(RULES.model_for(data).observation_count, 2)

    def test_lead_protection_folds_when_exposure_loses_first_place(self):
        seats = players(30, [15, 5, 0, -5, -10])
        seats[0]["stack"] = 225  # five chips are already committed this hand
        seats[0]["bet_this_round"] = 5
        seats[1]["bet_this_round"] = 15
        data = request(
            hand_number=25,
            round="post_reveal",
            your_number=13,
            community_number=4,
            your_stack=225,
            players=seats,
            pot=35,
            to_call=10,
            min_raise_to=30,
            max_raise_to=225,
            legal_actions=["fold", "call", "raise"],
            current_hand_actions=[
                {"round": "post_reveal", "seat": 0, "action": "bet", "amount": 5},
                {"round": "post_reveal", "seat": 1, "action": "raise", "amount": 15},
            ],
        )
        self.assertEqual(decide(data), {"action": "fold"})

    def test_repeated_raise_does_not_trigger_stack_off(self):
        data = request(
            hand_number=20,
            round="post_reveal",
            your_number=13,
            community_number=4,
            your_stack=150,
            pot=180,
            to_call=80,
            min_raise_to=150,
            max_raise_to=150,
            legal_actions=["fold", "call", "raise"],
            current_hand_actions=[
                {"round": "post_reveal", "seat": 1, "action": "raise", "amount": 20},
                {"round": "post_reveal", "seat": 0, "action": "raise", "amount": 50},
                {"round": "post_reveal", "seat": 1, "action": "raise", "amount": 130},
            ],
        )
        self.assertNotEqual(decide(data)["action"], "raise")

    def test_dominant_pair_calls_large_reraise_at_profitable_odds(self):
        seats = players(-3, [159, -38, -200, 173, -91])
        seats[3]["busted"] = True
        data = request(
            hand_number=10,
            round="post_reveal",
            your_number=11,
            community_number=11,
            your_stack=178,
            players=seats,
            pot=194,
            to_call=55,
            min_raise_to=129,
            max_raise_to=178,
            legal_actions=["fold", "call", "raise"],
            current_hand_actions=[
                {"round": "post_reveal", "seat": 5, "action": "bet", "amount": 8},
                {"round": "post_reveal", "seat": 0, "action": "raise", "amount": 19},
                {"round": "post_reveal", "seat": 2, "action": "call", "amount": 19},
                {"round": "post_reveal", "seat": 4, "action": "raise", "amount": 74},
                {"round": "post_reveal", "seat": 5, "action": "call", "amount": 74},
            ],
        )
        self.assertEqual(decide(data), {"action": "call"})

    def test_objective_pressure_rises_with_gap_and_time(self):
        early = request(
            hand_number=5,
            your_stack=200,
            players=players(0, [20, 0, 0, 0, 0]),
        )
        late = request(
            hand_number=50,
            your_stack=100,
            players=players(-100, [250, 0, 0, 0, 0]),
        )
        self.assertGreater(_objective_pressure(late), _objective_pressure(early))
        self.assertEqual(_objective_pressure(late), 1.0)

    def test_trailing_value_bet_uses_objective_sized_pressure(self):
        modest = request(
            hand_number=5,
            round="post_reveal",
            community_number=4,
            pot=20,
            min_raise_to=4,
            max_raise_to=200,
            legal_actions=["check", "bet"],
            players=players(0, [20, 0, 0, 0, 0]),
        )
        urgent = request(
            hand_number=50,
            round="post_reveal",
            community_number=4,
            your_stack=100,
            pot=20,
            min_raise_to=4,
            max_raise_to=100,
            legal_actions=["check", "bet"],
            players=players(-100, [250, 0, 0, 0, 0]),
        )
        with patch("bot._multiway_equity", return_value=(0.85, 0.95, 1, 0.99)):
            modest_action = decide(modest)
            urgent_action = decide(urgent)
        self.assertEqual(modest_action["action"], "bet")
        self.assertEqual(urgent_action["action"], "bet")
        self.assertGreater(urgent_action["amount"], modest_action["amount"])

    def test_late_unreachable_gap_forces_double_with_strong_single(self):
        data = request(
            table_rule="obsidian",
            hand_number=57,
            round="post_reveal",
            your_number=2,
            community_number=6,
            your_stack=276,
            pot=12,
            min_raise_to=4,
            max_raise_to=276,
            legal_actions=["check", "bet"],
            players=players(76, [-200, 724, -200, -200, -200]),
        )
        with patch("bot._multiway_equity", return_value=(0.927, 0.999, 1, 0.968)):
            action = decide(data)
        self.assertTrue(_force_double(data, 0.927, 0.968))
        self.assertEqual(action, {"action": "bet", "amount": 276})

    def test_early_gap_does_not_force_premature_all_in(self):
        data = request(
            hand_number=4,
            round="post_reveal",
            your_number=12,
            community_number=12,
            your_stack=200,
            pot=12,
            min_raise_to=4,
            max_raise_to=200,
            legal_actions=["check", "bet"],
            players=players(0, [406, -100, -100, -100, -106]),
        )
        with patch("bot._multiway_equity", return_value=(0.968, 0.95, 1, 0.999)):
            action = decide(data)
        self.assertFalse(_force_double(data, 0.968, 0.999))
        self.assertEqual(action["action"], "bet")
        self.assertLess(action["amount"], 200)

    def test_forced_cost_skips_busted_seats(self):
        full = request(hand_number=59, button_seat=4)
        reduced_players = players()
        for seat in (1, 3, 5):
            reduced_players[seat]["busted"] = True
        reduced = request(hand_number=59, button_seat=4, players=reduced_players)
        self.assertEqual(_future_forced_cost(full), 1)
        self.assertEqual(_future_forced_cost(reduced), 0)

    def test_amount_honours_authoritative_total_bounds(self):
        data = request(
            your_stack=7,
            pot=100,
            to_call=5,
            min_raise_to=9,
            max_raise_to=9,
        )
        self.assertEqual(_amount(data, 10.0), 9)

    def test_diagnostics_include_multiway_objective(self):
        data = request(players=players(5, [12, 3, 0, -4, -8]))
        action = decide(data)
        diagnostics = decision_diagnostics(data, action)
        self.assertEqual(diagnostics["live_opponents"], 5)
        self.assertEqual(diagnostics["target_delta"], 13)
        self.assertEqual(diagnostics["leader_delta"], 12)
        self.assertEqual(diagnostics["action"], action)

    def test_diagnostics_reuse_the_decision_equity(self):
        data = request(round="post_reveal", community_number=7)
        action = decide(data)
        with patch("bot._multiway_equity", side_effect=AssertionError("recomputed")):
            diagnostics = decision_diagnostics(data, action)
        self.assertEqual(diagnostics["action"], action)

    def test_concurrent_duplicate_requests_are_safe(self):
        hand = completed(1, 6, {0: 13, 1: 8, 2: 3}, [0])
        data = request(table_rule="parallel-rule", recent_hands=[hand])
        with ThreadPoolExecutor(max_workers=8) as pool:
            actions = list(pool.map(lambda _: decide(data), range(24)))
        self.assertTrue(all(action == actions[0] for action in actions))
        self.assertEqual(RULES.model_for(data).observation_count, 2)

    def test_random_six_seat_requests_always_return_legal_actions(self):
        rng = random.Random(303)
        for index in range(300):
            seats = players(
                rng.randint(-100, 150),
                [rng.randint(-150, 250) for _ in range(5)],
            )
            for player in seats[1:]:
                player["folded"] = rng.random() < 0.25
                player["busted"] = rng.random() < 0.05
            if all(player["folded"] or player["busted"] for player in seats[1:]):
                seats[1]["folded"] = False
                seats[1]["busted"] = False
            facing = rng.random() < 0.55
            legal = ["fold", "call", "raise"] if facing else ["check", "bet"]
            stack = rng.randint(4, 400)
            minimum = rng.randint(2, max(2, stack))
            maximum = rng.randint(minimum, max(minimum, stack))
            data = request(
                match_id=f"fuzz-{index}",
                table_rule=("verdigris", "cinnabar", "amaranth", "obsidian")[index % 4],
                players=seats,
                hand_number=rng.randint(1, 60),
                round=rng.choice(["pre_reveal", "post_reveal"]),
                your_number=rng.randint(1, 13),
                your_stack=stack,
                community_number=rng.randint(1, 13),
                pot=rng.randint(3, 600),
                to_call=rng.randint(1, stack) if facing else 0,
                min_raise_to=minimum,
                max_raise_to=maximum,
                legal_actions=legal,
            )
            if data["round"] == "pre_reveal":
                data["community_number"] = None
            self.assert_legal(data)


if __name__ == "__main__":
    unittest.main()
