"""No-network tests for Amigo's workspace-only file tools."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import tools  # noqa: E402
from app.agent import DesktopAgent, AgentError  # noqa: E402


class WorkspaceToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self._old_workspace = tools.WORKSPACE_DIR
        self.temp = tempfile.TemporaryDirectory()
        self.outside = tempfile.TemporaryDirectory()
        tools.WORKSPACE_DIR = Path(self.temp.name).resolve()
        self.outside_path = Path(self.outside.name).resolve()

    def tearDown(self) -> None:
        tools.WORKSPACE_DIR = self._old_workspace
        self.temp.cleanup()
        self.outside.cleanup()

    def test_create_and_read_text_file(self) -> None:
        created = tools.create_file("notes/hello.txt", "hello Amigo")
        self.assertTrue(created["ok"], created)
        read = tools.read_file("notes/hello.txt")
        self.assertTrue(read["ok"], read)
        self.assertEqual(read["content"], "hello Amigo")

    def test_existing_file_is_not_overwritten(self) -> None:
        tools.create_file("hello.txt", "first")
        result = tools.create_file("hello.txt", "second")
        self.assertFalse(result["ok"])
        self.assertEqual((tools.WORKSPACE_DIR / "hello.txt").read_text(encoding="utf-8"), "first")

    def test_rename_workspace_file_and_folder(self) -> None:
        tools.create_folder("drafts")
        tools.create_file("drafts/notes.txt", "keep this text")

        renamed_file = tools.execute_tool(
            "rename_workspace_item", {"path": "drafts/notes.txt", "new_name": "final.txt"}
        )
        renamed_folder = tools.execute_tool(
            "rename_workspace_item", {"path": "drafts", "new_name": "archive"}
        )

        self.assertTrue(renamed_file["ok"], renamed_file)
        self.assertTrue(renamed_folder["ok"], renamed_folder)
        self.assertEqual((tools.WORKSPACE_DIR / "archive" / "final.txt").read_text(encoding="utf-8"), "keep this text")
        self.assertFalse((tools.WORKSPACE_DIR / "drafts").exists())

    def test_move_file_and_folder_preserves_contents_and_refuses_overwrite(self) -> None:
        tools.create_folder("archive")
        tools.create_folder("drafts")
        tools.create_file("drafts/notes.txt", "keep this text")
        tools.create_file("archive/notes.txt", "do not replace")

        conflict = tools.move_workspace_item("drafts/notes.txt", "archive")
        self.assertFalse(conflict["ok"])
        self.assertEqual((tools.WORKSPACE_DIR / "drafts" / "notes.txt").read_text(encoding="utf-8"), "keep this text")
        (tools.WORKSPACE_DIR / "archive" / "notes.txt").unlink()
        moved_folder = tools.move_workspace_item("drafts", "archive")
        moved_file = tools.execute_tool(
            "move_workspace_item", {"path": "archive/drafts/notes.txt", "destination_path": "archive"}
        )

        self.assertTrue(moved_file["ok"], moved_file)
        self.assertTrue(moved_folder["ok"], moved_folder)
        self.assertEqual((tools.WORKSPACE_DIR / "archive" / "notes.txt").read_text(encoding="utf-8"), "keep this text")
        self.assertTrue((tools.WORKSPACE_DIR / "archive" / "drafts").is_dir())
        self.assertFalse((tools.WORKSPACE_DIR / "drafts").exists())

    def test_rename_and_move_refuse_traversal_self_moves_and_missing_destination(self) -> None:
        tools.create_folder("source")
        tools.create_file("source/item.txt", "inside")

        bad_name = tools.rename_workspace_item("source/item.txt", "../escape.txt")
        self_move = tools.move_workspace_item("source", "source/child")
        missing_folder = tools.move_workspace_item("source/item.txt", "missing/")

        self.assertFalse(bad_name["ok"])
        self.assertFalse(self_move["ok"])
        self.assertFalse(missing_folder["ok"])
        self.assertEqual((tools.WORKSPACE_DIR / "source" / "item.txt").read_text(encoding="utf-8"), "inside")
        self.assertFalse((self.outside_path / "escape.txt").exists())

    def test_move_refuses_absolute_destinations_and_linked_folders(self) -> None:
        tools.create_file("item.txt", "inside")
        external = self.outside_path / "private"
        external.mkdir()
        alias = tools.WORKSPACE_DIR / "linked"
        try:
            alias.symlink_to(external, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"Directory symlinks are unavailable in this environment: {exc}")

        absolute = tools.move_workspace_item("item.txt", str(external / "item.txt"))
        linked_destination = tools.move_workspace_item("item.txt", "linked/")

        self.assertFalse(absolute["ok"])
        self.assertFalse(linked_destination["ok"])
        self.assertEqual((tools.WORKSPACE_DIR / "item.txt").read_text(encoding="utf-8"), "inside")
        self.assertEqual(list(external.iterdir()), [])

    def test_rename_and_move_schemas_are_registered(self) -> None:
        names = {entry["function"]["name"] for entry in tools.tool_schemas()}
        self.assertIn("rename_workspace_item", names)
        self.assertIn("move_workspace_item", names)

    def test_posix_path_traversal_is_blocked(self) -> None:
        result = tools.create_file("../escape.txt", "no")
        self.assertFalse(result["ok"])
        self.assertFalse((self.outside_path / "escape.txt").exists())

    def test_windows_path_traversal_is_blocked(self) -> None:
        result = tools.create_file(r"..\..\escape.txt", "no")
        self.assertFalse(result["ok"])
        absolute = tools.read_file(r"C:\Users\someone\secret.txt")
        self.assertFalse(absolute["ok"])

    def test_absolute_paths_are_blocked(self) -> None:
        result = tools.read_file(str(self.outside_path / "secret.txt"))
        self.assertFalse(result["ok"])

    def test_ntfs_alternate_data_stream_path_is_blocked(self) -> None:
        result = tools.create_file("notes.txt:private-stream", "no")
        self.assertFalse(result["ok"])

    def test_symlink_to_outside_cannot_be_read_or_written_through(self) -> None:
        secret = self.outside_path / "secret.txt"
        secret.write_text("outside secret", encoding="utf-8")
        link = tools.WORKSPACE_DIR / "outside-link.txt"
        try:
            link.symlink_to(secret)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"Symlinks are unavailable in this environment: {exc}")

        read = tools.read_file("outside-link.txt")
        write = tools.create_file("outside-link.txt", "overwrite")
        self.assertFalse(read["ok"])
        self.assertFalse(write["ok"])
        self.assertEqual(secret.read_text(encoding="utf-8"), "outside secret")

    def test_symlink_directory_cannot_be_used_as_workspace_subfolder(self) -> None:
        outside_dir = self.outside_path / "private"
        outside_dir.mkdir()
        (outside_dir / "secret.txt").write_text("outside secret", encoding="utf-8")
        link = tools.WORKSPACE_DIR / "linked-folder"
        try:
            link.symlink_to(outside_dir, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"Directory symlinks are unavailable in this environment: {exc}")

        result = tools.read_file("linked-folder/secret.txt")
        self.assertFalse(result["ok"])
        listing = tools.list_files(".")
        self.assertTrue(listing["ok"], listing)
        entry = next(item for item in listing["items"] if item["name"] == "linked-folder")
        self.assertEqual(entry["type"], "blocked_link")

    def test_hard_linked_file_is_not_read(self) -> None:
        source = self.outside_path / "shared.txt"
        source.write_text("outside secret", encoding="utf-8")
        linked = tools.WORKSPACE_DIR / "linked.txt"
        try:
            import os
            os.link(source, linked)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"Hard links are unavailable in this environment: {exc}")
        result = tools.read_file("linked.txt")
        self.assertFalse(result["ok"])
        self.assertEqual(source.read_text(encoding="utf-8"), "outside secret")

    def test_workspace_root_cannot_be_a_symlink(self) -> None:
        alias = self.outside_path / "workspace-alias"
        try:
            alias.symlink_to(tools.WORKSPACE_DIR, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"Directory symlinks are unavailable in this environment: {exc}")
        tools.WORKSPACE_DIR = alias
        result = tools.list_files(".")
        self.assertFalse(result["ok"])

    def test_research_report_is_saved_only_through_workspace_guard(self) -> None:
        agent = DesktopAgent()
        relative = agent._save_research_report("test research", "Sample findings.")
        saved = (tools.WORKSPACE_DIR / relative).resolve()
        self.assertTrue(saved.is_relative_to(tools.WORKSPACE_DIR.resolve()))
        self.assertTrue(saved.is_file())
        self.assertIn("Sample findings.", saved.read_text(encoding="utf-8"))

    def test_report_writer_refuses_symlinked_reports_folder(self) -> None:
        reports = self.outside_path / "reports"
        reports.mkdir()
        link = tools.WORKSPACE_DIR / "reports"
        try:
            link.symlink_to(reports, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"Directory symlinks are unavailable in this environment: {exc}")
        with self.assertRaises(AgentError):
            DesktopAgent()._save_research_report("blocked research", "must not escape")
        self.assertEqual(list(reports.iterdir()), [])

    def test_unknown_tool_is_blocked(self) -> None:
        result = tools.execute_tool("run_powershell", {"command": "echo dangerous"})
        self.assertFalse(result["ok"])


if __name__ == "__main__":
    unittest.main()
