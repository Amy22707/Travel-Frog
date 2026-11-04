"""Weekend question windows, rollover and saved rewards."""

import json
import unittest
from unittest.mock import patch

from desktop.saves import new_state
from server import server as game


class WeekendLotteryTests(unittest.TestCase):
    def setUp(self):
        self.state = new_state()
        self.start = game.LOTTERY_PHASE_171_START + 8 * game.LOTTERY_WEEK_SECONDS
        self.next_week = self.start + game.LOTTERY_WEEK_SECONDS
        save = patch.object(game, "save_state")
        save.start()
        self.addCleanup(save.stop)

    def test_beijing_weekend_boundaries(self):
        for offset, opened in ((-1, False), (0, True), (172799, True), (172800, False)):
            self.assertEqual(game.weekend_lottery_window(self.start + offset)[1], opened)
        self.assertEqual(game.lottery_open(self.state, now=self.start - 1)[1], "outside_weekend")
        self.assertTrue(game.lottery_open(self.state, now=self.start + 172799)[0])
        choices = game.get_lottery_data(self.state)["select_list"][:5]
        self.assertEqual(game.lottery_select(self.state, choices, now=self.start + 172800)[1], "outside_weekend")

    def test_answer_settles_next_week_and_mail_is_once(self):
        self.assertTrue(game.lottery_open(self.state, now=self.start)[0])
        self.assertFalse(game.lottery_open(self.state, now=self.start)[0])
        data = game.get_lottery_data(self.state)
        answer = data["select_list"][:5]
        self.assertTrue(game.lottery_select(self.state, answer, now=self.start)[0])
        game.advance_weekend_lottery(self.state, self.next_week - 1)
        self.assertEqual(data["state"], game.LOTTERY_COMPLETE)
        game.advance_weekend_lottery(self.state, self.next_week)
        self.assertEqual(data["state"], game.LOTTERY_REWARD)
        self.assertEqual(data["answer"], answer)
        self.assertEqual(len(data["right_flag"]), 5)
        restored = json.loads(json.dumps(self.state))
        later = self.next_week + 3 * game.LOTTERY_WEEK_SECONDS
        game.advance_weekend_lottery(restored, later)
        self.assertEqual(game.get_lottery_data(restored)["reward"], data["reward"])
        self.assertTrue(game.lottery_confirm_reward(restored, now=later)[0])
        self.assertFalse(game.lottery_confirm_reward(restored, now=later)[0])
        self.assertEqual(len(game.get_mail_list(restored)), 1)
        self.assertEqual(game.get_lottery_data(restored)["phase"], game.weekend_lottery_window(later)[0])
        self.assertTrue(game.lottery_open(restored, now=later)[0])
        current = game.get_lottery_data(restored)
        self.assertTrue(game.lottery_select(restored, current["select_list"][:5], now=later)[0])
        self.assertTrue(game.lottery_confirm_reward(restored, now=later + game.LOTTERY_WEEK_SECONDS)[0])
        self.assertEqual(len(game.get_mail_list(restored)), 2)

    def test_matching_preserved_capture_keeps_its_observed_reward(self):
        self.state["offline_meta"].pop("portable_save")
        data = game.get_lottery_data(self.state)
        pool = game.CAPTURED_LOTTERY_SELECT_LISTS[171]
        data.update(phase=171, state=game.LOTTERY_COMPLETE, select_list=pool, answer=pool[:5])
        game.advance_weekend_lottery(self.state, game.LOTTERY_PHASE_171_START + game.LOTTERY_WEEK_SECONDS)
        self.assertEqual(data["last_phase"], 171)
        self.assertEqual(data["right_flag"], [0, 0, 0, 1, 1])
        self.assertEqual([row["id"] for row in data["reward"]], [game.LOTTERY_CLOVER_ITEM_ID, 202107])

    def test_unsubmitted_question_expires_and_dudu_has_plant_choices(self):
        self.assertTrue(game.lottery_open(self.state, now=self.start)[0])
        choices = game.get_lottery_data(self.state)["select_list"][:5]
        self.assertFalse(game.lottery_select(self.state, choices, now=self.next_week)[0])
        data = game.get_lottery_data(self.state)
        self.assertEqual(data["phase"] % 4, 0)
        self.assertEqual(set(data["select_list"]), set(game.CAPTURED_LOTTERY_SELECT_LISTS[172]))
        self.assertEqual(data["state"], game.LOTTERY_OPEN)
        self.assertFalse(game.get_mail_list(self.state))
