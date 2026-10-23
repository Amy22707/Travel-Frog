"""Regression checks for annual event windows and permanent local events."""

import unittest
from datetime import datetime
from unittest.mock import patch

from server import server as game


def stamp(value: str) -> int:
    return int(datetime.fromisoformat(value).timestamp())


def blank_state() -> dict:
    return {
        "offline_meta": {},
        "sync_pushes": [
            {"cmd": "client.load_role", "data": {"frog": {"achieves": []}, "res": {"clover_point": 500}}},
            {"cmd": "item.load_items", "data": {"house": []}},
            {"cmd": "clover.load_clovers", "data": []},
        ],
    }


class SeasonalActivityTests(unittest.TestCase):
    def test_event_boundaries(self):
        cases = [
            ("greet_card", "2026-09-14", False),
            ("greet_card", "2026-09-15", True),
            ("greet_card", "2026-10-05", True),
            ("greet_card", "2026-10-06", False),
            ("spring_card", "2026-02-01", False),
            ("spring_card", "2026-02-02", True),
            ("spring_card", "2026-03-04", True),
            ("spring_card", "2026-03-05", False),
            ("spring_card", "2027-01-22", True),
            ("spring_card", "2027-02-22", False),
            ("greet_card", "2027-09-05", True),
            ("greet_card", "2028-10-03", True),
            ("party_cake", "2026-12-13", False),
            ("party_cake", "2026-12-14", True),
            ("party_cake", "2027-01-12", True),
            ("party_cake", "2027-01-13", False),
        ]
        for kind, day, expected in cases:
            with self.subTest(kind=kind, day=day):
                self.assertEqual(game.seasonal_activity_window(kind, stamp(day))[1] > 0, expected)

    def test_cake_progress_waits_for_next_season(self):
        state = blank_state()
        game.party_cake_payload(state, now=stamp("2026-12-14"))
        state["offline_meta"]["party_cake"]["cream"] = 5
        self.assertEqual(game.party_cake_payload(state, now=stamp("2026-12-15"))[0]["cream"], 5)
        closed = game.party_cake_payload(state, now=stamp("2027-01-13"))[0]
        self.assertEqual((closed["end_time"], closed["cream"]), (0, 5))
        fresh = game.party_cake_payload(state, now=stamp("2027-12-14"))[0]
        self.assertEqual(fresh["cream"], 0)

    def test_permanent_events_keep_progress(self):
        state = blank_state()
        state["offline_meta"]["capsule"] = {"end_time": 1, "coin": 7}
        now = stamp("2026-09-19")
        capsule = game.capsule_payload(state, now=now)[0]
        self.assertEqual(capsule["coin"], 7)
        self.assertGreater(capsule["end_time"], now)
        self.assertEqual(game.capsule_payload(state, now=now + 86400)[0]["coin"], 7)
        state["offline_meta"]["museum_day"] = {"end_time": 1, "compass": 3}
        museum = game.museum_day_payload(state, now=now)[0]
        self.assertEqual(museum["compass"], 3)
        self.assertGreater(museum["end_time"], now)
        wishing = game.get_wishing_pool_state(state)
        self.assertGreater(wishing["end_time"], now)

    def test_children_day_reward_is_annual_and_title_is_unique(self):
        state = blank_state()
        with patch.object(game.time, "time", return_value=stamp("2026-05-31")):
            self.assertFalse(game.claim_children_day_login_reward(state))
        with patch.object(game.time, "time", return_value=stamp("2026-06-01")):
            self.assertTrue(game.claim_children_day_login_reward(state))
            self.assertFalse(game.claim_children_day_login_reward(state))
        with patch.object(game.time, "time", return_value=stamp("2027-06-01")):
            self.assertTrue(game.claim_children_day_login_reward(state))
        _, house, _, _ = game.get_item_parts(state)
        self.assertEqual(game.get_house_count(house, 17), 2)
        self.assertEqual(game.get_role_parts(state)[1]["achieves"].count(78), 1)

    def test_spring_gold_depends_on_card_and_stickers_not_send_count(self):
        state = blank_state()
        game.spring_card_payload(state, now=stamp("2026-02-17"))
        card_state = state["offline_meta"]["spring_card"]
        for background, tags in (
            (1, [110, 101, 109]), (2, [105, 112, 102]),
            (3, [103, 106, 112]), (4, [109, 110, 103]),
            (5, [107, 103, 112]),
        ):
            card_state["card_info"] = {"bg": background, "bless": 0, "tags": tags}
            self.assertEqual(game.spring_card_send(state)[1], 200010)
        card_state["card_info"] = {"bg": 1, "bless": 0, "tags": [106, 101, 110]}
        self.assertEqual(game.spring_card_send(state)[1], 200009)
        self.assertEqual(card_state["sent_count"], 6)
        self.assertEqual(len(game.SPRING_CARD_GOLD_CHANCES), 20)

    def test_spring_shop_sells_half_price_paper_once(self):
        state = blank_state()
        spring_noon = stamp("2026-02-17 12:00:00")
        with patch.object(game, "save_state"):
            game.refresh_dudu_garden_shop(state, now=spring_noon)
            arrival = state["offline_meta"]["garden"]["dudu_window"]["start_time"]
            game.refresh_dudu_garden_shop(state, now=arrival + 1)
            shop = game.get_furniture_data(state, "furniture.load_furniture", {})["shop"]
            rows = shop["shop_list"]
            self.assertEqual(sum(row["shop_id"] == 28 for row in rows), 1)
            self.assertLessEqual(len(rows), 10)
            self.assertFalse(any(row["shop_id"] == 3002 for row in rows))
            self.assertEqual(game._cfg_dict("furnitureShopData_json.json")["28"]["price"], 100)
            self.assertTrue(game.apply_furniture_shop_buy(state, 28)[0])
            self.assertEqual(game.get_role_parts(state)[2]["clover_point"], 400)
            game.refresh_dudu_garden_shop(state, now=arrival + 2)
            self.assertEqual(sum(row["shop_id"] == 28 for row in shop["shop_list"]), 1)
            self.assertEqual(next(row["num"] for row in shop["shop_list"] if row["shop_id"] == 28), 0)


if __name__ == "__main__":
    unittest.main()
