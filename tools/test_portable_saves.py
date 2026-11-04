"""Portable save integrity, edition separation and personal-history isolation."""

import copy
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from desktop.saves import create_save, export_save, import_save, mark_portable, new_state, read_save, save_path
from server import server as game
from tools.build_gallery_state import make_gallery_state


class PortableSaveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.seed = new_state("测试青蛙")

    def test_new_editions_do_not_share_progress(self):
        normal = copy.deepcopy(self.seed)
        gallery = make_gallery_state(normal)
        self.assertEqual(len(game.get_album_index(normal)), 0)
        self.assertGreater(len(game.get_album_index(gallery)), 300)
        self.assertEqual(game.get_role_parts(normal)[2]["clover_point"], 500)
        self.assertEqual(game.get_role_parts(gallery)[2]["clover_point"], 9999)
        self.assertEqual(game.get_role_parts(gallery)[2]["ticket"], 999)
        normal_tumbler = game.find_push(normal, "furniture.load_tumbler")["data"]
        gallery_tumbler = game.find_push(gallery, "furniture.load_tumbler")["data"]
        self.assertEqual(normal_tumbler["tumbler_list"], [])
        self.assertEqual(normal_tumbler["show_index"], 0)
        self.assertGreater(len(gallery_tumbler["tumbler_list"]), 0)
        self.assertTrue(all(row["layers"] for row in gallery_tumbler["tumbler_list"]))
        self.assertEqual(game.get_story_state(normal)["stories"], [])
        self.assertNotIn("travelDiary", game.get_role_parts(normal)[0]["settings"]["client"])
        self.assertEqual(json.loads(game.get_role_parts(gallery)[0]["settings"]["client"])["guideStep"], "Complete")

    def test_default_title_is_owned_before_any_trip_and_restored_for_old_saves(self):
        state = copy.deepcopy(self.seed)
        frog = game.get_role_parts(state)[1]
        self.assertEqual(frog["cur_achieve"], 0)
        self.assertEqual(frog["achieves"], [0])
        for old_owned, equipped in (([], 0), ([1], 1)):
            frog.update(achieves=old_owned, cur_achieve=equipped)
            with patch.object(game, "save_state") as save:
                self.assertEqual(game.refresh_achievements(state), [])
                self.assertEqual(frog["achieves"], [0, *old_owned])
                self.assertEqual(frog["cur_achieve"], equipped)
                self.assertNotIn(0, state["offline_meta"].get("achievement_notices", []))
                game.refresh_achievements(state)
                save.assert_called_once_with(state, "achievement.default_title")

    def test_capsule_food_in_bag_or_desk_completes_without_payment(self):
        class Socket:
            def __init__(self, area):
                self.request = {"cmd": f"item.putin_{area}", "session": 1, "data": {"pos": 1, "item_id": 0}}
                self.messages = []

            async def __aiter__(self):
                yield json.dumps(self.request)

            async def send(self, message):
                self.messages.append(json.loads(message))

        for area in ("bag", "desk"):
            state = copy.deepcopy(self.seed)
            game.capsule_payload(state)
            capsule = state["offline_meta"]["capsule"]
            capsule.update(task_list=[2], pre_coin=0)
            socket = Socket(area)
            with patch.object(game, "save_state"):
                asyncio.run(game.serve_client(socket, state))
            self.assertTrue(game.capsule_food_is_prepared(state))
            self.assertEqual(capsule["task_list"], [])
            self.assertEqual(capsule["pre_coin"], 1)
            self.assertEqual(game.get_role_parts(state)[2]["clover_point"], 500)

    def test_normal_shop_unlock_items_and_first_animatable_photo(self):
        state = copy.deepcopy(self.seed)
        game.get_role_parts(state)[2]["clover_point"] = 3500
        with patch.object(game, "save_state"):
            self.assertTrue(game.apply_shop_purchase(state, shop_id=27, item_id=7000, price=2000)[0])
            self.assertTrue(game.apply_shop_purchase(state, shop_id=35, item_id=7001, price=1000)[0])
            house = game.get_item_parts(state)[1]
            self.assertEqual([game.get_house_count(house, item) for item in (7000, 7001)], [1, 1])
            self.assertFalse(game.unlock_animpicture_guide(state))
            game.find_push(state, "album.load_all")["data"]["id_list"].append({"id": 1, "pic_id": 100})
            self.assertTrue(game.unlock_animpicture_guide(state))
            self.assertEqual(game.get_animpicture_state(state)["guide"], 1)
            self.assertFalse(game.unlock_animpicture_guide(state))

    def test_observations_share_one_drop_path_from_empty_save(self):
        state = copy.deepcopy(self.seed)
        notes = game.get_travel_note_state(state)["note_list"]
        self.assertEqual(notes, [])
        with patch.object(game, "deterministic_roll", side_effect=[0, 99, 0, 0, 0, 0]):
            self.assertIsNone(game.roll_travel_observation(state, 100))
            first = game.roll_travel_observation(state, 200)
            second = game.roll_travel_observation(state, 300)
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertNotEqual(first, second)
        self.assertEqual(notes[0], {"id": first, "read": False, "timestamp": 200})

    def test_export_import_restores_layers_and_backs_up_existing_save(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as folder:
            root = Path(folder)
            source = root / "original.json"
            state = copy.deepcopy(self.seed)
            state["offline_meta"]["album_full"] = {"123": {"id": 123, "pic_id": 100, "layers": [{"res": 1, "x": 5}]}}
            game.atomic_write_json(source, state)
            archive = root / "backup.frogsave"
            export_save(source, archive, "normal")
            target = import_save(archive, root / "data", "normal")
            imported = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(imported["offline_meta"]["album_full"], state["offline_meta"]["album_full"])
            import_save(archive, root / "data", "normal")
            self.assertEqual(len(list(target.parent.glob("import-backups/*.frogsave"))), 1)
            with game.state_process_lock(target):
                with self.assertRaises((RuntimeError, OSError)):
                    import_save(archive, root / "data", "normal")
            with self.assertRaises(ValueError):
                import_save(archive, root / "data", "gallery")
            self.assertFalse(save_path(root / "data", "gallery").exists())

    def test_capture_import_keeps_all_photo_detail_batches(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as folder:
            capture = Path(folder) / "capture.jsonl"
            records = [{"kind": "recv", "message": row} for row in self.seed["sync_pushes"]]
            for iid in (11, 12):
                records += [{"kind": "recv", "message": {"session": iid, "data": {
                    "pictures": [{"id": iid, "pic_id": 100, "layers": [{"x": iid}]}]}}}]
            capture.write_text("\n".join(json.dumps(row) for row in records), encoding="utf-8")
            state, edition = read_save(capture)
            self.assertEqual(edition, "normal")
            self.assertEqual(set(state["offline_meta"]["album_full"]), {"11", "12"})

    def test_imported_save_skips_personal_history_repairs(self):
        state = mark_portable(copy.deepcopy(self.seed))
        with patch.object(game, "save_state") as save:
            self.assertFalse(game.migrate_stage6c1_state(state))
            self.assertFalse(game.migrate_stage6h_album_official_layers(state))
            data = game.get_lottery_data(state)
            data.update(phase=171, state=game.LOTTERY_COMPLETE, answer=game.CAPTURED_LOTTERY_SELECT_LISTS[171][:5])
            self.assertFalse(game.settle_captured_lottery_phase172(state))
            save.assert_not_called()
            self.assertEqual(data["phase"], 171)

    def test_single_writer_lock_releases_after_exit(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as folder:
            with patch.object(game, "STATE_FILE", Path(folder) / "state.json"):
                with game.state_process_lock():
                    with self.assertRaises((RuntimeError, OSError)):
                        with game.state_process_lock():
                            self.fail("Two servers acquired the same save")
                with game.state_process_lock():
                    pass


if __name__ == "__main__":
    unittest.main()
