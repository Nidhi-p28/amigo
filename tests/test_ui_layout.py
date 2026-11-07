from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication, QScrollArea

from app.features_ui import GlobalSearchDialog, ProfileDialog
from app.storage import SqliteDatabase
from app.help_ui import HelpDialog, HELP_SECTIONS
from app.today import TodayService
from app.today_ui import TodayPanel
from app.ui import HistoryDialog, MainWindow


class MainWindowWorkspaceLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_today_goals_knowledge_and_calendar_share_the_workspace_column(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = SqliteDatabase(root / "amigo.sqlite3", copy_legacy=False)
            with patch("app.ui.APP_DATA_DIR", root / "app-data"), patch("app.ui.PROJECT_ROOT", root):
                window = MainWindow(database)
                window.agent.health_check = lambda: "Test Ollama status"
                window.show()
                self.application.processEvents()
                self.assertEqual(6, window.workspace_stack.count())
                self.assertIs(window.workspace_stack.currentWidget(), window.today_panel)
                self.assertEqual("Amigo", window.windowTitle())
                self.assertEqual("?", window.help_button.text())
                for button in (
                    window.today_button,
                    window.goals_button,
                    window.projects_button,
                    window.learning_button,
                    window.knowledge_button,
                    window.calendar_button,
                    window.search_button,
                    window.profile_button,
                    window.help_button,
                ):
                    self.assertTrue(button.isVisible())
                    self.assertTrue(button.isEnabled())
                    top_left = button.mapTo(window, QPoint(0, 0))
                    self.assertLessEqual(top_left.x() + button.width(), window.width())
                help_dialog = HelpDialog(window, "Projects")
                self.assertEqual(len(HELP_SECTIONS), help_dialog.topic_list.count())
                self.assertEqual("Projects", help_dialog.topic_list.currentItem().text())
                help_dialog.close()
                help_dialog.deleteLater()
                with patch.object(GlobalSearchDialog, "exec", return_value=0) as search_exec:
                    window.search_button.click()
                    search_exec.assert_called_once()
                with patch.object(ProfileDialog, "exec", return_value=0) as profile_exec:
                    window.profile_button.click()
                    profile_exec.assert_called_once()
                with patch.object(HelpDialog, "exec", return_value=0) as help_exec:
                    window.help_button.click()
                    help_exec.assert_called_once()
                window.new_button.click()
                self.assertEqual("New chat · ready", window.status_label.text())
                with patch.object(HistoryDialog, "exec", return_value=0) as history_exec:
                    window.history_button.click()
                    history_exec.assert_called_once()

                window.goals_button.click()
                self.assertIs(window.workspace_stack.currentWidget(), window.goals_panel)
                window.projects_button.click()
                self.assertIs(window.workspace_stack.currentWidget(), window.projects_panel)
                window.learning_button.click()
                self.assertIs(window.workspace_stack.currentWidget(), window.learning_panel)
                window.knowledge_button.click()
                self.assertIs(window.workspace_stack.currentWidget(), window.knowledge_panel)
                window.calendar_button.click()
                self.assertIs(window.workspace_stack.currentWidget(), window.calendar_panel)
                window.goals_button.click()
                self.assertIs(window.workspace_stack.currentWidget(), window.goals_panel)
                window.today_button.click()
                self.assertIs(window.workspace_stack.currentWidget(), window.today_panel)

                self.application.processEvents()
                if window.health_worker is not None:
                    window.health_worker.wait(3000)
                    self.application.processEvents()
                window._quitting = True
                window.close()
                window.deleteLater()
                self.application.processEvents()


class TodayPanelCompactLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_dashboard_has_no_page_scroll_and_checklist_grows_with_items(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = SqliteDatabase(Path(directory) / "amigo.sqlite3", copy_legacy=False)
            panel = TodayPanel(database)
            panel.resize(460, 457)
            panel.show()
            self.application.processEvents()
            panel._fit_component_heights()
            self.application.processEvents()

            empty_height = panel.activity_card.height()
            self.assertFalse(panel.activity_list.isVisible())
            self.assertEqual([], panel.findChildren(QScrollArea))

            TodayService(database).add_activity_item("Probability practice")
            panel.refresh()
            self.application.processEvents()
            panel._fit_component_heights()
            self.application.processEvents()

            self.assertTrue(panel.activity_list.isVisible())
            self.assertGreater(panel.activity_card.height(), empty_height)
            for card_name in (
                "focus_card", "goal_card", "activity_card", "heatmap_card",
                "continue_card", "curiosity_card", "garden_card",
            ):
                card = getattr(panel, card_name)
                self.assertLessEqual(card.geometry().bottom() + 1, panel.height(), card_name)
            panel.deleteLater()
            self.application.processEvents()


if __name__ == "__main__":
    unittest.main()
