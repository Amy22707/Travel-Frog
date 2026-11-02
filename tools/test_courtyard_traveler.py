"""Passing courtyard frogs, province gifts and gift-box independence."""

import copy
import unittest
from unittest.mock import patch

from desktop.saves import new_state
from server import server as game


class CourtyardTravelerTests(unittest.TestCase):
    def setUp(self):
        self.state = new_state("测试青蛙")
        game.find_push(self.state, "client.load_role")["data"]["settings"]["client"] = '{"guideStep":"Complete"}'
        self.policy = {"courtyard_traveler": {"arrival_min_seconds": 60, "arrival_max_seconds": 60}}
        self.save = patch.object(game, "save_state").start()
        self.addCleanup(patch.stopall)

    def arrive(self):
        self.assertIsNone(game.advance_courtyard_traveler(self.state, 1000, self.policy))
        self.assertIsNone(game.advance_courtyard_traveler(self.state, 1059, self.policy))
        return game.advance_courtyard_traveler(self.state, 1060, self.policy)

    def test_arrival_without_gifts_and_restart_stability(self):
        visitor = self.arrive()
        self.assertEqual(visitor["name"], "过路旅蛙")
        self.assertGreaterEqual(visitor["expire_time"], 1060 + 1200)
        self.assertLessEqual(visitor["expire_time"], 1060 + 3600)
        self.assertIn(str(visitor["food"]), game.load_static_json("visitors_json.json")["actionList"])
        restored = copy.deepcopy(self.state)
        self.assertIsNone(game.advance_courtyard_traveler(restored, 999999, self.policy))
        self.assertEqual(game.get_visit_state(restored)["visitor"], visitor)

    def test_first_gift_once_and_no_early_claim(self):
        visitor = self.arrive()
        self.assertIsNone(game.apply_visit_open(self.state, self.policy, now=1060))
        result = game.apply_visit_open(self.state, self.policy, now=visitor["expire_time"])
        self.assertTrue(result["first"])
        self.assertIn(result["province"], game.get_visit_state(self.state)["acquire"])
        self.assertIsNone(game.apply_visit_open(self.state, self.policy, now=visitor["expire_time"]))
        self.assertEqual(self.state["offline_meta"]["courtyard_traveler"]["next_arrival"], visitor["expire_time"] + 60)

    def test_repeat_gift_both_editions_and_no_giftbox_consumption(self):
        for gallery in (False, True):
            state = copy.deepcopy(self.state)
            game.get_visit_state(state)["acquire"] = [p for p, _ in game._visitor_province_rows()]
            box = game.get_giftbox_state(state)
            box["specialtys"] = [{"item_id": 4000, "count": 2}]
            original = copy.deepcopy(box)
            state["offline_meta"]["courtyard_traveler"] = {"next_arrival": 1}
            with patch.object(game, "GALLERY_MODE", gallery):
                visitor = game.advance_courtyard_traveler(state, 1000, self.policy)
                self.assertFalse(visitor["first"])
                result = game.apply_visit_open(state, self.policy, now=visitor["expire_time"])
                self.assertIn(result["ticket"], (1, 2))
            self.assertEqual(game.get_giftbox_state(state), original)

    def test_tutorial_does_not_spawn_visitors(self):
        game.find_push(self.state, "client.load_role")["data"]["settings"]["client"] = "{}"
        self.assertIsNone(game.advance_courtyard_traveler(self.state, 1000, self.policy))
        self.assertNotIn("visitor", game.get_visit_state(self.state))

    def test_default_frequency_is_days_and_stays_vary(self):
        durations = set()
        for i in range(12):
            state = copy.deepcopy(self.state)
            state["offline_meta"]["rng_counter"] = i * 10
            game.schedule_courtyard_traveler(state, 1000 + i, {})
            due = state["offline_meta"]["courtyard_traveler"]["next_arrival"]
            self.assertGreaterEqual(due, 1000 + i + 172800)
            self.assertLessEqual(due, 1000 + i + 432000)
            visitor = game.advance_courtyard_traveler(state, due, {})
            durations.add(visitor["expire_time"] - due)
        self.assertGreater(len(durations), 1)


if __name__ == "__main__":
    unittest.main()
