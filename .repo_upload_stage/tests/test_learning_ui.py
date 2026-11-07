from __future__ import annotations

import os
import tempfile
import unittest
import json
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog

from app.learning_ui import QUIZ_RESPONSE_SCHEMA, LearningPanel, QuizGenerationWorker, QuizTakeDialog
from app.services import GoalService, KnowledgeLinkService
from app.storage import SqliteDatabase


class LearningPanelActionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_state_reflection_and_recommendation_controls(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = SqliteDatabase(Path(directory) / "amigo.sqlite3", copy_legacy=False)
            goal = GoalService(database).create_goal("Prepare for biology")
            topic_id = KnowledgeLinkService(database).link_topic_to_goal(goal["id"], "Cell division")["topic_id"]
            panel = LearningPanel(database)

            self.assertTrue(panel.save_state_button.isEnabled())
            panel.state_combo.setCurrentIndex(panel.state_combo.findData("learning"))
            panel.save_state_button.click()
            self.assertEqual("learning", panel.learning.topics()[0]["learning_state"])
            self.assertEqual(1, panel.recommendation_list.count())

            panel.recommendation_list.setCurrentRow(0)
            self.assertTrue(panel.accept_recommendation.isEnabled())
            panel.accept_recommendation.click()
            self.assertIn("Accepted", panel.recommendation_list.item(0).text())
            self.assertFalse(panel.accept_recommendation.isEnabled())
            panel.postpone_recommendation.click()
            self.assertIn("No recommendations yet", panel.recommendation_list.item(0).text())
            panel.refresh()
            self.assertIn("No recommendations yet", panel.recommendation_list.item(0).text())

            with patch("app.learning_ui.QInputDialog.getMultiLineText", return_value=("I can explain the phases.", True)):
                panel.reflection_button.click()
            self.assertTrue(any(row["evidence_type"] == "reflection" for row in panel.learning.evidence(topic_id)))
            self.assertEqual("pending", panel.learning.recommendations()[0]["status"])

            second_goal = GoalService(database).create_goal("Review genetics")
            KnowledgeLinkService(database).link_topic_to_goal(second_goal["id"], "Inheritance")
            panel.refresh()
            self.assertEqual(2, panel.topic_list.count())
            self.assertEqual(topic_id, str(panel.topic_list.currentItem().data(Qt.ItemDataRole.UserRole)))
            panel.deleteLater()
            self.application.processEvents()

    def test_quiz_answers_require_every_question_and_parser_accepts_json(self) -> None:
        quiz = {
            "title": "Cell division · quick quiz",
            "sources": ["cell-notes.md"],
            "questions": [
                {"question": "Which stage follows prophase?", "options": ["Metaphase", "Telophase"],
                 "answer_index": 0, "explanation": "Metaphase follows prophase."},
                {"question": "What separates?", "options": ["Chromosomes", "Cell walls"],
                 "answer_index": 0, "explanation": "Chromosomes separate."},
            ],
        }
        dialog = QuizTakeDialog(quiz)
        with patch("app.learning_ui.QMessageBox.information") as information:
            dialog.submit_button.click()
            information.assert_called_once()
        for group in dialog._groups:
            group.button(0).click()
        dialog.submit_button.click()
        self.assertEqual(QDialog.DialogCode.Accepted, dialog.result())
        self.assertEqual([0, 0], dialog.answers())

        parsed = QuizGenerationWorker._json_object('Here is the quiz:\n```json\n{"questions": []}\n```\nDone.')
        self.assertEqual([], parsed["questions"])
        with self.assertRaisesRegex(ValueError, "incomplete quiz"):
            QuizGenerationWorker._json_object('{"questions":[{"question":"unfinished"}')
        dialog.deleteLater()
        self.application.processEvents()

    def test_quiz_worker_requests_constrained_json_and_saves_the_response(self) -> None:
        questions = [
            {"question": f"Question {index}?", "options": ["A", "B", "C", "D"],
             "answer_index": 0, "explanation": "The notes support A."}
            for index in range(5)
        ]
        response = Mock()
        response.json.return_value = {"done_reason": "stop", "message": {"content": json.dumps({"questions": questions})}}
        service = Mock()
        service.quiz_source.return_value = {
            "topic": {"name": "Cell division"},
            "chunks": [{"source_path": "cell.md", "source_locator": "", "title": "Mitosis", "body": "Cells divide."}],
        }
        service.save_quiz.return_value = {"id": "quiz-1", "questions": questions}
        created: list[dict] = []
        failed: list[str] = []
        worker = QuizGenerationWorker(None, "topic-1")
        worker.created.connect(created.append)
        worker.failed.connect(failed.append)

        with patch("app.learning_ui.LearningService", return_value=service), patch(
            "app.learning_ui.requests.post", return_value=response
        ) as request:
            worker.run()

        self.assertEqual([], failed)
        self.assertEqual("quiz-1", created[0]["id"])
        self.assertEqual(QUIZ_RESPONSE_SCHEMA, request.call_args.kwargs["json"]["format"])
        self.assertEqual(4, service.quiz_source.call_args.kwargs["limit"])


if __name__ == "__main__":
    unittest.main()
