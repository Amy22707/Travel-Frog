"""Regression checks for offline state transitions shared by both editions."""

import copy
import asyncio
import json
import unittest
from unittest.mock import patch

from server import server as game


def blank_state():
    return {
        "offline_meta": {},
        "sync_pushes": [
            {"cmd": "client.load_role", "data": {"frog": {"achieves": []}, "res": {"clover_point": 500}}},
            {"cmd": "item.load_items", "data": {"house": []}},
            {"cmd": "clover.load_clovers", "data": []},
        ],
    }


class GardenTimeTests(unittest.TestCase):
    def garden(self, items):
        state = blank_state()
        game.get_flowerpot_data(state)["plant_list"][0].update(id=2010101, stage=1)
        game.sync_garden_meta_from_wire(state, now=1000)
        comp = game.get_compost_data(state)
        comp["box_list"] = items + [0] * (6 - len(items))
        game._compost_meta(state)["slots"] = {
            str(i): {"item_id": item, "placed_at": 1000}
            for i, item in enumerate(items, 1)
        }
        return state

    def test_offline_queue_matches_frequent_updates_and_persists(self):
        for gallery in (False, True):
            with self.subTest(gallery=gallery), patch.object(game, "GALLERY_MODE", gallery):
                offline = self.garden([20001] * 6)
                online = copy.deepcopy(offline)
                finish = 1000 + 300 + 6 * 7200
                for now in range(1300, finish + 1, 300):
                    game.advance_garden_growth(online, now=now, save=False)
                game.advance_garden_growth(offline, now=finish, save=False)
                self.assertEqual(game.get_compost_data(offline)["box_list"], [0] * 6)
                self.assertEqual(game.get_compost_data(offline)["state"], 4)
                a, b = game.garden_meta(offline), game.garden_meta(online)
                self.assertEqual(a["fertility_bonus_points"], 210)
                self.assertEqual(a["fertility_until"], b["fertility_until"])
                self.assertAlmostEqual(a["plants"]["1"]["progress"], b["plants"]["1"]["progress"])
                snapshot = copy.deepcopy(offline)
                game.advance_garden_growth(offline, now=finish, save=False)
                self.assertEqual(offline, snapshot)

    def test_fertility_applies_only_after_absorption_and_before_expiry(self):
        state = self.garden([20001])
        completion = 8500
        game.advance_garden_growth(state, now=completion, save=False)
        plant = game.garden_meta(state)["plants"]["1"]
        self.assertAlmostEqual(plant["progress"], 100 + 7500 * 10 / 3600)
        game.advance_garden_growth(state, now=completion + 25 * 3600, save=False)
        self.assertAlmostEqual(plant["progress"], 100 + 7500 * 10 / 3600 + 24 * 10 * 1.25 + 10)
        self.assertEqual(game.get_compost_data(state)["state"], 0)

    def test_expired_effect_is_not_reissued_on_login(self):
        state = self.garden([20001] * 6)
        game.advance_garden_growth(state, now=1000 + 10 * 86400, save=False)
        self.assertEqual(game.garden_meta(state)["fertility_bonus_points"], 0)
        self.assertEqual(game.get_compost_data(state)["box_list"], [0] * 6)

    def test_specialty_lights_first_of_three_original_flowers(self):
        state = self.garden([4000])
        game.advance_garden_growth(state, now=8500, save=False)
        self.assertEqual(game.get_compost_data(state)["state"], 2)


class GalleryRewardTests(unittest.TestCase):
    def test_guide_rewards_match_fixed_inventory_and_pushes(self):
        class Socket:
            def __init__(self):
                self.messages = []

            async def send(self, message):
                self.messages.append(json.loads(message))

        with patch.object(game, "GALLERY_MODE", True):
            state = blank_state()
            for item_id, expected in ((game.LOTTERY_CLOVER_ITEM_ID, 9999),
                                      (game.LOTTERY_TICKET_ITEM_ID, 999),
                                      (101, 99)):
                reward = game.grant_guide_task_item(state, item_id, 1)
                self.assertEqual(reward["total"], expected)
            self.assertEqual(game._calendar_add_item(state, 101), 99)
            socket = Socket()
            asyncio.run(game.send_json(socket, {"cmd": "item.update_ticket", "data": {"ticket": 1000}}))
            self.assertEqual(socket.messages[-1]["data"]["ticket"], 999)


class FurnitureReturnTests(unittest.TestCase):
    def test_return_progress_is_saved_even_without_visible_change(self):
        for messy in (False, True):
            with self.subTest(messy=messy):
                state = blank_state()
                state["offline_meta"]["furniture"] = {"workbench_mess": messy}
                policy = {"furniture": {"mess_recovery_trips": 3, "home_auto_replace_percent": 0}}
                with patch.object(game, "save_state") as save:
                    game.apply_furniture_home_return(state, policy=policy)
                    save.assert_called_once()
                furniture = state["offline_meta"]["furniture"]
                if messy:
                    self.assertEqual(furniture["mess_recovery_progress"], 1)
                else:
                    self.assertEqual(furniture["home_return_count"], 1)


if __name__ == "__main__":
    unittest.main()
