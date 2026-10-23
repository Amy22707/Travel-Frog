"""Portable save integrity, edition separation and personal-history isolation."""

import copy
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
        self.assertEqual(game.get_story_state(normal)["stories"], [])
        self.assertNotIn("travelDiary", game.get_role_parts(normal)[0]["settings"]["client"])
        self.assertEqual(json.loads(game.get_role_parts(gallery)[0]["settings"]["client"])["guideStep"], "Complete")

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
