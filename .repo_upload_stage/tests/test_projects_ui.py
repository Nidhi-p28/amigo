from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialog

from app.features import ProjectService
from app.product_ui import GoalManagerPanel
from app.projects_ui import ProjectEditorDialog, ProjectsPanel
from app.services import GoalService
from app.storage import SqliteDatabase


class ProjectsPanelActionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_study_hub_creation_and_ask_button_work(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = SqliteDatabase(Path(directory) / "amigo.sqlite3", copy_legacy=False)
            panel = ProjectsPanel(database)
            prompt_messages: list[str] = []
            panel.askRequested.connect(prompt_messages.append)
            with patch("app.projects_ui.ProjectEditorDialog") as dialog_class:
                dialog = dialog_class.return_value
                dialog.exec.return_value = QDialog.DialogCode.Accepted
                dialog.values.return_value = {
                    "title": "Biology revision",
                    "description": "Collect study work in one place.",
                    "kind": "study",
                    "target_date": None,
                }

                panel.add_study_button.click()

            projects = ProjectService(database).list_projects()
            self.assertEqual(1, len(projects))
            self.assertEqual("study", projects[0]["kind"])
            self.assertEqual("Biology revision", panel.title_label.text())
            self.assertTrue(panel.ask_button.isEnabled())

            panel.ask_button.click()

            self.assertEqual(1, len(prompt_messages))
            self.assertIn("Biology revision", prompt_messages[0])
            self.assertIn("What should I work on next?", prompt_messages[0])
            panel.deleteLater()
            self.application.processEvents()

    def test_existing_milestone_button_completes_and_reopens(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = SqliteDatabase(Path(directory) / "amigo.sqlite3", copy_legacy=False)
            goals = GoalService(database)
            goal = goals.create_goal("Prepare for exam")
            milestone = goals.add_milestone(goal["id"], "Finish practice set")
            panel = GoalManagerPanel(database)
            panel._refresh_goals(goal["id"])
            existing_button = panel.complete_milestone_button

            self.assertEqual("Mark complete", existing_button.text())
            existing_button.click()
            self.assertIs(existing_button, panel.complete_milestone_button)
            self.assertEqual("Reopen milestone", existing_button.text())
            self.assertEqual("completed", goals.list_milestones(goal["id"])[0]["status"])

            existing_button.click()
            self.assertEqual("Mark complete", existing_button.text())
            self.assertEqual("pending", goals.list_milestones(goal["id"])[0]["status"])
            panel.deleteLater()
            self.application.processEvents()


if __name__ == "__main__":
    unittest.main()
